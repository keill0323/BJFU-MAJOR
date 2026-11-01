"""Compaction through the actual agent loop, using isolated DB and mock models only."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from uuid import uuid4
from unittest.mock import patch
from fastapi import HTTPException

from tests.support import DatabaseTestCase
from tests.test_assistant_agent import AgentClock, ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.assistant_session import AssistantConversation, AssistantTurn
from app.services import assistant_service as service
from app.services import assistant_provider


SUMMARY = {
    'topics': ['用户在了解报名流程'],
    'preferences': ['偏好简洁中文说明'],
    'open_questions': ['想确认本队资格，需重新查询'],
    'source_notes': ['仅为已完成历史的有损摘要，不作为当前事实'],
}


def comparable_message_sizes(rows, actual_messages):
    """Same JSON representation on both sides; synthetic working context only."""
    before = [actual_messages[0]]
    for _, user, reply in rows:
        before.extend([{'role': 'user', 'content': user}, {'role': 'assistant', 'content': reply}])
    before.append(actual_messages[-1])
    size = lambda value: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    before_bytes, after_bytes = size(before), size(actual_messages)
    return {'messages_before_bytes': before_bytes, 'messages_after_bytes': after_bytes,
            'bytes_saved_percent': round((1 - after_bytes / before_bytes) * 100, 2)}


class AssistantCompactionAgentTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        values = {
            'ASSISTANT_ENABLED': True, 'ASSISTANT_MAX_STEPS': 3,
            'ASSISTANT_MAX_TOOL_CALLS': 4, 'ASSISTANT_TIMEOUT_SECONDS': 20,
            'ASSISTANT_SUMMARY_ENABLED': True,
            'ASSISTANT_SUMMARY_TRIGGER_BYTES': 1000,
            'ASSISTANT_SUMMARY_KEEP_TURNS': 2,
            'ASSISTANT_HISTORY_BUDGET_BYTES': 20000,
            'ASSISTANT_SUMMARY_MAX_BYTES': 4000,
            'ASSISTANT_SUMMARY_INPUT_MAX_BYTES': 20000,
            'ASSISTANT_SUMMARY_TIMEOUT_SECONDS': 8,
        }
        for key, value in values.items():
            mocked = patch.object(settings, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.actor = self.make_user(nickname='压缩集成用户')
        self.db.commit()
        self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']

    def seed(self, count=8, *, match_id=None, latest_long=False):
        scope = {} if match_id is None else {'match_id': match_id}
        start = datetime.utcnow() - timedelta(minutes=10)
        rows = []
        for index in range(count):
            user = f'历史用户片段_{index}：' + '想了解报名流程与队友选择。' * 25
            reply = f'历史回答片段_{index}：' + '具体资格需实时查询；记录讨论意图。' * 25
            if latest_long and index == count - 1:
                user, reply = '最近完整用户：' + '问' * 990, '最近完整回答：' + '答' * 2090
            response = {'reply': reply, 'cards': [], 'context': scope, 'code': 'completed'}
            row = AssistantTurn(id=str(uuid4()), conversation_id=self.conversation_id,
                client_message_id=f'seed_{index}', input_digest='a' * 64,
                user_text=user, state='completed', response_json=json.dumps(response, ensure_ascii=False),
                created_at=start + timedelta(seconds=index))
            self.db.add(row)
            rows.append((row.id, user, reply))
        self.db.get(AssistantConversation, self.conversation_id).context_json = json.dumps(scope)
        self.db.commit()
        return rows

    def summarizer(self, *, value=None, effect=None):
        calls = []
        def generate(messages, timeout):
            self.assertFalse(self.db.in_transaction(), 'summary network must not hold a DB transaction')
            calls.append({'messages': deepcopy(messages), 'timeout': timeout})
            if effect:
                return effect(messages, timeout)
            return json.dumps(SUMMARY if value is None else value, ensure_ascii=False)
        return generate, calls

    def send(self, model, *, summary=None, context=None, client_id='new_client', clock=None, message='继续了解报名流程'):
        options = {'provider': model}
        if summary is not None:
            options['summary_provider'] = summary
        if clock is not None:
            options['clock'] = clock
        return service.send_message(self.db, self.actor, self.conversation_id,
                                    message, client_id, context, **options)

    def test_short_history_has_no_extra_model_call(self):
        self.seed(1)
        summary, calls = self.summarizer()
        model = ScriptedProvider(self.db, [answer()])
        result = self.send(model, summary=summary)
        self.assertEqual(result['code'], 'completed')
        self.assertEqual(calls, [])
        self.assertEqual(len(model.calls), 1)

    def test_actual_loop_sends_summary_and_exact_recent_complete_pairs(self):
        rows = self.seed(latest_long=True)
        summary, calls = self.summarizer()
        model = ScriptedProvider(self.db, [answer()])
        result = self.send(model, summary=summary)
        self.assertEqual(result['code'], 'completed')
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0]['timeout'], 8)
        messages = model.calls[0]['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'user', 'assistant', 'user', 'assistant', 'user'])
        self.assertIn(SUMMARY['topics'][0], messages[1]['content'])
        for _, user, reply in rows[-2:]:
            self.assertTrue(any(m['role'] == 'user' and m['content'] == user for m in messages))
            self.assertTrue(any(m['role'] == 'assistant' and m['content'] == reply for m in messages))
        self.assertFalse(any(m['content'] == rows[0][1] for m in messages))
        self.assertEqual(self.db.query(AssistantTurn).filter_by(state='completed').count(), 9)

    def test_rolling_summary_does_not_resend_already_summarized_source(self):
        rows = self.seed()
        summary, calls = self.summarizer()
        first = self.send(ScriptedProvider(self.db, [answer('继续。')]), summary=summary)
        second = self.send(ScriptedProvider(self.db, [answer('再继续。')]), summary=summary, client_id='next_client')
        self.assertEqual((first['code'], second['code']), ('completed', 'completed'))
        self.assertEqual(len(calls), 2)
        first_input, second_input = [json.dumps(call['messages'], ensure_ascii=False) for call in calls]
        self.assertIn(rows[0][1], first_input)
        self.assertNotIn(rows[0][1], second_input)
        self.assertIn(SUMMARY['topics'][0], second_input)
        self.assertIn(rows[-2][1], second_input)

    def test_summary_failure_or_invalid_output_keeps_normal_query_available(self):
        self.seed()
        for index, value in enumerate(('not-json', '{}', 'x' * 5000)):
            with self.subTest(value=index):
                recent = [(row.user_text, json.loads(row.response_json)['reply']) for row in
                          self.db.query(AssistantTurn).filter_by(state='completed').order_by(
                              AssistantTurn.created_at.desc(), AssistantTurn.id.desc()).limit(2).all()[::-1]]
                summary, calls = self.summarizer(effect=lambda *_: value)
                model = ScriptedProvider(self.db, [answer('仍可查询。')])
                result = self.send(model, summary=summary, client_id=f'bad_summary_{index}')
                self.assertEqual(result['code'], 'completed')
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(model.calls), 1)
                for user, reply in recent:
                    self.assertTrue(any(m['role'] == 'user' and m['content'] == user for m in model.calls[0]['messages']))
                    self.assertTrue(any(m['role'] == 'assistant' and m['content'] == reply for m in model.calls[0]['messages']))

    def test_summary_timeout_is_nonblocking_and_counted_inside_whole_turn_deadline(self):
        self.seed()
        clock = AgentClock()
        def timeout_summary(*_):
            clock.now += 8
            raise TimeoutError('mock timeout')
        summary, calls = self.summarizer(effect=timeout_summary)
        model = ScriptedProvider(self.db, [answer()])
        result = self.send(model, summary=summary, clock=clock)
        self.assertEqual(result['code'], 'completed')
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(model.calls[0]['timeout'], 12)

    def test_summary_spending_entire_deadline_never_starts_main_model(self):
        self.seed()
        clock = AgentClock()
        def exceed_deadline(*_):
            clock.now = 21
            return json.dumps(SUMMARY, ensure_ascii=False)
        summary, calls = self.summarizer(effect=exceed_deadline)
        model = ScriptedProvider(self.db, [answer()])
        result = self.send(model, summary=summary, clock=clock)
        self.assertEqual(result['code'], 'time_budget')
        self.assertEqual(len(calls), 1)
        self.assertEqual(model.calls, [])

    def test_injected_main_model_never_implicitly_uses_real_summary_provider(self):
        self.seed()
        model = ScriptedProvider(self.db, [answer()])
        with patch.object(assistant_provider, 'summarize', side_effect=AssertionError('must not call real summary')) as remote:
            result = self.send(model)
        self.assertEqual(result['code'], 'completed')
        remote.assert_not_called()

    def test_new_event_and_explicit_clear_do_not_revive_old_summary(self):
        self.seed(match_id=7)
        summary, calls = self.summarizer()
        self.send(ScriptedProvider(self.db, [answer()]), summary=summary, context={'match_id': 7})
        self.assertEqual(len(calls), 1)
        for index, scope in enumerate(({'match_id': 8}, {}, {'match_id': 7})):
            model = ScriptedProvider(self.db, [answer('新的讨论对象。')])
            result = self.send(model, summary=summary, context=scope, client_id=f'scope_{index}')
            self.assertEqual(result['context'], scope)
            sent = json.dumps(model.calls[0]['messages'], ensure_ascii=False)
            self.assertNotIn(SUMMARY['topics'][0], sent)
            self.assertNotIn('历史用户片段_', sent)
        self.assertEqual(len(calls), 1)

    def test_repeated_empty_context_retains_same_scope_summary(self):
        self.seed()
        summary, calls = self.summarizer()
        for index in range(2):
            model = ScriptedProvider(self.db, [answer()])
            self.send(model, summary=summary, context={}, client_id=f'empty_{index}')
            self.assertIn(SUMMARY['topics'][0], json.dumps(model.calls[0]['messages'], ensure_ascii=False))
        self.assertEqual(len(calls), 2)

    def test_lost_summary_lease_stops_before_tool_model(self):
        model = ScriptedProvider(self.db, [answer()])
        with patch('app.services.assistant_summary_service.build_history', return_value=([], {'status': 'lease_lost'})):
            result = self.send(model)
        self.assertEqual(result['code'], 'lease_lost')
        self.assertEqual(model.calls, [])

    def test_reasoning_stays_with_current_protocol_and_never_enters_next_summary(self):
        self.seed()
        summary, calls = self.summarizer()
        response = tool_call('list_matches')
        response['reasoning_content'] = '绝不能存入摘要的供应商思考'
        model = ScriptedProvider(self.db, [response, answer()])
        self.send(model, summary=summary)
        self.assertIn('reasoning_content', json.dumps(model.calls[-1]['messages'], ensure_ascii=False))
        self.send(ScriptedProvider(self.db, [answer()]), summary=summary, client_id='after_reasoning')
        stored = json.dumps([row.response_json for row in self.db.query(AssistantTurn).all()], ensure_ascii=False)
        summary_inputs = json.dumps(calls, ensure_ascii=False)
        self.assertNotIn('绝不能存入摘要的供应商思考', stored)
        self.assertNotIn('reasoning_content', summary_inputs)
        self.assertNotIn('绝不能存入摘要的供应商思考', summary_inputs)

    def test_large_tool_result_is_replaced_by_valid_json_without_breaking_pair(self):
        model = ScriptedProvider(self.db, [tool_call('list_matches'), answer()])
        result = {'status': 'ok', 'data': {'items': ['x' * 3000] * 20}, 'cards': []}
        with patch.object(service, 'execute_tool', return_value=result):
            response = self.send(model)
        self.assertEqual(response['code'], 'completed')
        tool = next(message for message in model.calls[1]['messages'] if message['role'] == 'tool')
        payload = json.loads(tool['content'])
        self.assertEqual(payload['status'], 'result_too_large')
        self.assertTrue(payload['data']['truncated'])
        self.assertEqual(tool['tool_call_id'], 'call_1')
        self.assertLessEqual(len(tool['content'].encode('utf-8')), 18000)

    def test_corrupt_memory_after_claim_finishes_request_and_releases_lease(self):
        row = self.db.get(AssistantConversation, self.conversation_id)
        row.context_json, row.memory_json = '{"match_id": 7}', 'invalid json'
        self.db.commit()
        model = ScriptedProvider(self.db, [answer()])
        result = self.send(model)
        self.assertEqual(result['code'], 'tool_execution_failed')
        self.assertEqual(model.calls, [])
        turn = self.db.query(AssistantTurn).filter_by(client_message_id='new_client').one()
        self.assertEqual(turn.state, 'completed')
        self.assertEqual(json.loads(turn.response_json), result)
        row = self.db.get(AssistantConversation, self.conversation_id)
        self.assertIsNone(row.lease_token)
        self.assertIsNone(row.lease_until)
        self.assertEqual(row.context_json, '{"match_id": 7}')
        self.assertEqual(row.memory_json, 'invalid json')
        self.assertEqual(self.send(model), result)
        self.assertEqual(model.calls, [])

    def test_expiry_during_post_claim_refresh_finishes_request_idempotently(self):
        original = service._conversation
        calls = []
        def expires_on_refresh(*args):
            calls.append(True)
            if len(calls) == 2:
                raise HTTPException(410, 'mock expiry between claim and refresh')
            return original(*args)
        model = ScriptedProvider(self.db, [answer()])
        with patch.object(service, '_conversation', side_effect=expires_on_refresh):
            result = self.send(model)
        self.assertEqual(result['code'], 'tool_execution_failed')
        self.assertEqual(model.calls, [])
        turn = self.db.query(AssistantTurn).filter_by(client_message_id='new_client').one()
        self.assertEqual(turn.state, 'completed')
        self.assertEqual(json.loads(turn.response_json), result)
        row = self.db.get(AssistantConversation, self.conversation_id)
        self.assertIsNone(row.lease_token)
        self.assertIsNone(row.lease_until)
        self.assertEqual(self.send(model), result)

    def test_synthetic_compaction_measures_work_context_without_deleting_turns(self):
        from app.services import assistant_summary_service
        rows = self.seed()
        summary, calls = self.summarizer()
        model = ScriptedProvider(self.db, [answer()])
        measured = []
        original = assistant_summary_service.build_history
        def capture(*args, **kwargs):
            messages, metrics = original(*args, **kwargs)
            measured.append(metrics)
            return messages, metrics
        with patch.object(assistant_summary_service, 'build_history', side_effect=capture):
            response = self.send(model, summary=summary)
        self.assertEqual(response['code'], 'completed')
        self.assertEqual(measured[0]['summary_calls'], 1)
        self.assertGreater(measured[0]['history_before_bytes'], measured[0]['history_after_bytes'])
        self.assertEqual(measured[0]['recent_turns'], 2)
        self.assertEqual(measured[0]['summarized_turns'], 6)
        # Helper指标的before/after使用不同内部表示，不据此声称精确压缩率。
        # 此处统一为实际role/content消息数组，包括同一system与当前user。
        messages = model.calls[0]['messages']
        self.assertEqual((messages[0]['role'], messages[-1]['role']), ('system', 'user'))
        comparison = comparable_message_sizes(rows, messages)
        self.assertGreater(comparison['messages_before_bytes'], comparison['messages_after_bytes'])
        self.assertGreater(comparison['bytes_saved_percent'], 0)
        for _, user, reply in rows[-2:]:
            self.assertTrue(any(m['content'] == user for m in model.calls[0]['messages']))
            self.assertTrue(any(m['content'] == reply for m in model.calls[0]['messages']))
        self.assertEqual(self.db.query(AssistantTurn).count(), 9)
