"""Text-only LangChain model boundary around the guarded provider callback.

The callback owns provider selection, schema policy, budgets and validation.  This
module only translates messages; it never opens a network or database connection.
"""
from collections.abc import Callable, Sequence
from copy import deepcopy
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


ModelCall = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _content(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError('Only text message content is supported')
    return value


def _tool_calls(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep wire arguments verbatim while giving LangChain parsed arguments."""
    if not isinstance(value, list):
        raise TypeError('Tool calls must be a list')
    raw_calls, parsed_calls = [], []
    for call in value:
        if not isinstance(call, dict) or call.get('type') != 'function':
            raise ValueError('Only function tool calls are supported')
        function = call.get('function')
        if (not isinstance(function, dict) or not isinstance(call.get('id'), str)
                or not isinstance(function.get('name'), str)
                or not isinstance(function.get('arguments'), str)):
            raise ValueError('Invalid function tool call')
        try:
            arguments = json.loads(function['arguments'])
        except (ValueError, TypeError):
            raise ValueError('Tool arguments must be a JSON object') from None
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be a JSON object')
        # Only protocol fields leave this boundary.  In particular, no framework
        # tool metadata is copied into the provider's messages.
        raw_calls.append({'id': call['id'], 'type': 'function', 'function': {
            'name': function['name'], 'arguments': function['arguments']}})
        parsed_calls.append({'id': call['id'], 'name': function['name'],
                             'args': arguments, 'type': 'tool_call'})
    return raw_calls, parsed_calls


def message_from_dict(value: dict[str, Any]) -> BaseMessage:
    """Convert the existing provider protocol without inventing optional fields."""
    if not isinstance(value, dict):
        raise TypeError('A provider message must be a dictionary')
    content = _content(value.get('content'))
    role = value.get('role')
    if role == 'system':
        return SystemMessage(content=content)
    if role == 'user':
        return HumanMessage(content=content)
    if role == 'tool':
        identifier = value.get('tool_call_id')
        if not isinstance(identifier, str):
            raise TypeError('A tool message requires its original tool call id')
        return ToolMessage(content=content, tool_call_id=identifier)
    if role != 'assistant':
        raise ValueError('Unsupported message role')

    additional, parsed_calls = {}, []
    if 'tool_calls' in value:
        raw_calls, parsed_calls = _tool_calls(value['tool_calls'])
        additional['tool_calls'] = raw_calls
    if 'reasoning_content' in value:
        additional['reasoning_content'] = _content(value['reasoning_content'])
    return AIMessage(content=content, additional_kwargs=additional, tool_calls=parsed_calls)


def message_to_dict(message: BaseMessage) -> dict[str, Any]:
    """Serialize only the four message roles accepted by the existing provider."""
    # Chunks, FunctionMessage, ChatMessage and multimodal content are not part of
    # this application's provider protocol.
    roles = {SystemMessage: 'system', HumanMessage: 'user', AIMessage: 'assistant', ToolMessage: 'tool'}
    role = roles.get(type(message))
    if role is None:
        raise TypeError('Unsupported message type')
    result = {'role': role, 'content': _content(message.content)}
    if role == 'tool':
        if not isinstance(message.tool_call_id, str):
            raise TypeError('A tool message requires its original tool call id')
        result['tool_call_id'] = message.tool_call_id
    elif role == 'assistant':
        if message.invalid_tool_calls:
            raise ValueError('Invalid tool calls must be normalized before conversion')
        if 'tool_calls' in message.additional_kwargs:
            raw_calls, parsed_calls = _tool_calls(message.additional_kwargs['tool_calls'])
            expected = [(call['id'], call['name'], call['args']) for call in parsed_calls]
            actual = [(call.get('id'), call.get('name'), call.get('args')) for call in message.tool_calls]
            if actual != expected:
                raise ValueError('Raw and parsed tool calls do not match')
            result['tool_calls'] = raw_calls
        elif message.tool_calls:
            # A caller-created standard AIMessage has no original argument
            # string.  Encode its arguments once, then use the same validator.
            raw_calls = [{'id': call.get('id'), 'type': 'function', 'function': {
                'name': call.get('name'), 'arguments': json.dumps(call.get('args'), ensure_ascii=False)}}
                for call in message.tool_calls]
            result['tool_calls'], _ = _tool_calls(raw_calls)
        if 'reasoning_content' in message.additional_kwargs:
            result['reasoning_content'] = _content(message.additional_kwargs['reasoning_content'])
    return result


class GuardedChatModel(BaseChatModel):
    """A fixed callback model; LangChain cannot select a different provider."""

    _model_call: ModelCall = PrivateAttr()

    def __init__(self, model_call: ModelCall):
        if not callable(model_call):
            raise TypeError('model_call must be a callable')
        super().__init__(cache=False, callbacks=[], verbose=False)
        self._model_call = model_call

    @property
    def _llm_type(self) -> str:
        return 'major-guarded-callback'

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {}

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> 'GuardedChatModel':
        # create_agent binds its execution tools here.  The trusted callback
        # supplies the fixed provider schema; binding never changes that schema
        # or forwards framework-injected arguments to the provider.
        return self

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        response = self._model_call([message_to_dict(message) for message in messages])
        if not isinstance(response, dict) or response.get('role') != 'assistant':
            raise ValueError('The model callback must return an assistant message')
        message = message_from_dict(deepcopy(response))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                         run_manager: Any = None, **kwargs: Any) -> ChatResult:
        # BaseChatModel's default async implementation uses an executor.  The
        # callback owns a request-scoped SQLAlchemy session, so keep this call on
        # the current thread instead.
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
