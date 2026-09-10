"""报名时间设置与执行校验；所有数据均位于独立内存 SQLite。"""
from tests.support import DatabaseTestCase

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.match import Match, MatchStatus, Registration
from app.models.user import UserRole
from app.routers.match import router
from app.services import match_service as service
from app.services.auth_service import get_current_user


def request(app, path, body, method="PUT"):
    """沿用测试套件的原生 ASGI 请求方式，不增加 HTTP 客户端依赖。"""
    messages = []

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(payload)


class RegistrationTimeTests(DatabaseTestCase):
    def test_creation_rejects_equal_or_reversed_dates_without_creating_match(self):
        start = datetime(2026, 9, 7, 8)
        for end in (start, start - timedelta(minutes=1)):
            with self.subTest(end=end), self.assertRaises(HTTPException) as error:
                service.create_match(self.db, "Invalid", register_start=start, register_end=end)
            self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.db.query(Match).count(), 0)

    def test_creation_converts_aware_dates_to_beijing_before_storage(self):
        match = service.create_match(
            self.db, "Timezone", register_start=datetime(2026, 9, 7, tzinfo=timezone.utc),
            register_end=datetime(2026, 9, 7, 10),
        )
        self.db.expire_all()
        self.assertEqual(match.register_start, datetime(2026, 9, 7, 8))
        self.assertEqual(match.register_end, datetime(2026, 9, 7, 10))
        self.assertIsNone(match.register_start.tzinfo)

    def test_creation_validates_actual_instants_across_timezones(self):
        with self.assertRaises(HTTPException) as error:
            service.create_match(
                self.db, "Invalid timezone", register_start=datetime(2026, 9, 7, 4, tzinfo=timezone.utc),
                register_end=datetime(2026, 9, 7, 10),
            )
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.db.query(Match).count(), 0)

    def test_update_saves_dates_without_changing_any_match_status(self):
        for status in MatchStatus:
            with self.subTest(status=status):
                match = self.make_match(status=status, description="Keep", max_teams=24)
                self.db.commit()
                saved = service.update_registration_window(
                    self.db, match.id, datetime(2026, 9, 7, tzinfo=timezone.utc),
                    datetime(2026, 9, 8, tzinfo=timezone(timedelta(hours=-5))),
                )
                self.assertEqual(saved.register_start, datetime(2026, 9, 7, 8))
                self.assertEqual(saved.register_end, datetime(2026, 9, 8, 13))
                self.assertEqual(saved.status, status)
                self.assertEqual((saved.description, saved.max_teams), ("Keep", 24))

    def test_update_supports_one_sided_and_cleared_limits(self):
        match = self.make_match()
        self.db.commit()
        date = datetime(2026, 9, 7, 8)
        for start, end in ((date, None), (None, date), (None, None)):
            saved = service.update_registration_window(self.db, match.id, start, end)
            self.assertEqual((saved.register_start, saved.register_end), (start, end))

    def test_invalid_update_keeps_saved_dates(self):
        start, end = datetime(2026, 9, 7, 8), datetime(2026, 9, 8, 8)
        match = self.make_match(register_start=start, register_end=end)
        self.db.commit()
        for bad_end in (start, start - timedelta(hours=1)):
            with self.assertRaises(HTTPException) as error:
                service.update_registration_window(self.db, match.id, start, bad_end)
            self.assertEqual(error.exception.status_code, 400)
            self.db.rollback()
            self.assertEqual((match.register_start, match.register_end), (start, end))

    def test_missing_match_returns_404(self):
        with self.assertRaises(HTTPException) as error:
            service.update_registration_window(self.db, 99999, None, None)
        self.assertEqual(error.exception.status_code, 404)

    def test_personal_and_team_registration_apply_saved_beijing_boundaries(self):
        start, end = datetime(2026, 9, 7, 8), datetime(2026, 9, 7, 10)
        for now, message in ((start - timedelta(seconds=1), "报名尚未开始"),
                             (start, None), (end, None),
                             (end + timedelta(seconds=1), "报名已截止")):
            with self.subTest(now=now):
                match = self.make_match()
                team = self.make_team()
                user = self.make_user()
                self.db.commit()
                service.update_registration_window(
                    self.db, match.id, datetime(2026, 9, 7, tzinfo=timezone.utc), end)
                actions = (
                    lambda: service.register_team(self.db, match.id, team.id),
                    lambda: service.register_user(self.db, match.id, user.id),
                )
                with patch.object(service, "_registration_now", return_value=now):
                    for action in actions:
                        if message:
                            with self.assertRaises(HTTPException) as error:
                                action()
                            self.assertEqual(error.exception.detail, message)
                            self.db.rollback()
                        else:
                            action()
                expected = 0 if message else 2
                self.assertEqual(self.db.query(Registration).filter_by(match_id=match.id).count(), expected)

    def test_clearing_time_limits_does_not_open_a_draft_match(self):
        match = self.make_match(status=MatchStatus.DRAFT)
        team, user = self.make_team(), self.make_user()
        self.db.commit()
        service.update_registration_window(self.db, match.id, None, None)
        for action in (lambda: service.register_team(self.db, match.id, team.id),
                       lambda: service.register_user(self.db, match.id, user.id)):
            with self.assertRaises(HTTPException) as error:
                action()
            self.assertIn("不在报名阶段", error.exception.detail)
            self.db.rollback()
        self.assertEqual(self.db.query(Registration).count(), 0)


