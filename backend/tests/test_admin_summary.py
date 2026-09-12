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
from app.routers.auth import router as auth_router
from app.services.admin_service import get_todos
from app.services.auth_service import (
    create_access_token, get_pending_rank_applications, get_unverified_users, update_verify_image,
)
from app.services.match_service import is_roster_locked
from app.services.team_service import get_team_registration_status


class AdminSummaryTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.include_router(auth_router)

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
                    "team_count": 0, "registration_count": 0, "total": 0, "registration_matches": [],
                    "blocked_registration_count": 0, "blocked_registration_matches": []})

    def test_review_counts_match_list_filters_and_exclude_completed_items(self):
        self.make_user(is_verified=False, verify_image=None)
        self.make_user(is_verified=False, verify_image="")
        self.make_user(is_verified=False, verify_image="   ")
        self.make_user(is_verified=False, verify_image="/uploads/first.png", ai_review_status="pending")
        self.make_user(is_verified=False, verify_image="/uploads/reject.png", ai_review_status="auto_reject")
        self.make_user(is_verified=False, verify_image="/uploads/legacy.png", ai_review_status=None)
        self.make_user(is_verified=False, verify_image="/uploads/manual.png", ai_review_status="manual_reject")
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
        self.assertEqual(result["verification_count"], 3)
        self.assertEqual(result["rank_application_count"], 2)
        self.assertEqual(result["team_count"], 2)
        self.assertEqual(result["total"], 7)

    def test_manual_rejection_with_retained_image_leaves_queue_until_resubmission(self):
        admin = self.make_user(role=UserRole.ADMIN)
        user = self.make_user(is_verified=False, verify_image="/uploads/evidence.png", ai_review_status="pending")
        self.db.commit()
        token = create_access_token(admin.id, "admin")
        user_id = user.id

        def assert_queue(expected_ids):
            status, items = request(self.app, "GET", "/api/auth/admin/verify-list", token=token)
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in items], expected_ids)
            status, summary = request(self.app, "GET", "/api/admin/todos", token=token)
            self.assertEqual(status, 200)
            self.assertEqual(summary["verification_count"], len(expected_ids))
            self.assertEqual(summary["total"], len(expected_ids))

        assert_queue([user_id])
        # The API permits retaining evidence for history when a human rejects it.
        status, rejected = request(self.app, "PUT", f"/api/auth/admin/users/{user_id}",
            {"is_verified": False, "verify_reject_reason": "请重新提交清晰凭证"}, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(rejected["verify_image"], "/uploads/evidence.png")
        self.assertEqual(rejected["ai_review_status"], "manual_reject")
        assert_queue([])

        self.db.expire_all()
        resubmitted = update_verify_image(self.db, user_id, "/uploads/resubmitted.png")
        self.assertIsNone(resubmitted.verify_reject_reason)
        self.assertEqual(resubmitted.ai_review_status, "pending")
        assert_queue([user_id])

        status, _ = request(self.app, "PUT", f"/api/auth/admin/users/{user_id}",
            {"is_verified": True}, token=token)
        self.assertEqual(status, 200)
        assert_queue([])

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
        self.assertEqual(result["registration_count"], 3)
        self.assertEqual(result["blocked_registration_count"], 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual([row["match_id"] for row in result["registration_matches"]], [first.id])
        self.assertEqual([row["match_id"] for row in result["blocked_registration_matches"]], [second.id])
        actual = {row["match_id"]: row for row in result["registration_matches"] + result["blocked_registration_matches"]}
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
        self.assertEqual(result["registration_count"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["blocked_registration_count"], len(matches) - 1)
        rows = {row["match_id"]: row for row in result["registration_matches"] + result["blocked_registration_matches"]}
        self.assertFalse(rows[matches[0].id]["roster_locked"])
        self.assertTrue(all(rows[match.id]["roster_locked"] for match in matches[1:]))
        for match in matches:
            self.assertEqual(rows[match.id]["roster_locked"], is_roster_locked(self.db, match.id))

    def test_only_locked_pending_records_are_visible_without_an_actionable_badge_or_data_changes(self):
        admin = self.make_user(role=UserRole.ADMIN)
        match = self.make_match()
        team = self.registrations(match, [RegistrationStatus.PENDING] * 2, progress={"seed": 1})
        self.db.commit()
        status, result = request(self.app, "GET", "/api/admin/todos",
                                 token=create_access_token(admin.id, "admin"))
        self.assertEqual(status, 200)
        self.assertEqual(result["registration_count"], 0)
        self.assertEqual(result["registration_matches"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["blocked_registration_count"], 1)
        self.assertEqual(result["blocked_registration_matches"], [
            {"match_id": match.id, "match_name": match.name, "count": 1, "roster_locked": True},
        ])
        self.db.expire_all()
        states = self.db.query(Registration.status).filter_by(match_id=match.id, team_id=team.id).all()
        self.assertEqual([row.status for row in states], [RegistrationStatus.PENDING] * 2)
        self.assertEqual(self.db.query(TeamProgress).filter_by(match_id=match.id, team_id=team.id).one().seed, 1)

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
