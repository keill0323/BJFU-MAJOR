"""Real schedule writes and private inbox routes against isolated SQLite."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from tests.support import DatabaseTestCase
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import get_db
from app.models.match import MatchRound, RoundStatus, StageWindow
from app.models.notification import ScheduleNotification
from app.routers.notifications import router
from app.services import match_service as matches, notification_service as notices
from app.services.auth_service import create_access_token
from app.migrations.schedule_notifications import upgrade


def request(app, method, url, token=None):
    url = urlsplit(url)
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": method, "scheme": "http", "path": url.path,
                    "raw_path": url.path.encode(), "query_string": url.query.encode(),
                    "root_path": "", "headers": headers, "client": ("127.0.0.1", 1),
                    "server": ("test", 80)}, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class ScheduleNotificationTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.time = datetime(2036, 9, 12, 19)
        fixed_clock = patch.object(matches, "_registration_now", return_value=self.time - timedelta(days=1))
        fixed_clock.start()
        self.addCleanup(fixed_clock.stop)
        self.match = self.make_match()
        self.first, self.second = self.make_team(), self.make_team()
        self.stranger = self.make_user()
        self.round = MatchRound(match_id=self.match.id, round_number=1, group_name="淘汰赛",
                                team1_id=self.first.id, team2_id=self.second.id, status=RoundStatus.PENDING)
        self.db.add(self.round)
        self.db.commit()
        matches.set_stage_window(self.db, self.match.id, "淘汰赛", self.time - timedelta(hours=4), self.time + timedelta(days=1))
        self.app = FastAPI()
        self.app.include_router(router)
        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def submit(self, when=None, actor=None):
        return matches.schedule_round(self.db, self.round.id, actor or self.first.captain_id, when or self.time)

    def inbox(self, user_id):
        return notices.list_notifications(self.db, user_id)

    def api(self, method="GET", suffix="", user=None):
        token = create_access_token(user, "user") if user else None
        return request(self.app, method, "/api/notifications/schedules" + suffix, token)

    def test_proposal_only_notifies_opponent_and_confirmation_notifies_proposer_once(self):
        self.submit()
        self.submit()  # Retry must not duplicate the event.
        self.assertEqual(self.inbox(self.first.captain_id)["items"], [])
        inbox = self.inbox(self.second.captain_id)
        self.assertEqual(inbox["unread_count"], 1)
        self.assertEqual(inbox["items"][0]["kind"], "proposed")
        self.assertEqual(self.inbox(self.stranger.id)["items"], [])
        for _ in range(2):
            result = matches.confirm_round_schedule(self.db, self.round.id, self.second.captain_id, self.time)
        self.assertEqual(result["schedule_status"], "confirmed")
        self.assertEqual(self.inbox(self.first.captain_id)["unread_count"], 1)
        self.assertEqual(self.inbox(self.first.captain_id)["items"][0]["kind"], "confirmed")

    def test_changes_preserve_old_notice_and_stale_confirmation_does_not_write(self):
        self.submit()
        later = self.time + timedelta(hours=1)
        self.submit(later)
        before = self.db.query(ScheduleNotification).count()
        with self.assertRaises(HTTPException) as error:
            matches.confirm_round_schedule(self.db, self.round.id, self.second.captain_id, self.time)
        self.assertEqual(error.exception.status_code, 409)
        self.db.rollback()
        rows = self.inbox(self.second.captain_id)["items"]
        self.assertEqual([r["kind"] for r in rows], ["updated", "proposed"])
        self.assertEqual([r["scheduled_time"] for r in rows], [later, self.time])
        self.assertEqual(self.db.query(ScheduleNotification).count(), before)

    def test_rejection_and_withdrawal_notify_the_other_captain(self):
        self.submit()
        matches.reject_round_schedule(self.db, self.round.id, self.second.captain_id, self.time)
        self.assertEqual(self.inbox(self.first.captain_id)["items"][0]["kind"], "rejected")
        self.assertIsNone(self.round.scheduled_time)
        self.submit()
        matches.reject_round_schedule(self.db, self.round.id, self.first.captain_id, self.time)
        self.assertEqual(self.inbox(self.second.captain_id)["items"][0]["kind"], "cancelled")
        self.submit()
        matches.confirm_round_schedule(self.db, self.round.id, self.second.captain_id, self.time)
        matches.reject_round_schedule(self.db, self.round.id, self.second.captain_id, self.time)
        self.assertEqual(self.inbox(self.first.captain_id)["items"][0]["kind"], "cancelled")

    def test_window_change_notifies_both_captains_but_preserves_finished_rounds(self):
        self.submit()
        matches.set_stage_window(self.db, self.match.id, "淘汰赛", self.time + timedelta(hours=2), self.time + timedelta(hours=4))
        for user_id in (self.first.captain_id, self.second.captain_id):
            self.assertEqual(self.inbox(user_id)["items"][0]["kind"], "window_changed")
        self.assertIsNone(self.round.scheduled_time)
        self.round.scheduled_time = self.time
        self.round.status = RoundStatus.FINISHED
        self.db.commit()
        before = self.db.query(ScheduleNotification).count()
        matches.set_stage_window(self.db, self.match.id, "淘汰赛", self.time + timedelta(hours=3), self.time + timedelta(hours=4))
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertEqual(self.db.query(ScheduleNotification).count(), before)

    def test_notice_failure_rolls_back_schedule_even_after_flush(self):
        original = notices.notify_schedule
        def fail(db, *args):
            original(db, *args)
            db.flush()
            raise RuntimeError("notice storage failure")
        with patch.object(notices, "notify_schedule", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "notice storage"):
                self.submit()
        self.db.expire_all()
        self.assertIsNone(self.round.scheduled_time)
        self.assertFalse(self.round.team1_confirmed)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 0)

    def test_window_notice_failure_rolls_back_window_and_all_cancellations(self):
        self.submit()
        window = self.db.query(StageWindow).one()
        previous = window.window_start
        with patch.object(notices, "notify_schedule", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                matches.set_stage_window(self.db, self.match.id, "淘汰赛", self.time + timedelta(hours=2), self.time + timedelta(hours=4))
        self.db.expire_all()
        self.assertEqual(window.window_start, previous)
        self.assertEqual(self.round.scheduled_time, self.time)
        self.assertEqual(self.db.query(ScheduleNotification).count(), 1)

    def test_permissions_window_and_time_validation_do_not_create_messages(self):
        for actor, when in [(self.stranger.id, self.time), (self.first.captain_id, self.time - timedelta(days=2))]:
            with self.assertRaises(HTTPException):
                self.submit(when, actor)
            self.db.rollback()
        self.db.query(StageWindow).delete()
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            self.submit()
        self.assertIn("时间窗口", error.exception.detail)
        self.db.rollback()
        self.assertEqual(self.db.query(ScheduleNotification).count(), 0)

    def test_timezone_is_converted_to_beijing_before_saving_and_confirming(self):
        utc = self.time.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        self.submit(utc)
        self.assertEqual(self.round.scheduled_time, self.time)
        result = matches.confirm_round_schedule(self.db, self.round.id, self.second.captain_id, utc)
        self.assertEqual(result["schedule_status"], "confirmed")

    def test_inbox_authentication_isolation_and_read_idempotence(self):
        self.submit()
        recipient = self.second.captain_id
        self.assertEqual(self.api()[0], 401)
        status, data = self.api(user=recipient)
        self.assertEqual(status, 200)
        notice_id = data["items"][0]["id"]
        self.assertNotIn("user_id", data["items"][0])
        self.assertEqual(self.api(user=self.stranger.id)[1]["items"], [])
        self.assertEqual(self.api("PUT", f"/{notice_id}/read", self.stranger.id)[0], 404)
        for _ in range(2):
            self.assertEqual(self.api("PUT", f"/{notice_id}/read", recipient)[0], 200)
        self.assertEqual(self.api(user=recipient)[1]["unread_count"], 0)
        for query in ("?limit=0", "?limit=101", "?before_id=0"):
            self.assertEqual(self.api(suffix=query, user=recipient)[0], 422)

    def test_cursor_does_not_duplicate_messages_when_a_new_proposal_arrives(self):
        self.submit()
        self.submit(self.time + timedelta(hours=1))
        recipient = self.second.captain_id
        first = self.api(suffix="?limit=1", user=recipient)[1]
        self.submit(self.time + timedelta(hours=2))
        second = self.api(suffix=f'?limit=1&before_id={first["next_cursor"]}', user=recipient)[1]
        self.assertEqual(first["items"][0]["kind"], "updated")
        self.assertEqual(second["items"][0]["kind"], "proposed")
        self.assertFalse(second["has_more"])
        self.assertEqual(second["unread_count"], 3)

    def test_deleted_round_retains_history_without_an_actionable_link(self):
        self.submit()
        self.db.delete(self.round)
        self.db.commit()
        result = self.inbox(self.second.captain_id)
        self.assertFalse(result["items"][0]["round_available"])
        self.assertEqual(result["items"][0]["team1_name"], self.first.name)

    def test_notification_migration_can_create_and_rerun_without_rewriting_notices(self):
        ScheduleNotification.__table__.drop(self.engine)
        upgrade(self.engine)
        self.submit()
        before = self.inbox(self.second.captain_id)
        upgrade(self.engine)
        self.db.expire_all()
        self.assertEqual(self.inbox(self.second.captain_id), before)
