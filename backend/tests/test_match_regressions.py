from tests.support import DatabaseTestCase

from unittest.mock import patch
from fastapi import HTTPException
from app.models.match import (MatchRound, RoundStatus, StageStatus, TeamProgress,
                              Registration, RegistrationStatus)
from app.services import match_service as service


class MatchRegressionTests(DatabaseTestCase):
    def enrolled(self, count):
        match = self.make_match()
        teams, progresses = [], []
        for i in range(count):
            team = self.make_team(rating=200 - i)
            progress = TeamProgress(match_id=match.id, team_id=team.id,
                                    stage=StageStatus.CHALLENGER, group_name="A", seed=i + 1)
            self.db.add_all([progress, Registration(match_id=match.id, team_id=team.id,
                              user_id=team.captain_id, status=RegistrationStatus.APPROVED)])
            teams.append(team)
            progresses.append(progress)
        self.db.commit()
        return match, teams, progresses

    def result(self, match, first, second, group, scores=(13, 5), finished=True):
        match_round = MatchRound(match_id=match.id, team1_id=first.id, team2_id=second.id,
            group_name=group, round_number=self.db.query(MatchRound).count() + 1,
            team1_score=scores[0], team2_score=scores[1],
            winner_id=(first.id if scores[0] > scores[1] else second.id) if finished else None,
            status=RoundStatus.FINISHED if finished else RoundStatus.PENDING)
        self.db.add(match_round)
        self.db.commit()
        return match_round

    def ready_legend(self):
        match, teams, progresses = self.enrolled(8)
        for i, p in enumerate(progresses):
            p.stage = StageStatus.LEGEND
            p.group_name = "上区" if i < 4 else "下区"
        self.db.commit()
        for offset, zone in [(0, "上区"), (4, "下区")]:
            for a, b, s1, s2 in [(0, 1, 13, 10), (1, 2, 13, 1), (2, 0, 13, 10),
                                  (0, 3, 13, 0), (1, 3, 13, 12), (2, 3, 13, 12)]:
                self.result(match, teams[offset + a], teams[offset + b], zone, (s1, s2))
        return match, teams, progresses

    def finish_knockout_round(self, match_round, reverse=False):
        scores = {"t1": 5 if reverse else 13, "t2": 13 if reverse else 5}
        return service.update_round_result(self.db, match_round.id,
            0 if reverse else 2, 2 if reverse else 0, bo3_scores=[scores, scores])

    def test_personal_registration_becomes_pending_team_registration(self):
        captain, member = self.make_user(), self.make_user()
        match = self.make_match()
        self.db.commit()
        service.register_user(self.db, match.id, captain.id)
        team = self.make_team(captain=captain, members=[member])
        self.db.commit()
        records = service.register_team(self.db, match.id, team.id)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.status == RegistrationStatus.PENDING for r in records))
        self.assertEqual(service.get_match_admin_detail(self.db, match.id)["teams"][0]["registration_status"], "pending")
        self.assertEqual(self.db.query(TeamProgress).count(), 0)
        service.approve_team_registration(self.db, match.id, team.id)
        self.assertEqual(self.db.query(TeamProgress).count(), 1)
        self.assertEqual(service.get_match_admin_detail(self.db, match.id)["teams"][0]["registration_status"], "approved")

    def test_legacy_approved_without_progress_can_be_reviewed(self):
        match, teams, _ = self.enrolled(1)
        self.db.query(TeamProgress).delete()
        self.db.commit()
        self.assertEqual(service.get_match_admin_detail(self.db, match.id)["teams"][0]["registration_status"], "pending")
        for _ in range(2):
            service.approve_team_registration(self.db, match.id, teams[0].id)
        self.assertEqual(self.db.query(TeamProgress).count(), 1)

    def test_review_rechecks_current_freshman_roster(self):
        captain = self.make_user(identity="new_student")
        members = [self.make_user(identity="new_student") for _ in range(2)]
        team = self.make_team(captain=captain, members=members)
        match = self.make_match(match_type="freshman")
        self.db.commit()
        service.register_team(self.db, match.id, team.id)
        members[0].identity = "senior"
        self.db.commit()
        with self.assertRaises(HTTPException):
            service.approve_team_registration(self.db, match.id, team.id)
        self.assertEqual(self.db.query(TeamProgress).count(), 0)
        self.assertTrue(all(r.status == RegistrationStatus.PENDING for r in self.db.query(Registration)))

    def test_no_results_cannot_finish_groups(self):
        match, _, progresses = self.enrolled(6)
        for i, progress in enumerate(progresses):
            progress.group_name = ["A", "B", "C"][i // 2]
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            service.finish_group_stage(self.db, match.id)
        self.assertIn("尚未完整生成", error.exception.detail)
        self.assertTrue(all(p.stage == StageStatus.CHALLENGER for p in progresses))

    def test_incomplete_pairings_cannot_finish_groups(self):
        match, teams, progresses = self.enrolled(9)
        for i, progress in enumerate(progresses):
            progress.group_name = ["A", "B", "C"][i // 3]
        self.db.commit()
        self.result(match, teams[0], teams[1], "A")
        with self.assertRaises(HTTPException) as error:
            service.finish_group_stage(self.db, match.id)
        self.assertIn("尚未完整生成", error.exception.detail)

    def test_unfinished_extra_rounds_block_promotion(self):
        match, _, _ = self.enrolled(16)
        service.auto_assign_seeds(self.db, match.id)
        service.auto_group_teams(self.db, match.id, ["A", "B", "C"])
        for group in ["A", "B", "C"]:
            for match_round in service.auto_generate_single_round_matches(self.db, match.id, group):
                service.update_round_result(self.db, match_round.id, 13, 5)
        service.finish_group_stage(self.db, match.id)
        playoff_rounds = self.db.query(MatchRound).filter_by(match_id=match.id, group_name="附加赛").all()
        for match_round in playoff_rounds:
            service.update_round_result(self.db, match_round.id, 13, 5)
        first = playoff_rounds[0]
        service.add_round(self.db, match.id, 999, first.team1_id, first.team2_id, "附加赛")
        with self.assertRaises(HTTPException) as error:
            service.finish_playoff_stage(self.db, match.id)
        self.assertIn("还有 1 场未结束", error.exception.detail)
        progresses = self.db.query(TeamProgress).filter_by(match_id=match.id, group_name="附加赛").all()
        self.assertTrue(all(p.stage == StageStatus.CHALLENGER for p in progresses))

    def test_knockout_bye_preserves_full_zone_standings(self):
        match, teams, _ = self.ready_legend()
        service.finish_legend_stage(self.db, match.id)
        service.generate_knockout(self.db, match.id)
        upper_first = self.db.query(TeamProgress).filter_by(match_id=match.id, seed=1).one()
        self.assertEqual(upper_first.team_id, teams[0].id)

    def test_cannot_finish_legend_before_zone_rounds_exist(self):
        match, _, progresses = self.enrolled(8)
        for p in progresses:
            p.stage = StageStatus.LEGEND
            p.group_name = None
        self.db.commit()
        with self.assertRaises(HTTPException):
            service.finish_legend_stage(self.db, match.id)

    def test_reseed_and_regroup_cannot_destroy_knockout(self):
        match, _, _ = self.ready_legend()
        service.finish_legend_stage(self.db, match.id)
        service.generate_knockout(self.db, match.id)
        before = [(p.team_id, p.stage, p.group_name, p.seed) for p in self.db.query(TeamProgress)]
        for action in [lambda: service.auto_assign_seeds(self.db, match.id),
                       lambda: service.auto_group_teams(self.db, match.id, ["A", "B", "C"])]:
            with self.assertRaises(HTTPException):
                action()
            self.db.rollback()
        self.assertEqual(before, [(p.team_id, p.stage, p.group_name, p.seed) for p in self.db.query(TeamProgress)])
        self.assertEqual(self.db.query(MatchRound).filter_by(group_name="淘汰赛").count(), 2)

    def test_score_corrections_allowed_until_next_stage_exists(self):
        match, _, _ = self.ready_legend()
        service.finish_legend_stage(self.db, match.id)
        service.generate_knockout(self.db, match.id)
        quarters = self.db.query(MatchRound).filter_by(group_name="淘汰赛").order_by(MatchRound.round_number).all()
        for r in quarters:
            self.finish_knockout_round(r)
        self.finish_knockout_round(quarters[0], reverse=True)
        expected_winner = quarters[0].winner_id
        service.advance_knockout(self.db, match.id)
        with self.assertRaises(HTTPException):
            self.finish_knockout_round(quarters[0])
        self.db.rollback()
        self.assertEqual(quarters[0].winner_id, expected_winner)
        self.assertEqual(self.db.query(MatchRound).filter_by(group_name="淘汰赛").count(), 4)

    def test_legend_scores_locked_after_qualification(self):
        match, _, _ = self.ready_legend()
        source = self.db.query(MatchRound).first()
        service.finish_legend_stage(self.db, match.id)
        with self.assertRaises(HTTPException):
            service.update_round_result(self.db, source.id, 1, 13)

    def test_manual_round_without_group_can_be_scored_and_corrected(self):
        match, teams, _ = self.enrolled(2)
        match_round = service.add_round(self.db, match.id, 1, teams[0].id, teams[1].id)
        service.update_round_result(self.db, match_round.id, 13, 5)
        corrected = service.update_round_result(self.db, match_round.id, 5, 13)
        self.assertEqual(corrected["winner_id"], teams[1].id)

    def test_complete_sixteen_team_competition(self):
        match, _, _ = self.enrolled(16)
        service.auto_assign_seeds(self.db, match.id)
        service.auto_group_teams(self.db, match.id, ["A", "B", "C"])
        for group in ["A", "B", "C"]:
            rounds = service.auto_generate_group_matches(self.db, match.id, group)
            for r in rounds:
                service.update_round_result(self.db, r.id, 13, 5)
        result = service.finish_group_stage(self.db, match.id)
        self.assertEqual(len(result["group_winners"]), 3)
        self.assertEqual(result["added_rounds"], 3)
        self.assertEqual(result["group_count"], 3)
        self.assertTrue(result["has_playoff"])
        with self.assertRaises(HTTPException):
            service.finish_group_stage(self.db, match.id)
        self.db.rollback()
        for r in self.db.query(MatchRound).filter_by(group_name="附加赛").all():
            service.update_round_result(self.db, r.id, 13, 5)
        service.finish_playoff_stage(self.db, match.id)
        service.divide_legend(self.db, match.id)
        for r in self.db.query(MatchRound).filter(MatchRound.group_name.in_(["上区", "下区"])).all():
            service.update_round_result(self.db, r.id, 13, 5)
        service.finish_legend_stage(self.db, match.id)
        service.generate_knockout(self.db, match.id)
        for expected_count in [4, 5]:
            for r in self.db.query(MatchRound).filter_by(group_name="淘汰赛", status=RoundStatus.PENDING).all():
                self.finish_knockout_round(r)
            service.advance_knockout(self.db, match.id)
            self.assertEqual(self.db.query(MatchRound).filter_by(group_name="淘汰赛").count(), expected_count)
        final = self.db.query(MatchRound).filter_by(group_name="淘汰赛", status=RoundStatus.PENDING).one()
        self.finish_knockout_round(final)
        self.assertIsNotNone(final.winner_id)

    def test_schedule_generation_is_one_transaction(self):
        match, _, _ = self.enrolled(3)
        original = service.add_round
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated interruption")
            return original(*args, **kwargs)
        with patch.object(service, "add_round", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                service.auto_generate_single_round_matches(self.db, match.id, "A")
        self.db.rollback()
        self.assertEqual(self.db.query(MatchRound).count(), 0)
