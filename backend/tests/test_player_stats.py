"""Real isolated imports and read-only HTTP aggregation; no external requests."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.support import DatabaseTestCase
from app.database import get_db
from app.models.match import MatchRound, MatchStatus, StageStatus, TeamProgress
from app.models.player_stats import PlayerMapStats, PlayerStatsMap
from app.models.user import User
from app.routers.player_stats import router
from app.services.player_stats_service import import_perfect_maps, list_player_stats


def request(app, path, method="GET"):
    path, _, query = path.partition("?")
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send))
    status = next(value["status"] for value in messages if value["type"] == "http.response.start")
    body = b"".join(value.get("body", b"") for value in messages if value["type"] == "http.response.body")
    return status, json.loads(body)


class PlayerStatsTests(DatabaseTestCase):
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

    def fixture(self, external="4183752848", match=None, teams=None):
        match = match or self.make_match()
        if teams is None:
            teams = [self.make_team(), self.make_team()]
            for index, team in enumerate(teams):
                self.db.add(TeamProgress(match_id=match.id, team_id=team.id,
                    stage=StageStatus.LEGEND, group_name="上区" if not index else "下区", seed=index + 1))
        self.db.commit()
        value = {"source": "perfect_match", "external_match_id": external,
            "source_url": "https://match.wmpvp.com/csgo/detail/" + external,
            "cup_name": "测试起源杯", "map_name": "de_mirage",
            "source_team1_name": teams[0].name if teams else "Source A",
            "source_team2_name": teams[1].name if teams else "Source B",
            "team1_id": teams[0].id if teams else None, "team2_id": teams[1].id if teams else None,
            "team1_score": 13, "team2_score": 0,
            "players": [{"steam_id": "76561198000000001", "nickname": "Alpha", "source_team_id": "1",
                "team_slot": 1, "kills": 26, "deaths": 13, "assists": 6,
                "first_kills": 2, "adr": 100.0, "rating": 1.0},
                {"steam_id": "76561198000000002", "nickname": "Beta", "source_team_id": "2",
                "team_slot": 2, "kills": 13, "deaths": 26, "assists": 2,
                "adr": None, "rating": None}]}
        return match, teams, value

    def api(self, match, query=""):
        status, page = request(self.app, f"/api/matches/{match.id}/player-stats" + query)
        self.assertEqual(status, 200)
        return page

    def test_weighted_adr_real_counts_metric_coverage_and_opaque_public_identity(self):
        match, teams, first = self.fixture()
        second = deepcopy(first)
        second.update(external_match_id="4183752849", source_url="https://match.wmpvp.com/csgo/detail/4183752849",
                      team1_score=13, team2_score=12)
        second["players"][0].update(kills=25, deaths=12, assists=4, adr=200.0, rating=2.0, first_kills=None)
        self.assertEqual(import_perfect_maps(self.db, match.id, [first, second]),
            {"inserted": 2, "updated": 0, "unchanged": 0, "processed_maps": 2, "map_count": 2, "player_rows": 4})
        page = self.api(match)
        alpha, beta = page["items"]
        self.assertEqual((page["map_count"], page["player_count"], page["total"]), (2, 2, 2))
        self.assertEqual((alpha["maps_played"], alpha["kills"], alpha["deaths"], alpha["assists"], alpha["kd"]), (2, 51, 25, 10, 2.04))
        self.assertEqual(alpha["adr"], round((13 * 100 + 25 * 200) / 38, 2))
        self.assertEqual((alpha["adr_maps"], alpha["average_rating"], alpha["rating_maps"]), (2, 1.5, 2))
        self.assertEqual((alpha["first_kills"], alpha["first_kill_maps"]), (2, 1))
        self.assertIsNone(beta["adr"])
        self.assertIsNone(beta["average_rating"])
        self.assertIsNone(beta["first_kills"])
        self.assertEqual(beta["adr_maps"], 0)
        serialized = json.dumps(page)
        for private in ("steam_id", "76561198000000001", "76561198000000002", "student_id", "wx_openid", "user_id"):
            self.assertNotIn(private, serialized)
        self.assertRegex(alpha["player_key"], r"^[0-9a-f]{32}$")
        self.assertIn("近似", "".join(page["notices"]))
        self.assertIn("完美Rating", "".join(page["notices"]))
        self.assertTrue(page["updated_at"].endswith("+00:00"))

    def test_reimport_is_noop_and_correction_replaces_map_without_double_counting(self):
        match, _, payload = self.fixture()
        import_perfect_maps(self.db, match.id, [payload])
        key = self.api(match)["items"][0]["player_key"]
        row = self.db.query(PlayerStatsMap).one()
        before_times = row.imported_at, row.updated_at
        reversed_players = deepcopy(payload)
        reversed_players["players"].reverse()
        result = import_perfect_maps(self.db, match.id, [reversed_players])
        self.assertEqual((result["inserted"], result["updated"], result["unchanged"]), (0, 0, 1))
        self.assertEqual((row.imported_at, row.updated_at), before_times)
        corrected = deepcopy(payload)
        corrected["players"][0]["kills"] = 20
        result = import_perfect_maps(self.db, match.id, [corrected])
        self.assertEqual((result["map_count"], result["player_rows"], result["updated"]), (1, 2, 1))
        alpha = self.api(match)["items"][0]
        self.assertEqual(alpha["kills"], 20)
        self.assertEqual(alpha["player_key"], key)
        self.assertEqual(row.imported_at, before_times[0])

    def test_bad_later_map_rolls_back_earlier_correction_and_player_replacement(self):
        match, _, payload = self.fixture()
        import_perfect_maps(self.db, match.id, [payload])
        before = self.api(match)
        correction = deepcopy(payload)
        correction["players"][0]["kills"] = 30
        broken = deepcopy(payload)
        broken.update(external_match_id="4183752849", source_url="https://match.wmpvp.com/csgo/detail/4183752849", round_id=999999)
        with self.assertRaises(ValueError):
            import_perfect_maps(self.db, match.id, [correction, broken])
        self.assertEqual(self.api(match), before)
        self.assertEqual(self.db.query(PlayerStatsMap).count(), 1)
        self.assertEqual(self.db.query(PlayerMapStats).count(), 2)

    def test_invalid_sources_credentials_duplicates_and_unreal_maps_leave_no_rows(self):
        match, _, payload = self.fixture()
        changes = [
            lambda item: item.update(source_url="https://evil.example/csgo/detail/4183752848"),
            lambda item: item.update(source_url="https://match.wmpvp.com/csgo/detail/4183752848?token=secret"),
            lambda item: item.update(source_url="https://user:secret@match.wmpvp.com/csgo/detail/4183752848"),
            lambda item: item.update(cookie="secret"),
            lambda item: item.update(team1_score=0, team2_score=0),
            lambda item: item["players"][1].update(steam_id=item["players"][0]["steam_id"]),
            lambda item: item["players"][0].update(steam_id=76561198000000001),
            lambda item: item["players"][0].update(kills=-1),
            lambda item: item["players"][0].update(kills=True),
            lambda item: item["players"][0].update(adr=float("nan")),
            lambda item: item["players"][0].update(hs_percent=101),
            lambda item: item["players"][0].update(first_kills=100),
            lambda item: item["players"][0].update(team_slot=True),
        ]
        for change in changes:
            with self.subTest(change=change):
                invalid = deepcopy(payload)
                change(invalid)
                with self.assertRaises((ValueError, ValidationError)):
                    import_perfect_maps(self.db, match.id, [payload, invalid])
                self.assertEqual(self.db.query(PlayerStatsMap).count(), 0)
                self.assertEqual(self.db.query(PlayerMapStats).count(), 0)
        with self.assertRaises(ValueError):
            import_perfect_maps(self.db, match.id, [payload, deepcopy(payload)])

    def test_source_id_cannot_move_events_and_team_and_round_associations_are_checked(self):
        match, teams, payload = self.fixture()
        import_perfect_maps(self.db, match.id, [payload])
        another = self.make_match()
        self.db.commit()
        unmapped = deepcopy(payload)
        unmapped.update(team1_id=None, team2_id=None)
        with self.assertRaises(ValueError):
            import_perfect_maps(self.db, another.id, [unmapped])
        linked = self.result_round(match, teams)
        reversed_map = deepcopy(payload)
        reversed_map.update(round_id=linked.id, map_index=1)
        reversed_map["team1_id"], reversed_map["team2_id"] = reversed_map["team2_id"], reversed_map["team1_id"]
        self.assertEqual(import_perfect_maps(self.db, match.id, [reversed_map])["updated"], 1)
        foreign_team = self.make_team()
        self.db.commit()
        invalid = deepcopy(reversed_map)
        invalid["team1_id"] = foreign_team.id
        with self.assertRaises(ValueError):
            import_perfect_maps(self.db, match.id, [invalid])

    def result_round(self, match, teams):
        row = MatchRound(match_id=match.id, round_number=1, group_name="淘汰赛",
            team1_id=teams[0].id, team2_id=teams[1].id)
        self.db.add(row)
        self.db.commit()
        return row

    def test_team_filter_counts_only_maps_played_for_that_team_and_nickname_search(self):
        match, teams, first = self.fixture()
        second = deepcopy(first)
        second.update(external_match_id="4183752849", source_url="https://match.wmpvp.com/csgo/detail/4183752849")
        second["players"][0].update(team_slot=2, nickname="Alpha NEW", kills=10, deaths=5)
        second["players"][1].update(team_slot=1)
        second["players"][0]["source_team_id"] = "2"
        second["players"][1]["source_team_id"] = "1"
        import_perfect_maps(self.db, match.id, [first, second])
        page = self.api(match, f"?team_id={teams[0].id}&keyword=ALPHA")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["maps_played"], 1)
        self.assertEqual(page["items"][0]["kills"], 26)
        self.assertEqual(page["items"][0]["teams"], [{"team_id": teams[0].id, "team_name": teams[0].name}])
        page = self.api(match, "?keyword=alpha")
        self.assertEqual(page["items"][0]["maps_played"], 2)
        self.assertEqual(page["items"][0]["nickname"], "Alpha NEW")
        self.assertEqual(len(page["items"][0]["teams"]), 2)

    def test_hidden_team_maps_and_unlinked_hidden_source_names_are_not_public(self):
        match, teams, first = self.fixture()
        second = deepcopy(first)
        second.update(external_match_id="4183752849", source_url="https://match.wmpvp.com/csgo/detail/4183752849", team1_id=None, team2_id=None)
        import_perfect_maps(self.db, match.id, [first, second])
        teams[1].is_hidden = True
        self.db.commit()
        page = self.api(match)
        self.assertEqual((page["map_count"], page["player_count"], page["total"]), (0, 0, 0))
        self.assertEqual(page["source_maps"], [])
        self.assertNotIn("Alpha", json.dumps(page))

    def test_deleted_draft_or_missing_events_are_404_without_deleting_import_history(self):
        match, _, payload = self.fixture(teams=[])
        import_perfect_maps(self.db, match.id, [payload])
        match.status = MatchStatus.DRAFT
        self.db.commit()
        self.assertEqual(request(self.app, f"/api/matches/{match.id}/player-stats")[0], 404)
        self.db.delete(match)
        self.db.commit()
        self.assertEqual(request(self.app, f"/api/matches/{match.id}/player-stats")[0], 404)
        self.assertEqual(self.db.query(PlayerStatsMap).count(), 1)
        self.assertEqual(self.db.query(PlayerMapStats).count(), 2)

    def test_empty_page_zero_deaths_pagination_and_only_get_is_exposed(self):
        match, _, payload = self.fixture()
        self.assertEqual(self.api(match)["items"], [])
        payload["players"][0]["deaths"] = 0
        import_perfect_maps(self.db, match.id, [payload])
        first = self.api(match, "?limit=1")
        second = self.api(match, "?limit=1&offset=1")
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertIsNone(first["items"][0]["kd"])
        self.assertEqual(first, self.api(match, "?limit=1"))
        self.assertEqual(self.api(match, "?offset=1000")["items"], [])
        for query in ("?limit=0", "?limit=101", "?offset=-1", "?team_id=0", "?keyword=" + "a" * 101):
            self.assertEqual(request(self.app, f"/api/matches/{match.id}/player-stats" + query)[0], 422)
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(request(self.app, f"/api/matches/{match.id}/player-stats", method)[0], 405)

    def test_public_read_is_select_only_and_never_claims_matching_local_user(self):
        match, _, payload = self.fixture()
        user = self.make_user(game_id=payload["players"][0]["steam_id"], nickname="Private local name")
        self.db.commit()
        original = (user.nickname, user.student_id, user.rank, user.individual_rating, user.game_id)
        import_perfect_maps(self.db, match.id, [payload])
        statements = []

        def record(_, __, statement, ___, ____, _____):
            statements.append(statement.strip().split()[0].upper())

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            page = self.api(match)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertTrue(statements)
        self.assertEqual(set(statements), {"SELECT"})
        self.assertEqual(page["items"][0]["nickname"], "Alpha")
        self.db.refresh(user)
        self.assertEqual((user.nickname, user.student_id, user.rank, user.individual_rating, user.game_id), original)

    def test_uid_fallback_is_not_republished_or_searchable_as_nickname(self):
        match, _, payload = self.fixture()
        steam_id = payload["players"][0]["steam_id"]
        payload["players"][0]["nickname"] = steam_id
        import_perfect_maps(self.db, match.id, [payload])
        page = self.api(match)
        self.assertNotIn(steam_id, json.dumps(page))
        self.assertTrue(page["items"][0]["nickname"].startswith("未命名选手 "))
        self.assertEqual(self.api(match, "?keyword=" + steam_id)["total"], 0)

    def test_external_team_identity_is_one_to_one_per_map_without_requiring_five_players(self):
        match, _, payload = self.fixture()
        substitute = deepcopy(payload["players"][0])
        substitute.update(steam_id="76561198000000003", nickname="Substitute")
        payload["players"].append(substitute)
        result = import_perfect_maps(self.db, match.id, [payload])
        self.assertEqual(result["player_rows"], 3)
        self.assertEqual(self.api(match)["player_count"], 3)
        before = self.api(match)
        mixed = deepcopy(payload)
        mixed["players"][2]["source_team_id"] = "other-external-team"
        with self.assertRaises(ValueError):
            import_perfect_maps(self.db, match.id, [mixed])
        self.assertEqual(self.api(match), before)
        mixed = deepcopy(payload)
        mixed["players"][1]["source_team_id"] = "1"
        with self.assertRaises(ValueError):
            import_perfect_maps(self.db, match.id, [mixed])
        self.assertEqual(self.api(match), before)

    def test_new_tables_compile_for_mysql_and_keep_source_and_player_unique_keys(self):
        from sqlalchemy.dialects import mysql
        from sqlalchemy.schema import CreateTable
        for model in (PlayerStatsMap, PlayerMapStats):
            ddl = str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))
            self.assertIn("CREATE TABLE", ddl)
        self.assertIn("uq_player_stats_provider_map", str(CreateTable(PlayerStatsMap.__table__).compile(dialect=mysql.dialect())))
        self.assertIn("uq_player_stats_map_steam", str(CreateTable(PlayerMapStats.__table__).compile(dialect=mysql.dialect())))
