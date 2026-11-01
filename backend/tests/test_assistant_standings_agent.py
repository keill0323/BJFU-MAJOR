"""查询排名的真实工具编排及已有结果保留，不调用外部模型。"""
from unittest.mock import patch
from tests.support import DatabaseTestCase
from tests.test_assistant_agent import ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.match import MatchRound, RoundStatus, StageStatus, TeamProgress
from app.services import assistant_service as service
from app.services.assistant_provider import ProviderError


class AssistantStandingsAgentTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        mocked = patch.object(settings, 'ASSISTANT_ENABLED', True)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.actor = self.make_user()
        self.match = self.make_match(name='2026 BJFU 起源杯大赛')
        names = ['拉面队', '甲队', '乙队', '泥头车Acadamy', '丙队', '丁队']
        teams = [self.make_team(name=name) for name in names]
        for index, team in enumerate(teams):
            self.db.add(TeamProgress(match_id=self.match.id, team_id=team.id,
                                    stage=StageStatus.PLAYOFF, seed=index + 1))
        for index, (left, right, winner) in enumerate(((1, 2, 1), (4, 5, 4), (0, 4, 0), (3, 1, 3), (0, 3, None))):
            self.db.add(MatchRound(match_id=self.match.id, team1_id=teams[left].id, team2_id=teams[right].id,
                group_name='淘汰赛', round_number=index + 1,
                status=RoundStatus.FINISHED if winner is not None else RoundStatus.PENDING,
                winner_id=teams[winner].id if winner is not None else None,
                team1_score=2 if winner == left else 0, team2_score=2 if winner == right else 0))
        self.db.commit()
        self.conversation_id = service.create_conversation(self.db, self.actor)['conversation_id']

    def send(self, model):
        return service.send_message(self.db, self.actor, self.conversation_id,
            '26届起源杯目前哪个队战绩最好？', 'standings_question', provider=model)

    def test_alias_to_full_standings_answer_is_grounded_and_not_a_false_action_claim(self):
        model = ScriptedProvider(self.db, [tool_call('get_match_standings', {'match_keyword': '26届起源杯'}),
                                           answer('我查到已报名的队伍中有两队领先。')])
        result = self.send(model)
        self.assertEqual(result['code'], 'completed')
        self.assertIn('拉面队', result['reply'])
        self.assertIn('泥头车Acadamy', result['reply'])
        self.assertIn('并列', result['reply'])
        self.assertIn('决赛', result['reply'])
        self.assertIn('冠军尚未确定', result['reply'])
        self.assertEqual(result['cards'][0]['type'], 'teams')
        self.assertEqual(len(model.calls), 2)

    def test_budget_failure_preserves_already_computed_rank_facts(self):
        model = ScriptedProvider(self.db, [tool_call('get_match_standings', {'match_id': self.match.id}),
            tool_call('get_match_rounds', {'match_id': self.match.id}, identifier='second')])
        with patch.object(settings, 'ASSISTANT_MAX_TOOL_CALLS', 1):
            result = self.send(model)
        self.assertEqual(result['code'], 'partial')
        self.assertIn('拉面队', result['reply'])
        self.assertIn('泥头车Acadamy', result['reply'])
        self.assertIn('其余整理未完成', result['reply'])
        self.assertNotIn('报名预览', result['reply'])

    def test_plain_query_failure_does_not_invent_a_registration_preview(self):
        result = self.send(ScriptedProvider(self.db, [ProviderError('provider_request_failed')]))
        self.assertEqual(result['code'], 'provider_request_failed')
        self.assertNotIn('报名', result['reply'])
        self.assertIn('查询', result['reply'])
