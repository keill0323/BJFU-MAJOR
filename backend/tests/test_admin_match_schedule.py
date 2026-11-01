"""管理员逐场排期：真实 HTTP/隔离 SQLite、通知与审计的原子提交。"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import get_db
from app.models.admin_audit import AdminAudit
from app.models.match import MatchRound, RoundStatus, StageWindow
from app.models.notification import ScheduleNotification
from app.models.user import User, UserRole
from app.routers.match import router
from app.services import admin_audit_service as audit, match_service as matches, notification_service as notices
from app.services.auth_service import create_access_token


def request(app, method, path, payload=None, token=None):
    messages = []
    body = json.dumps(payload).encode() if payload is not None else b""
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(message):
        messages.append(message)
    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                    "query_string": b"", "root_path": "", "headers": headers,
                    "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    return status, json.loads(b"".join(m.get("body", b"") for m in messages
                                     if m["type"] == "http.response.body"))


class AdminMatchScheduleTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.time = datetime(2036, 9, 12, 19)
        clock = patch.object(matches, "_registration_now", return_value=self.time - timedelta(days=1))
        clock.start()
        self.addCleanup(clock.stop)
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.reviewer = self.make_user(role=UserRole.REVIEWER)
        self.first, self.second = self.make_team(), self.make_team()
        self.match = self.make_match()
        self.round = MatchRound(match_id=self.match.id, round_number=1, group_name="A",
                                team1_id=self.first.id, team2_id=self.second.id, status=RoundStatus.PENDING)
        self.db.add(self.round)
        self.db.commit()
        self.path = f"/api/matches/rounds/{self.round.id}/admin-schedule"
        self.app = FastAPI()
        self.app.include_router(router)
        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def api(self, scheduled_time, expected_time, actor=None):
        actor = actor or self.admin
        token = create_access_token(actor.id, actor.role.value)
        encode = lambda value: value.isoformat() if value is not None else None
        return request(self.app, "PUT", self.path,
                       {"scheduled_time": encode(scheduled_time), "expected_time": encode(expected_time)}, token)

    def submit(self, scheduled_time, expected_time, actor=None):
        return matches.admin_schedule_round(self.db, self.round.id, actor or self.admin,
                                            scheduled_time, expected_time)

    def records(self):
        self.db.expire_all()
        return self.db.query(AdminAudit).order_by(AdminAudit.id).all()

    def test_admin_and_reviewer_permissions_are_checked_by_actual_http_dependency(self):
        self.assertEqual(request(self.app, "PUT", self.path)[0], 401)
        captain = self.db.get(User, self.first.captain_id)
        self.assertEqual(self.api(self.time, None, captain)[0], 403)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 0)
        status, result = self.api(self.time, None, self.reviewer)
        self.assertEqual(status, 200)
        self.assertEqual(result["schedule_status"], "confirmed")
        self.assertEqual({entry.actor_role for entry in self.records()}, {"reviewer"})

    def test_both_nullable_fields_must_be_present(self):
        token = create_access_token(self.admin.id, "admin")
        for payload in ({}, {"scheduled_time": self.time.isoformat()}, {"expected_time": None}):
            with self.subTest(payload=payload):
                self.assertEqual(request(self.app, "PUT", self.path, payload, token)[0], 422)
        self.assertEqual(self.api(None, None)[0], 200)
        self.assertEqual(self.records(), [])

    def test_scheduling_needs_no_window_and_records_both_captains_and_actor(self):
        self.assertEqual(self.db.query(StageWindow).count(), 0)
        status, result = self.api(self.time, None)
        self.assertEqual(status, 200)
        self.assertTrue(result["team1_confirmed"] and result["team2_confirmed"])
        rows = self.db.query(ScheduleNotification).all()
        self.assertEqual({row.user_id for row in rows}, {self.first.captain_id, self.second.captain_id})
        self.assertTrue(all(row.kind == "admin_scheduled" and row.scheduled_time == self.time for row in rows))
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertEqual({entry.target_user_id for entry in records}, {self.first.captain_id, self.second.captain_id})
        for entry in records:
            self.assertEqual((entry.actor_id, entry.actor_role, entry.action), (self.admin.id, "admin", "round.schedule"))
            self.assertEqual(entry.before_values, {"match_id": self.match.id, "round_id": self.round.id,
                "scheduled_time": None, "team1_confirmed": False, "team2_confirmed": False})
            self.assertEqual(entry.after_values["scheduled_time"], self.time.isoformat())
        self.assertEqual(audit.list_audits(self.db, action="round.schedule")["total"], 2)

    def test_retries_reschedule_and_cancel_are_idempotent_and_preserve_previous_time(self):
        for _ in range(2):
            self.assertEqual(self.api(self.time, None)[0], 200)
        later = self.time + timedelta(hours=1)
        for _ in range(2):
            self.assertEqual(self.api(later, self.time)[0], 200)
        for _ in range(2):
            status, result = self.api(None, later)
            self.assertEqual(status, 200)
            self.assertIsNone(result["scheduled_time"])
            self.assertFalse(result["team1_confirmed"] or result["team2_confirmed"])
        rows = notices.list_notifications(self.db, self.first.captain_id)["items"]
        self.assertEqual([row["kind"] for row in rows], ["admin_cancelled", "admin_rescheduled", "admin_scheduled"])
        self.assertEqual([row["scheduled_time"] for row in rows], [later, later, self.time])
        self.assertEqual(len(self.records()), 6)

    def test_changed_time_rejects_stale_writes_without_messages_or_audits(self):
        self.submit(self.time, None)
        for new_time in (None, self.time + timedelta(hours=1)):
            with self.subTest(new_time=new_time):
                status, result = self.api(new_time, None)
                self.assertEqual(status, 409)
                self.assertIn("刷新", result["detail"])
        self.db.expire_all()
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 2)
        self.assertEqual(len(self.records()), 2)

    def test_stale_session_refreshes_round_under_the_existing_write_lock(self):
        with Session(self.engine, autoflush=False, expire_on_commit=False) as stale:
            cached_round = stale.get(MatchRound, self.round.id)
            cached_admin = stale.get(User, self.admin.id)
            self.assertIsNone(cached_round.scheduled_time)
            self.submit(self.time, None)
            with self.assertRaises(HTTPException) as error:
                matches.admin_schedule_round(stale, self.round.id, cached_admin, self.time + timedelta(hours=1), None)
            self.assertEqual(error.exception.status_code, 409)
        self.db.expire_all()
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertEqual(len(self.records()), 2)

    def test_timezone_conversion_applies_to_both_requested_and_expected_times(self):
        utc = self.time.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        self.assertEqual(self.api(utc, None)[0], 200)
        self.db.expire_all()
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertEqual(self.api(utc + timedelta(hours=1), utc)[0], 200)
        self.db.expire_all()
        self.assertEqual(self.round.scheduled_time, self.time + timedelta(hours=1))

    def test_started_finished_bye_missing_round_and_past_times_do_not_change_state(self):
        for state in (RoundStatus.IN_PROGRESS, RoundStatus.FINISHED):
            self.round.status = state
            self.db.commit()
            self.assertEqual(self.api(self.time, None)[0], 400)
        self.round.status = RoundStatus.PENDING
        self.round.team2_id = None
        self.db.commit()
        self.assertEqual(self.api(self.time, None)[0], 400)
        self.round.team2_id = self.second.id
        self.db.commit()
        self.assertEqual(self.api(self.time - timedelta(days=2), None)[0], 400)
        self.round.team2_id = self.first.id
        self.db.commit()
        self.assertEqual(self.api(self.time, None)[0], 400)
        self.assertEqual(request(self.app, "PUT", "/api/matches/rounds/999999/admin-schedule",
            {"scheduled_time": self.time.isoformat(), "expected_time": None}, create_access_token(self.admin.id, "admin"))[0], 404)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 0)
        self.assertEqual(self.records(), [])

    def test_admin_who_is_also_captain_receives_the_notice(self):
        self.first.captain_id = self.admin.id
        self.db.commit()
        self.submit(self.time, None)
        self.assertEqual(notices.list_notifications(self.db, self.admin.id)["unread_count"], 1)

    def test_notification_uses_current_captain(self):
        old_id = self.second.captain_id
        new_captain = self.make_user()
        self.second.captain_id = new_captain.id
        self.db.commit()
        self.submit(self.time, None)
        self.assertEqual(notices.list_notifications(self.db, old_id)["unread_count"], 0)
        self.assertEqual(notices.list_notifications(self.db, new_captain.id)["unread_count"], 1)
        self.assertIn(new_captain.id, {record.target_user_id for record in self.records()})

    def test_notice_failure_rolls_back_time_flags_and_audit_even_after_flush(self):
        original = notices.notify_schedule
        def fail(db, *args):
            original(db, *args)
            db.flush()
            raise RuntimeError("notice failed")
        with patch.object(notices, "notify_schedule", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "notice failed"):
                self.submit(self.time, None)
        self.assert_rolled_back()

    def test_audit_failure_rolls_back_time_flags_and_notifications_even_after_flush(self):
        original = audit.record_round_schedule
        def fail(db, *args):
            original(db, *args)
            db.flush()
            raise RuntimeError("audit failed")
        with patch.object(audit, "record_round_schedule", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                self.submit(self.time, None)
        self.assert_rolled_back()

    def test_commit_failure_rolls_back_all_changes(self):
        with patch.object(self.db, "commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                self.submit(self.time, None)
        self.assert_rolled_back()

    def assert_rolled_back(self):
        self.db.expire_all()
        self.assertIsNone(self.round.scheduled_time)
        self.assertFalse(self.round.team1_confirmed or self.round.team2_confirmed)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 0)
        self.assertEqual(self.records(), [])

    def test_stale_actor_role_cannot_bypass_service_authorization(self):
        self.admin.role = UserRole.USER
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            self.submit(self.time, None)
        self.assertEqual(error.exception.status_code, 403)
        self.assert_rolled_back()

    def test_legacy_captain_http_writes_return_410_without_mutating_data(self):
        self.submit(self.time, None)
        for actor_id in (self.admin.id, self.first.captain_id, self.second.captain_id):
            for suffix, method in (("schedule", "PUT"), ("schedule/confirm", "POST"), ("schedule/reject", "POST")):
                with self.subTest(actor_id=actor_id, suffix=suffix):
                    status, result = request(self.app, method, f"/api/matches/rounds/{self.round.id}/{suffix}",
                        {"scheduled_time": (self.time + timedelta(hours=1)).isoformat(), "expected_time": self.time.isoformat()},
                        create_access_token(actor_id, "user"))
                    self.assertEqual(status, 410)
                    self.assertIn("管理员统一安排", result["detail"])
        self.db.expire_all()
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertTrue(self.round.team1_confirmed and self.round.team2_confirmed)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 2)
        self.assertEqual(len(self.records()), 2)

    def test_reference_window_does_not_cancel_admin_schedule_or_notify_again(self):
        self.submit(self.time, None)
        matches.set_stage_window(self.db, self.match.id, "A", self.time + timedelta(hours=3), self.time + timedelta(hours=5))
        self.db.expire_all()
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertTrue(self.round.team1_confirmed and self.round.team2_confirmed)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 2)
        self.assertEqual(len(self.records()), 2)
