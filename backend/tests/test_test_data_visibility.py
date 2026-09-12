"""Visibility is reversible and never changes historical results or rank positions."""
import json
from unittest.mock import patch

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.support import DatabaseTestCase
from tests.test_manual_champions import request
from tests.test_hall import get
from app.database import get_db
from app.migrations.test_data_visibility import upgrade
from app.models.hall import ChampionSnapshot
from app.models.match import MatchRound, Registration, RegistrationStatus, RoundStatus
from app.models.user import UserRole
from app.routers import hall, match, team
from app.services import auth_service, hall_service, team_service


class VisibilityTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.app = FastAPI()
        for router in (hall.router, match.router, team.router):
            self.app.include_router(router)

        def isolated_db():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def test_player_ranks_and_pagination_exclude_hidden_before_ranking(self):
        bot = self.make_user(rank="S50", is_hidden=True)
        first = self.make_user(rank="S10", nickname="BOT-like real nickname")
        self.make_user(rank="A")
        self.db.commit()
        status, page = get(self.app, "/api/hall/players?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["user_id"], first.id)
        self.assertEqual(page["items"][0]["position"], 1)
        self.assertTrue(page["has_more"])
        bot.is_hidden = False
        self.db.commit()
        self.assertEqual(hall_service.list_players(self.db)["total"], 3)

    def test_public_teams_and_direct_details_hide_bots_admin_keeps_them(self):
        bot = self.make_team(is_hidden=True)
        real = self.make_team(name="BOT is just my team name")
        admin = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        status, rows = request(self.app, "GET", "/api/teams")
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in rows], [real.id])
        self.assertEqual(request(self.app, "GET", f"/api/teams/{bot.id}")[0], 404)
        token = auth_service.create_access_token(admin.id, admin.role.value)
        self.assertEqual(request(self.app, "GET", f"/api/teams/{bot.id}", token=token)[0], 200)
        self.assertEqual(len(team_service.get_all_teams(self.db)), 2)

    def test_mixed_real_roster_filters_only_hidden_members(self):
        bot = self.make_user(is_hidden=True)
        real = self.make_team(members=[bot])
        self.db.commit()
        status, detail = request(self.app, "GET", f"/api/teams/{real.id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["members"]), 1)
        self.assertEqual(detail["members"][0]["user_id"], real.captain_id)

    def test_event_keeps_results_and_admin_roster_but_masks_public_opponents(self):
        event = self.make_match()
        bot = self.make_team(is_hidden=True)
        real = self.make_team()
        admin = self.make_user(role=UserRole.ADMIN)
        for entry in (bot, real):
            self.db.add(Registration(match_id=event.id, team_id=entry.id, user_id=entry.captain_id,
                                     status=RegistrationStatus.APPROVED))
        round_ = MatchRound(match_id=event.id, round_number=1, team1_id=real.id, team2_id=bot.id,
                            team1_score=1, team2_score=2, winner_id=bot.id, status=RoundStatus.FINISHED)
        self.db.add(round_)
        self.db.commit()
        status, detail = request(self.app, "GET", f"/api/matches/{event.id}/detail")
        self.assertEqual(status, 200)
        self.assertEqual([row["team_id"] for row in detail["teams"]], [real.id])
        for rows in (detail["rounds"], request(self.app, "GET", f"/api/matches/{event.id}/rounds")[1]):
            self.assertEqual(rows[0]["team2_name"], "已隐藏测试队伍")
            self.assertEqual(rows[0]["winner_id"], bot.id)
            self.assertEqual(rows[0]["team2_score"], 2)
        token = auth_service.create_access_token(admin.id, admin.role.value)
        status, detail = request(self.app, "GET", f"/api/matches/{event.id}/admin-detail", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["teams"]), 2)
        self.assertEqual(detail["rounds"][0]["team2_name"], bot.name)
        self.db.refresh(round_)
        self.assertEqual(round_.winner_id, bot.id)

    def test_talent_market_excludes_only_hidden_accounts(self):
        event = self.make_match()
        bot = self.make_user(is_hidden=True)
        real = self.make_user()
        for user in (bot, real):
            self.db.add(Registration(match_id=event.id, user_id=user.id, status=RegistrationStatus.APPROVED))
        self.db.commit()
        self.assertEqual([user.id for user in team_service.get_users_without_team(self.db, event.id)], [real.id])

    def test_champion_pagination_and_frozen_rosters_respect_hidden_flags(self):
        bot_user = self.make_user(is_hidden=True)
        bot = self.make_team(captain=bot_user, is_hidden=True)
        real = self.make_team()
        for index, winner in enumerate((bot, real), 1):
            self.db.add(ChampionSnapshot(match_id=index, champion_team_id=winner.id,
                match_name=f"Event {index}", champion_name=winner.name, runner_up_name=bot.name,
                roster_json=json.dumps([{"user_id": bot_user.id}, {"user_id": real.captain_id}]),
                finalists_json="{}", snapshot_source="manual", is_valid=True))
        self.db.commit()
        page = hall_service.list_champions(self.db, limit=1)
        self.assertEqual(page["total"], 1)
        self.assertFalse(page["has_more"])
        self.assertEqual(page["items"][0]["champion_team_id"], real.id)
        self.assertEqual(page["items"][0]["runner_up_name"], "已隐藏测试队伍")
        self.assertEqual(page["items"][0]["roster"], [{"user_id": real.captain_id}])
        self.assertEqual(len(json.loads(self.db.get(ChampionSnapshot, 2).roster_json)), 2)

    def test_additive_migration_preserves_rows_and_reruns(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            for table in ("users", "teams"):
                connection.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, name TEXT)")
                connection.exec_driver_sql(f"INSERT INTO {table} VALUES (1, 'existing record')")
        self.assertEqual(upgrade(engine), ["users", "teams"])
        self.assertEqual(upgrade(engine), [])
        with engine.begin() as connection:
            for table in ("users", "teams"):
                self.assertEqual(connection.exec_driver_sql(f"SELECT * FROM {table}").one(),
                                 (1, "existing record", 0))
