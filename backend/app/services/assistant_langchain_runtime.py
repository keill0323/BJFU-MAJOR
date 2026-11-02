"""A real create_agent graph for one guarded, request-local model/tool turn.

No checkpointer, store, model retry, tracing export or cross-request graph cache.
Async graph hooks keep request-scoped database work on the original thread.
"""
import asyncio
import json

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langsmith import tracing_context

from app.services import assistant_provider as provider_api
from app.services.assistant_langchain_model import GuardedChatModel, message_from_dict


async def _middleware_required(**arguments):
    # Every call must pass through the service-owned authorization middleware.
    raise provider_api.ProviderError('unguarded_tool_execution')


class OrderedTools(AgentMiddleware):
    def __init__(self, execution):
        super().__init__()
        self.execution = execution
        self.condition = asyncio.Condition()
        self.failure = None

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        identifier = call['id']
        async with self.condition:
            try:
                if identifier not in self.execution.pending or identifier in self.execution.consumed:
                    raise provider_api.ProviderError('provider_response_invalid')
                await self.condition.wait_for(lambda: self.failure is not None or (
                    self.execution.tool_order and self.execution.tool_order[0] == identifier))
                if self.failure is not None:
                    raise self.failure
                result = self.execution.tool_response(identifier, call['name'], call['args'])
                return ToolMessage(content=result['content'], tool_call_id=identifier)
            except Exception as error:
                # ToolNode normally converts wrapper exceptions into model-
                # visible strings. Stop the turn before the next model request
                # instead, preserving the service's original error/partial path.
                if self.failure is None:
                    self.failure = error
                return ToolMessage(content=json.dumps({'status': 'error',
                    'message': '本轮工具执行已停止，请核查结果后重试。'}, ensure_ascii=False),
                    tool_call_id=identifier)
            finally:
                self.condition.notify_all()


async def _run(execution):
    guarded_tools = OrderedTools(execution)

    def model_call(messages):
        if guarded_tools.failure is not None:
            raise guarded_tools.failure
        return execution.model_response(messages)

    tools = [StructuredTool(name=item['function']['name'],
        description=item['function']['description'], args_schema=item['function']['parameters'],
        coroutine=_middleware_required) for item in execution.tools]
    # Unknown names are sanitized to this non-business tool before routing.
    # It is never advertised in execution.tools, the fixed provider schema.
    tools.append(StructuredTool(name='unknown_tool', description='Rejected non-business tool.',
        args_schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
        coroutine=_middleware_required))
    graph = create_agent(model=GuardedChatModel(model_call), tools=tools,
        middleware=[guarded_tools], checkpointer=None, store=None, cache=None, debug=False)
    with tracing_context(enabled=False):
        await graph.ainvoke({'messages': [message_from_dict(message) for message in execution.messages]},
            config={'callbacks': [], 'max_concurrency': 1,
                    'recursion_limit': (execution.max_steps + 1) * 4 + 10})
    if guarded_tools.failure is not None:
        raise guarded_tools.failure


def run(execution):
    """The existing synchronous FastAPI service supplies one request thread."""
    asyncio.run(_run(execution))
