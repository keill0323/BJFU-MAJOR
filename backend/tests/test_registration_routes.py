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


if __name__ == "__main__":
    unittest.main()
