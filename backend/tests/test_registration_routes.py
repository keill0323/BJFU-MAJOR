"""Exercise the real approval HTTP route with no network, credentials, or DB access."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

# Initialize isolated settings before importing any application module.
from tests import support  # noqa: F401
from fastapi import FastAPI, HTTPException
from app.database import get_db
from app.models.user import UserRole
from app.routers.match import router
from app.services.auth_service import get_current_user
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.models.match import Registration, RegistrationStatus, TeamProgress


def post(app, path, query=b""):
    """Send one in-process ASGI request without requiring an external HTTP client."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query, "root_path": "",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class RegistrationRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router)
        self.db = object()
        self.app.dependency_overrides[get_db] = lambda: self.db

    def login_as(self, role):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=role)

    def test_batch_route_accepts_admin_and_reviewer_and_uses_team_not_registration_id(self):
        for role in (UserRole.ADMIN, UserRole.REVIEWER):
            with self.subTest(role=role):
                self.login_as(role)
                with patch("app.services.match_service.approve_team_registration", return_value={"team_id": 12, "approved": 5}) as approve:
                    status, body = post(self.app, "/api/matches/8/registrations/approve-team", b"team_id=12")
                self.assertEqual(status, 200)
                self.assertEqual(body["data"], {"team_id": 12, "approved": 5})
                approve.assert_called_once_with(self.db, 8, 12)

    def test_batch_route_rejects_regular_user_before_approving(self):
        self.login_as(UserRole.USER)
        with patch("app.services.match_service.approve_team_registration") as approve:
            status, body = post(self.app, "/api/matches/8/registrations/approve-team", b"team_id=12")
        self.assertEqual(status, 403)
        self.assertEqual(body["detail"], "权限不足")
        approve.assert_not_called()

    def test_batch_route_rejects_guest_before_approving(self):
        with patch("app.services.match_service.approve_team_registration") as approve:
            status, body = post(self.app, "/api/matches/8/registrations/approve-team", b"team_id=12")
        self.assertEqual(status, 401)
        self.assertEqual(body["detail"], "未登录")
        approve.assert_not_called()

    def test_missing_registration_remains_a_distinct_business_404(self):
        self.login_as(UserRole.ADMIN)
        with patch("app.services.match_service.approve_team_registration", side_effect=HTTPException(404, "该队伍未报名此赛事")):
            status, body = post(self.app, "/api/matches/8/registrations/approve-team", b"team_id=12")
        self.assertEqual(status, 404)
        self.assertEqual(body["detail"], "该队伍未报名此赛事")

    def test_team_id_is_required(self):
        self.login_as(UserRole.ADMIN)
        with patch("app.services.match_service.approve_team_registration") as approve:
            status, _ = post(self.app, "/api/matches/8/registrations/approve-team")
        self.assertEqual(status, 422)
        approve.assert_not_called()

    def test_batch_route_is_present_in_openapi(self):
        operation = self.app.openapi()["paths"]["/api/matches/{match_id}/registrations/approve-team"]["post"]
        parameters = {p["name"]: p for p in operation["parameters"]}
        self.assertEqual(parameters["match_id"]["in"], "path")
        self.assertEqual(parameters["team_id"]["in"], "query")
        self.assertTrue(parameters["team_id"]["required"])


