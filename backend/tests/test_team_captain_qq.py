"""建队联系 QQ 的校验、权限、隐私与兼容迁移。"""
from unittest.mock import patch

from tests.support import DatabaseTestCase
from tests.test_manual_champions import request
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.migrations.team_captain_qq import upgrade
from app.models.team import Team, TeamApplication, TeamInvitation, TeamMember, MemberRole
from app.models.user import UserRole
from app.routers import team as team_routes
from app.schemas.team import TeamContactRequest, TeamCreateRequest
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

    def test_schemas_require_qq_and_reject_non_ascii_or_invalid_formats(self):
        invalid = [None, "", "  ", "1234", "1234567890123", "012345", "１２３４５",
                   "١٢٣٤٥", "12 345", "12a45", "12345.0", 123456, True, ["12345"]]
        for schema in (TeamCreateRequest, TeamContactRequest):
            base = {"name": "联系测试队"} if schema is TeamCreateRequest else {}
            with self.subTest(schema=schema.__name__, missing=True):
                with self.assertRaises(ValidationError) as error:
                    schema.model_validate(base)
                self.assertIn("请填写队长 QQ 号", str(error.exception))
            for qq in invalid:
                with self.subTest(schema=schema.__name__, qq=qq):
                    with self.assertRaises(ValidationError) as error:
                        schema.model_validate({**base, "captain_qq": qq})
                    self.assertIn("QQ", str(error.exception))

    def test_schemas_trim_and_preserve_valid_qq_as_string(self):
        for qq in ("12345", "123456789012", " 987654321 "):
            with self.subTest(qq=qq):
                self.assertEqual(TeamCreateRequest(name="联系测试队", captain_qq=qq).captain_qq, qq.strip())
                self.assertEqual(TeamContactRequest(captain_qq=qq).captain_qq, qq.strip())

    def test_service_rejects_missing_or_invalid_qq_without_creating_or_invalidating(self):
        captain = self.make_user()
        target = self.make_team()
        application = TeamApplication(team_id=target.id, user_id=captain.id)
        invitation = TeamInvitation(team_id=target.id, user_id=captain.id)
        self.db.add_all([application, invitation])
        self.db.commit()
        original_count = self.db.query(Team).count()
        for kwargs in ({}, {"captain_qq": None}, {"captain_qq": "abc"}, {"captain_qq": 12345}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as error:
                    teams.create_team(self.db, "不应创建的队伍", captain.id, **kwargs)
                self.assertEqual(error.exception.status_code, 422)
                self.assertIn("QQ", error.exception.detail)
                self.assertEqual(self.db.query(Team).count(), original_count)
                self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=captain.id).first())
                self.db.refresh(application)
                self.db.refresh(invitation)
                self.assertIsNone(application.invalidated_at)
                self.assertIsNone(invitation.invalidated_at)

    def test_service_creates_captain_and_saves_trimmed_qq_atomically(self):
        captain = self.make_user()
        self.db.commit()
        team = teams.create_team(self.db, "新建联系队伍", captain.id, "欢迎联系", captain_qq=" 123456789 ")
        self.assertEqual(team.captain_qq, "123456789")
        self.assertEqual(team.description, "欢迎联系")
        member = self.db.query(TeamMember).filter_by(user_id=captain.id).one()
        self.assertEqual((member.team_id, member.role), (team.id, MemberRole.CAPTAIN))
        self.assertEqual(team.rating, captain.individual_rating)

    def test_service_creation_failure_rolls_back_contact_team_and_member(self):
        captain = self.make_user()
        self.db.commit()
        with patch.object(self.db, "commit", side_effect=RuntimeError("模拟提交失败")):
            with self.assertRaises(RuntimeError):
                teams.create_team(self.db, "回滚联系队伍", captain.id, captain_qq="123456789")
        self.assertEqual(self.db.query(Team).count(), 0)
        self.assertEqual(self.db.query(TeamMember).count(), 0)

    def test_only_current_captain_can_fill_and_change_legacy_contact(self):
        member = self.make_user()
        team = self.make_team(members=[member])
        outsider = self.make_user()
        admin = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        self.assertIsNone(team.captain_qq)
        for user in (member, outsider, admin):
            with self.subTest(user=user.id):
                with self.assertRaises(HTTPException) as error:
                    teams.update_team_contact(self.db, team.id, user.id, "123456789")
                self.assertEqual(error.exception.status_code, 403)
                self.db.refresh(team)
                self.assertIsNone(team.captain_qq)
        updated = teams.update_team_contact(self.db, team.id, team.captain_id, " 123456789 ")
        self.assertEqual(updated.captain_qq, "123456789")
        updated = teams.update_team_contact(self.db, team.id, team.captain_id, "987654321")
        self.assertEqual(updated.captain_qq, "987654321")
        self.assertEqual(self.db.query(TeamMember).filter_by(team_id=team.id).count(), 2)

    def test_update_validation_and_commit_failure_preserve_existing_contact(self):
        team = self.make_team(captain_qq="123456789")
        self.db.commit()
        for qq in (None, "", "012345", 987654321):
            with self.subTest(qq=qq):
                with self.assertRaises(HTTPException) as error:
                    teams.update_team_contact(self.db, team.id, team.captain_id, qq)
                self.assertEqual(error.exception.status_code, 422)
                self.db.refresh(team)
                self.assertEqual(team.captain_qq, "123456789")
        with patch.object(self.db, "commit", side_effect=RuntimeError("模拟提交失败")):
            with self.assertRaises(RuntimeError):
                teams.update_team_contact(self.db, team.id, team.captain_id, "987654321")
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, "123456789")
        with self.assertRaises(HTTPException) as error:
            teams.update_team_contact(self.db, 99999, team.captain_id, "987654321")
        self.assertEqual(error.exception.status_code, 404)

    def test_api_creation_requires_contact_and_returns_saved_value(self):
        captain = self.make_user()
        self.db.commit()
        token = self.token(captain)
        for payload in ({"name": "接口创建队伍"}, {"name": "接口创建队伍", "captain_qq": "１２３４５"}):
            status, body = request(self.app, "POST", "/api/teams", payload, token)
            self.assertEqual(status, 422, body)
            self.assertIn("QQ", str(body))
            self.assertEqual(self.db.query(Team).count(), 0)
        status, body = request(self.app, "POST", "/api/teams",
                               {"name": "接口创建队伍", "captain_qq": " 123456789 "}, token)
        self.assertEqual(status, 201, body)
        self.assertEqual(body["captain_qq"], "123456789")
        self.assertEqual(body["captain_id"], captain.id)

    def test_contact_api_requires_auth_and_captain_and_cannot_clear(self):
        team = self.make_team()
        outsider = self.make_user()
        self.db.commit()
        path = f"/api/teams/{team.id}/contact"
        payload = {"captain_qq": "123456789"}
        self.assertEqual(request(self.app, "PUT", path, payload)[0], 401)
        self.assertEqual(request(self.app, "PUT", path, payload, self.token(outsider))[0], 403)
        self.assertEqual(request(self.app, "POST", "/api/teams", {"name": "匿名建队", **payload})[0], 401)
        token = self.token(team.captain)
        status, body = request(self.app, "PUT", path, payload, token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["captain_qq"], payload["captain_qq"])
        self.assertEqual(request(self.app, "PUT", path, {"captain_qq": ""}, token)[0], 422)
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, payload["captain_qq"])

    def test_team_detail_hides_contact_from_guests_and_exposes_to_logged_in_viewers(self):
        team = self.make_team(captain_qq="123456789")
        viewers = [team.captain, self.make_user(), self.make_user(role=UserRole.ADMIN),
                   self.make_user(role=UserRole.REVIEWER)]
        self.db.commit()
        path = f"/api/teams/{team.id}"
        status, body = request(self.app, "GET", path)
        self.assertEqual(status, 200)
        self.assertIsNone(body["captain_qq"])
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, "123456789")
        for user in viewers:
            with self.subTest(role=user.role, user=user.id):
                status, body = request(self.app, "GET", path, token=self.token(user))
                self.assertEqual(status, 200, body)
                self.assertEqual(body["captain_qq"], "123456789")
                for private_field in ("student_id", "verify_image", "wx_openid"):
                    self.assertNotIn(private_field, str(body))
        status, body = request(self.app, "GET", "/api/teams/my", token=self.token(team.captain))
        self.assertEqual(status, 200)
        self.assertEqual(body["captain_qq"], "123456789")

    def test_team_lists_do_not_bulk_expose_contact_and_hidden_team_stays_hidden(self):
        self.make_team(captain_qq="123456789")
        hidden = self.make_team(captain_qq="987654321", is_hidden=True)
        viewer = self.make_user()
        admin = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        for path, token in (("/api/teams", None), ("/api/teams/admin", self.token(admin))):
            status, rows = request(self.app, "GET", path, token=token)
            self.assertEqual(status, 200, rows)
            for row in rows:
                self.assertNotIn("captain_qq", row)
        self.assertEqual(request(self.app, "GET", f"/api/teams/{hidden.id}", token=self.token(viewer))[0], 404)

    def test_empty_authorization_header_is_guest_but_invalid_token_is_rejected(self):
        team = self.make_team(captain_qq="123456789")
        self.db.commit()
        for authorization in ("", "   \t", "Bearer invalid-token"):
            with self.subTest(authorization=authorization):
                async def app_with_header(scope, receive, send):
                    await self.app({**scope, "headers": [
                        *scope["headers"], (b"authorization", authorization.encode())
                    ]}, receive, send)

                status, body = request(app_with_header, "GET", f"/api/teams/{team.id}")
                if authorization.strip():
                    self.assertEqual(status, 401, body)
                    self.assertNotIn("captain_qq", body)
                else:
                    self.assertEqual(status, 200, body)
                    self.assertIsNone(body["captain_qq"])
        self.db.refresh(team)
        self.assertEqual(team.captain_qq, "123456789")

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