class RegistrationTimeRouteTests(DatabaseTestCase):
    def setUp(self):
        # FastAPI 工作线程只能访问这一测试专用连接；不导入会建真实表的 app.main。
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.user = self.make_user(role=UserRole.USER)
        self.match = self.make_match(status=MatchStatus.DRAFT)
        self.db.commit()
        self.current_user = self.admin

        def isolated_db():
            with Session(self.engine) as db:
                yield db

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[get_current_user] = lambda: self.current_user
        self.url = f"/api/matches/{self.match.id}/registration-window"

    def test_admin_can_save_and_clear_times_with_match_info_response(self):
        status, body = request(self.app, self.url, {
            "register_start": "2026-09-07T00:00:00Z", "register_end": "2026-09-08T10:00:00+08:00"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["register_start"], "2026-09-07T08:00:00")
        self.assertEqual(body["register_end"], "2026-09-08T10:00:00")
        self.assertEqual(body["status"], "draft")
        status, body = request(self.app, self.url, {"register_start": None, "register_end": None})
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["register_start"])
        self.assertIsNone(body["register_end"])

    def test_normal_user_cannot_change_registration_window(self):
        self.current_user = self.user
        status, body = request(self.app, self.url, {"register_start": None, "register_end": None})
        self.assertEqual(status, 403, body)

    def test_anonymous_request_is_unauthorized(self):
        del self.app.dependency_overrides[get_current_user]
        status, body = request(self.app, self.url, {"register_start": None, "register_end": None})
        self.assertEqual(status, 401, body)

    def test_invalid_or_incomplete_requests_do_not_change_dates(self):
        for body, status in (({}, 422), ({"register_start": None}, 422),
                             ({"register_start": "not a date", "register_end": None}, 422),
                             ({"register_start": "2026-09-07T12:00:00", "register_end": "2026-09-07T10:00:00"}, 400)):
            with self.subTest(body=body):
                response_status, response_body = request(self.app, self.url, body)
                self.assertEqual(response_status, status, response_body)
                self.db.expire_all()
                self.assertIsNone(self.match.register_start)
                self.assertIsNone(self.match.register_end)

    def test_nonexistent_match_returns_domain_404(self):
        status, body = request(self.app, "/api/matches/99999/registration-window",
                               {"register_start": None, "register_end": None})
        self.assertEqual(status, 404, body)
        self.assertEqual(body["detail"], "赛事不存在")

    def test_create_route_rejects_reversed_time_range(self):
        status, body = request(self.app, "/api/matches", {"name": "Invalid",
            "register_start": "2026-09-08T10:00:00", "register_end": "2026-09-07T10:00:00"}, "POST")
        self.assertEqual(status, 400, body)
        self.assertEqual(self.db.query(Match).count(), 1)
