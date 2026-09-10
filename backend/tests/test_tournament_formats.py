"""三组含附加赛、四组直晋级的八队传奇赛制回归。"""
from tests.support import DatabaseTestCase

from fastapi import HTTPException

from app.models.match import (MatchRound, Registration, RegistrationStatus,
                              RoundStatus, StageStatus, TeamProgress)
from app.services import match_service as service


class TournamentFormatTests(DatabaseTestCase):
    def enroll(self, count):
        match = self.make_match(max_teams=count)
        for i in range(count):
            team = self.make_team(rating=200 - i)
            self.db.add_all([
                TeamProgress(match_id=match.id, team_id=team.id, stage=StageStatus.CHALLENGER),
                Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id,
                             status=RegistrationStatus.APPROVED),
            ])
        self.db.commit()
        service.auto_assign_seeds(self.db, match.id)
        return match

    def test_complete_twenty_team_four_group_competition_skips_playoff(self):
        match = self.enroll(20)
        groups = ["A", "B", "C", "D"]
        service.auto_group_teams(self.db, match.id, groups)
        for group in groups:
            self.assertEqual(self.db.query(TeamProgress).filter_by(
                match_id=match.id, group_name=group).count(), 4)
            for match_round in service.auto_generate_single_round_matches(self.db, match.id, group):
                service.update_round_result(self.db, match_round.id, 13, 5)
        result = service.finish_group_stage(self.db, match.id)
        self.assertEqual(result["group_count"], 4)
        self.assertFalse(result["has_playoff"])
        self.assertEqual(len(result["group_winners"]), 4)
        self.assertEqual(result["runner_ups"], [])
        self.assertEqual(result["added_rounds"], 0)
        progresses = self.db.query(TeamProgress).filter_by(match_id=match.id)
        self.assertEqual(progresses.filter_by(stage=StageStatus.LEGEND).count(), 8)
        self.assertEqual(progresses.filter_by(stage=StageStatus.ELIMINATED).count(), 12)
        self.assertEqual(progresses.filter_by(stage=StageStatus.CHALLENGER).count(), 0)
        self.assertEqual(self.db.query(MatchRound).filter_by(match_id=match.id, group_name="附加赛").count(), 0)
        with self.assertRaises(HTTPException) as error:
            service.finish_playoff_stage(self.db, match.id)
        self.assertIn("四个挑战者小组不设附加赛", error.exception.detail)
        self.db.rollback()
        self.assertEqual(progresses.filter_by(stage=StageStatus.LEGEND).count(), 8)

        zones = service.divide_legend(self.db, match.id)
        self.assertEqual(zones["added_rounds"], 12)
        for match_round in self.db.query(MatchRound).filter(
                MatchRound.match_id == match.id, MatchRound.group_name.in_(["上区", "下区"])).all():
            service.update_round_result(self.db, match_round.id, 13, 5)
        service.finish_legend_stage(self.db, match.id)
        self.assertEqual(progresses.filter_by(stage=StageStatus.PLAYOFF).count(), 6)
        service.generate_knockout(self.db, match.id)
        for expected_count in (4, 5):
            for match_round in self.db.query(MatchRound).filter_by(
                    match_id=match.id, group_name="淘汰赛", status=RoundStatus.PENDING).all():
                service.update_round_result(self.db, match_round.id, 2, 0,
                    bo3_scores=[{"t1": 13, "t2": 5}, {"t1": 13, "t2": 5}])
            service.advance_knockout(self.db, match.id)
            self.assertEqual(self.db.query(MatchRound).filter_by(
                match_id=match.id, group_name="淘汰赛").count(), expected_count)
        final = self.db.query(MatchRound).filter_by(
            match_id=match.id, group_name="淘汰赛", status=RoundStatus.PENDING).one()
        service.update_round_result(self.db, final.id, 2, 0,
            bo3_scores=[{"t1": 13, "t2": 5}, {"t1": 13, "t2": 5}])
        self.assertIsNotNone(final.winner_id)

    def test_grouping_rejects_unsupported_counts_without_changing_progress(self):
        match = self.enroll(20)
        for count in (1, 2, 5, 6):
            with self.subTest(count=count), self.assertRaises(HTTPException) as error:
                service.auto_group_teams(self.db, match.id, [chr(65 + i) for i in range(count)])
            self.assertIn("仅支持3或4", error.exception.detail)
        self.assertTrue(all(p.group_name is None for p in self.db.query(TeamProgress)))

    def test_grouping_rejects_too_few_challengers_to_play_each_group(self):
        match = self.enroll(8)
        with self.assertRaises(HTTPException) as error:
            service.auto_group_teams(self.db, match.id, ["A", "B", "C"])
        self.assertIn("至少需要6支挑战者队伍", error.exception.detail)
        self.db.rollback()
        self.assertTrue(all(p.group_name is None for p in self.db.query(TeamProgress)))

    def test_legacy_unsupported_group_counts_cannot_partially_advance(self):
        for count in (2, 5):
            with self.subTest(count=count):
                match = self.enroll(20)
                groups = [chr(65 + i) for i in range(count)]
                challengers = self.db.query(TeamProgress).filter_by(
                    match_id=match.id, stage=StageStatus.CHALLENGER).all()
                for i, progress in enumerate(challengers):
                    progress.group_name = groups[i % count]
                self.db.commit()
                for group in groups:
                    for match_round in service.auto_generate_single_round_matches(self.db, match.id, group):
                        service.update_round_result(self.db, match_round.id, 13, 5)
                with self.assertRaises(HTTPException) as error:
                    service.finish_group_stage(self.db, match.id)
                self.assertIn("仅支持3或4", error.exception.detail)
                self.db.rollback()
                self.assertEqual(self.db.query(TeamProgress).filter_by(
                    match_id=match.id, stage=StageStatus.LEGEND).count(), 4)
                self.assertTrue(all(p.stage == StageStatus.CHALLENGER for p in challengers))
                self.assertEqual(self.db.query(MatchRound).filter_by(
                    match_id=match.id, group_name="附加赛").count(), 0)
