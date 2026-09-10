"""Public and admin detail must expose the same current roster lock state."""
import asyncio
import json
from datetime import datetime
from unittest.mock import patch

from tests.support import DatabaseTestCase
from tests.test_registration_routes import post
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import get_db
from app.models.match import MatchRound, StageStatus, StageWindow, TeamProgress
from app.models.user import UserRole
from app.routers.match import router
from app.services import match_service as service
from app.services.auth_service import get_current_user


def get(app, path):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class RosterDetailTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.match = self.make_match()
        self.team = self.make_team()
        self.db.commit()
        service.register_team(self.db, self.match.id, self.team.id)
        service.approve_team_registration(self.db, self.match.id, self.team.id)

        def isolated_db():
            with Session(self.engine) as db:
                yield db

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[get_current_user] = lambda: self.admin

    def details(self):
        values = []
        for suffix in ("detail", "admin-detail"):
            status, body = get(self.app, f"/api/matches/{self.match.id}/{suffix}")
            self.assertEqual(status, 200, body)
            values.append(body)
        return values

    def test_approved_unseeded_team_remains_unlocked_and_can_be_shown_awaiting_group(self):
        for body in self.details():
            self.assertIs(body["match"]["roster_locked"], False)
            team = body["teams"][0]
            self.assertEqual(team["registration_status"], "approved")
            self.assertEqual(team["seed"], 0)
            self.assertIsNone(team["group_name"])
            self.assertEqual(team["stage"], "challenger")

    def test_assigning_seeds_locks_both_details_before_rounds_exist(self):
        service.auto_assign_seeds(self.db, self.match.id)
        for body in self.details():
            self.assertIs(body["match"]["roster_locked"], True)
            self.assertEqual(body["rounds"], [])
            self.assertEqual(body["teams"][0]["stage"], "legend")

    def test_legacy_round_without_seed_or_group_still_locks_the_match(self):
        opponent = self.make_team()
        self.db.add(MatchRound(match_id=self.match.id, round_number=1,
                               team1_id=self.team.id, team2_id=opponent.id))
        self.db.commit()
        for body in self.details():
            self.assertIs(body["match"]["roster_locked"], True)
            self.assertEqual(body["teams"][0]["seed"], 0)

    def test_a_saved_stage_window_alone_does_not_lock_registration(self):
        self.db.add(StageWindow(match_id=self.match.id, group_name="A",
                                window_start=datetime(2026, 10, 1), window_end=datetime(2026, 10, 2)))
        self.db.commit()
        for body in self.details():
            self.assertIs(body["match"]["roster_locked"], False)

    def test_legacy_group_or_later_stage_with_zero_seed_is_locked(self):
        progress = self.db.query(TeamProgress).filter_by(match_id=self.match.id).one()
        for stage, group in ((StageStatus.CHALLENGER, "A"), (StageStatus.ELIMINATED, None)):
            with self.subTest(stage=stage, group=group):
                progress.stage, progress.group_name = stage, group
                self.db.commit()
                for body in self.details():
                    self.assertIs(body["match"]["roster_locked"], True)

    def test_missing_detail_is_still_a_domain_404(self):
        status, body = get(self.app, "/api/matches/99999/detail")
        self.assertEqual(status, 404)
        self.assertEqual(body["detail"], "赛事不存在")

    def test_atomic_group_route_failure_does_not_lock_an_incomplete_roster(self):
        status, body = post(self.app, f"/api/matches/{self.match.id}/seed-and-group",
                            b"group_names=A&group_names=B&group_names=C")
        self.assertEqual(status, 400, body)
        for detail in self.details():
            self.assertIs(detail["match"]["roster_locked"], False)
            self.assertEqual(detail["teams"][0]["seed"], 0)

    def test_atomic_group_route_assigns_four_legends_and_groups_the_rest(self):
        for _ in range(9):
            team = self.make_team()
            self.db.commit()
            service.register_team(self.db, self.match.id, team.id)
            service.approve_team_registration(self.db, self.match.id, team.id)
        status, body = post(self.app, f"/api/matches/{self.match.id}/seed-and-group",
                            b"group_names=A&group_names=B&group_names=C")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"], {"group_count": 3, "has_playoff": True, "team_count": 10})
        for detail in self.details():
            self.assertIs(detail["match"]["roster_locked"], True)
            self.assertEqual(sum(t["stage"] == "legend" for t in detail["teams"]), 4)
            self.assertEqual({t["group_name"] for t in detail["teams"] if t["stage"] == "challenger"}, {"A", "B", "C"})

    def test_atomic_group_route_rejects_non_admin(self):
        user = self.make_user(role=UserRole.USER)
        self.db.commit()
        self.app.dependency_overrides[get_current_user] = lambda: user
        status, _ = post(self.app, f"/api/matches/{self.match.id}/seed-and-group",
                         b"group_names=A&group_names=B&group_names=C")
        self.assertEqual(status, 403)
