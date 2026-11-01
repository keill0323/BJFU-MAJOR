"""完整赛事聚合的隔离回归；不调用模型、网络或生产数据。"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event

from tests.support import DatabaseTestCase
from app.models.match import MatchRound, MatchStatus, Registration, RegistrationStatus, RoundStatus, StageStatus, TeamProgress
from app.models.team import TeamStatus
from app.services.assistant_standings_service import get_match_standings


class AssistantStandingsTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user(nickname='统计查询者')

    def progress(self, match, team, seed=1, stage=StageStatus.PLAYOFF, group='上区'):
        row = TeamProgress(match_id=match.id, team_id=team.id, stage=stage, group_name=group, seed=seed)
        self.db.add(row)
        self.db.flush()
        return row

    def round(self, match, left, right, winner=None, number=1, group='淘汰赛', **changes):
        ids = [value.id if value else None for value in (left, right)]
        winner_id = winner.id if winner else None
        win_score = 2 if group == '淘汰赛' else 13
        row = dict(match_id=match.id, team1_id=ids[0], team2_id=ids[1], winner_id=winner_id,
                   group_name=group, round_number=number, status=RoundStatus.FINISHED if winner else RoundStatus.PENDING,
                   team1_score=win_score if winner_id == ids[0] and winner_id is not None else 0,
                   team2_score=win_score if winner_id == ids[1] and winner_id is not None else 0)
        row.update(changes)
        model = MatchRound(**row)
        self.db.add(model)
        self.db.flush()
        return model

    def fixture(self, *, played=4):
        match = self.make_match(name='2026 BJFU 起源杯大赛-%s' % self._next(), status=MatchStatus.IN_PROGRESS)
        teams = [self.make_team(name='战队%s-赛事%s' % (index + 1, match.id)) for index in range(6)]
        for index, team in enumerate(teams):
            self.progress(match, team, seed=index + 1, group='上区' if index < 3 else '下区')
        specs = [(teams[1], teams[2], teams[1]), (teams[4], teams[5], teams[4]),
                 (teams[0], teams[4], teams[4]), (teams[3], teams[1], teams[3]),
                 (teams[4], teams[3], teams[4])]
        rows = [self.round(match, left, right, winner=winner, number=101 + index)
                for index, (left, right, winner) in enumerate(specs[:played])]
        return match, teams, rows

    def call(self, match=None, **changes):
        args = dict(match_id=match.id) if match else {}
        args.update(changes)
        return get_match_standings(self.db, self.actor, args)

    @staticmethod
    def by_id(result):
        return {row['team_id']: row for row in result['data']['items']}

    def test_semifinal_winners_lead_together_before_final_is_created(self):
        match, teams, _ = self.fixture()
        result = self.call(match, limit=20)
        self.assertEqual(result['data']['leader_team_ids'], [teams[3].id, teams[4].id])
        self.assertEqual([row['stage_text'] for row in result['data']['leaders']], ['决赛', '决赛'])
        self.assertIn('并列领先', result['message'])
        self.assertIn('冠军尚未确定', result['message'])
        self.assertFalse(result['data']['champion_decided'])
        self.assertTrue(self.by_id(result)[teams[0].id]['eliminated'])

    def test_finished_complete_bracket_awards_champion_and_runner_up(self):
        match, teams, _ = self.fixture(played=5)
        result = self.call(match, limit=20)
        self.assertTrue(result['data']['champion_decided'])
        self.assertEqual(result['data']['leader_team_ids'], [teams[4].id])
        self.assertEqual([row['stage_text'] for row in result['data']['items'][:2]], ['冠军', '亚军'])
        self.assertEqual(self.by_id(result)[teams[4].id]['wins'], 3)
        self.assertEqual(self.by_id(result)[teams[3].id]['losses'], 1)

    def test_one_semifinal_winner_is_already_finalist_while_other_match_pending(self):
        match, teams, rows = self.fixture()
        rows[-1].status, rows[-1].winner_id = RoundStatus.PENDING, None
        self.db.flush()
        result = self.call(match, limit=20)
        self.assertEqual(result['data']['leader_team_ids'], [teams[4].id])
        self.assertEqual(result['data']['leaders'][0]['stage_text'], '决赛')
        self.assertFalse(result['data']['champion_decided'])
        self.assertEqual(self.by_id(result)[teams[3].id]['pending'], 1)

    def test_six_team_byes_improve_progress_without_creating_a_win(self):
        match, teams, rows = self.fixture(played=2)
        for row in rows:
            row.status, row.winner_id = RoundStatus.PENDING, None
        self.db.flush()
        result = self.call(match, limit=20)
        self.assertEqual(result['data']['leader_team_ids'], [teams[0].id, teams[3].id])
        self.assertTrue(all(row['stage_text'] == '四强' and row['wins'] == 0 for row in result['data']['leaders']))
        self.assertTrue(all(row['losses'] == 0 for row in result['data']['items']))

    def test_seed_only_changes_display_order_of_tied_teams(self):
        match, teams, _ = self.fixture()
        self.db.query(TeamProgress).filter_by(match_id=match.id, team_id=teams[4].id).update({'seed': 1})
        result = self.call(match, limit=1)
        self.assertEqual(result['data']['leader_team_ids'], [teams[4].id, teams[3].id])
        self.assertEqual(result['data']['leader_count'], 2)
        self.assertTrue(result['data']['truncated'])
        self.assertTrue(all(row['stage_order'] == 2 for row in result['data']['leaders']))

    def test_pending_missing_winner_zero_scores_and_invalid_bo3_never_award_champion(self):
        cases = [dict(status=RoundStatus.PENDING), dict(status=RoundStatus.IN_PROGRESS),
                 dict(winner_id=None), dict(team1_score=0, team2_score=0),
                 dict(team1_score=13, team2_score=0), dict(team1_score=-2), dict(team2_score=2)]
        for index, changes in enumerate(cases):
            with self.subTest(case=index):
                match, _, rows = self.fixture(played=5)
                match.name += str(index)
                for key, value in changes.items():
                    setattr(rows[-1], key, value)
                self.db.flush()
                result = self.call(match)
                self.assertFalse(result['data']['champion_decided'])
                self.assertTrue(all(row['stage_text'] not in ('冠军', '亚军') for row in result['data']['items']))
                self.assertIn('冠军尚未确定', result['message'])

    def test_wrong_final_path_and_extra_knockout_round_do_not_create_honour(self):
        match, teams, rows = self.fixture(played=5)
        rows[-1].team1_id = teams[0].id
        rows[-1].winner_id = teams[0].id
        self.db.flush()
        self.assertFalse(self.call(match)['data']['champion_decided'])
        rows[-1].team1_id = rows[-1].winner_id = teams[4].id
        self.round(match, teams[0], teams[3], winner=teams[0], number=110)
        self.assertFalse(self.call(match)['data']['champion_decided'])

    def test_bo3_maps_count_as_one_match_and_historical_groups_survive_promotion(self):
        match, teams, rows = self.fixture(played=2)
        rows[0].team1_score, rows[0].team2_score = 2, 1
        rows[0].bo3_scores = json.dumps([{'t1': 5, 't2': 13}, {'t1': 13, 't2': 9}, {'t1': 13, 't2': 11}])
        self.round(match, teams[1], teams[0], winner=teams[1], number=1, group='竹林')
        self.round(match, teams[1], teams[3], winner=teams[3], number=2, group='上区')
        result = self.call(match, limit=20)
        row = self.by_id(result)[teams[1].id]
        self.assertEqual((row['wins'], row['losses']), (2, 1))
        stages = {part['group_name']: part for part in row['stages']}
        self.assertEqual((stages['淘汰赛']['wins'], stages['淘汰赛']['format']), (1, 'BO3'))
        self.assertEqual(stages['竹林']['wins'], 1)
        self.assertEqual(stages['上区']['losses'], 1)
        self.assertNotIn('diff', row)

    def test_byes_pending_and_unresolved_results_are_not_losses(self):
        match, teams, _ = self.fixture(played=0)
        self.round(match, teams[0], None, winner=teams[0], group='A')
        self.round(match, teams[0], teams[1], group='A')
        self.round(match, teams[0], teams[2], group='A', status=RoundStatus.FINISHED)
        self.round(match, teams[0], teams[3], winner=teams[3], group='A', team1_score=13, team2_score=0)
        result = self.call(match, limit=20)
        row = self.by_id(result)[teams[0].id]
        self.assertEqual([row[k] for k in ('wins', 'losses', 'byes', 'pending', 'unresolved')], [0, 0, 1, 1, 2])

    def test_entire_event_is_aggregated_beyond_twenty_rounds(self):
        match, teams, _ = self.fixture(played=5)
        for number in range(30):
            self.round(match, teams[1], teams[2], winner=teams[1], number=number + 1, group='A')
        result = self.call(match, limit=20)
        self.assertEqual(result['data']['total_rounds'], 35)
        self.assertEqual(result['data']['leader_team_ids'], [teams[4].id])
        self.assertEqual(self.by_id(result)[teams[1].id]['wins'], 31)
        alternative = self.call(match, metric='match_wins', limit=20)
        self.assertEqual(alternative['data']['leader_team_ids'], [teams[1].id])
        self.assertIn('不决定晋级或奖金', alternative['message'])

    def test_ambiguous_name_and_draft_use_existing_safe_selection(self):
        one = self.make_match(name='起源杯甲')
        self.make_match(name='起源杯乙')
        draft = self.make_match(name='秘密杯', status=MatchStatus.DRAFT)
        result = self.call(match_keyword='起源杯')
        self.assertEqual(result['status'], 'needs_selection')
        self.assertEqual(result['cards'][0]['type'], 'matches')
        self.assertEqual(len(result['cards'][0]['items']), 2)
        self.assertEqual(self.call(draft)['status'], 'not_found')
        self.assertEqual(self.call(one)['data']['total_teams'], 0)

    def test_hidden_teams_hidden_captains_and_unapproved_teams_are_never_projected(self):
        match = self.make_match()
        visible = self.make_team(name='公开队')
        hidden = self.make_team(name='隐藏测试秘密', is_hidden=True)
        hidden_captain = self.make_user(is_hidden=True)
        hidden_owner = self.make_team(captain=hidden_captain, name='隐藏队长秘密')
        pending = self.make_team(name='待审队秘密', status=TeamStatus.PENDING)
        for index, team in enumerate((visible, hidden, hidden_owner, pending)):
            self.progress(match, team, seed=index + 1, stage=StageStatus.LEGEND)
        self.round(match, visible, hidden, winner=visible, group='上区')
        result = self.call(match)
        self.assertEqual(result['data']['total_teams'], 1)
        self.assertEqual(result['data']['leader_team_ids'], [visible.id])
        raw = json.dumps(result, ensure_ascii=False)
        for forbidden in ('隐藏测试秘密', '隐藏队长秘密', '待审队秘密', 'student_id', 'wx_openid', 'captain_id'):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(result['data']['items'][0]['wins'], 1)

    def test_approved_waiting_team_has_no_competitive_leader_and_pending_team_not_enrolled(self):
        match = self.make_match()
        approved, pending = self.make_team(), self.make_team()
        for team, status in ((approved, RegistrationStatus.APPROVED), (pending, RegistrationStatus.PENDING)):
            self.db.add(Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id, status=status))
        self.db.flush()
        result = self.call(match)
        self.assertEqual(result['data']['total_teams'], 1)
        self.assertEqual(result['data']['items'][0]['stage_text'], '待分组')
        self.assertEqual(result['data']['leaders'], [])
        self.assertEqual(self.call(match, metric='match_wins')['data']['leaders'], [])

    def test_invalid_actor_and_forged_or_unbounded_arguments_are_rejected(self):
        match = self.make_match()
        for args in ({'user_id': self.actor.id}, {'match_id': True}, {'match_id': -1}, {'match_keyword': 2},
                     {'metric': 'rating'}, {'metric': ''}, {'limit': True}, {'limit': 21}):
            with self.subTest(args=args), self.assertRaises(HTTPException):
                get_match_standings(self.db, self.actor, args)
        for actor in (None, SimpleNamespace(id=True), SimpleNamespace(id=999999)):
            with self.subTest(actor=actor), self.assertRaises(HTTPException):
                get_match_standings(self.db, actor, {'match_id': match.id})
        self.actor.is_hidden = True
        self.db.flush()
        with self.assertRaises(HTTPException):
            self.call(match)

    def test_service_is_select_only_and_does_not_mutate_progress_or_scores(self):
        match, _, _ = self.fixture()
        self.db.commit()
        statements = []
        def capture(_connection, _cursor, statement, *_args):
            statements.append(statement.lstrip().upper())
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            self.call(match)
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertTrue(statements)
        self.assertTrue(all(statement.startswith('SELECT') for statement in statements))

    def test_size_limit_returns_incomplete_without_a_claim_of_best_team(self):
        match, _, _ = self.fixture()
        with patch('app.services.assistant_standings_service.MAX_ROUNDS', 2):
            result = self.call(match)
        self.assertEqual(result['status'], 'scope_incomplete')
        self.assertFalse(result['data']['scope_complete'])
        self.assertFalse(result['data']['champion_decided'])
        self.assertEqual(result['data']['leaders'], [])
        self.assertIn('不能完整判断', result['message'])

    def test_output_budget_limits_whole_rows_without_losing_full_event_leaders(self):
        match = self.make_match()
        teams = [self.make_team(name='长' * 55 + str(i)) for i in range(20)]
        for i, team in enumerate(teams):
            self.progress(match, team, seed=i + 1, stage=StageStatus.LEGEND)
        for group in range(13):
            for i in range(0, 20, 2):
                self.round(match, teams[i], teams[i + 1], winner=teams[i], group='历史组%s' % group,
                           number=group * 10 + i + 1)
        result = self.call(match, limit=20)
        body = {key: value for key, value in result.items() if key != 'cards'}
        self.assertLessEqual(len(json.dumps(body, ensure_ascii=False).encode('utf-8')), 15500)
        self.assertEqual(result['data']['leader_count'], 20)
        self.assertEqual(result['data']['total_rounds'], 130)
        self.assertTrue(result['data']['scope_complete'])
        self.assertTrue(result['data']['truncated'])
        self.assertTrue(result['data']['items'])
        self.assertTrue(all(row['stage_group_count'] == 13 and not row['stage_records_complete']
                            for row in result['data']['items']))
        self.assertTrue(all(len(row['stages']) == 12 for row in result['data']['items']))
        self.assertNotIn('stages', result['data']['leaders'][0], 'leaders do not duplicate the full detailed rows')
