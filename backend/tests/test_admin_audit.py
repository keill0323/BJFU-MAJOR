"""管理员审计：隔离数据库、合成用户、模拟识图，无外部请求。"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.support import DatabaseTestCase, settings
from app.database import get_db
from app.migrations.admin_audit import upgrade
from app.models.admin_audit import AdminAudit
from app.models.user import User, UserRole
from app.routers import admin, auth
from app.services import admin_audit_service as audit, auth_service


def request(app, method, path, payload=None, token=None, query=None):
    messages = []
    body = json.dumps(payload).encode() if payload is not None else b""

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    asyncio.run(app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(), "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send))
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    return status, json.loads(b"".join(item.get("body", b"") for item in messages
                                    if item["type"] == "http.response.body"))


class AdminAuditTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.reviewer = self.make_user(role=UserRole.REVIEWER)
        self.user = self.make_user(is_verified=False, student_id=None,
                                   verify_image="/uploads/private-verify.png", ai_review_status="pending")
        self.db.commit()
        self.app = FastAPI()
        self.app.include_router(admin.router)
        self.app.include_router(auth.router)

        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db
        self.user_path = f"/api/auth/admin/users/{self.user.id}"
        self.audit_path = "/api/admin/audit-logs"

    def token(self, actor):
        return auth_service.create_access_token(actor.id, "admin")

    def records(self):
        self.db.expire_all()
        return self.db.query(AdminAudit).order_by(AdminAudit.id).all()

    def call(self, method, path, payload=None, actor=None, query=None):
        return request(self.app, method, path, payload, self.token(actor or self.admin), query)

    def test_profile_audit_has_authenticated_actor_and_changed_fields_only(self):
        status, _ = self.call("PUT", self.user_path, {
            "student_id": "260123456", "is_verified": True, "identity": "senior",
            "rank": "s10", "individual_rating": 51, "audit_reason": "  核对凭证后更正  ",
            "actor_id": self.reviewer.id, "actor_role": "user", "before_values": {"rank": "S50"},
        })
        self.assertEqual(status, 200)
        record, = self.records()
        self.assertEqual(record.actor_id, self.admin.id)
        self.assertEqual(record.actor_role, "admin")
        self.assertEqual(record.target_user_id, self.user.id)
        self.assertEqual(record.action, "user.update")
        self.assertEqual(record.reason, "核对凭证后更正")
        self.assertEqual(record.before_values, {"student_id": None, "is_verified": False,
            "identity": None, "rank": "A", "individual_rating": 25, "ai_review_status": "pending"})
        self.assertEqual(record.after_values, {"student_id": "260123456", "is_verified": True,
            "identity": "senior", "rank": "S10", "individual_rating": 51, "ai_review_status": "manual_pass"})
        status, result = self.call("GET", self.audit_path)
        self.assertEqual(status, 200)
        self.assertTrue(result["items"][0]["created_at"].endswith("Z"))
        self.assertNotIn("wx_openid", json.dumps(result))
        self.assertNotIn("/uploads/", json.dumps(result))

    def test_reviewer_is_recorded_but_cannot_read_audit(self):
        self.assertEqual(self.call("PUT", self.user_path, {"rank": "B"}, self.reviewer)[0], 200)
        record, = self.records()
        self.assertEqual((record.actor_id, record.actor_role), (self.reviewer.id, "reviewer"))
        self.assertEqual(self.call("GET", self.audit_path, actor=self.reviewer)[0], 403)
        self.assertEqual(self.call("GET", self.audit_path, actor=self.user)[0], 403)
        self.assertEqual(request(self.app, "GET", self.audit_path)[0], 401)
        self.assertEqual(self.call("DELETE", self.audit_path)[0], 405)

    def test_unchanged_values_and_normalization_do_not_add_duplicate_records(self):
        self.assertEqual(self.call("PUT", self.user_path, {"rank": " a "})[0], 200)
        self.assertEqual(self.records(), [])
        payload = {"student_id": "260123456", "is_verified": True}
        self.assertEqual(self.call("PUT", self.user_path, payload)[0], 200)
        self.assertEqual(self.call("PUT", self.user_path, payload)[0], 200)
        self.assertEqual(len(self.records()), 1)

    def test_image_replacement_records_presence_and_change_but_no_path(self):
        self.user.ai_student_id = "260999999"
        self.db.commit()
        self.assertEqual(self.call("PUT", self.user_path,
            {"verify_image": "/uploads/replacement-private.png"})[0], 200)
        record, = self.records()
        self.assertEqual(record.before_values, {"ai_student_id": "260999999", "verify_image_changed": False})
        self.assertEqual(record.after_values, {"ai_student_id": None, "verify_image_changed": True})
        self.assertNotIn("/uploads/", json.dumps(record.before_values | record.after_values))
        self.assertEqual(self.call("PUT", self.user_path, {"verify_image": ""})[0], 200)
        self.assertFalse(self.records()[-1].after_values["verify_image_present"])

    def test_rejection_records_status_and_reason(self):
        self.assertEqual(self.call("PUT", self.user_path,
            {"is_verified": False, "verify_reject_reason": "学籍信息不清晰"})[0], 200)
        record, = self.records()
        self.assertEqual(record.after_values["ai_review_status"], "manual_reject")
        self.assertEqual(record.after_values["verify_reject_reason"], "学籍信息不清晰")
        self.assertEqual(record.reason, "管理员更新用户资料")

    def test_unique_student_id_failure_rolls_back_mutation_and_audit(self):
        self.make_user(student_id="260123456")
        self.db.commit()
        self.assertEqual(self.call("PUT", self.user_path,
            {"student_id": "260123456", "is_verified": True})[0], 400)
        self.assertEqual(self.records(), [])
        self.assertIsNone(self.user.student_id)
        self.assertFalse(self.user.is_verified)

    def test_failed_audit_insert_cannot_commit_user_change(self):
        def reject_insert(_mapper, _connection, _target):
            raise RuntimeError("simulated audit write failure")
        event.listen(AdminAudit, "before_insert", reject_insert)
        try:
            with self.assertRaises(RuntimeError):
                auth_service.admin_update_user(self.db, self.user.id, rank="S25", operator=self.admin)
            self.db.rollback()
        finally:
            event.remove(AdminAudit, "before_insert", reject_insert)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.user.rank, "A")
        self.assertEqual(self.user.individual_rating, 25)

    def test_audit_failure_rolls_back_rank_application_and_user_together(self):
        application = auth_service.create_rank_application(self.db, self.user.id, ai_rank="S25")
        def reject_insert(_mapper, _connection, _target):
            raise RuntimeError("simulated audit write failure")
        event.listen(AdminAudit, "before_insert", reject_insert)
        try:
            with self.assertRaises(RuntimeError):
                auth_service.approve_rank_application(self.db, application.id, operator=self.admin)
            self.db.rollback()
        finally:
            event.remove(AdminAudit, "before_insert", reject_insert)
        self.assertEqual(self.records(), [])
        self.assertEqual(application.status, "pending")
        self.assertEqual(self.user.rank, "A")

    def test_team_recalculation_failure_keeps_audit_of_already_committed_profile(self):
        with patch.object(auth_service.team_service, "recalc_user_team_rating", side_effect=RuntimeError("team failure")):
            with self.assertRaises(RuntimeError):
                auth_service.admin_update_user(self.db, self.user.id, rank="B", operator=self.admin)
        record, = self.records()
        self.assertEqual(self.user.rank, "B")
        self.assertEqual(record.after_values["rank"], "B")

    def test_role_changes_require_current_admin_and_self_demotion_is_audited(self):
        role_path = self.user_path + "/role"
        self.assertEqual(self.call("PUT", role_path, {"role": "reviewer"}, self.reviewer)[0], 403)
        self.assertEqual(self.call("PUT", role_path, {"role": "reviewer"})[0], 200)
        self.assertEqual(self.call("PUT", role_path, {"role": "reviewer"})[0], 200)
        record, = self.records()
        self.assertEqual(record.before_values, {"role": "user"})
        self.assertEqual(record.after_values, {"role": "reviewer"})
        self.assertEqual(self.call("PUT", f"/api/auth/admin/users/{self.admin.id}/role", {"role": "user"})[0], 200)
        record = self.records()[-1]
        self.assertEqual(record.actor_role, "admin")
        self.assertEqual(record.target_user_id, record.actor_id)
        self.assertEqual(record.before_values["role"], "admin")
        self.assertEqual(record.after_values["role"], "user")
        self.assertEqual(self.call("GET", self.audit_path)[0], 403)

    def test_stale_or_forged_actor_role_cannot_bypass_service_check(self):
        actor = User(id=self.user.id, role=UserRole.ADMIN)
        with self.assertRaises(HTTPException) as error:
            auth_service.admin_update_user(self.db, self.user.id, rank="S50", operator=actor)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException):
            auth_service.update_user_role(self.db, self.user.id, "admin", self.reviewer)
        self.assertEqual(self.records(), [])

    def test_rank_approval_audits_application_status_even_if_rank_does_not_change(self):
        application = auth_service.create_rank_application(self.db, self.user.id, ai_rank="A",
                                                          rank_image="/uploads/private-rank.png")
        path = f"/api/auth/admin/rank-applications/{application.id}/approve"
        self.assertEqual(self.call("POST", path, actor=self.reviewer)[0], 200)
        self.assertEqual(self.call("POST", path)[0], 200)
        record, = self.records()
        self.assertEqual(record.action, "rank_application.approve")
        self.assertEqual(record.application_id, application.id)
        self.assertEqual(record.before_values, {"application_status": "pending"})
        self.assertEqual(record.after_values, {"application_status": "approved"})

    def test_rank_rejection_audits_once_and_does_not_change_rank(self):
        application = auth_service.create_rank_application(self.db, self.user.id, ai_rank="S50")
        path = f"/api/auth/admin/rank-applications/{application.id}/reject"
        self.assertEqual(self.call("POST", path, query={"reject_reason": "历史段位不明", "audit_reason": "核验后驳回"})[0], 200)
        self.assertEqual(self.call("POST", path)[0], 200)
        record, = self.records()
        self.assertEqual(record.action, "rank_application.reject")
        self.assertEqual(record.application_id, application.id)
        self.assertEqual(record.reason, "核验后驳回")
        self.assertEqual(record.after_values, {"application_status": "rejected", "reject_reason": "历史段位不明"})
        self.assertEqual(self.user.rank, "A")

    def test_invalid_or_stale_requests_leave_no_success_audit(self):
        for payload, expected in (({"rank": "not-rank"}, 400),
            ({"student_id": "260123456", "expected_verify_image": "/uploads/stale.png"}, 409),
            ({"audit_reason": "x" * 501, "rank": "B"}, 422)):
            self.assertEqual(self.call("PUT", self.user_path, payload)[0], expected)
        application = auth_service.create_rank_application(self.db, self.user.id)
        self.assertEqual(self.call("POST", f"/api/auth/admin/rank-applications/{application.id}/approve")[0], 400)
        self.assertEqual(self.call("PUT", "/api/auth/admin/users/999999", {"rank": "B"})[0], 404)
        self.assertEqual(self.records(), [])

    def test_candidate_recognition_is_audited_and_duplicate_candidate_is_not(self):
        with TemporaryDirectory() as directory:
            Path(directory, "private-verify.png").write_bytes(b"synthetic only")
            with patch.object(settings, "UPLOAD_DIR", directory), patch.object(settings, "AI_REVIEW_ENABLED", True), \
                 patch.object(auth_service, "settings", settings), \
                 patch("app.services.ai_review_service.review_image", return_value={
                     "student_id": "260123456", "confidence": 0.7, "reason": "需核验", "is_valid": True}):
                path = self.user_path + "/recognize-student-id"
                payload = {"expected_verify_image": self.user.verify_image}
                self.assertEqual(self.call("POST", path, payload)[0], 200)
                self.assertEqual(self.call("POST", path, payload)[0], 200)
        record, = self.records()
        self.assertEqual(record.action, "user.student_id.recognize")
        self.assertEqual(record.before_values, {"ai_student_id": None})
        self.assertEqual(record.after_values, {"ai_student_id": "260123456"})
        self.assertIsNone(self.user.student_id)
        self.assertFalse(self.user.is_verified)

    def test_recognition_rechecks_operator_permission_after_remote_wait(self):
        def revoke(*_args):
            with Session(self.engine) as db:
                db.query(User).filter(User.id == self.reviewer.id).update({"role": UserRole.USER})
                db.commit()
            return {"student_id": "260123456", "confidence": .7, "reason": "需核验", "is_valid": True}
        with TemporaryDirectory() as directory:
            Path(directory, "private-verify.png").write_bytes(b"synthetic only")
            with patch.object(settings, "UPLOAD_DIR", directory), patch.object(settings, "AI_REVIEW_ENABLED", True), \
                 patch("app.services.ai_review_service.review_image", side_effect=revoke):
                self.assertEqual(self.call("POST", self.user_path + "/recognize-student-id",
                    {"expected_verify_image": self.user.verify_image}, self.reviewer)[0], 403)
        self.assertEqual(self.records(), [])
        self.assertIsNone(self.user.ai_student_id)

    def test_query_filters_pagination_and_utc_boundaries(self):
        for actor, rank in ((self.admin, "B"), (self.reviewer, "C"), (self.admin, "D")):
            self.assertEqual(self.call("PUT", self.user_path, {"rank": rank}, actor)[0], 200)
        first_status, first = self.call("GET", self.audit_path, query={"page_size": 2})
        second_status, second = self.call("GET", self.audit_path, query={"page_size": 2, "page": 2})
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (3, 2, 1))
        self.assertGreater(first["items"][0]["id"], second["items"][0]["id"])
        status, filtered = self.call("GET", self.audit_path, query={
            "actor_id": self.reviewer.id, "target_user_id": self.user.id, "action": "user.update"})
        self.assertEqual(status, 200)
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["actor_id"], self.reviewer.id)
        self.assertEqual(self.call("GET", self.audit_path, query={"application_id": 999})[1]["total"], 0)
        instant = self.records()[1].created_at.replace(tzinfo=timezone.utc)
        local = instant.astimezone(timezone(timedelta(hours=8))).isoformat()
        self.assertEqual(self.call("GET", self.audit_path, query={"created_from": local, "created_to": local})[1]["total"], 1)
        for query, code in (({"page_size": 101}, 422), ({"page": 0}, 422),
                            ({"action": "unknown"}, 400),
                            ({"created_from": "2026-02-01", "created_to": "2026-01-01"}, 400)):
            self.assertEqual(self.call("GET", self.audit_path, query=query)[0], code)

    def test_explicit_whitelist_discards_account_image_and_secret_fields(self):
        actor = audit.resolve_actor(self.db, self.admin)
        before = {"rank": "A", "wx_openid": "private", "verify_image": "/uploads/private.png", "token": "secret"}
        after = {"rank": "B", "wx_openid": "different", "verify_image": "/uploads/next.png", "token": "other"}
        audit.record_change(self.db, actor, self.user.id, "user.update", before, after)
        self.db.commit()
        record, = self.records()
        self.assertEqual(record.before_values, {"rank": "A"})
        self.assertEqual(record.after_values, {"rank": "B"})

    def test_migration_is_idempotent_and_audit_survives_user_deletion(self):
        migration_engine = create_engine("sqlite:///:memory:")
        try:
            upgrade(migration_engine)
            upgrade(migration_engine)
            self.assertEqual(inspect(migration_engine).get_table_names(), ["admin_audits"])
        finally:
            migration_engine.dispose()
        self.assertEqual(self.call("PUT", self.user_path, {"rank": "B"})[0], 200)
        self.db.delete(self.user)
        self.db.commit()
        record, = self.records()
        self.assertEqual(record.target_user_id, self.user.id)

    def test_legacy_internal_calls_without_actor_remain_compatible(self):
        auth_service.admin_update_user(self.db, self.user.id, rank="B")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.user.rank, "B")