class RegistrationRosterValidationRouteTests(support.DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.app = FastAPI()
        self.app.include_router(router)
        self.current_user = None

        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db

        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[get_current_user] = lambda: self.current_user

    def roster(self, **member_values):
        captain = self.make_user()
        member = self.make_user(**member_values)
        team = self.make_team(captain=captain, members=[member], captain_qq=None)
        match = self.make_match()
        self.db.commit()
        self.current_user = captain
        return match, team, member

    def register(self, match, team):
        return post(self.app, f"/api/matches/{match.id}/register/team",
                    f"team_id={team.id}".encode())

    def assert_unregistered(self, match):
        self.assertEqual(self.db.query(Registration).filter_by(match_id=match.id).count(), 0)
        self.assertEqual(self.db.query(TeamProgress).filter_by(match_id=match.id).count(), 0)

    def test_missing_or_blank_member_names_return_400_and_user_id_without_registration(self):
        for qualification, reason in (({"is_verified": False}, "未完成学籍认证"),
                                      ({"rank": None}, "未完成段位认证")):
            for nickname, game_id in ((None, None), ("", ""), (" \t", "\n "), (" ", None)):
                with self.subTest(qualification=qualification, nickname=nickname, game_id=game_id):
                    match, team, member = self.roster(nickname=nickname, game_id=game_id,
                                                     **qualification)
                    status, body = self.register(match, team)
                    self.assertEqual(status, 400, body)
                    self.assertIn(f"用户{member.id}", body["detail"])
                    self.assertIn(reason, body["detail"])
                    self.assertNotIn(member.student_id, body["detail"])
                    self.assert_unregistered(match)

    def test_validation_names_prefer_nickname_then_game_id(self):
        for nickname, game_id, expected in (("  招募队员  ", "Game-Name", "招募队员"),
                                            (None, "  Game-Name  ", "Game-Name"),
                                            (" \t", "Game-Name", "Game-Name")):
            with self.subTest(nickname=nickname, game_id=game_id):
                match, team, member = self.roster(nickname=nickname, game_id=game_id,
                                                 is_verified=False)
                status, body = self.register(match, team)
                self.assertEqual(status, 400, body)
                self.assertEqual(body["detail"], f"以下队员未完成学籍认证，无法报名：{expected}")
                self.assert_unregistered(match)

    def test_eligible_unnamed_member_and_legacy_missing_qq_can_register_and_be_approved(self):
        match, team, member = self.roster(nickname=None, game_id=None)
        status, body = self.register(match, team)
        self.assertEqual(status, 200, body)
        records = self.db.query(Registration).filter_by(match_id=match.id).all()
        self.assertEqual({r.user_id for r in records}, {team.captain_id, member.id})
        self.assertTrue(all(r.status == RegistrationStatus.PENDING for r in records))
        self.assertEqual(self.db.query(TeamProgress).filter_by(match_id=match.id).count(), 0)

        self.current_user = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        status, body = post(self.app, f"/api/matches/{match.id}/registrations/approve-team",
                            f"team_id={team.id}".encode())
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["approved"], 2)
        self.db.expire_all()
        self.assertTrue(all(r.status == RegistrationStatus.APPROVED for r in records))
        self.assertEqual(self.db.query(TeamProgress).filter_by(match_id=match.id).count(), 1)

    def test_both_approval_routes_preserve_pending_records_for_unnamed_ineligible_member(self):
        for qualification, reason in (({"is_verified": False}, "未完成学籍认证"),
                                      ({"rank": None}, "未完成段位认证")):
            with self.subTest(qualification=qualification):
                match, team, member = self.roster(nickname=None, game_id=None)
                status, body = self.register(match, team)
                self.assertEqual(status, 200, body)
                for key, value in qualification.items():
                    setattr(member, key, value)
                self.current_user = self.make_user(role=UserRole.ADMIN)
                self.db.commit()
                records = self.db.query(Registration).filter_by(match_id=match.id).all()
                for path, query in (
                    (f"/api/matches/{match.id}/registrations/approve-team", f"team_id={team.id}".encode()),
                    (f"/api/matches/registrations/{records[0].id}/approve", b""),
                ):
                    with self.subTest(path=path):
                        status, body = post(self.app, path, query)
                        self.assertEqual(status, 400, body)
                        self.assertIn(f"用户{member.id}", body["detail"])
                        self.assertIn(reason, body["detail"])
                        self.db.expire_all()
                        self.assertTrue(all(r.status == RegistrationStatus.PENDING for r in records))
                        self.assertEqual(self.db.query(TeamProgress).filter_by(match_id=match.id).count(), 0)

    def test_unnamed_unverified_member_keeps_manual_student_id_review_hint_private(self):
        match, team, member = self.roster(nickname=None, game_id=None,
                                         is_verified=False, student_id="20260123456")
        status, body = self.register(match, team)
        self.assertEqual(status, 400, body)
        self.assertIn(f"用户{member.id}", body["detail"])
        self.assertIn("非9位数字学号需人工认证", body["detail"])
        self.assertNotIn(member.student_id, body["detail"])
        self.assert_unregistered(match)


if __name__ == "__main__":
    unittest.main()
