"""Pure LangChain adapter tests: no settings, .env, database or real provider."""
import asyncio
from copy import deepcopy
import json
import threading
import unittest

from langchain.agents import create_agent
from langchain_core.messages import (AIMessage, AIMessageChunk, ChatMessage, FunctionMessage,
                                     HumanMessage, SystemMessage, ToolMessage)
from langchain_core.tools import tool
from langsmith import tracing_context

from app.services.assistant_langchain_model import GuardedChatModel, message_from_dict, message_to_dict


def assistant(content='  已查询。\n', *, calls=None, reasoning=None):
    result = {'role': 'assistant', 'content': content}
    if calls is not None:
        result['tool_calls'] = calls
    if reasoning is not None:
        result['reasoning_content'] = reasoning
    return result


def call(identifier='call_1', name='list_matches', arguments=' { "query" : "杯赛", "limit": 2 }\n'):
    return {'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


class AssistantLangChainModelTests(unittest.TestCase):
    def setUp(self):
        self.tracing = tracing_context(enabled=False)
        self.tracing.__enter__()
        self.addCleanup(self.tracing.__exit__, None, None, None)

    def test_four_roles_round_trip_without_adding_optional_fields(self):
        values = [{'role': 'system', 'content': '  系统\n'}, {'role': 'user', 'content': '\t问题  '},
                  assistant(), {'role': 'tool', 'content': ' {"ok": true}\n', 'tool_call_id': 'same-ID_2'}]
        for value in values:
            with self.subTest(role=value['role']):
                self.assertEqual(message_to_dict(message_from_dict(value)), value)

    def test_explicit_empty_tool_calls_are_preserved_and_missing_reasoning_is_not_added(self):
        value = assistant(calls=[])
        self.assertEqual(message_to_dict(message_from_dict(value)), value)
        self.assertNotIn('reasoning_content', message_from_dict(value).additional_kwargs)

    def test_raw_tool_arguments_and_reasoning_are_lossless_and_parsed_for_framework(self):
        raw = assistant(calls=[call(), call('call_2', 'unknown_tool', '{}')], reasoning='  同轮思考\n + 保留  ')
        message = message_from_dict(raw)
        self.assertIsInstance(message, AIMessage)
        self.assertEqual(message.additional_kwargs['tool_calls'], raw['tool_calls'])
        self.assertEqual(message.tool_calls[0]['args'], {'query': '杯赛', 'limit': 2})
        self.assertEqual(message.tool_calls[1]['name'], 'unknown_tool')
        self.assertEqual(message_to_dict(message), raw)
        raw['tool_calls'][0]['function']['arguments'] = '{}'
        self.assertNotEqual(message.additional_kwargs['tool_calls'], raw['tool_calls'])

    def test_serialization_does_not_expose_framework_metadata(self):
        message = AIMessage(content='答复', id='framework-message-id', name='framework-name',
                            additional_kwargs={'extra': {'schema': 'internal'}},
                            response_metadata={'provider': 'internal'})
        tool = ToolMessage(content='结果', tool_call_id='original-ID', name='internal-tool-name',
                           artifact={'private': 'internal'}, status='success')
        self.assertEqual(message_to_dict(message), {'role': 'assistant', 'content': '答复'})
        self.assertEqual(message_to_dict(tool), {'role': 'tool', 'content': '结果', 'tool_call_id': 'original-ID'})

    def test_standard_framework_tool_calls_are_encoded_without_inventing_ids(self):
        message = AIMessage(content='', tool_calls=[{'id': 'standard_1', 'name': 'list_matches', 'args': {'query': '中'}}])
        serialized = message_to_dict(message)
        self.assertEqual(json.loads(serialized['tool_calls'][0]['function']['arguments']), {'query': '中'})
        self.assertEqual(serialized['tool_calls'][0]['id'], 'standard_1')
        message.tool_calls[0]['id'] = None
        with self.assertRaises(ValueError):
            message_to_dict(message)

    def test_changed_raw_or_parsed_tool_call_is_rejected(self):
        message = message_from_dict(assistant(calls=[call()]))
        message.tool_calls[0]['args']['limit'] = 9
        with self.assertRaises(ValueError):
            message_to_dict(message)

    def test_unknown_message_types_and_multimodal_content_are_rejected(self):
        messages = [ChatMessage(content='text', role='custom'), FunctionMessage(content='text', name='old'),
                    AIMessageChunk(content='text'), HumanMessage(content=[{'type': 'text', 'text': 'text'}])]
        for message in messages:
            with self.subTest(kind=type(message).__name__):
                with self.assertRaises(TypeError):
                    message_to_dict(message)
        for content in (None, 1, [], {}):
            with self.subTest(content=content):
                with self.assertRaises(TypeError):
                    message_from_dict({'role': 'user', 'content': content})
        with self.assertRaises(ValueError):
            message_from_dict({'role': 'developer', 'content': 'text'})

    def test_invalid_unnormalized_tool_calls_are_rejected(self):
        invalid = [None, {}, [call(arguments='bad-json')], [call(arguments='[]')],
                   [call(arguments='null')], [dict(call(), id=None)], [dict(call(), type='other')]]
        for value in invalid:
            with self.subTest(calls=value):
                with self.assertRaises((TypeError, ValueError)):
                    message_from_dict(assistant(calls=value) if value is not None else
                                      {'role': 'assistant', 'content': '', 'tool_calls': None})
        with self.assertRaises(TypeError):
            message_from_dict({'role': 'tool', 'content': '结果', 'tool_call_id': 7})
        with self.assertRaises(TypeError):
            message_from_dict({'role': 'assistant', 'content': '', 'reasoning_content': None})

    def test_model_uses_callback_once_and_does_not_forward_binding_or_invocation_kwargs(self):
        observed = []
        response = assistant(calls=[call()], reasoning=' 临时思考 ')
        def complete(messages):
            observed.append(deepcopy(messages))
            return response
        model = GuardedChatModel(complete)
        bound = model.bind_tools([{'internal': 'not a provider schema'}], tool_choice='injected', framework='hidden')
        self.assertIs(bound, model)
        output = bound.invoke([SystemMessage(content='  系统\n'), HumanMessage(content=' 问题 ')],
                              stop=['ignored'], framework={'hidden': True})
        self.assertEqual(observed, [[{'role': 'system', 'content': '  系统\n'}, {'role': 'user', 'content': ' 问题 '}]])
        self.assertEqual(message_to_dict(output), response)
        self.assertIs(model.cache, False)
        self.assertEqual(model.callbacks, [])
        self.assertIs(model.verbose, False)

    def test_async_and_sync_callbacks_run_on_the_calling_thread(self):
        observed = []
        model = GuardedChatModel(lambda messages: observed.append(threading.get_ident()) or assistant(calls=[]))
        calling_thread = threading.get_ident()
        model.invoke([HumanMessage(content='同步')])
        asyncio.run(model.ainvoke([HumanMessage(content='异步')]))
        self.assertEqual(observed, [calling_thread, calling_thread])

    def test_create_agent_round_trip_keeps_wire_arguments_and_does_not_remember_reasoning(self):
        @tool
        def echo_query(query: str) -> str:
            """Return a scripted query without accessing any external service."""
            return query

        original = call('graph-ID_1', 'echo_query', '{\n "query" : "杯赛" } ')
        observed, observed_threads = [], []
        responses = [assistant('', calls=[original], reasoning=' 同轮思考\n '),
                     assistant('第一轮完成', calls=[]), assistant('新轮完成', calls=[])]
        def complete(messages):
            observed.append(deepcopy(messages))
            observed_threads.append(threading.get_ident())
            return responses.pop(0)

        agent = create_agent(model=GuardedChatModel(complete), tools=[echo_query])
        first = agent.invoke({'messages': [HumanMessage(content='第一轮问题')]})
        self.assertEqual(first['messages'][-1].content, '第一轮完成')
        self.assertEqual(observed[1][1], assistant('', calls=[original], reasoning=' 同轮思考\n '))
        self.assertEqual(observed[1][2], {'role': 'tool', 'tool_call_id': 'graph-ID_1', 'content': '杯赛'})
        self.assertEqual(observed_threads, [threading.get_ident(), threading.get_ident()])
        second = agent.invoke({'messages': [HumanMessage(content='全新问题')]})
        self.assertEqual(second['messages'][-1].content, '新轮完成')
        self.assertEqual(observed[2], [{'role': 'user', 'content': '全新问题'}])

    def test_callback_errors_propagate_without_retry(self):
        calls = []
        error = RuntimeError('scripted callback failure')
        def fail(messages):
            calls.append(messages)
            raise error
        with self.assertRaises(RuntimeError) as raised:
            GuardedChatModel(fail).invoke([HumanMessage(content='问题')])
        self.assertIs(raised.exception, error)
        self.assertEqual(len(calls), 1)

    def test_string_model_and_non_assistant_callback_response_are_rejected(self):
        with self.assertRaises(TypeError):
            GuardedChatModel('provider:model')
        with self.assertRaises(ValueError):
            GuardedChatModel(lambda messages: {'role': 'user', 'content': 'wrong'}).invoke([HumanMessage(content='问题')])


if __name__ == '__main__':
    unittest.main()
