"""Management overview uses existing review rules and bounded aggregate reads."""
from unittest.mock import patch

from tests.support import DatabaseTestCase
from tests.test_manual_champions import request
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.match import MatchRound, Registration, RegistrationStatus, StageStatus, TeamProgress
from app.models.team import TeamStatus
from app.models.user import RankApplication, UserRole
from app.routers.admin import router
from app.services.admin_service import get_todos
from app.services.auth_service import create_access_token, get_pending_rank_applications, get_unverified_users
from app.services.match_service import is_roster_locked
from app.services.team_service import get_team_registration_status


class AdminSummaryTests(DatabaseTestCase):
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

    def registrations(self, match, states, *, progress=None):
        users = [self.make_user() for _ in states]
        team = self.make_team(captain=users[0], members=users[1:])
        for user, status in zip(users, states):
            self.db.add(Registration(match_id=match.id, team_id=team.id, user_id=user.id, status=status))
        if progress is not None:
            self.db.add(TeamProgress(match_id=match.id, team_id=team.id, **progress))
        self.db.flush()
        return team

    def test_zero_counts_and_authenticated_roles(self):
        self.assertEqual(request(self.app, "GET", "/api/admin/todos")[0], 401)
        for role in UserRole:
            user = self.make_user(role=role)
            self.db.commit()
            token = create_access_token(user.id, "admin")
            status, result = request(self.app, "GET", "/api/admin/todos", token=token)
            self.assertEqual(status, 200 if role in (UserRole.ADMIN, UserRole.REVIEWER) else 403)
            if status == 200:
                self.assertEqual(result, {"verification_count": 0, "rank_application_count": 0,
                    "team_count": 0, "registration_count": 0, "total": 0, "registration_matches": []})

    def test_review_counts_match_list_filters_and_exclude_completed_items(self):
        self.make_user(is_verified=False, verify_image=None)
        self.make_user(is_verified=False, verify_image="/uploads/first.png", ai_review_status="pending")
        self.make_user(is_verified=False, verify_image="/uploads/reject.png", ai_review_status="auto_reject")
        self.make_user(is_verified=True, verify_image="/uploads/approved.png")
        user = self.make_user()
        for status in ("pending", "pending", "approved", "rejected"):
            self.db.add(RankApplication(user_id=user.id, status=status, rank_image="private-rank-image"))
        for status in (TeamStatus.PENDING, TeamStatus.PENDING, TeamStatus.APPROVED, TeamStatus.REJECTED):
            self.make_team(status=status)
        self.db.commit()
        result = get_todos(self.db)
        self.assertEqual(result["verification_count"], len(get_unverified_users(self.db)))
        self.assertEqual(result["rank_application_count"], len(get_pending_rank_applications(self.db)))
        self.assertEqual(result["verification_count"], 2)
        self.assertEqual(result["rank_application_count"], 2)
        self.assertEqual(result["team_count"], 2)
        self.assertEqual(result["total"], 6)

    def test_registration_counts_match_team_status_not_member_counts_or_individuals(self):
        first, second = self.make_match(), self.make_match()
        pairs = []
        for match, states, progress in (
            (first, [RegistrationStatus.PENDING] * 3, None),
            (first, [RegistrationStatus.APPROVED] * 2, None),
            (first, [RegistrationStatus.APPROVED] * 2, {}),
            (first, [RegistrationStatus.REJECTED] * 2, None),
            (first, [RegistrationStatus.APPROVED, RegistrationStatus.REJECTED], None),
            (second, [RegistrationStatus.PENDING], {"group_name": "A"}),
            (second, [RegistrationStatus.REJECTED], {}),
        ):
            pairs.append((match, self.registrations(match, states, progress=progress)))
        individual = self.make_user()
        self.db.add(Registration(match_id=first.id, user_id=individual.id, status=RegistrationStatus.PENDING))
        self.db.commit()
        result = get_todos(self.db)
        self.assertEqual(result["registration_count"], 5)
        self.assertEqual(result["total"], 5)
        actual = {row["match_id"]: row for row in result["registration_matches"]}
        self.assertEqual(actual[first.id]["count"], 3)
        self.assertEqual(actual[second.id]["count"], 2)
        for match in (first, second):
            expected = sum(get_team_registration_status(self.db, match.id, team.id) == RegistrationStatus.PENDING
                           for event_, team in pairs if event_.id == match.id)
            self.assertEqual(actual[match.id]["count"], expected)
            self.assertEqual(actual[match.id]["roster_locked"], is_roster_locked(self.db, match.id))

    def test_all_roster_lock_signals_match_detail_without_hardcoded_groups(self):
        matches = []
        for progress in ({}, {"seed": 1}, {"group_name": "自定义第七组"},
                         {"stage": StageStatus.LEGEND}, {"stage": StageStatus.PLAYOFF}, {"stage": StageStatus.ELIMINATED}):
            match = self.make_match()
            self.registrations(match, [RegistrationStatus.PENDING], progress=progress)
            matches.append(match)
        match = self.make_match()
        self.registrations(match, [RegistrationStatus.PENDING])
        self.db.add(MatchRound(match_id=match.id, round_number=1))
        matches.append(match)
        self.db.commit()
        result = get_todos(self.db)
        rows = {row["match_id"]: row for row in result["registration_matches"]}
        self.assertFalse(rows[matches[0].id]["roster_locked"])
        self.assertTrue(all(rows[match.id]["roster_locked"] for match in matches[1:]))
        for match in matches:
            self.assertEqual(rows[match.id]["roster_locked"], is_roster_locked(self.db, match.id))

    def test_summary_uses_two_readonly_queries_even_for_many_events_and_no_private_payloads(self):
        for _ in range(24):
            match = self.make_match()
            self.registrations(match, [RegistrationStatus.PENDING] * 2)
        self.db.commit()
        statements = []

        def collect(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", collect)
        try:
            result = get_todos(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", collect)
        self.assertEqual(result["registration_count"], 24)
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT") for statement in statements))
        self.assertTrue(all("users.nickname" not in statement and "users.avatar" not in statement for statement in statements))
        for row in result["registration_matches"]:
            self.assertEqual(set(row), {"match_id", "match_name", "count", "roster_locked"})
