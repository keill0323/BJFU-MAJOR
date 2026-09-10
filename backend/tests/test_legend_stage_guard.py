"""传奇组分区不能越过尚未结束的挑战者组/附加赛。"""
from tests.support import DatabaseTestCase

from fastapi import HTTPException

from app.models.match import (MatchRound, Registration, RegistrationStatus,
                              StageStatus, TeamProgress)
from app.services import match_service as service


class LegendStageGuardTests(DatabaseTestCase):
    def finish_challenger_groups(self, group_names):
        match = self.make_match()
        for i in range(16):
            team = self.make_team(rating=200 - i)
            self.db.add_all([
                TeamProgress(match_id=match.id, team_id=team.id, stage=StageStatus.CHALLENGER),
                Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id,
                             status=RegistrationStatus.APPROVED),
            ])
        self.db.commit()
        service.auto_assign_seeds(self.db, match.id)
        service.auto_group_teams(self.db, match.id, group_names)
        for group in group_names:
            rounds = service.auto_generate_single_round_matches(self.db, match.id, group)
            for match_round in rounds:
                service.update_round_result(self.db, match_round.id, 13, 5)
        service.finish_group_stage(self.db, match.id)
        return match

    def finish_playoff_results(self, match):
        for match_round in self.db.query(MatchRound).filter_by(
                match_id=match.id, group_name="附加赛").all():
            service.update_round_result(self.db, match_round.id, 13, 5)

    def legacy_four_group_playoff(self):
        """显式模拟旧代码留下的四组附加赛；新流程本身不会再产生这些记录。"""
        match = self.finish_challenger_groups(["A", "B", "C", "D"])
        for group in ["A", "B", "C", "D"]:
            runner_up = self.db.query(TeamProgress).filter_by(
                match_id=match.id, group_name=group, stage=StageStatus.ELIMINATED,
            ).order_by(TeamProgress.seed).first()
            runner_up.stage = StageStatus.CHALLENGER
            runner_up.group_name = "附加赛"
        self.db.commit()
        service.auto_generate_single_round_matches(self.db, match.id, "附加赛")
        return match

    def assert_division_blocked_without_changes(self, match):
        progresses = self.db.query(TeamProgress).filter_by(match_id=match.id)
        self.assertEqual(progresses.filter_by(stage=StageStatus.LEGEND).count(), 8)
        before_progresses = [(p.team_id, p.stage, p.group_name, p.seed)
                             for p in progresses.order_by(TeamProgress.id)]
        before_rounds = self.db.query(MatchRound).filter_by(match_id=match.id).count()
        with self.assertRaises(HTTPException) as error:
            service.divide_legend(self.db, match.id)
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("尚未完成晋级", error.exception.detail)
        self.db.rollback()
        self.assertEqual([(p.team_id, p.stage, p.group_name, p.seed)
                          for p in progresses.order_by(TeamProgress.id)], before_progresses)
        self.assertEqual(self.db.query(MatchRound).filter_by(match_id=match.id).count(), before_rounds)
        self.assertEqual(self.db.query(MatchRound).filter(
            MatchRound.match_id == match.id, MatchRound.group_name.in_(["上区", "下区"])).count(), 0)

    def test_legacy_four_group_eight_legends_cannot_divide_before_playoff_games_finish(self):
        match = self.legacy_four_group_playoff()
        self.assert_division_blocked_without_changes(match)

    def test_finished_playoff_scores_still_require_explicit_stage_advancement(self):
        match = self.legacy_four_group_playoff()
        self.finish_playoff_results(match)
        self.assert_division_blocked_without_changes(match)

    def test_legacy_four_group_playoff_cannot_add_a_ninth_legend(self):
        match = self.legacy_four_group_playoff()
        self.finish_playoff_results(match)
        with self.assertRaises(HTTPException) as error:
            service.finish_playoff_stage(self.db, match.id)
        self.assertIn("四个挑战者小组不设附加赛", error.exception.detail)
        self.db.rollback()
        self.assertEqual(self.db.query(TeamProgress).filter_by(
            match_id=match.id, stage=StageStatus.LEGEND).count(), 8)
        self.assertEqual(self.db.query(TeamProgress).filter_by(
            match_id=match.id, group_name="附加赛", stage=StageStatus.CHALLENGER).count(), 4)

    def test_completed_three_group_path_can_still_generate_both_legend_zones(self):
        match = self.finish_challenger_groups(["A", "B", "C"])
        self.finish_playoff_results(match)
        service.finish_playoff_stage(self.db, match.id)
        result = service.divide_legend(self.db, match.id)
        self.assertEqual(len(result["zone_teams"]), 8)
        self.assertEqual(result["added_rounds"], 12)
        for zone in ("上区", "下区"):
            self.assertEqual(self.db.query(TeamProgress).filter_by(
                match_id=match.id, group_name=zone, stage=StageStatus.LEGEND).count(), 4)
            self.assertEqual(self.db.query(MatchRound).filter_by(
                match_id=match.id, group_name=zone).count(), 6)
