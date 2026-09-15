"""定向站内通知：隔离数据库、无线上发送、无微信网络调用。"""
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from tests.support import DatabaseTestCase
from tests.test_manual_champions import request
from app.database import get_db
from app.models.admin_notice import AdminNotice, AdminNoticeBatch
from app.models.user import UserRole
from app.routers import notifications
from app.schemas.admin_notice import SendAdminNotice
from app.services import admin_notice_service as notices, notification_service
from app.services.auth_service import create_access_token
from app.migrations.admin_notices import upgrade


class AdminNoticeTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.reader = self.make_user()
        self.other = self.make_user()
        self.reviewer = self.make_user(role=UserRole.REVIEWER)
        self.hidden = self.make_user(is_hidden=True)
        self.db.commit()
        self.app = FastAPI()
        self.app.include_router(notifications.router)
        def isolated_db():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def token(self, user):
        return create_access_token(user.id, user.role.value)

    def body(self, **overrides):
        return {"request_id": "test-notice-request-0001", "recipient_ids": [self.reader.id],
                "title": "比赛安排", "content": "请在赛事详情确认本轮比赛时间。", **overrides}

    def send(self, **overrides):
        return notices.send_notice(self.db, self.admin.id, SendAdminNotice(**self.body(**overrides)))

    def test_only_admin_can_search_send_and_view_history(self):
        for user, code in ((None, 401), (self.reader, 403), (self.reviewer, 403)):
            token = self.token(user) if user else None
            for method, url, body in (("GET", "/api/notifications/admin/recipients", None),
                                      ("POST", "/api/notifications/admin/send", self.body()),
                                      ("GET", "/api/notifications/admin/sent", None)):
                status, result = request(self.app, method, url, body, token)
                self.assertEqual(status, code, result)
        self.assertEqual(self.db.query(AdminNotice).count(), 0)

    def test_target_search_uses_public_fields_and_excludes_hidden_users(self):
        self.reader.nickname = "Unique%_name"
        self.reader.game_id = "SelectedPlayer"
        self.db.commit()
        for keyword in (str(self.reader.id), "SelectedPlayer", "Unique%_"):
            result = notices.recipients(self.db, keyword)
            self.assertEqual([u["id"] for u in result["items"]], [self.reader.id])
        self.assertEqual(notices.recipients(self.db, self.reader.student_id)["items"], [])
        result = notices.recipients(self.db)
        self.assertNotIn(self.hidden.id, [u["id"] for u in result["items"]])
        self.assertNotIn("student_id", str(result))
        self.assertNotIn("wx_openid", str(result))
        page = notices.recipients(self.db, limit=2)
        second = notices.recipients(self.db, before_id=page["next_cursor"], limit=2)
        self.assertFalse({u["id"] for u in page["items"]} & {u["id"] for u in second["items"]})

    def test_selected_users_receive_once_and_other_users_receive_nothing(self):
        result = self.send(recipient_ids=[self.reader.id, self.reader.id, self.other.id])
        self.assertEqual(result["recipient_count"], 2)
        for user in (self.reader, self.other):
            inbox = notices.inbox(self.db, user.id)
            self.assertEqual(inbox["unread_count"], 1)
            self.assertEqual(inbox["items"][0]["title"], "比赛安排")
            self.assertNotIn("user_id", str(inbox))
            self.assertNotIn("recipient_ids", str(inbox))
        self.assertEqual(notices.inbox(self.db, self.reviewer.id)["items"], [])

    def test_repeat_request_across_sessions_is_idempotent_but_changed_content_conflicts(self):
        first = self.send()
        with Session(self.engine) as other:
            again = notices.send_notice(other, self.admin.id, SendAdminNotice(**self.body()))
        self.assertEqual(first["id"], again["id"])
        self.assertTrue(again["replayed"])
        self.assertEqual(self.db.query(AdminNotice).count(), 1)
        with self.assertRaises(HTTPException) as error:
            self.send(content="不同内容")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.db.query(AdminNoticeBatch).count(), 1)

    def test_invalid_or_hidden_recipient_prevents_entire_batch(self):
        for bad in (self.hidden.id, 999999):
            with self.assertRaises(HTTPException) as error:
                self.send(recipient_ids=[self.reader.id, bad])
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(self.db.query(AdminNoticeBatch).count(), 0)
            self.assertEqual(self.db.query(AdminNotice).count(), 0)

    def test_transaction_failure_leaves_no_batch_or_inbox_records(self):
        with patch.object(self.db, "commit", side_effect=RuntimeError("write failure")):
            with self.assertRaises(RuntimeError):
                self.send()
        self.assertEqual(self.db.query(AdminNoticeBatch).count(), 0)
        self.assertEqual(self.db.query(AdminNotice).count(), 0)

    def test_revoked_sender_role_cannot_send_via_service(self):
        self.admin.role = UserRole.REVIEWER
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            self.send()
        self.assertEqual(error.exception.status_code, 403)

    def test_inbox_read_is_owned_idempotent_and_updates_home_and_history(self):
        self.send()
        summary = notification_service.personal_summary(self.db, self.reader.id)
        self.assertEqual(summary["admin_notice_count"], 1)
        self.assertEqual(summary["admin_notice_title"], "比赛安排")
        self.assertEqual(summary["total"], 1)
        row = notices.inbox(self.db, self.reader.id)["items"][0]
        with self.assertRaises(HTTPException) as error:
            notices.mark_read(self.db, self.other.id, row["id"])
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(notices.inbox(self.db, self.reader.id)["unread_count"], 1)
        notices.mark_read(self.db, self.reader.id, row["id"])
        first_read = self.db.get(AdminNotice, row["id"]).read_at
        notices.mark_read(self.db, self.reader.id, row["id"])
        self.assertEqual(self.db.get(AdminNotice, row["id"]).read_at, first_read)
        summary = notification_service.personal_summary(self.db, self.reader.id)
        self.assertEqual((summary["admin_notice_count"], summary["admin_notice_title"], summary["total"]), (0, "", 0))
        self.assertEqual(notices.sent(self.db)["items"][0]["read_count"], 1)

    def test_inbox_pagination_stays_stable_when_new_notice_arrives(self):
        for number in range(4):
            self.send(request_id=f"notice-pagination-{number}")
        page = notices.inbox(self.db, self.reader.id, limit=2)
        self.send(request_id="notice-pagination-new")
        second = notices.inbox(self.db, self.reader.id, before_id=page["next_cursor"], limit=2)
        self.assertFalse({n["id"] for n in page["items"]} & {n["id"] for n in second["items"]})
        self.assertEqual(second["unread_count"], 5)
        self.assertFalse(second["has_more"])

    def test_api_rejects_bad_payload_and_delivers_only_to_authenticated_recipient(self):
        for override in ({"title": " "}, {"content": "x" * 501}, {"recipient_ids": []},
                         {"recipient_ids": [True]}, {"recipient_ids": [1] * 101}, {"request_id": "short"}):
            status, result = request(self.app, "POST", "/api/notifications/admin/send", self.body(**override), self.token(self.admin))
            self.assertEqual(status, 422, result)
        status, result = request(self.app, "POST", "/api/notifications/admin/send", self.body(), self.token(self.admin))
        self.assertEqual(status, 200, result)
        self.assertEqual(request(self.app, "GET", "/api/notifications/admin/inbox")[0], 401)
        status, inbox = request(self.app, "GET", "/api/notifications/admin/inbox", token=self.token(self.reader))
        self.assertEqual(status, 200, inbox)
        status, _ = request(self.app, "PUT", f'/api/notifications/admin/inbox/{inbox["items"][0]["id"]}/read', token=self.token(self.other))
        self.assertEqual(status, 404)

    def test_migration_is_idempotent_and_retains_existing_notifications(self):
        self.send()
        upgrade(self.engine)
        upgrade(self.engine)
        self.assertEqual(self.db.query(AdminNotice).count(), 1)
        self.assertIn("ix_admin_notice_inbox", {i["name"] for i in inspect(self.engine).get_indexes("admin_notices")})

    def test_migration_creates_missing_tables_without_changing_existing_users(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, nickname TEXT)")
                connection.exec_driver_sql("INSERT INTO users VALUES (1, 'existing user')")
            upgrade(engine)
            upgrade(engine)
            self.assertTrue({"admin_notice_batches", "admin_notices"}.issubset(inspect(engine).get_table_names()))
            with engine.connect() as connection:
                self.assertEqual(tuple(connection.exec_driver_sql("SELECT * FROM users").one()), (1, 'existing user'))
                self.assertEqual(connection.exec_driver_sql("SELECT COUNT(*) FROM admin_notices").scalar_one(), 0)
        finally:
            engine.dispose()
