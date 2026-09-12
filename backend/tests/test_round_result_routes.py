"""BO3 HTTP submission, legacy champion schemas and atomic final results."""
import asyncio
import json
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.migrations.manual_champions import ADDED_FIELDS, upgrade
from app.models.hall import ChampionSnapshot
from app.models.match import MatchRound, MatchStatus, RoundStatus
from app.models.user import UserRole
from app.routers.match import router
from app.services import hall_service
from app.services.auth_service import create_access_token


def put_result(app, round_id, payload, token):
    path = f"/api/matches/rounds/{round_id}"
    body = json.dumps(payload).encode()
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "PUT", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json"),
                    (b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send))
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    response = b"".join(item.get("body", b"") for item in messages
                        if item["type"] == "http.response.body")
    return status, json.loads(response)


class RoundResultRouteTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.app = FastAPI()
        self.app.include_router(router)

        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db

        self.app.dependency_overrides[get_db] = isolated_db
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.db.commit()
        self.token = create_access_token(self.admin.id, "admin")

    @staticmethod
    def scores(*, three_games=False, reverse=False):
        games = [{"t1": 13, "t2": 5}, {"t1": 13, "t2": 7}]
        if three_games:
            games.insert(1, {"t1": 8, "t2": 13})
        if reverse:
            games = [{"t1": game["t2"], "t2": game["t1"]} for game in games]
        loser = 1 if three_games else 0
        return {"team1_score": loser if reverse else 2,
                "team2_score": 2 if reverse else loser, "bo3_scores": games}

    def make_quarter_final(self):
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        first, second = self.make_team(), self.make_team()
        round_ = MatchRound(match_id=match.id, round_number=1,
                            team1_id=first.id, team2_id=second.id,
                            team1_score=0, team2_score=0,
                            group_name="淘汰赛", status=RoundStatus.PENDING)
        self.db.add(round_)
        self.db.commit()
        return round_

    def make_pending_final(self):
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        teams = [self.make_team() for _ in range(6)]
        # Six-team knockout: two quarter-finals, two semi-finals and a final.
        pairs = [(2, 4), (3, 5), (0, 3), (1, 2), (0, 1)]
        for index, (first, second) in enumerate(pairs):
            final = index == 4
            round_ = MatchRound(match_id=match.id, round_number=index + 1,
                                team1_id=teams[first].id, team2_id=teams[second].id,
                                team1_score=0 if final else 2, team2_score=0,
                                winner_id=None if final else teams[first].id,
                                group_name="淘汰赛",
                                status=RoundStatus.PENDING if final else RoundStatus.FINISHED)
            self.db.add(round_)
        self.db.commit()
        return round_

    def submit(self, round_id, payload=None):
        return put_result(self.app, round_id, payload or self.scores(), self.token)

    def stored_round(self, round_id):
        with Session(self.engine) as db:
            row = db.get(MatchRound, round_id)
            return (row.status, row.team1_score, row.team2_score, row.winner_id, row.bo3_scores)

    def test_valid_two_game_quarter_final_returns_scores_as_array(self):
        round_ = self.make_quarter_final()
        round_id, winner_id = round_.id, round_.team1_id
        payload = self.scores()
        status, result = self.submit(round_id, payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "finished")
        self.assertEqual((result["team1_score"], result["team2_score"]), (2, 0))
        self.assertEqual(result["winner_id"], winner_id)
        self.assertEqual(result["bo3_scores"], payload["bo3_scores"])
        self.assertIsInstance(result["bo3_scores"], list)
        self.assertEqual(json.loads(self.stored_round(round_id)[4]), payload["bo3_scores"])

    def test_valid_three_game_quarter_final_can_award_second_team(self):
        round_ = self.make_quarter_final()
        round_id, winner_id = round_.id, round_.team2_id
        payload = self.scores(three_games=True, reverse=True)
        status, result = self.submit(round_id, payload)
        self.assertEqual(status, 200)
        self.assertEqual((result["team1_score"], result["team2_score"]), (1, 2))
        self.assertEqual(result["winner_id"], winner_id)
        self.assertEqual(result["bo3_scores"], payload["bo3_scores"])
        self.assertIsInstance(result["bo3_scores"], list)

    def test_legacy_champion_columns_return_upgrade_error_then_migration_allows_retry(self):
        round_ = self.make_quarter_final()
        round_id = round_.id
        before_round = self.stored_round(round_id)
        history = ChampionSnapshot(match_id=999, final_round_id=998,
            match_name="往届赛事", champion_team_id=997, champion_name="往届冠军",
            runner_up_name="往届亚军", champion_score=2, runner_up_score=1,
            roster_json='[{"nickname":"历史成员"}]', finalists_json="{}",
            snapshot_source="final_result", is_valid=True)
        self.db.add(history)
        self.db.commit()
        # Reproduce a deployed first-generation table without the manual upgrade.
        with self.engine.begin() as connection:
            for column in ADDED_FIELDS:
                connection.exec_driver_sql(f"ALTER TABLE champion_snapshots DROP COLUMN {column}")
            before_history = dict(connection.execute(text(
                "SELECT * FROM champion_snapshots WHERE match_id = 999")).mappings().one())
        status, result = self.submit(round_id)
        self.assertEqual(status, 503)
        for phrase in ("冠军", "升级", "比分未保存"):
            self.assertIn(phrase, result["detail"])
        self.assertEqual(self.stored_round(round_id), before_round)
        with self.engine.connect() as connection:
            failed_history = dict(connection.execute(text(
                "SELECT * FROM champion_snapshots WHERE match_id = 999")).mappings().one())
        self.assertEqual(failed_history, before_history)
        self.assertTrue(upgrade(self.engine)["upgraded"])
        status, saved = self.submit(round_id)
        self.assertEqual(status, 200)
        self.assertEqual(saved["status"], "finished")
        self.assertEqual(saved["bo3_scores"], self.scores()["bo3_scores"])
        with self.engine.connect() as connection:
            upgraded_history = dict(connection.execute(text(
                "SELECT * FROM champion_snapshots WHERE match_id = 999")).mappings().one())
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM champion_snapshots")).scalar_one(), 1)
        self.assertEqual({name: upgraded_history[name] for name in before_history}, before_history)
        self.assertEqual(upgraded_history["manual_version"], 0)

    def test_missing_champion_table_returns_upgrade_error_without_saving_scores(self):
        round_id = self.make_quarter_final().id
        before = self.stored_round(round_id)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE champion_snapshots")
        status, result = self.submit(round_id)
        self.assertEqual(status, 503)
        for phrase in ("冠军", "升级", "比分未保存"):
            self.assertIn(phrase, result["detail"])
        self.assertEqual(self.stored_round(round_id), before)
        self.assertTrue(upgrade(self.engine)["created"])
        self.assertEqual(self.submit(round_id)[0], 200)

    def test_mysql_missing_champion_schema_errors_have_actionable_response_and_rollback(self):
        round_id = self.make_quarter_final().id
        before = self.stored_round(round_id)
        for exception_type, code, message in (
            (OperationalError, 1054, "Unknown column 'champion_snapshots.manual_created_by' in 'field list'"),
            (ProgrammingError, 1146, "Table 'test.champion_snapshots' doesn't exist"),
        ):
            with self.subTest(mysql_code=code):
                error = exception_type("SELECT * FROM champion_snapshots", {}, Exception(code, message))
                with patch.object(hall_service, "sync_champion_snapshot", side_effect=error):
                    status, result = self.submit(round_id)
                self.assertEqual(status, 503)
                for phrase in ("冠军", "升级", "比分未保存"):
                    self.assertIn(phrase, result["detail"])
                self.assertEqual(self.stored_round(round_id), before)

    def test_connection_and_unrelated_schema_errors_are_not_reported_as_champion_upgrade(self):
        round_id = self.make_quarter_final().id
        before = self.stored_round(round_id)
        errors = [
            OperationalError("SELECT * FROM champion_snapshots", {},
                             Exception(2006, "MySQL server has gone away")),
            OperationalError("SELECT users.unknown_column FROM users", {},
                             Exception(1054, "Unknown column 'users.unknown_column' in 'field list'")),
        ]
        for error in errors:
            with self.subTest(statement=error.statement, code=error.orig.args[0]):
                with patch.object(hall_service, "sync_champion_snapshot", side_effect=error):
                    with self.assertRaises(OperationalError) as raised:
                        self.submit(round_id)
                self.assertIs(raised.exception, error)
                self.assertEqual(self.stored_round(round_id), before)

    def test_first_final_archive_failure_rolls_back_new_champion_and_scores(self):
        final = self.make_pending_final()
        round_id, match_id = final.id, final.match_id
        before = self.stored_round(round_id)
        real_sync = hall_service.sync_champion_snapshot

        def fail_after_archive_flush(db, *args, **kwargs):
            self.assertTrue(real_sync(db, *args, **kwargs))
            db.flush()
            raise RuntimeError("simulated archive storage failure")

        with patch.object(hall_service, "sync_champion_snapshot", side_effect=fail_after_archive_flush):
            with self.assertRaisesRegex(RuntimeError, "simulated archive storage failure"):
                self.submit(round_id)
        self.assertEqual(self.stored_round(round_id), before)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ChampionSnapshot, match_id))

    def test_failed_final_correction_preserves_previously_saved_champion(self):
        final = self.make_pending_final()
        round_id, match_id = final.id, final.match_id
        self.assertEqual(self.submit(round_id)[0], 200)
        before_round = self.stored_round(round_id)
        with self.engine.connect() as connection:
            before_champion = dict(connection.execute(text(
                "SELECT * FROM champion_snapshots WHERE match_id = :match_id"),
                {"match_id": match_id}).mappings().one())
        real_sync = hall_service.sync_champion_snapshot

        def fail_after_correction_flush(db, *args, **kwargs):
            self.assertTrue(real_sync(db, *args, **kwargs))
            db.flush()
            raise RuntimeError("simulated correction storage failure")

        with patch.object(hall_service, "sync_champion_snapshot", side_effect=fail_after_correction_flush):
            with self.assertRaisesRegex(RuntimeError, "simulated correction storage failure"):
                self.submit(round_id, self.scores(three_games=True, reverse=True))
        self.assertEqual(self.stored_round(round_id), before_round)
        with self.engine.connect() as connection:
            after_champion = dict(connection.execute(text(
                "SELECT * FROM champion_snapshots WHERE match_id = :match_id"),
                {"match_id": match_id}).mappings().one())
        self.assertEqual(after_champion, before_champion)
