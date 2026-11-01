"""助手专用适配器的出站边界，所有网络均mock。"""
from contextlib import ExitStack
import json
from unittest.mock import patch, Mock
from tests.support import DatabaseTestCase
from app.config import settings
from app.services import assistant_provider as provider


class AssistantProviderTests(DatabaseTestCase):
    def configured(self, **changes):
        stack = ExitStack()
        self.addCleanup(stack.close)
        values = dict(ASSISTANT_EXTERNAL_ENABLED=True, ASSISTANT_TOOL_MODEL_VERIFIED=True,
                      ASSISTANT_MODEL='glm-5.3-flash', ASSISTANT_API_KEY='mock-secret',
                      ASSISTANT_BASE_URL='https://open.bigmodel.cn/api/paas/v4')
        values.update(changes)
        for key, value in values.items():
            stack.enter_context(patch.object(settings, key, value))

    def fake_response(self, content):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = content
        opener = Mock()
        opener.open.return_value = response
        return opener

    def response_bytes(self, message, finish_reason='stop'):
        return json.dumps({'choices': [{'finish_reason': finish_reason,
                                      'message': message}]}, ensure_ascii=False).encode('utf-8')

    def tool_message(self, **changes):
        message = {'content': None,
                   'tool_calls': [{'id': 'call_1', 'type': 'function',
                                   'function': {'name': 'list_matches', 'arguments': '{}'}}],
                   'reasoning_content': '先查询实际赛事再回答。'}
        message.update(changes)
        return message

    def summary_configured(self, **changes):
        values = dict(ASSISTANT_SUMMARY_ENABLED=True, ASSISTANT_SUMMARY_MAX_BYTES=4000,
                      ASSISTANT_SUMMARY_TIMEOUT_SECONDS=8)
        values.update(changes)
        self.configured(**values)

    def test_default_never_calls_network_or_reuses_review_key(self):
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(provider.ProviderError):
                provider.complete([], [], 10)
            network.assert_not_called()

    def test_latest_flash_enables_thinking_with_bounded_generation(self):
        self.configured()
        message = self.tool_message(reasoning_content='  先查赛事\n再按结果回答。  ')
        opener = self.fake_response(self.response_bytes(message, 'tool_calls'))
        with patch('urllib.request.build_opener', return_value=opener):
            result = provider.complete([{'role': 'user', 'content': '查赛事'}], [{'type': 'function'}], 80)
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'glm-5.3-flash')
        self.assertEqual(payload['thinking'], {'type': 'enabled', 'clear_thinking': False})
        self.assertEqual(payload['reasoning_effort'], 'low')
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertEqual(payload['temperature'], 1.0)
        self.assertEqual(payload['top_p'], 0.95)
        self.assertEqual(payload['tool_choice'], 'auto')
        self.assertEqual(result['tool_calls'], message['tool_calls'])
        self.assertEqual(result['reasoning_content'], message['reasoning_content'])
        self.assertEqual(result['content'], '')
        self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 20)

    def test_explicit_legacy_flash_preserves_non_thinking_protocol(self):
        self.configured(ASSISTANT_MODEL='glm-4.7-flash')
        message = self.tool_message()
        del message['reasoning_content']
        opener = self.fake_response(self.response_bytes(message, 'tool_calls'))
        with patch('urllib.request.build_opener', return_value=opener):
            result = provider.complete([{'role': 'user', 'content': '查赛事'}], [{'type': 'function'}], 80)
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['model'], 'glm-4.7-flash')
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['max_tokens'], 1200)
        self.assertEqual(payload['temperature'], 0.2)
        self.assertNotIn('reasoning_effort', payload)
        self.assertNotIn('top_p', payload)
        self.assertEqual(payload['tool_choice'], 'auto')
        self.assertEqual(result['tool_calls'], message['tool_calls'])
        self.assertNotIn('reasoning_content', result)

    def test_tool_round_trip_returns_original_reasoning_in_next_request(self):
        self.configured()
        message = self.tool_message(reasoning_content='  查实际赛事；不根据昵称猜测。\n  ')
        opener = self.fake_response(self.response_bytes(message, 'tool_calls'))
        opener.open.side_effect = [
            opener.open.return_value,
            self.fake_response(self.response_bytes({'content': '赛事已查询。'})).open.return_value,
        ]
        messages = [{'role': 'user', 'content': '查赛事'}]
        tools = [{'type': 'function'}]
        with patch('urllib.request.build_opener', return_value=opener):
            first = provider.complete(messages, tools, 20)
            messages.append(first)
            messages.append({'role': 'tool', 'tool_call_id': 'call_1',
                             'content': json.dumps({'matches': []})})
            second = provider.complete(messages, tools, 20)
        second_payload = json.loads(opener.open.call_args_list[1].args[0].data)
        self.assertEqual(second_payload['messages'][1]['reasoning_content'],
                         message['reasoning_content'])
        self.assertEqual(second_payload['messages'][1]['tool_calls'], message['tool_calls'])
        self.assertEqual(second_payload['messages'][2]['tool_call_id'], 'call_1')
        self.assertEqual(second['content'], '赛事已查询。')
        self.assertNotIn('reasoning_content', second)

    def test_latest_flash_accepts_missing_or_empty_reasoning_without_inventing_it(self):
        self.configured()
        missing = self.tool_message()
        del missing['reasoning_content']
        cases = [missing, self.tool_message(reasoning_content=''),
                 self.tool_message(reasoning_content=' \n\t')]
        for message in cases:
            with self.subTest(message=message):
                opener = self.fake_response(self.response_bytes(message, 'tool_calls'))
                with patch('urllib.request.build_opener', return_value=opener):
                    result = provider.complete([], [], 10)
                self.assertEqual(result['tool_calls'], message['tool_calls'])
                self.assertEqual(result['content'], '')
                if 'reasoning_content' in message:
                    self.assertEqual(result['reasoning_content'], message['reasoning_content'])
                else:
                    self.assertNotIn('reasoning_content', result)

    def test_tool_response_rejects_present_nonstring_reasoning(self):
        self.configured()
        for reasoning in (None, 42, False, [], {}):
            with self.subTest(reasoning=reasoning):
                message = self.tool_message(reasoning_content=reasoning)
                opener = self.fake_response(self.response_bytes(message, 'tool_calls'))
                with patch('urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.complete([], [], 10)
                self.assertEqual(raised.exception.code, 'provider_response_invalid')

    def test_reasoning_field_must_be_a_string_even_without_tools(self):
        self.configured()
        for reasoning in (None, 42, False, [], {}):
            with self.subTest(reasoning=reasoning):
                opener = self.fake_response(self.response_bytes(
                    {'content': '不能当作有效回答。', 'reasoning_content': reasoning}))
                with patch('urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.complete([], [], 10)
                self.assertEqual(raised.exception.code, 'provider_response_invalid')

    def test_reasoning_utf8_budget_accepts_boundary_and_rejects_one_character_more(self):
        self.configured()
        reasoning = '查' * 8000
        self.assertEqual(len(reasoning.encode('utf-8')), 24000)
        opener = self.fake_response(self.response_bytes(self.tool_message(reasoning_content=reasoning),
                                                       'tool_calls'))
        with patch('urllib.request.build_opener', return_value=opener):
            result = provider.complete([], [], 10)
        self.assertEqual(result['reasoning_content'], reasoning)
        opener = self.fake_response(self.response_bytes(self.tool_message(reasoning_content=reasoning + '查'),
                                                       'tool_calls'))
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(provider.ProviderError) as raised:
                provider.complete([], [], 10)
        self.assertEqual(raised.exception.code, 'provider_reasoning_limit')

    def test_final_answer_can_omit_reasoning_without_inventing_a_field(self):
        self.configured()
        opener = self.fake_response(self.response_bytes({'content': '你好。'}))
        with patch('urllib.request.build_opener', return_value=opener):
            result = provider.complete([], [], 10)
        self.assertEqual(result['content'], '你好。')
        self.assertNotIn('reasoning_content', result)

    def test_context_over_budget_is_rejected_before_network(self):
        self.configured()
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(provider.ProviderError) as raised:
                provider.complete([{'role': 'user', 'content': '查' * 33334}], [], 10)
        self.assertEqual(raised.exception.code, 'context_limit')
        network.assert_not_called()

    def test_disallows_arbitrary_endpoints_and_credentials_in_url(self):
        for endpoint in ('http://127.0.0.1:80/api', 'https://evil.test/api/paas/v4',
                         'https://x:secret@open.bigmodel.cn/api/paas/v4', 'https://open.bigmodel.cn/api/paas/v4?key=secret'):
            self.configured(ASSISTANT_BASE_URL=endpoint)
            with patch('urllib.request.build_opener') as network:
                with self.assertRaises(provider.ProviderError):
                    provider.complete([], [], 10)
                network.assert_not_called()

    def test_incomplete_or_oversized_response_is_not_treated_as_answer(self):
        self.configured()
        cases = [json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}).encode(),
                 b'x'*131073, b'not-json']
        for content in cases:
            with patch('urllib.request.build_opener', return_value=self.fake_response(content)):
                with self.assertRaises(provider.ProviderError):
                    provider.complete([], [], 10)

    def test_response_read_and_response_size_have_a_fixed_upper_bound(self):
        self.configured()
        opener = self.fake_response(b'x' * 131073)
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(provider.ProviderError) as raised:
                provider.complete([], [], 10)
        opener.open.return_value.read.assert_called_once_with(131073)
        self.assertEqual(raised.exception.code, 'provider_response_limit')

    def test_transport_failure_has_no_supplier_body_or_secret(self):
        self.configured()
        opener = Mock()
        opener.open.side_effect = TimeoutError('private upstream body mock-secret')
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(provider.ProviderError) as raised:
                provider.complete([], [], 1)
        self.assertEqual(raised.exception.code, 'provider_request_failed')
        self.assertNotIn('mock-secret', str(raised.exception))

    def test_redirect_cannot_forward_authorization(self):
        with self.assertRaises(provider.ProviderError):
            provider.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test')

    def test_unknown_models_cannot_silently_fallback_or_call_network(self):
        for model in ('glm-5.3', 'glm-5.3-flashx', 'arbitrary-model', ''):
            with self.subTest(model=model):
                self.configured(ASSISTANT_MODEL=model)
                with patch('urllib.request.build_opener') as network:
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.complete([], [], 10)
                    network.assert_not_called()
                self.assertEqual(raised.exception.code, 'provider_not_configured')

    def test_summary_disabled_even_with_a_configured_tool_model_never_calls_network(self):
        self.configured(ASSISTANT_SUMMARY_ENABLED=False)
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(provider.ProviderError) as raised:
                provider.summarize([{'role': 'user', 'content': '生成摘要'}], 8)
        self.assertEqual(raised.exception.code, 'provider_summary_disabled')
        network.assert_not_called()

    def test_summary_requires_its_own_enabled_and_fully_configured_provider(self):
        for changes in ({'ASSISTANT_EXTERNAL_ENABLED': False},
                        {'ASSISTANT_TOOL_MODEL_VERIFIED': False},
                        {'ASSISTANT_API_KEY': ''},
                        {'ASSISTANT_MODEL': 'glm-5.3-flashx'}):
            with self.subTest(changes=changes):
                self.summary_configured(**changes)
                with patch('urllib.request.build_opener') as network:
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.summarize([], 8)
                self.assertEqual(raised.exception.code, 'provider_not_configured')
                network.assert_not_called()

    def test_latest_summary_payload_has_no_tools_and_discards_reasoning(self):
        self.summary_configured()
        messages = [{'role': 'system', 'content': '只压缩已提供的文字。'},
                    {'role': 'user', 'content': '用户正在讨论起源杯。'}]
        content = ' {"summary":"用户正在讨论起源杯"}\n'
        opener = self.fake_response(self.response_bytes(
            {'content': content, 'tool_calls': [], 'reasoning_content': '仅供内部计算，不得保存。'}))
        with patch('urllib.request.build_opener', return_value=opener):
            result = provider.summarize(messages, 30)
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['messages'], messages)
        self.assertEqual(payload['model'], 'glm-5.3-flash')
        self.assertEqual(payload['thinking'], {'type': 'enabled', 'clear_thinking': False})
        self.assertEqual(payload['reasoning_effort'], 'low')
        self.assertEqual(payload['max_tokens'], 2048)
        self.assertEqual(payload['temperature'], 1.0)
        self.assertEqual(payload['top_p'], 0.95)
        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertNotIn('reasoning_content', payload)
        self.assertEqual(result, content)
        self.assertIsInstance(result, str)
        self.assertNotIn('不得保存', result)

    def test_explicit_legacy_summary_keeps_disabled_thinking_without_tools(self):
        self.summary_configured(ASSISTANT_MODEL='glm-4.7-flash')
        opener = self.fake_response(self.response_bytes({'content': '旧模型摘要。'}))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(provider.summarize([], 8), '旧模型摘要。')
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['model'], 'glm-4.7-flash')
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['max_tokens'], 1200)
        self.assertEqual(payload['temperature'], 0.2)
        for name in ('tools', 'tool_choice', 'reasoning_effort', 'top_p'):
            self.assertNotIn(name, payload)

    def test_summary_timeout_never_exceeds_eight_seconds_or_remaining_budget(self):
        self.summary_configured()
        for budget, expected in ((30, 8), (3.5, 3.5), (0.04, 0.04)):
            with self.subTest(budget=budget):
                opener = self.fake_response(self.response_bytes({'content': '有效摘要。'}))
                with patch('urllib.request.build_opener', return_value=opener):
                    provider.summarize([], budget)
                self.assertEqual(opener.open.call_args.kwargs['timeout'], expected)
        self.summary_configured(ASSISTANT_SUMMARY_TIMEOUT_SECONDS=4)
        opener = self.fake_response(self.response_bytes({'content': '有效摘要。'}))
        with patch('urllib.request.build_opener', return_value=opener):
            provider.summarize([], 30)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 4)

    def test_summary_exhausted_or_invalid_time_budget_never_calls_network(self):
        self.summary_configured()
        for budget in (0, -1, float('nan'), float('inf'), None, 'bad-timeout'):
            with self.subTest(budget=budget):
                with patch('urllib.request.build_opener') as network:
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.summarize([], budget)
                self.assertEqual(raised.exception.code, 'time_budget')
                network.assert_not_called()

    def test_summary_requires_a_complete_nonempty_string_and_never_accepts_tools(self):
        self.summary_configured()
        cases = [(self.response_bytes({'content': value}), 'provider_response_invalid')
                 for value in (None, '', ' \n\t', 123, False, [], {})]
        cases += [(self.response_bytes({'content': '伪装的无工具摘要', 'tool_calls': value}),
                   'provider_response_invalid') for value in (False, 0, '', {})]
        cases += [(self.response_bytes({'content': '部分摘要'}, 'length'), 'provider_response_invalid'),
                  (self.response_bytes({'content': '不能返回工具'}, 'tool_calls'), 'provider_response_invalid'),
                  (self.response_bytes(self.tool_message(content='伪装成正常摘要')), 'provider_response_invalid'),
                  (b'not-json mock-secret', 'provider_response_invalid'),
                  (b'x' * 131073, 'provider_response_limit')]
        for index, (body, code) in enumerate(cases):
            with self.subTest(index=index):
                opener = self.fake_response(body)
                with patch('urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.summarize([], 8)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn('mock-secret', str(raised.exception))

    def test_summary_utf8_content_budget_preserves_boundary_and_rejects_overflow(self):
        self.summary_configured()
        content = '中' * 1333 + 'a'
        self.assertEqual(len(content.encode('utf-8')), 4000)
        opener = self.fake_response(self.response_bytes({'content': content}))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(provider.summarize([], 8), content)
        opener = self.fake_response(self.response_bytes({'content': content + 'b'}))
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(provider.ProviderError) as raised:
                provider.summarize([], 8)
        self.assertEqual(raised.exception.code, 'provider_summary_limit')

    def test_summary_validates_but_never_returns_reasoning(self):
        self.summary_configured()
        for reasoning in (None, 7, False, {}, [], '中' * 8001):
            with self.subTest(reasoning_type=type(reasoning).__name__):
                opener = self.fake_response(self.response_bytes(
                    {'content': '摘要正文', 'reasoning_content': reasoning}))
                with patch('urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(provider.ProviderError) as raised:
                        provider.summarize([], 8)
                self.assertEqual(raised.exception.code,
                                 'provider_reasoning_limit' if isinstance(reasoning, str)
                                 else 'provider_response_invalid')

    def test_summary_keeps_existing_endpoint_and_request_size_boundaries(self):
        for endpoint in ('http://127.0.0.1:80/api', 'https://evil.test/api/paas/v4',
                         'https://x:secret@open.bigmodel.cn/api/paas/v4',
                         'https://open.bigmodel.cn/api/paas/v4?key=secret'):
            with self.subTest(endpoint=endpoint):
                self.summary_configured(ASSISTANT_BASE_URL=endpoint)
                with patch('urllib.request.build_opener') as network:
                    with self.assertRaises(provider.ProviderError):
                        provider.summarize([], 8)
                network.assert_not_called()
        self.summary_configured()
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(provider.ProviderError) as raised:
                provider.summarize([{'role': 'user', 'content': '查' * 33334}], 8)
        self.assertEqual(raised.exception.code, 'context_limit')
        network.assert_not_called()

    def test_summary_transport_errors_hide_supplier_body_and_use_no_redirect(self):
        self.summary_configured()
        opener = Mock()
        opener.open.side_effect = TimeoutError('private upstream body mock-secret')
        with patch('urllib.request.build_opener', return_value=opener) as network:
            with self.assertRaises(provider.ProviderError) as raised:
                provider.summarize([], 8)
        network.assert_called_once_with(provider.NoRedirect)
        self.assertEqual(raised.exception.code, 'provider_request_failed')
        self.assertNotIn('mock-secret', str(raised.exception))
        opener.open.side_effect = provider.ProviderError('provider_redirect_blocked')
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(provider.ProviderError) as raised:
                provider.summarize([], 8)
        self.assertEqual(raised.exception.code, 'provider_redirect_blocked')
