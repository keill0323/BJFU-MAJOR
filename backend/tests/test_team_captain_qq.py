"""停止收集队长 QQ：旧客户端兼容、响应隐私、建队事务和历史迁移。"""
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from tests.support import DatabaseTestCase
from tests.test_manual_champions import request
from app.database import get_db
from app.migrations.team_captain_qq import upgrade
from app.models.team import Team, TeamMember, TeamApplication, TeamInvitation, MemberRole
from app.models.user import UserRole
from app.routers import team as team_routes
from app.schemas.team import TeamCreateRequest, TeamInfo
from app.services import team_service as teams
from app.services.auth_service import create_access_token


class TeamCaptainQQTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.app = FastAPI()
        self.app.include_router(team_routes.router)
        def isolated_db():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def token(self, user):
        return create_access_token(user.id, user.role.value)

    def test_schema_no_longer_collects_contact_even_from_legacy_payloads(self):
        for extra in ({}, {"captain_qq": "123456789"}, {"captain_qq": None}, {"captain_qq": {"private": "ignored"}}):
            body = TeamCreateRequest.model_validate({"name": "不收集联系信息", **extra})
            self.assertEqual(body.model_dump(), {"name": "不收集联系信息", "description": None})
        for name in ("", "x" * 21):
            with self.assertRaises(ValidationError):
                TeamCreateRequest(name=name)

    def test_creation_without_contact_preserves_captain_membership_and_closes_old_requests(self):
        captain = self.make_user()
        target = self.make_team()
        application = TeamApplication(team_id=target.id, user_id=captain.id)
        invitation = TeamInvitation(team_id=target.id, user_id=captain.id)
        self.db.add_all([application, invitation])
        self.db.commit()
        created = teams.create_team(self.db, "无联系字段建队", captain.id, "队伍简介")
        self.assertIsNone(created.captain_qq)
        self.assertEqual(created.description, "队伍简介")
        member = self.db.query(TeamMember).filter_by(user_id=captain.id).one()
        self.assertEqual((member.team_id, member.role), (created.id, MemberRole.CAPTAIN))
        self.db.refresh(application)
        self.db.refresh(invitation)
        self.assertIsNotNone(application.invalidated_at)
        self.assertIsNotNone(invitation.invalidated_at)
        with self.assertRaises(HTTPException):
            teams.create_team(self.db, "不能重复建队", captain.id)

    def test_creation_failure_rolls_back_team_and_member(self):
        captain = self.make_user()
        self.db.commit()
        with patch.object(self.db, "commit", side_effect=RuntimeError("模拟提交失败")):
            with self.assertRaises(RuntimeError):
                teams.create_team(self.db, "回滚测试", captain.id)
        self.assertEqual(self.db.query(Team).count(), 0)
        self.assertEqual(self.db.query(TeamMember).count(), 0)

    def test_new_and_legacy_create_api_do_not_save_or_return_contact(self):
        for extra in ({}, {"captain_qq": "123456789"}, {"captain_qq": ""}):
            captain = self.make_user()
            self.db.commit()
            status, body = request(self.app, "POST", "/api/teams", {"name": f"无联系字段{captain.id}", **extra}, self.token(captain))
            self.assertEqual(status, 201, body)
            self.assertNotIn("captain_qq", body)
            self.assertIsNone(self.db.get(Team, body["id"]).captain_qq)
        self.assertEqual(request(self.app, "POST", "/api/teams", {"name": "匿名建队"})[0], 401)

    def test_retired_contact_endpoint_cannot_write_and_keeps_authorization(self):
        team = self.make_team(captain_qq="123456789")
        outsider = self.make_user()
        self.db.commit()
        url = f"/api/teams/{team.id}/contact"
        for user, expected in ((None, 401), (outsider, 403), (team.captain, 410)):
            status, body = request(self.app, "PUT", url, {"captain_qq": "987654321"}, self.token(user) if user else None)
            self.assertEqual(status, expected, body)
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, "123456789")

    def test_all_detail_roles_and_my_team_omit_historical_contact(self):
        team = self.make_team(captain_qq="123456789")
        viewers = [None, team.captain, self.make_user(), self.make_user(role=UserRole.ADMIN), self.make_user(role=UserRole.REVIEWER)]
        self.db.commit()
        for user in viewers:
            status, body = request(self.app, "GET", f"/api/teams/{team.id}", token=self.token(user) if user else None)
            self.assertEqual(status, 200, body)
            self.assertNotIn("captain_qq", body)
            self.assertNotIn("123456789", str(body))
        status, body = request(self.app, "GET", "/api/teams/my", token=self.token(team.captain))
        self.assertEqual(status, 200, body)
        self.assertNotIn("captain_qq", body)
        self.assertNotIn("captain_qq", TeamInfo.model_validate(team).model_dump())
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, "123456789", "本次整改不自动删除历史数据")

    def test_lists_hidden_access_and_empty_auth_keep_existing_behavior(self):
        team = self.make_team(captain_qq="123456789")
        hidden = self.make_team(captain_qq="987654321", is_hidden=True)
        admin = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        for url, token in (("/api/teams", None), ("/api/teams/admin", self.token(admin))):
            status, rows = request(self.app, "GET", url, token=token)
            self.assertEqual(status, 200)
            self.assertNotIn("captain_qq", str(rows))
        self.assertEqual(request(self.app, "GET", f"/api/teams/{hidden.id}")[0], 404)
        for auth in ("", "  ", "Bearer invalid"):
            async def app_with_header(scope, receive, send):
                await self.app({**scope, "headers": [*scope["headers"], (b"authorization", auth.encode())]}, receive, send)
            status, body = request(app_with_header, "GET", f"/api/teams/{team.id}")
            self.assertEqual(status, 401 if auth.strip() else 200)
            self.assertNotIn("captain_qq", str(body))

    def test_migration_only_adds_nullable_field_and_preserves_legacy_data(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT, captain_id INTEGER)")
                connection.exec_driver_sql("INSERT INTO teams VALUES (5, '已有队伍', 42)")
                connection.exec_driver_sql("CREATE TABLE team_members (id INTEGER PRIMARY KEY, team_id INTEGER, user_id INTEGER)")
                connection.exec_driver_sql("INSERT INTO team_members VALUES (7, 5, 42)")
            self.assertEqual(upgrade(engine), {"captain_qq_added": True})
            self.assertEqual(upgrade(engine), {"captain_qq_added": False})
            columns = {column["name"]: column for column in inspect(engine).get_columns("teams")}
            self.assertTrue(columns["captain_qq"]["nullable"])
            self.assertEqual(columns["captain_qq"]["type"].length, 12)
            with engine.begin() as connection:
                self.assertEqual(tuple(connection.exec_driver_sql("SELECT * FROM teams").one()), (5, "已有队伍", 42, None))
                self.assertEqual(tuple(connection.exec_driver_sql("SELECT * FROM team_members").one()), (7, 5, 42))
                connection.exec_driver_sql("UPDATE teams SET captain_qq='123456789' WHERE id=5")
            self.assertEqual(upgrade(engine), {"captain_qq_added": False})
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("SELECT captain_qq FROM teams").scalar_one(), "123456789")
        finally:
            engine.dispose()

    def test_migration_is_noop_for_current_schema(self):
        team = self.make_team(captain_qq="123456789")
        self.db.commit()
        self.assertEqual(upgrade(self.engine), {"captain_qq_added": False})
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, "123456789")
