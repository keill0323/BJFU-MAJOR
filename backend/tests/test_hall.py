"""Exercise frozen final honours and database-ranked public pages in isolation."""
import asyncio
import json
from datetime import datetime
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.hall import ChampionSnapshot
from app.models.match import MatchRound, MatchStatus, RoundStatus
from app.models.team import TeamMember
from app.models.user import RankApplication
from app.routers.hall import router
from app.services import hall_service as hall, match_service as matches


def get(app, url):
    path, _, query = url.partition("?")
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class HallTests(DatabaseTestCase):
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

    def make_bracket(self, *, finished=True, **match_kwargs):
        match = self.make_match(**match_kwargs)
        teams = [self.make_team() for _ in range(6)]
        # QF: C/E, D/F; SF: A/D, B/C; final: A/B.
        pairs = [(2, 4), (3, 5), (0, 3), (1, 2), (0, 1)]
        rounds = []
        for index, (first, second) in enumerate(pairs):
            round_ = MatchRound(
                match_id=match.id, round_number=index + 1,
                team1_id=teams[first].id, team2_id=teams[second].id,
                team1_score=2, team2_score=0, winner_id=teams[first].id,
                group_name="淘汰赛", status=RoundStatus.FINISHED,
            )
            if index == 4 and not finished:
                round_.status = RoundStatus.PENDING
                round_.winner_id = None
                round_.team1_score = 0
            self.db.add(round_)
            rounds.append(round_)
        self.db.commit()
        return match, teams, rounds

    def record_final(self, final, winner=1, loser_score=0):
        games = [{"t1": 13, "t2": 7}, {"t1": 13, "t2": 9}]
        if loser_score:
            games.insert(0, {"t1": 8, "t2": 13})
        if winner == 2:
            games = [{"t1": g["t2"], "t2": g["t1"]} for g in games]
        return matches.update_round_result(
            self.db, final.id, 2 if winner == 1 else loser_score,
            loser_score if winner == 1 else 2, bo3_scores=games,
        )

    def test_every_rank_plus_and_s_star_is_sorted_by_rank_not_rating_or_verification(self):
        names = [name for name in hall.RANK_ORDER if name != "S"]
        for index, name in enumerate(names):
            self.make_user(rank=name, individual_rating=100000 - index, is_verified=False)
        for invalid in (None, "", "S51", "S-1", "S01", "魔王S", "A+++", "s50"):
            self.make_user(rank=invalid, individual_rating=999999)
        self.db.commit()
        status, page = get(self.app, "/api/hall/players?limit=100")
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 61)
        self.assertEqual([p["rank"] for p in page["items"]], list(reversed(names)))
        self.assertEqual([p["position"] for p in page["items"]], list(range(1, 62)))
        self.assertFalse(page["has_more"])

    def test_tied_ranks_across_pages_use_competition_position_and_stable_id(self):
        leaders = [self.make_user(rank="S50", individual_rating=value) for value in (10, 999, 1)]
        fourth = self.make_user(rank="S49")
        legacy = self.make_user(rank="S")
        current = self.make_user(rank="S0")
        self.db.commit()
        _, first = get(self.app, "/api/hall/players?limit=2")
        _, second = get(self.app, "/api/hall/players?offset=2&limit=2")
        _, third = get(self.app, "/api/hall/players?offset=4&limit=2")
        self.assertEqual([p["user_id"] for p in first["items"]], [u.id for u in leaders[:2]])
        self.assertEqual([p["position"] for p in second["items"]], [1, 4])
        self.assertEqual([p["user_id"] for p in second["items"]], [leaders[2].id, fourth.id])
        self.assertEqual([p["position"] for p in third["items"]], [5, 5])
        self.assertEqual([p["user_id"] for p in third["items"]], [legacy.id, current.id])

    def test_pending_rank_application_does_not_replace_effective_rank_or_publish_private_fields(self):
        user = self.make_user(rank="C+", verify_image="secret-verify", rank_image="secret-rank")
        unranked = self.make_user(rank=None)
        for applicant in (user, unranked):
            self.db.add(RankApplication(user_id=applicant.id, ai_rank="S50", status="pending"))
        self.db.commit()
        _, page = get(self.app, "/api/hall/players")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["rank"], "C+")
        self.assertEqual(set(page["items"][0]), {"position", "user_id", "nickname", "avatar", "rank", "individual_rating"})

    def test_pagination_validates_bounds_and_empty_pages(self):
        for feed in ("players", "champions"):
            for params in ("limit=0", "limit=101", "offset=-1", "offset=abc"):
                self.assertEqual(get(self.app, f"/api/hall/{feed}?{params}")[0], 422)
            status, page = get(self.app, f"/api/hall/{feed}?offset=1000&limit=100")
            self.assertEqual(status, 200)
            self.assertEqual(page["items"], [])
            self.assertFalse(page["has_more"])

    def test_recording_real_final_creates_snapshot_in_same_commit(self):
        when = datetime(2026, 8, 20, 12)
        match, teams, rounds = self.make_bracket(finished=False, match_start=when)
        self.record_final(rounds[-1])
        self.db.expire_all()
        snapshot = self.db.get(ChampionSnapshot, match.id)
        self.assertIsNotNone(snapshot)
        status, page = get(self.app, "/api/hall/champions")
        self.assertEqual(status, 200)
        item = page["items"][0]
        self.assertEqual(item["champion_team_id"], teams[0].id)
        self.assertEqual(item["champion_score"], 2)
        self.assertEqual(item["runner_up_score"], 0)
        self.assertEqual(item["event_date"], when.isoformat())
        self.assertEqual(item["snapshot_source"], "final_result")
        self.assertIsNotNone(item["awarded_at"])
        self.assertEqual(set(item["roster"][0]), {"user_id", "nickname", "avatar", "rank"})

    def test_finished_status_pending_final_or_incomplete_bracket_never_creates_champion(self):
        match, _, rounds = self.make_bracket(finished=False, status=MatchStatus.FINISHED)
        self.assertFalse(hall.sync_champion_snapshot(self.db, match.id))
        self.db.delete(rounds[0])
        self.db.commit()
        self.record_final(rounds[-1])
        self.assertEqual(hall.list_champions(self.db)["total"], 0)

    def test_fake_final_with_wrong_semifinal_winner_or_conflicting_score_is_rejected(self):
        match, teams, rounds = self.make_bracket()
        final = rounds[-1]
        final.team2_id = teams[2].id
        self.db.commit()
        self.assertFalse(hall.sync_champion_snapshot(self.db, match.id))
        final.team2_id = teams[1].id
        final.winner_id = teams[1].id  # Score still says team 1 won.
        self.db.commit()
        self.assertFalse(hall.sync_champion_snapshot(self.db, match.id))

    def test_invalid_quarter_to_semifinal_path_and_extra_knockout_round_are_rejected(self):
        match, teams, rounds = self.make_bracket()
        rounds[2].team2_id = teams[5].id
        self.db.commit()
        self.assertFalse(hall.sync_champion_snapshot(self.db, match.id))
        rounds[2].team2_id = teams[3].id
        self.db.add(MatchRound(match_id=match.id, round_number=6, group_name="淘汰赛"))
        self.db.commit()
        self.assertFalse(hall.sync_champion_snapshot(self.db, match.id))

    def test_renames_transfers_and_rank_updates_never_rewrite_frozen_roster(self):
        match, teams, rounds = self.make_bracket(finished=False)
        self.record_final(rounds[-1])
        before = hall.list_champions(self.db)["items"][0]
        teams[0].name = "Renamed winner"
        teams[0].logo = "/uploads/new-logo.png"
        member = self.db.query(TeamMember).filter_by(team_id=teams[0].id).first()
        member.user.nickname = "Renamed player"
        member.user.rank = "S50"
        self.db.delete(member)
        match.name = "Renamed event"
        self.db.commit()
        self.record_final(rounds[-1], loser_score=1)
        after = hall.list_champions(self.db)["items"][0]
        for field in ("champion_name", "champion_logo", "match_name", "roster", "awarded_at"):
            self.assertEqual(before[field], after[field])
        self.assertEqual(after["runner_up_score"], 1)

    def test_corrected_winner_uses_other_finalists_original_snapshot(self):
        match, teams, rounds = self.make_bracket(finished=False)
        original_name = teams[1].name
        original_user = teams[1].captain.nickname
        self.record_final(rounds[-1])
        teams[1].name = "New name"
        teams[1].captain.nickname = "New nickname"
        self.db.commit()
        self.record_final(rounds[-1], winner=2)
        item = hall.list_champions(self.db)["items"][0]
        self.assertEqual(item["champion_team_id"], teams[1].id)
        self.assertEqual(item["champion_name"], original_name)
        self.assertEqual(item["roster"][0]["nickname"], original_user)
        self.assertEqual(self.db.query(ChampionSnapshot).count(), 1)

    def test_invalid_correction_withdraws_honour_but_keeps_frozen_finalists_for_recovery(self):
        match, teams, rounds = self.make_bracket(finished=False)
        self.record_final(rounds[-1])
        original_name = teams[0].name
        matches.update_round_result(self.db, rounds[-1].id, 0, 0)
        self.assertEqual(hall.list_champions(self.db)["total"], 0)
        teams[0].name = "Later team name"
        self.db.commit()
        self.record_final(rounds[-1])
        self.assertEqual(hall.list_champions(self.db)["items"][0]["champion_name"], original_name)

    def test_snapshot_failure_rolls_back_result_and_snapshot_together(self):
        match, _, rounds = self.make_bracket(finished=False)
        real_sync = hall.sync_champion_snapshot

        def fail_after_snapshot(*args, **kwargs):
            real_sync(*args, **kwargs)
            self.db.flush()
            raise RuntimeError("simulated persistence failure")

        with patch.object(hall, "sync_champion_snapshot", side_effect=fail_after_snapshot):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.record_final(rounds[-1])
        self.db.expire_all()
        self.assertEqual(self.db.get(MatchRound, rounds[-1].id).status, RoundStatus.PENDING)
        self.assertIsNone(self.db.get(ChampionSnapshot, match.id))

    def test_public_get_does_not_backfill_or_write_database(self):
        self.make_bracket()
        statements = []

        def record(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.strip().split()[0].upper())

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            status, page = get(self.app, "/api/hall/champions")
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 0)
        self.assertTrue(statements)
        self.assertTrue(all(statement == "SELECT" for statement in statements))

    def test_explicit_backfill_is_idempotent_marks_unknown_time_and_preserves_later_changes(self):
        match, teams, _ = self.make_bracket(match_start=None, created_at=datetime(2026, 1, 1))
        original_name = teams[0].name
        self.assertEqual(hall.backfill_champions(self.db), {"created": 1, "skipped": 0})
        item = hall.list_champions(self.db)["items"][0]
        self.assertEqual(item["snapshot_source"], "backfill")
        self.assertIsNone(item["awarded_at"])
        self.assertEqual(item["event_date"], match.created_at)
        teams[0].name = "Changed after backfill"
        self.db.commit()
        self.assertEqual(hall.backfill_champions(self.db), {"created": 0, "skipped": 1})
        self.assertEqual(hall.list_champions(self.db)["items"][0]["champion_name"], original_name)

    def test_backfill_skips_unfinished_or_falsely_finished_events(self):
        self.make_bracket(finished=False, status=MatchStatus.FINISHED)
        self.assertEqual(hall.backfill_champions(self.db), {"created": 0, "skipped": 1})

    def test_first_snapshot_on_old_final_correction_is_honest_about_unknown_historical_time(self):
        _, _, rounds = self.make_bracket(finished=True)
        self.record_final(rounds[-1], loser_score=1)
        item = hall.list_champions(self.db)["items"][0]
        self.assertEqual(item["snapshot_source"], "backfill")
        self.assertIsNone(item["awarded_at"])

    def test_event_deletion_keeps_historical_honours_without_foreign_keys(self):
        match, _, rounds = self.make_bracket(finished=False)
        self.record_final(rounds[-1])
        self.assertTrue(hall.list_champions(self.db)["items"][0]["match_available"])
        matches.delete_match(self.db, match.id)
        self.assertEqual(hall.list_champions(self.db)["total"], 1)
        self.assertFalse(hall.list_champions(self.db)["items"][0]["match_available"])

    def test_snapshot_and_bracket_reads_use_current_locks_after_waiting_for_event(self):
        _, _, rounds = self.make_bracket(finished=False)
        statements = []

        def collect(state):
            if state.is_select:
                statements.append(str(state.statement.compile(dialect=mysql.dialect())))

        event.listen(self.db, "do_orm_execute", collect)
        try:
            self.record_final(rounds[-1])
        finally:
            event.remove(self.db, "do_orm_execute", collect)
        snapshot_reads = [sql for sql in statements if "FROM champion_snapshots" in sql]
        self.assertTrue(snapshot_reads)
        self.assertTrue(all("FOR UPDATE" in sql for sql in snapshot_reads))
        bracket_reads = [sql for sql in statements if "FROM match_rounds" in sql and "ORDER BY" in sql]
        self.assertTrue(bracket_reads)
        self.assertTrue(all("FOR UPDATE" in sql for sql in bracket_reads))

    def test_champion_feed_orders_event_date_and_paginates(self):
        for year in (2024, 2026, 2025):
            self.make_bracket(match_start=datetime(year, 1, 1))
        hall.backfill_champions(self.db)
        _, first = get(self.app, "/api/hall/champions?limit=2")
        _, second = get(self.app, "/api/hall/champions?offset=2&limit=2")
        self.assertEqual([item["event_date"][:4] for item in first["items"]], ["2026", "2025"])
        self.assertEqual(second["items"][0]["event_date"][:4], "2024")
        self.assertEqual(first["total"], 3)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
