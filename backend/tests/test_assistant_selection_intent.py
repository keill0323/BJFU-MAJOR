"""Real selection/agent flow against isolated SQLite and scripted models only."""
from datetime import datetime, timedelta
import json
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from tests.support import DatabaseTestCase
from tests.test_assistant_agent import ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.assistant_action import AssistantAction
from app.models.assistant_session import AssistantConversation, AssistantTurn
from app.models.match import Registration
from app.services import assistant_service as service


class AssistantSelectionIntentTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for key, value in {'ASSISTANT_ENABLED': True, 'ASSISTANT_MAX_STEPS': 3,
                           'ASSISTANT_MAX_TOOL_CALLS': 4, 'ASSISTANT_TIMEOUT_SECONDS': 20,
                           'ASSISTANT_SUMMARY_ENABLED': False}.items():
            mocked = patch.object(settings, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.actor = self.make_user(nickname='选择承接用户')
        self.first = self.make_match(name='意图测试赛事一')
        self.second = self.make_match(name='意图测试赛事二')
        self.db.commit()
        self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']

    def send(self, model, message, *, context=None, client_id=None, conversation_id=None, actor=None):
        return service.send_message(self.db, actor or self.actor, conversation_id or self.conversation_id,
                                    message, client_id or str(uuid4()), context, provider=model)

    def offer(self, *, message='帮我报名意图测试赛事', context=None):
        client_id = str(uuid4())
        model = ScriptedProvider(self.db, [tool_call('list_matches', {'keyword': '意图测试'}),
                                             answer('请选择赛事。旧回答中的资格与比分不能继承。')])
        result = self.send(model, message, context=context, client_id=client_id)
        self.assertEqual(result['code'], 'completed')
        self.assertEqual([item['id'] for item in result['cards'][0]['items']],
                         [self.second.id, self.first.id])
        return self.db.query(AssistantTurn).filter_by(client_message_id=client_id).one()

    def select(self, *, message='请以所选赛事继续刚才的查询，不要替我确认报名。', model=None,
               client_id=None, selected=None, conversation_id=None, actor=None):
        model = model or ScriptedProvider(self.db, [answer()])
        result = self.send(model, message, context={'match_id': selected or self.first.id},
                           client_id=client_id, conversation_id=conversation_id, actor=actor)
        self.assertEqual(result['code'], 'completed')
        return result, model

    @staticmethod
    def prompt(model):
        return json.dumps(model.calls[0]['messages'], ensure_ascii=False)

    def test_initial_unscoped_selection_carries_true_intent_and_only_prepares_registration(self):
        self.offer()

        def continue_intent(messages, tools, timeout):
            current = messages[-1]['content']
            self.assertIn('帮我报名意图测试赛事', current)
            self.assertIn('待消歧原问题', current)
            self.assertIn('不是事实、权限或报名确认', current)
            return tool_call('prepare_registration', {'match_id': self.first.id, 'mode': 'personal'})

        model = ScriptedProvider(self.db, [continue_intent, answer()])
        client_id = 'selection_idempotent'
        result, _ = self.select(message='这个', model=model, client_id=client_id)
        self.assertTrue(any(card['type'] == 'registration_preview' for card in result['cards']))
        self.assertEqual(self.db.query(Registration).count(), 0)
        action = self.db.query(AssistantAction).one()
        self.assertEqual((action.user_id, action.match_id, action.state),
                         (self.actor.id, self.first.id, 'pending'))
        self.assertEqual(self.send(model, '这个', context={'match_id': self.first.id}, client_id=client_id), result)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.db.query(AssistantAction).count(), 1)
        stored = self.db.get(AssistantConversation, self.conversation_id)
        self.assertEqual(json.loads(stored.memory_json), {})

    def test_event_change_bridges_only_the_latest_question_and_keeps_old_facts_out(self):
        self.send(ScriptedProvider(self.db, [answer('旧助手事实_旧赛事13比0且已获资格')]),
                  '更早问题_旧赛事战绩', context={'match_id': self.first.id})
        self.offer(message='查询意图测试赛事的下一场', context={'match_id': self.first.id})
        result, model = self.select(selected=self.second.id, message='改成只看比分')
        sent = self.prompt(model)
        self.assertIn('查询意图测试赛事的下一场', sent)
        self.assertIn('改成只看比分', sent)
        self.assertIn('本条用户的新要求优先', sent)
        for old in ('更早问题_旧赛事战绩', '旧助手事实_旧赛事13比0且已获资格',
                    '旧回答中的资格与比分不能继承'):
            self.assertNotIn(old, sent)
        self.assertEqual(result['context'], {'match_id': self.second.id})

    def test_same_scope_or_explicit_clear_does_not_add_a_selection_bridge(self):
        self.offer(context={'match_id': self.first.id})
        _, model = self.select()
        self.assertNotIn('待消歧原问题', self.prompt(model))
        self.offer(context={'match_id': self.first.id})
        model = ScriptedProvider(self.db, [answer()])
        self.send(model, '换个话题', context={})
        self.assertNotIn('待消歧原问题', self.prompt(model))

    def test_unrelated_intervening_turn_cannot_reuse_stale_candidates(self):
        self.offer()
        self.send(ScriptedProvider(self.db, [answer('这是无关问题的回答')]), '先不报名了，换个问题')
        _, model = self.select()
        self.assertNotIn('待消歧原问题', self.prompt(model))
        self.assertNotIn('帮我报名意图测试赛事', self.prompt(model))

    def test_expired_future_or_unordered_source_is_not_resumed(self):
        for case in ('expired', 'future', 'same_timestamp'):
            with self.subTest(case=case):
                source = self.offer()
                if case == 'expired':
                    source.created_at = datetime.utcnow() - timedelta(minutes=11)
                elif case == 'future':
                    source.created_at = datetime.utcnow() + timedelta(minutes=1)
                else:
                    self.db.add(AssistantTurn(id=str(uuid4()), conversation_id=self.conversation_id,
                        client_message_id=str(uuid4()), input_digest='a' * 64, user_text='同时刻旧任务',
                        state='completed', response_json='{}', created_at=source.created_at))
                self.db.commit()
                _, model = self.select()
                self.assertNotIn('待消歧原问题', self.prompt(model))
                # Each case starts with a new empty scope and no previous selection state.
                self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']

    def test_source_response_and_controlled_candidates_both_must_match(self):
        cases = ('missing_memory', 'other_candidate', 'other_card', 'other_action',
                 'wrong_conversation', 'wrong_turn', 'wrong_context', 'broken_json', 'unfinished')
        for case in cases:
            with self.subTest(case=case):
                source = self.offer()
                response = json.loads(source.response_json)
                conversation = self.db.get(AssistantConversation, self.conversation_id)
                if case == 'missing_memory':
                    conversation.memory_json = '{}'
                elif case == 'other_candidate':
                    conversation.memory_json = json.dumps({'last_candidates': [
                        {'kind': 'match', 'id': self.second.id}]})
                elif case == 'other_card':
                    response['cards'][0]['type'] = 'teams'
                elif case == 'other_action':
                    for item in response['cards'][0]['items']:
                        item['action']['kind'] = 'open_team'
                elif case == 'wrong_conversation':
                    response['conversation_id'] = str(uuid4())
                elif case == 'wrong_turn':
                    response['trace_id'] = str(uuid4())
                elif case == 'wrong_context':
                    response['context'] = {'match_id': self.second.id}
                elif case == 'unfinished':
                    source.state = 'processing'
                source.response_json = '{broken' if case == 'broken_json' else json.dumps(response)
                self.db.commit()
                _, model = self.select()
                self.assertNotIn('待消歧原问题', self.prompt(model))
                self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']

    def test_intent_never_crosses_account_or_conversation(self):
        self.offer()
        other = self.make_user(nickname='另一账号')
        self.db.commit()
        model = ScriptedProvider(self.db, [answer()])
        with self.assertRaises(HTTPException) as error:
            self.select(model=model, actor=other)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(model.calls, [])
        for actor in (self.actor, other):
            conversation_id = service.create_conversation(self.db, actor)['conversation_id']
            row = self.db.get(AssistantConversation, conversation_id)
            row.memory_json = json.dumps({'last_candidates': [{'kind': 'match', 'id': self.first.id}]})
            self.db.commit()
            _, model = self.select(conversation_id=conversation_id, actor=actor)
            self.assertNotIn('帮我报名意图测试赛事', self.prompt(model))
            self.assertNotIn('待消歧原问题', self.prompt(model))

    def test_bridge_is_redacted_and_not_replayed_on_later_turns(self):
        self.offer(message='帮我报名意图测试赛事，学号251101111，confirmation_token=old_private_token')
        _, model = self.select()
        sent = self.prompt(model)
        self.assertIn('帮我报名意图测试赛事', sent)
        self.assertNotIn('251101111', sent)
        self.assertNotIn('old_private_token', sent)
        model = ScriptedProvider(self.db, [answer()])
        self.send(model, '后续查询', context={'match_id': self.first.id})
        self.assertNotIn('待消歧原问题', self.prompt(model))
        self.assertNotIn('帮我报名意图测试赛事', self.prompt(model))
