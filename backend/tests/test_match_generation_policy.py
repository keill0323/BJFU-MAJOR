"""Real HTTP generation policy with isolated users and SQLite, never live services."""
import asyncio
from collections import Counter
from datetime import datetime
import json
from urllib.parse import urlencode
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.match import (MatchRound, MatchStatus, Registration, RegistrationStatus,
                              RoundStatus, StageStatus, TeamProgress)
from app.models.user import UserRole
from app.routers.match import router
from app.services import match_service
from app.services.auth_service import create_access_token


def post_generation(app, match_id, mode, group, token=None):
    path = f"/api/matches/{match_id}/auto-matches/{mode}"
    query = urlencode({"group_name": group}).encode() if group is not None else b""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    asyncio.run(app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query, "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }, receive, send))
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return status, json.loads(body)


class MatchGenerationPolicyTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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

    def group(self, name="A", count=3, stage=StageStatus.CHALLENGER, match=None):
        match = match or self.make_match(status=MatchStatus.IN_PROGRESS)
        teams = [self.make_team() for _ in range(count)]
        for team in teams:
            self.db.add(TeamProgress(match_id=match.id, team_id=team.id, group_name=name, stage=stage))
            self.db.add(Registration(match_id=match.id, team_id=team.id,
                                     user_id=team.captain_id, status=RegistrationStatus.APPROVED))
        self.db.commit()
        return match, teams

    def post(self, match, mode, group, token=None):
        return post_generation(self.app, match.id, mode, group, self.token if token is None else token)

    def rounds(self, match):
        self.db.expire_all()
        return self.db.query(MatchRound).filter_by(match_id=match.id).order_by(MatchRound.id).all()

    def snapshot(self, match):
        return [tuple(getattr(row, column.name) for column in MatchRound.__table__.columns)
                for row in self.rounds(match)]

    def assert_double_three_team_group(self, rounds, teams):
        self.assertEqual(len(rounds), 6)
        team_counts = Counter(team_id for row in rounds for team_id in (row.team1_id, row.team2_id))
        self.assertEqual(team_counts, {team.id: 4 for team in teams})
        directed = Counter((row.team1_id, row.team2_id) for row in rounds)
        self.assertEqual(directed, {(a.id, b.id): 1 for a in teams for b in teams if a.id != b.id})
        self.assertTrue(all(row.bo3_scores is None and row.status == RoundStatus.PENDING for row in rounds))

    def test_legacy_single_entry_generates_double_bo1_for_all_challenger_groups(self):
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        for group_name in ("A", "B", "C", "D"):
            _, teams = self.group(group_name, match=match)
            status, result = self.post(match, "single", group_name)
            self.assertEqual(status, 200)
            self.assertEqual(result, {"message": "对阵生成完成", "round_robin": "double"})
            rounds = [row for row in self.rounds(match) if row.group_name == group_name]
            self.assert_double_three_team_group(rounds, teams)
        self.assertEqual(len(self.rounds(match)), 24)

    def test_double_entry_generates_six_bo1_rounds_per_challenger_group(self):
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        for group_name in ("A", "B", "C", "D"):
            _, teams = self.group(group_name, match=match)
            status, result = self.post(match, "double", group_name)
            self.assertEqual((status, result["round_robin"]), (200, "double"))
            self.assert_double_three_team_group([row for row in self.rounds(match) if row.group_name == group_name], teams)
        self.assertEqual(len(self.rounds(match)), 24)

    def test_actual_custom_challenger_group_is_not_limited_to_literal_abcd(self):
        match, teams = self.group("新人甲组")
        self.assertEqual(self.post(match, "single", "新人甲组")[0], 200)
        self.assert_double_three_team_group(self.rounds(match), teams)

    def test_single_preserves_playoff_and_legend_round_robin(self):
        for name, count, stage in (("附加赛", 3, StageStatus.CHALLENGER),
                                   ("上区", 4, StageStatus.LEGEND), ("下区", 4, StageStatus.LEGEND)):
            with self.subTest(group=name):
                match, teams = self.group(name, count=count, stage=stage)
                status, result = self.post(match, "single", name)
                self.assertEqual((status, result["round_robin"]), (200, "single"))
                rounds = self.rounds(match)
                self.assertEqual(len(rounds), count * (count - 1) // 2)
                unordered = Counter(frozenset((row.team1_id, row.team2_id)) for row in rounds)
                self.assertTrue(all(number == 1 for number in unordered.values()))
                self.assertTrue(all(row.bo3_scores is None for row in rounds))

    def test_double_rejects_reserved_stages_without_writing(self):
        for name in ("附加赛", "上区", "下区", "淘汰赛"):
            with self.subTest(group=name):
                match, _ = self.group(name, stage=StageStatus.LEGEND)
                status, result = self.post(match, "double", name)
                self.assertEqual(status, 400)
                self.assertIn("只有挑战者", result["detail"])
                self.assertEqual(self.rounds(match), [])

    def test_single_rejects_knockout_without_writing(self):
        match, _ = self.group("淘汰赛", stage=StageStatus.PLAYOFF)
        status, result = self.post(match, "single", "淘汰赛")
        self.assertEqual(status, 400)
        self.assertIn("BO3", result["detail"])
        self.assertEqual(self.rounds(match), [])

    def test_repeated_generation_preserves_round_ids_times_results_and_numbering(self):
        match, _ = self.group()
        self.assertEqual(self.post(match, "single", "A")[0], 200)
        first = self.rounds(match)[0]
        first.round_number = 27
        first.status = RoundStatus.FINISHED
        first.scheduled_time = datetime(2026, 10, 10, 13)
        first.team1_confirmed = first.team2_confirmed = True
        first.team1_score, first.team2_score, first.winner_id = 13, 9, first.team1_id
        self.db.commit()
        before = self.snapshot(match)
        for mode in ("single", "double"):
            status, result = self.post(match, mode, "A")
            self.assertEqual(status, 400)
            self.assertIn("已生成过对阵", result["detail"])
            self.assertEqual(self.snapshot(match), before)

    def test_legacy_single_rounds_are_not_deleted_reset_or_automatically_filled(self):
        match, _ = self.group()
        match_service.auto_generate_single_round_matches(self.db, match.id, "A")
        before = self.snapshot(match)
        self.assertEqual(len(before), 3)
        for mode in ("single", "double"):
            self.assertEqual(self.post(match, mode, "A")[0], 400)
            self.assertEqual(self.snapshot(match), before)

    def test_missing_empty_whitespace_and_unknown_groups_do_not_write(self):
        match, _ = self.group()
        for mode in ("single", "double"):
            for name, expected in ((None, 422), ("", 422), (" ", 400), ("不存在", 400), ("X" * 21, 422)):
                with self.subTest(mode=mode, group=name):
                    self.assertEqual(self.post(match, mode, name)[0], expected)
                    self.assertEqual(self.rounds(match), [])

    def test_group_names_are_scoped_to_the_requested_match(self):
        match, _ = self.group("A")
        self.group("B")
        for mode in ("single", "double"):
            self.assertEqual(self.post(match, mode, "B")[0], 400)
            self.assertEqual(self.rounds(match), [])

    def test_advanced_or_mixed_stage_groups_cannot_generate_challenger_rounds(self):
        for stage in (StageStatus.LEGEND, StageStatus.PLAYOFF, StageStatus.ELIMINATED):
            match, _ = self.group(stage=stage)
            for mode in ("single", "double"):
                self.assertEqual(self.post(match, mode, "A")[0], 400)
                self.assertEqual(self.rounds(match), [])
        match, teams = self.group()
        self.db.query(TeamProgress).filter_by(match_id=match.id, team_id=teams[-1].id).one().stage = StageStatus.LEGEND
        self.db.commit()
        self.assertEqual(self.post(match, "single", "A")[0], 400)
        self.assertEqual(self.rounds(match), [])

    def test_group_with_one_team_rejects_without_partial_rounds(self):
        match, _ = self.group(count=1)
        for mode in ("single", "double"):
            self.assertEqual(self.post(match, mode, "A")[0], 400)
            self.assertEqual(self.rounds(match), [])

    def test_missing_match_returns_not_found(self):
        for mode in ("single", "double"):
            status, _ = post_generation(self.app, 99999, mode, "A", self.token)
            self.assertEqual(status, 404)
        self.assertEqual(self.db.query(MatchRound).count(), 0)

    def test_http_authentication_and_role_checks_precede_generation(self):
        match, _ = self.group()
        for mode in ("single", "double"):
            status, _ = post_generation(self.app, match.id, mode, "A")
            self.assertEqual(status, 401)
            self.assertEqual(post_generation(self.app, match.id, mode, "A", "invalid-token")[0], 401)
            for role in (UserRole.USER, UserRole.COMMENTATOR):
                user = self.make_user(role=role)
                self.db.commit()
                token = create_access_token(user.id, role.value)
                self.assertEqual(self.post(match, mode, "A", token=token)[0], 403)
            self.assertEqual(self.rounds(match), [])

    def test_reviewer_keeps_existing_generation_permission(self):
        match, teams = self.group()
        reviewer = self.make_user(role=UserRole.REVIEWER)
        self.db.commit()
        token = create_access_token(reviewer.id, "reviewer")
        self.assertEqual(self.post(match, "single", "A", token=token)[0], 200)
        self.assert_double_three_team_group(self.rounds(match), teams)
