"""Manual champion input, permissions, immutable history and schema migration."""
import asyncio
import json
from datetime import datetime
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI
from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.migrations.manual_champions import ADDED_FIELDS, NULLABLE_FIELDS, upgrade
from app.models.hall import ChampionSnapshot
from app.models.match import MatchRound, MatchStatus, RoundStatus
from app.models.user import UserRole
from app.routers.hall import router
from app.schemas.hall import ManualChampionRequest
from app.services import hall_service as hall, match_service
from app.services.auth_service import create_access_token


def request(app, method, path, payload=None, token=None):
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
        "query_string": b"", "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send))
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, json.loads(response)


class ManualChampionTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.app = FastAPI()
        self.app.include_router(router)

        def isolated_db():
            with Session(self.engine) as db:
                yield db

        self.app.dependency_overrides[get_db] = isolated_db
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.match = self.make_match(status=MatchStatus.FINISHED)
        self.db.commit()
        self.path = f"/api/hall/admin/champions/{self.match.id}"
        self.token = create_access_token(self.admin.id, "admin")

    def payload(self, **changes):
        result = {"champion_name": " 老队伍 ", "event_date": "2024-08-09T00:00:00+08:00",
                  "roster": [{"nickname": " 历史选手 ", "rank": "A+"}],
                  "note": " 往届赛事公示档案 ", "expected_version": 0}
        result.update(changes)
        return result

    def put(self, payload=None):
        return request(self.app, "PUT", self.path, payload or self.payload(), self.token)

    def make_bracket(self):
        teams = [self.make_team() for _ in range(6)]
        for number, (first, second) in enumerate(((2, 4), (3, 5), (0, 3), (1, 2), (0, 1)), 1):
            self.db.add(MatchRound(match_id=self.match.id, round_number=number,
                team1_id=teams[first].id, team2_id=teams[second].id,
                team1_score=2, team2_score=0, winner_id=teams[first].id,
                group_name="淘汰赛", status=RoundStatus.FINISHED))
        self.db.commit()

    def test_empty_admin_get_and_create_publish_null_unknown_fields_with_private_provenance(self):
        status, before = request(self.app, "GET", self.path, token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(before, {"champion": None, "note": None, "updated_at": None, "version": 0})
        status, saved = self.put()
        self.assertEqual(status, 200)
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["note"], "往届赛事公示档案")
        self.assertEqual(saved["champion"]["champion_name"], "老队伍")
        self.assertEqual(saved["champion"]["snapshot_source"], "manual")
        self.assertEqual(saved["champion"]["event_date"], "2024-08-09T00:00:00")
        for field in ("champion_team_id", "runner_up_name", "champion_score", "runner_up_score", "awarded_at"):
            self.assertIsNone(saved["champion"][field])
        self.assertIsNone(saved["champion"]["roster"][0]["user_id"])
        status, public = request(self.app, "GET", "/api/hall/champions")
        self.assertEqual(status, 200)
        self.assertEqual(public["items"][0], saved["champion"])
        self.assertNotIn("往届赛事公示档案", json.dumps(public, ensure_ascii=False))
        self.assertFalse(set(ADDED_FIELDS).intersection(public["items"][0]))
        self.db.expire_all()
        row = self.db.get(ChampionSnapshot, self.match.id)
        self.assertEqual(row.manual_created_by, self.admin.id)
        self.assertEqual(row.manual_updated_by, self.admin.id)
        self.assertIsNotNone(row.manual_created_at)

    def test_reviewer_user_guest_and_forged_role_claim_cannot_read_or_write(self):
        for method in ("GET", "PUT"):
            self.assertEqual(request(self.app, method, self.path, self.payload())[0], 401)
        for role in (UserRole.USER, UserRole.REVIEWER, UserRole.COMMENTATOR):
            user = self.make_user(role=role)
            self.db.commit()
            token = create_access_token(user.id, "admin")
            for method in ("GET", "PUT"):
                self.assertEqual(request(self.app, method, self.path, self.payload(), token)[0], 403)
        self.assertEqual(self.db.query(ChampionSnapshot).count(), 0)

    def test_only_existing_finished_events_can_be_archived(self):
        for state in (MatchStatus.DRAFT, MatchStatus.REGISTERING, MatchStatus.IN_PROGRESS):
            self.match.status = state
            self.db.commit()
            self.assertEqual(self.put()[0], 400)
        self.assertEqual(request(self.app, "PUT", "/api/hall/admin/champions/999999", self.payload(), self.token)[0], 404)
        self.assertEqual(request(self.app, "GET", "/api/hall/admin/champions/999999", token=self.token)[0], 404)

    def test_input_validation_rejects_partial_or_false_history_and_unsafe_urls(self):
        changes = [
            {"champion_name": "  "}, {"note": "  "}, {"note": "x" * 501},
            {"event_date": "not-a-date"}, {"event_date": "0999-01-01T00:00:00"},
            {"champion_team_id": 0}, {"champion_team_id": True},
            {"champion_score": 2}, {"champion_score": 1, "runner_up_score": 1},
            {"champion_score": 0, "runner_up_score": 2}, {"champion_score": -1, "runner_up_score": 0},
            {"champion_score": 2.5, "runner_up_score": 0},
            {"champion_logo": "javascript:alert(1)"}, {"champion_logo": "data:image/png;base64,QQ=="},
            {"champion_logo": "//evil.example/p.png"}, {"champion_logo": "/\\evil.example/p.png"},
            {"roster": [{"nickname": ""}]}, {"roster": [{"nickname": "x", "rank": "S51"}]},
            {"roster": [{"nickname": "x", "user_id": -1}]},
            {"roster": [{"nickname": "x", "avatar": "file:///private"}]},
            {"roster": [{"nickname": "x"}] * 21},
            {"roster": [{"nickname": "x", "user_id": self.admin.id}] * 2},
            {"manual_created_by": self.admin.id},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(self.put(self.payload(**change))[0], 422)
        self.assertEqual(self.db.query(ChampionSnapshot).count(), 0)

    def test_selected_ids_must_exist_and_supplied_historical_roster_is_not_current_roster(self):
        team = self.make_team()
        former_member = self.make_user()
        self.db.commit()
        self.assertEqual(self.put(self.payload(champion_team_id=999999))[0], 400)
        self.assertEqual(self.put(self.payload(roster=[{"nickname": "x", "user_id": 999999}]))[0], 400)
        status, saved = self.put(self.payload(champion_team_id=team.id, champion_logo="/uploads/historical.png",
            roster=[{"nickname": "旧昵称", "user_id": former_member.id, "rank": "S50"}],
            champion_score=2, runner_up_score=0, runner_up_name="历史亚军"))
        self.assertEqual(status, 200)
        self.assertEqual(saved["champion"]["roster"], [{"nickname": "旧昵称", "user_id": former_member.id, "rank": "S50", "avatar": None}])
        team.name = "现在的队名"
        former_member.nickname = "现在的昵称"
        self.db.commit()
        self.assertEqual(request(self.app, "GET", "/api/hall/champions")[1]["items"][0], saved["champion"])

    def test_edits_increment_version_preserve_creator_and_stale_retry_does_not_duplicate(self):
        self.assertEqual(self.put()[0], 200)
        self.assertEqual(self.put()[0], 409)
        other_admin = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        self.token = create_access_token(other_admin.id, "admin")
        status, saved = self.put(self.payload(champion_name="修正后的队名", expected_version=1, roster=[]))
        self.assertEqual(status, 200)
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["champion"]["roster"], [])
        self.db.expire_all()
        row = self.db.get(ChampionSnapshot, self.match.id)
        self.assertEqual(row.manual_created_by, self.admin.id)
        self.assertEqual(row.manual_updated_by, other_admin.id)
        self.assertEqual(self.db.query(ChampionSnapshot).count(), 1)

    def test_valid_bracket_or_existing_auto_snapshot_cannot_be_overwritten(self):
        self.make_bracket()
        self.assertEqual(self.put()[0], 409)
        self.assertEqual(hall.backfill_champions(self.db), {"created": 1, "skipped": 0})
        self.assertEqual(self.put()[0], 409)
        self.assertEqual(hall.list_champions(self.db)["items"][0]["snapshot_source"], "backfill")

    def test_manual_archive_survives_score_sync_and_event_deletion(self):
        self.assertEqual(self.put()[0], 200)
        before = hall.list_champions(self.db)["items"][0]
        self.assertTrue(hall.sync_champion_snapshot(self.db, self.match.id))
        self.make_bracket()
        self.assertTrue(hall.sync_champion_snapshot(self.db, self.match.id))
        self.db.commit()
        self.assertEqual(hall.list_champions(self.db)["items"][0], before)
        match_service.delete_match(self.db, self.match.id)
        public = hall.list_champions(self.db)["items"][0]
        self.assertFalse(public["match_available"])
        public["match_available"] = True
        self.assertEqual(public, before)

    def test_failed_commit_rolls_back_both_archive_and_metadata(self):
        self.assertEqual(self.put()[0], 200)
        before = hall.get_admin_champion(self.db, self.match.id)
        with patch.object(self.db, "commit", side_effect=RuntimeError("simulated commit failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                hall.save_manual_champion(self.db, self.match.id,
                    ManualChampionRequest(**self.payload(champion_name="不能写入", expected_version=1)), self.admin.id)
        self.db.expire_all()
        self.assertEqual(hall.get_admin_champion(self.db, self.match.id), before)


class ManualChampionMigrationTests(DatabaseTestCase):
    def test_fresh_schema_and_repeated_upgrade_are_noops(self):
        engine = create_engine("sqlite://")
        try:
            self.assertEqual(upgrade(engine), {"created": True, "upgraded": False})
            self.assertEqual(upgrade(engine), {"created": False, "upgraded": False})
        finally:
            engine.dispose()

    def test_legacy_schema_upgrades_idempotently_preserving_frozen_data_and_indexes(self):
        engine = create_engine("sqlite://")
        legacy = ChampionSnapshot.__table__.to_metadata(MetaData())
        for name in ADDED_FIELDS:
            legacy._columns.remove(legacy.c[name])
        for name in NULLABLE_FIELDS:
            legacy.c[name].nullable = False
        legacy.create(engine)
        with engine.begin() as connection:
            connection.execute(legacy.insert().values(
                match_id=9, final_round_id=88, match_name="旧赛事", champion_team_id=7,
                champion_name="旧冠军", runner_up_name="旧亚军", champion_score=2, runner_up_score=1,
                roster_json='[{"user_id": 5, "nickname": "旧成员"}]', finalists_json="{}",
                snapshot_source="backfill", is_valid=True, updated_at=datetime(2026, 1, 1),
            ))
            connection.exec_driver_sql("CREATE INDEX custom_champion_name ON champion_snapshots(champion_name)")
        try:
            self.assertEqual(upgrade(engine), {"created": False, "upgraded": True})
            self.assertEqual(upgrade(engine), {"created": False, "upgraded": False})
            columns = {column["name"]: column for column in inspect(engine).get_columns("champion_snapshots")}
            self.assertTrue(all(columns[name]["nullable"] for name in NULLABLE_FIELDS))
            self.assertTrue(all(name in columns for name in ADDED_FIELDS))
            self.assertIn("custom_champion_name", {index["name"] for index in inspect(engine).get_indexes("champion_snapshots")})
            with Session(engine) as db:
                row = db.get(ChampionSnapshot, 9)
                self.assertEqual(row.champion_name, "旧冠军")
                self.assertEqual(row.final_round_id, 88)
                self.assertEqual(json.loads(row.roster_json)[0]["nickname"], "旧成员")
                self.assertEqual(row.manual_version, 0)
                db.add(ChampionSnapshot(match_id=10, match_name="补录赛事", champion_name="历史冠军",
                    roster_json="[]", finalists_json="{}", snapshot_source="manual", is_valid=True))
                db.commit()
                self.assertIsNone(db.get(ChampionSnapshot, 10).champion_team_id)
        finally:
            engine.dispose()
