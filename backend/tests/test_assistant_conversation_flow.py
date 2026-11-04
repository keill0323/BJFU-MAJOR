"""Conversation wiring and real tools with isolated SQLite/scripted providers.

These checks verify what reaches the provider and which business operations run;
they do not claim to measure a real model's understanding of Chinese requests.
"""
from datetime import datetime
import json
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from tests.support import DatabaseTestCase
from tests.test_assistant_agent import ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.assistant_action import AssistantAction
from app.models.assistant_session import AssistantToolTrace
from app.models.match import MatchRound, MatchStatus, Registration, RoundStatus
from app.services import assistant_service as service
from app.services import assistant_registration_service as registration


class AssistantConversationFlowTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        values = {
            'ASSISTANT_ENABLED': True,
            'ASSISTANT_MAX_STEPS': 6,
            'ASSISTANT_MAX_TOOL_CALLS': 8,
            'ASSISTANT_TIMEOUT_SECONDS': 30,
            'ASSISTANT_SUMMARY_ENABLED': False,
        }
        if hasattr(settings, 'ASSISTANT_ENGINE'):
            values['ASSISTANT_ENGINE'] = 'legacy'
        for key, value in values.items():
            mocked = patch.object(settings, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.actor = self.make_user(nickname='会话流程测试用户')
        self.db.commit()
        self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']

    def send(self, model, message, *, context=None):
        return service.send_message(self.db, self.actor, self.conversation_id,
            message, str(uuid4()), context, provider=model)

    @staticmethod
    def tool_result(messages, name):
        names = {
            call['id']: call['function']['name']
            for message in messages if message['role'] == 'assistant'
            for call in message.get('tool_calls', [])
        }
        replies = [json.loads(message['content']) for message in messages
                   if message['role'] == 'tool' and names.get(message['tool_call_id']) == name]
        if not replies:
            raise AssertionError('Expected a real tool result for ' + name)
        return replies[-1]

    def preview_action(self, result):
        cards = [card for card in result['cards'] if card['type'] == 'registration_preview']
        self.assertEqual(len(cards), 1, result)
        self.assertEqual(cards[0]['action']['kind'], 'confirm_registration')
        action = self.db.get(AssistantAction, cards[0]['action']['action_id'])
        self.assertIsNotNone(action)
        self.assertEqual((action.user_id, action.state), (self.actor.id, 'pending'))
        self.assertEqual(self.db.query(Registration).count(), 0)
        return action

    def test_first_signup_resolves_current_captain_and_prepares_team_without_registration(self):
        team = self.make_team(captain=self.actor, name='本人队伍')
        match = self.make_match(name='流程测试赛事')
        self.db.commit()

        def find_event(messages, tools, timeout):
            own = self.tool_result(messages, 'get_my_team')['data']
            self.assertEqual((own['team_id'], own['is_captain']), (team.id, True))
            self.assertEqual(own['recommended_registration_mode'], 'team')
            return tool_call('list_matches', {'keyword': '流程测试'}, identifier='find_event')

        def prepare(messages, tools, timeout):
            events = self.tool_result(messages, 'list_matches')['data']['items']
            self.assertEqual([item['id'] for item in events], [match.id])
            return tool_call('prepare_registration', {
                'match_id': events[0]['id'], 'mode': 'team', 'team_id': team.id,
            }, identifier='prepare_team')

        model = ScriptedProvider(self.db, [tool_call('get_my_team'), find_event, prepare,
            answer('我已替你报名并审核通过。')])
        result = self.send(model, '帮我报名流程测试赛事')
        self.assertEqual(result['code'], 'completed')
        action = self.preview_action(result)
        self.assertEqual((action.match_id, action.team_id, action.mode), (match.id, team.id, 'team'))
        self.assertIn('尚未提交', result['reply'])
        self.assertIn('确认', result['reply'])
        self.assertNotIn('审核通过', result['reply'])
        self.assertEqual(len(model.calls), 4)
        self.assertNotIn('confirm_registration', [item['function']['name']
                         for item in model.calls[0]['tools']])

    def test_corrected_team_preview_replaces_prior_personal_mode_blocker(self):
        team = self.make_team(captain=self.actor)
        match = self.make_match()
        self.db.commit()

        def inspect_team(messages, tools, timeout):
            failed = self.tool_result(messages, 'check_registration')
            self.assertEqual(failed['status'], 'blocked')
            self.assertIn('请由队长', failed['message'])
            return tool_call('get_my_team', identifier='correct_mode')

        def correct_preview(messages, tools, timeout):
            own = self.tool_result(messages, 'get_my_team')['data']
            self.assertEqual(own['recommended_registration_mode'], 'team')
            return tool_call('prepare_registration', {
                'match_id': match.id, 'mode': 'team', 'team_id': own['team_id'],
            }, identifier='valid_preview')

        model = ScriptedProvider(self.db, [tool_call('check_registration', {
            'match_id': match.id, 'mode': 'personal'}), inspect_team, correct_preview,
            answer('不能帮你报名，请由队长发起。')])
        result = self.send(model, '给本队报名这场比赛')
        action = self.preview_action(result)
        self.assertEqual((action.team_id, action.mode), (team.id, 'team'))
        self.assertIn('尚未提交', result['reply'])
        self.assertIn('确认', result['reply'])
        self.assertNotIn('请由队长', result['reply'])
        self.assertNotIn('不能帮你报名', result['reply'])

    def test_member_gets_real_captain_required_reason_without_personal_fallback(self):
        captain = self.make_user(nickname='实际队长')
        team = self.make_team(captain=captain, members=[self.actor])
        match = self.make_match()
        self.db.commit()

        def attempt_team(messages, tools, timeout):
            own = self.tool_result(messages, 'get_my_team')['data']
            self.assertEqual((own['team_id'], own['is_captain']), (team.id, False))
            self.assertIsNone(own['recommended_registration_mode'])
            self.assertFalse(own['can_request_team_registration'])
            return tool_call('prepare_registration', {
                'match_id': match.id, 'mode': 'team', 'team_id': team.id,
            }, identifier='member_attempt')

        model = ScriptedProvider(self.db, [tool_call('get_my_team'), attempt_team,
            answer('我已绕过队长替你提交报名。')])
        result = self.send(model, '帮我给队伍报名')
        self.assertIn('只有队长', result['reply'])
        self.assertNotIn('已绕过', result['reply'])
        self.assertFalse(any(card['type'] == 'registration_preview' for card in result['cards']))
        self.assertEqual(self.db.query(AssistantAction).count(), 0)
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_later_ineligible_result_removes_old_preview_and_confirmation_guidance(self):
        team = self.make_team(captain=self.actor)
        match = self.make_match()
        self.db.commit()
        captured = {}

        def qualification_changes(messages, tools, timeout):
            prepared = self.tool_result(messages, 'prepare_registration')
            self.assertEqual(prepared['status'], 'ok')
            # Provider payloads are sanitized; random action IDs can include
            # contact-looking text such as QQ. They are not the authority for
            # replaying a confirmation: the database/frontend card keeps it.
            action = self.db.query(AssistantAction).filter_by(
                user_id=self.actor.id, match_id=match.id, team_id=team.id).one()
            captured['action_id'] = action.id
            self.assertEqual(self.db.query(Registration).count(), 0)
            # An actual qualification change makes the previously valid preview
            # stale. Persist it before the next real read-only eligibility tool.
            self.actor.rank = None
            self.db.commit()
            return tool_call('check_registration', {
                'match_id': match.id, 'mode': 'team', 'team_id': team.id,
            }, identifier='changed_qualification')

        def stale_confirmation_answer(messages, tools, timeout):
            blocked = self.tool_result(messages, 'check_registration')
            self.assertEqual(blocked['status'], 'blocked')
            captured['reason'] = blocked['message']
            self.assertIn('段位认证', blocked['message'])
            return answer('报名预览仍然有效，请点击确认报名。')

        model = ScriptedProvider(self.db, [tool_call('prepare_registration', {
            'match_id': match.id, 'mode': 'team', 'team_id': team.id}),
            qualification_changes, stale_confirmation_answer])
        # Exercise the formerly intermittent redaction case deterministically.
        with patch.object(registration.secrets, 'token_urlsafe',
                          return_value='A' * 10 + 'QQ' + 'B' * 31):
            result = self.send(model, '帮本队报名，并检查一下最新资格')
        self.assertEqual(result['code'], 'completed')
        self.assertIn(captured['reason'], result['reply'])
        self.assertFalse(any(card['type'] == 'registration_preview' for card in result['cards']))
        self.assertNotIn('点击确认', result['reply'])
        self.assertNotIn('只是预览', result['reply'])
        self.assertEqual(self.db.query(Registration).count(), 0)
        old_action = self.db.get(AssistantAction, captured['action_id'])
        self.assertEqual(old_action.state, 'pending')
        # Removing a display card does not replace the original independent
        # confirmation boundary: an old copy of the action must still recheck.
        with self.assertRaises(HTTPException) as caught:
            registration.confirm_registration(self.db, self.actor, captured['action_id'])
        self.assertEqual(caught.exception.detail['code'], 'preconditions_changed')
        self.assertEqual(self.db.query(Registration).count(), 0)
        self.assertEqual(self.db.get(AssistantAction, captured['action_id']).state, 'invalidated')

    def test_candidate_followups_keep_signup_purpose_and_prepare_selected_event(self):
        first = self.make_match(name='会话测试赛事甲')
        second = self.make_match(name='会话测试赛事乙')
        self.db.commit()
        for followup, context in (('第二个', None), ('这个', {'match_id': first.id})):
            with self.subTest(followup=followup):
                self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']
                offered = self.send(ScriptedProvider(self.db, [tool_call('list_matches', {
                    'keyword': '会话测试赛事'}), answer(
                        '模型反序列表：\n1. %s\n2. %s\n请选择要报名的赛事。' % (first.name, second.name))]),
                    '帮我报名会话测试赛事')
                items = offered['cards'][0]['items']
                ids = [item['id'] for item in items]
                self.assertEqual(ids, [second.id, first.id])
                # Even a provider that renumbers its prose backwards cannot
                # change what "第二个" means relative to the visible cards.
                self.assertNotIn('模型反序列表', offered['reply'])
                for ordinal, item in enumerate(items, 1):
                    self.assertIn('%s. %s' % (ordinal, item['title']), offered['reply'])
                self.assertLess(offered['reply'].index(items[0]['title']),
                                offered['reply'].index(items[1]['title']))

                def inspect_original_request(messages, tools, timeout):
                    received = json.dumps(messages, ensure_ascii=False)
                    self.assertIn('帮我报名会话测试赛事', received)
                    self.assertIn(followup, messages[-1]['content'])
                    if context is None:
                        self.assertIn(first.name, received)
                        self.assertIn(second.name, received)
                        previous = [item['content'] for item in messages[:-1]
                                    if item['role'] == 'assistant'][-1]
                        self.assertLess(previous.index(second.name), previous.index(first.name))
                    return tool_call('get_my_team', identifier='followup_team')

                def selected_preview(messages, tools, timeout):
                    own = self.tool_result(messages, 'get_my_team')['data']
                    self.assertEqual(own['recommended_registration_mode'], 'personal')
                    return tool_call('prepare_registration', {
                        'match_id': first.id, 'mode': 'personal',
                    }, identifier='selected_preview')

                model = ScriptedProvider(self.db, [inspect_original_request, selected_preview,
                    answer('报名预览已准备，请核对确认。')])
                result = self.send(model, followup, context=context)
                action = self.preview_action(result)
                self.assertEqual((action.match_id, action.mode), (first.id, 'personal'))
                self.assertEqual(result['code'], 'completed')
                traces = self.db.query(AssistantToolTrace).filter_by(turn_id=result['trace_id']).all()
                self.assertEqual({trace.tool for trace in traces}, {'get_my_team', 'prepare_registration'})

    def test_next_round_tool_result_survives_unrelated_model_answer(self):
        team = self.make_team(captain=self.actor, name='本人对阵队')
        opponent = self.make_team(name='下一场对手')
        match = self.make_match(name='下一场流程杯', status=MatchStatus.IN_PROGRESS)
        self.make_match(name='不相关报名赛')
        row = MatchRound(match_id=match.id, team1_id=team.id, team2_id=opponent.id,
            group_name='淘汰赛', round_number=1, status=RoundStatus.PENDING,
            scheduled_time=datetime(2030, 11, 9, 13))
        self.db.add(row)
        self.db.commit()

        def find_round(messages, tools, timeout):
            own = self.tool_result(messages, 'get_my_team')['data']
            self.assertEqual(own['team_id'], team.id)
            return tool_call('get_match_rounds', {'my_team': True, 'next_only': True},
                             identifier='my_next_round')

        def wrong_final(messages, tools, timeout):
            found = self.tool_result(messages, 'get_match_rounds')
            self.assertEqual(found['status'], 'ok')
            self.assertEqual([item['id'] for item in found['data']['items']], [row.id])
            self.assertEqual(found['data']['items'][0]['team2_name'], opponent.name)
            return answer('我还不知道你说的是哪个赛事；请重新告诉我赛事名称。')

        model = ScriptedProvider(self.db, [tool_call('get_my_team'), find_round, wrong_final])
        result = self.send(model, '我的下一场对阵是谁？')
        self.assertEqual(result['code'], 'completed')
        self.assertIn(team.name, result['reply'])
        self.assertIn(opponent.name, result['reply'])
        self.assertIn('BO3', result['reply'])
        self.assertNotIn('请重新告诉', result['reply'])
        self.assertEqual(self.db.query(AssistantAction).count(), 0)
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_self_team_lookup_never_discards_twentieth_event_candidate_or_signup_intent(self):
        team = self.make_team(captain=self.actor, name='候选记忆本人队伍')
        for index in range(20):
            self.make_match(name='候选完整赛事%02d' % (index + 1))
        self.db.commit()
        question = '帮本队报名候选完整赛事'
        for team_first in (True, False):
            with self.subTest(team_first=team_first):
                self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']
                own = tool_call('get_my_team', identifier='own_team')
                events = tool_call('list_matches', {'keyword': '候选完整赛事', 'limit': 20},
                                   identifier='twenty_events')
                replies = [own, events] if team_first else [events, own]
                offered = self.send(ScriptedProvider(self.db, replies + [
                    answer('有20场赛事，请选择本队要报名的赛事。')]), question)
                cards = [card for card in offered['cards'] if card['type'] == 'matches']
                self.assertEqual(len(cards), 1)
                self.assertEqual(len(cards[0]['items']), 20)
                target = cards[0]['items'][19]['id']

                def selected_last_candidate(messages, tools, timeout):
                    # Match scope changes rebuild the current wire. The original
                    # request must survive via validated candidate provenance,
                    # including the twentieth event after either tool order.
                    self.assertIn(question, messages[-1]['content'])
                    return tool_call('prepare_registration', {
                        'match_id': target, 'mode': 'team', 'team_id': team.id,
                    }, identifier='prepare_twentieth_event')

                model = ScriptedProvider(self.db, [selected_last_candidate,
                    answer('已准备本队报名预览，请核对后确认。')])
                result = self.send(model, '就第20个', context={'match_id': target})
                action = self.preview_action(result)
                self.assertEqual((action.match_id, action.team_id, action.mode),
                                 (target, team.id, 'team'))
                self.assertEqual(result['code'], 'completed')

    def test_resolved_event_selection_does_not_swallow_player_stats_answer(self):
        target = self.make_match(name='统计消歧赛事甲')
        self.make_match(name='统计消歧赛事乙')
        self.db.commit()
        player_key = 'b' * 32
        source = {
            'status': 'ok',
            'message': '已查询所选赛事导入的选手数据。',
            'data': {'match_id': target.id, 'items': [
                {'player_key': player_key, 'nickname': '统计选手', 'maps_played': 1, 'kills': 20}]},
            'cards': [{'type': 'player_stats', 'title': '所选赛事选手数据',
                'items': [{'id': player_key, 'title': '统计选手', 'subtitle': '1张已导入地图 · 20次击杀'}]}],
        }
        execute = service.execute_tool

        def with_stats(db, actor, name, arguments):
            if name == 'get_player_stats':
                self.assertEqual(arguments['match_id'], target.id)
                return source
            return execute(db, actor, name, arguments)

        def select_stats(messages, tools, timeout):
            choices = self.tool_result(messages, 'list_matches')
            self.assertEqual(choices['status'], 'needs_selection')
            return tool_call('get_player_stats', {'match_id': target.id}, identifier='resolved_stats')

        expected = '统计选手在所选赛事已导入的1张地图中拿到20次击杀；未导入地图不计入。'
        model = ScriptedProvider(self.db, [tool_call('list_matches', {'keyword': '统计消歧赛事'}),
            select_stats, answer(expected)])
        with patch.object(service, 'execute_tool', side_effect=with_stats) as dispatch:
            result = self.send(model, '看看统计消歧赛事的选手数据，先选甲场')
        self.assertEqual(result['code'], 'completed')
        self.assertEqual(result['reply'], expected)
        self.assertTrue(result['reply'].strip())
        self.assertTrue(any(card['type'] == 'player_stats' for card in result['cards']))
        self.assertEqual([call.args[2] for call in dispatch.call_args_list],
                         ['list_matches', 'get_player_stats'])
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(self.db.query(Registration).count(), 0)
        self.assertEqual(self.db.query(AssistantAction).count(), 0)
