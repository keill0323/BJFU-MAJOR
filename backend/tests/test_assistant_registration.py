"""确认链路使用隔离数据库，不连接生产或模型。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import Base
from app.models.assistant_action import AssistantAction
from app.models.match import Match, MatchStatus, Registration, RegistrationStatus, TeamProgress
from app.models.team import Team, TeamMember, TeamStatus
from app.models.user import User
from app.services import assistant_registration_service as service, match_service


class AssistantRegistrationTests(DatabaseTestCase):
    def personal(self):
        user, match = self.make_user(), self.make_match()
        self.db.commit()
        return user, match

    def team(self, **match_values):
        captain = self.make_user()
        member = self.make_user()
        team = self.make_team(captain=captain, members=[member])
        match = self.make_match(**match_values)
        self.db.commit()
        return captain, team, member, match

    def preview(self, user, match, mode="personal", team_id=None):
        result = service.preview_registration(self.db, user, match.id, mode, team_id)
        self.assertEqual(result["status"], "ok", result)
        return result["data"]["action_id"]

    def assert_blocked_confirm(self, user, action_id, code):
        with self.assertRaises(HTTPException) as caught:
            service.confirm_registration(self.db, user, action_id)
        self.assertEqual(caught.exception.detail["code"], code)
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_diagnosis_uses_only_reads_without_actual_registration_or_transaction_mutations(self):
        captain, team, _, match = self.team()
        for mode, actor, team_id in (("team", captain, team.id),):
            statements = []
            def capture(_conn, _cursor, statement, *_args):
                statements.append(statement)
            event.listen(self.engine, "before_cursor_execute", capture)
            try:
                with patch.object(self.db, "commit", side_effect=AssertionError("diagnosis committed")), \
                     patch.object(self.db, "flush", side_effect=AssertionError("diagnosis flushed")), \
                     patch.object(match_service, "register_team", side_effect=AssertionError("simulated registration")), \
                     patch.object(match_service, "register_user", side_effect=AssertionError("simulated registration")):
                    result = service.check_registration(self.db, actor, match.id, mode, team_id)
                self.assertTrue(result["data"]["eligible"])
            finally:
                event.remove(self.engine, "before_cursor_execute", capture)
            self.assertTrue(statements)
            self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))

    def test_preview_does_not_register_and_contains_only_explicit_button_confirmation(self):
        user, match = self.personal()
        result = service.preview_registration(self.db, user, match.id, "personal")
        self.assertEqual(self.db.query(Registration).count(), 0)
        self.assertEqual(self.db.query(AssistantAction).count(), 1)
        self.assertEqual(result["cards"][0]["action"]["kind"], "confirm_registration")
        self.assertEqual(result["cards"][0]["mode"], "personal")
        self.assertIn("点击确认", result["message"])
        self.assertNotIn(user.student_id, json.dumps(result))
        self.assertNotIn("snapshot_hash", result["data"])

    def test_personal_confirm_reuses_original_auto_approval_and_replay_checks_live_status(self):
        user, match = self.personal()
        action = self.preview(user, match)
        first = service.confirm_registration(self.db, user, action)
        self.assertEqual(first["data"]["registration_status"], "approved")
        self.assertFalse(first["data"]["replayed"])
        record = self.db.query(Registration).one()
        self.assertIsNone(record.team_id)
        record.status = RegistrationStatus.REJECTED
        self.db.commit()
        second = service.confirm_registration(self.db, user, action)
        self.assertTrue(second["data"]["replayed"])
        self.assertEqual(second["data"]["registration_status"], "rejected")
        self.assertEqual(self.db.query(Registration).count(), 1)

    def test_team_confirm_stays_pending_and_does_not_create_qualified_progress(self):
        captain, team, _, match = self.team()
        action = self.preview(captain, match, "team", team.id)
        result = service.confirm_registration(self.db, captain, action)
        records = self.db.query(Registration).all()
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.status == RegistrationStatus.PENDING for r in records))
        self.assertEqual(result["data"]["registration_status"], "pending")
        self.assertEqual(result["cards"][0]["mode"], "team")
        self.assertEqual(self.db.query(TeamProgress).count(), 0)
        self.assertIn("待管理员审核", result["message"])

    def test_unknown_user_cannot_query_or_confirm_another_users_action(self):
        user, match = self.personal()
        other = self.make_user()
        self.db.commit()
        action = self.preview(user, match)
        self.assert_blocked_confirm(other, action, "action_not_found")
        self.assertEqual(self.db.get(AssistantAction, action).state, "pending")

    def test_only_current_captain_can_preview_or_confirm_team(self):
        captain, team, member, match = self.team()
        result = service.preview_registration(self.db, member, match.id, "team", team.id)
        self.assertEqual(result["data"]["code"], "captain_required")
        action = self.preview(captain, match, "team", team.id)
        team.captain_id = member.id
        self.db.commit()
        self.assert_blocked_confirm(captain, action, "captain_required")

    def test_expired_confirmation_is_recorded_and_never_registers(self):
        user, match = self.personal()
        action = self.preview(user, match)
        self.db.get(AssistantAction, action).expires_at = service._now() - timedelta(seconds=1)
        self.db.commit()
        self.assert_blocked_confirm(user, action, "action_expired")
        self.assertEqual(self.db.get(AssistantAction, action).state, "expired")

    def test_changed_rank_or_rules_require_a_new_confirmation(self):
        for change in ("rank", "team_size", "name", "register_end"):
            with self.subTest(change=change):
                user, match = self.personal()
                action = self.preview(user, match)
                if change == "rank":
                    user.rank = "B"
                elif change == "register_end":
                    match.register_end = match_service._registration_now() + timedelta(hours=1)
                elif change == "name":
                    match.name += "修订"
                else:
                    match.team_size = 6
                self.db.commit()
                self.assert_blocked_confirm(user, action, "preconditions_changed")
                self.assertEqual(self.db.get(AssistantAction, action).state, "invalidated")

    def test_roster_change_after_preview_is_not_silently_registered(self):
        captain, team, _, match = self.team()
        action = self.preview(captain, match, "team", team.id)
        user = self.make_user()
        self.db.add(TeamMember(team_id=team.id, user_id=user.id))
        self.db.commit()
        self.assert_blocked_confirm(captain, action, "preconditions_changed")

    def test_closed_window_and_full_capacity_are_rechecked(self):
        captain, team, _, match = self.team(max_teams=1)
        action = self.preview(captain, match, "team", team.id)
        other = self.make_team()
        self.db.add(Registration(match_id=match.id, team_id=other.id, user_id=other.captain_id))
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            service.confirm_registration(self.db, captain, action)
        self.assertEqual(caught.exception.detail["code"], "preconditions_changed")
        self.assertIn("已满", caught.exception.detail["message"])
        self.assertEqual(self.db.query(Registration).filter_by(team_id=team.id).count(), 0)

        user, match2 = self.personal()
        action2 = self.preview(user, match2)
        match2.status = MatchStatus.IN_PROGRESS
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            service.confirm_registration(self.db, user, action2)
        self.assertIn("条件已变化", caught.exception.detail["message"])
        self.assertEqual(self.db.query(Registration).filter_by(match_id=match2.id).count(), 0)

    def test_team_roster_lock_prevents_preview_and_confirmation(self):
        captain, team, _, match = self.team()
        action = self.preview(captain, match, "team", team.id)
        self.db.add(TeamProgress(match_id=match.id, team_id=team.id, seed=1))
        self.db.commit()
        self.assertFalse(service.check_registration(self.db, captain, match.id, "team", team.id)["data"]["eligible"])
        self.assert_blocked_confirm(captain, action, "preconditions_changed")

    def test_unverified_and_missing_rank_block_preview_without_exposing_student_id_or_qq(self):
        for values in ({"is_verified": False, "student_id": "114514"}, {"rank": None}):
            user = self.make_user(**values)
            match = self.make_match()
            self.db.commit()
            result = service.preview_registration(self.db, user, match.id, "personal")
            self.assertFalse(result["data"]["eligible"])
            self.assertNotIn(user.student_id, json.dumps(result, ensure_ascii=False))
            self.assertNotIn("3761215994", json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.db.query(AssistantAction).count(), 0)

    def test_freshman_team_rule_is_the_existing_three_new_students_rule(self):
        captain, team, _, match = self.team(match_type="freshman")
        result = service.check_registration(self.db, captain, match.id, "team", team.id)
        self.assertFalse(result["data"]["eligible"])
        self.assertIn("至少 3 名新生", result["message"])

    def test_existing_individual_registration_can_upgrade_to_team_pending(self):
        captain, team, member, match = self.team()
        self.db.add(Registration(match_id=match.id, user_id=member.id, status=RegistrationStatus.APPROVED))
        self.db.commit()
        action = self.preview(captain, match, "team", team.id)
        service.confirm_registration(self.db, captain, action)
        self.assertEqual(self.db.query(Registration).count(), 2)
        self.assertTrue(all(r.status == RegistrationStatus.PENDING and r.team_id == team.id
                            for r in self.db.query(Registration).all()))

    def test_multiple_previews_do_not_allow_a_second_side_effect(self):
        user, match = self.personal()
        first, second = self.preview(user, match), self.preview(user, match)
        service.confirm_registration(self.db, user, first)
        with self.assertRaises(HTTPException) as caught:
            service.confirm_registration(self.db, user, second)
        self.assertEqual(caught.exception.detail["code"], "preconditions_changed")
        self.assertEqual(self.db.query(Registration).count(), 1)

    def test_hidden_accounts_teams_and_invalid_parameters_never_create_actions(self):
        user, match = self.personal()
        for match_id, mode, team_id in ((True, "personal", None), (match.id, "auto_approve", None),
                                        (match.id, "personal", 1)):
            self.assertEqual(service.preview_registration(self.db, user, match_id, mode, team_id)["status"], "blocked")
        user.is_hidden = True
        self.db.commit()
        self.assertEqual(service.preview_registration(self.db, user, match.id, "personal")["status"], "blocked")
        self.assertEqual(self.db.query(AssistantAction).count(), 0)

    def test_commit_failure_rolls_back_registration_and_claim_then_same_action_can_retry(self):
        user, match = self.personal()
        action = self.preview(user, match)
        original = self.db.commit
        calls = 0
        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OperationalError("COMMIT", {}, RuntimeError("simulated disconnect before commit"))
            return original()
        with patch.object(self.db, "commit", side_effect=fail_once), self.assertRaises(HTTPException) as caught:
            service.confirm_registration(self.db, user, action)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.db.query(Registration).count(), 0)
        self.assertEqual(self.db.get(AssistantAction, action).state, "pending")
        result = service.confirm_registration(self.db, user, action)
        self.assertEqual(result["data"]["registration_status"], "approved")
        self.assertEqual(self.db.query(Registration).count(), 1)

    def test_lost_commit_response_recovers_committed_result_without_resubmitting(self):
        user, match = self.personal()
        action = self.preview(user, match)
        original = self.db.commit
        def committed_but_response_lost():
            original()
            raise OperationalError("COMMIT", {}, RuntimeError("simulated response lost"))
        with patch.object(self.db, "commit", side_effect=committed_but_response_lost):
            result = service.confirm_registration(self.db, user, action)
        self.assertTrue(result["data"]["replayed"])
        self.assertEqual(self.db.query(Registration).count(), 1)

    def test_deleted_result_is_reported_as_changed_not_registered_again(self):
        user, match = self.personal()
        action = self.preview(user, match)
        service.confirm_registration(self.db, user, action)
        self.db.query(Registration).delete()
        self.db.commit()
        result = service.confirm_registration(self.db, user, action)
        self.assertEqual(result["data"]["registration_status"], "changed")
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_deleting_a_match_cascades_confirmation_records_without_blocking_existing_admin_flow(self):
        user, match = self.personal()
        action = self.preview(user, match)
        other = self.make_match()
        self.db.commit()
        other_action = self.preview(user, other)
        match_service.delete_match(self.db, match.id)
        self.assertIsNone(self.db.query(AssistantAction).filter_by(id=action).first())
        self.assertIsNotNone(self.db.query(AssistantAction).filter_by(id=other_action).first())


class ConcurrentAssistantConfirmationTests(DatabaseTestCase):
    def test_two_independent_database_connections_confirm_once(self):
        root = Path(__file__).resolve().parents[2]
        local = root / ".local"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local, prefix="assistant-confirm-test-") as directory:
            engine = create_engine("sqlite:///" + (Path(directory) / "test.sqlite").as_posix(),
                                   connect_args={"check_same_thread": False, "timeout": 30})
            try:
                Base.metadata.create_all(engine)
                with Session(engine, autoflush=False) as db:
                    user = User(nickname="Concurrent", is_verified=True, rank="A", student_id="260111111")
                    match = Match(name="Concurrent test", status=MatchStatus.REGISTERING, match_type="major")
                    db.add_all([user, match])
                    db.commit()
                    user_id, match_id = user.id, match.id
                    action = service.preview_registration(db, user, match_id, "personal")["data"]["action_id"]
                barrier = threading.Barrier(2)
                def worker():
                    with Session(engine, autoflush=False) as db:
                        actor = SimpleNamespace(id=user_id)
                        barrier.wait(timeout=10)
                        return service.confirm_registration(db, actor, action)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _n: worker(), range(2)))
                self.assertEqual(sorted(r["data"]["replayed"] for r in results), [False, True])
                with Session(engine) as db:
                    self.assertEqual(db.query(Registration).count(), 1)
                    self.assertEqual(db.get(AssistantAction, action).state, "completed")
            finally:
                engine.dispose()
