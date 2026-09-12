"""Database filtering must match public badges/identity before cursor pagination."""
import json
from datetime import datetime
from unittest.mock import patch
from urllib.parse import urlencode

from tests.support import DatabaseTestCase
from tests.test_hall import get
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.match import Registration
from app.models.user import User
from app.routers.recruitment import router
from app.services import recruitment_service as hall


class RecruitmentFilterTests(DatabaseTestCase):
    def ids(self, **filters):
        return {p["id"] for p in hall.public_players(self.db, limit=50, **filters)["items"]}

    def test_each_standard_rank_is_exact_and_plus_levels_are_distinct(self):
        ranks = ["D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++"]
        players = {rank: self.make_user(rank=rank) for rank in ranks}
        for rank in ("a", "A ", "A+ ", "D+", "C+++", " A++"):
            self.make_user(rank=rank)
        self.db.commit()
        for rank, user in players.items():
            with self.subTest(rank=rank):
                self.assertEqual(self.ids(rank=rank), {user.id})

    def test_s_categories_cover_legacy_zero_and_each_star_boundary(self):
        groups = {
            "s": ["S", "S0", "S1", "S9", "S00", "S009"],
            "s_gold": ["S10", "S24", "S010"],
            "s_diamond": ["S25", "S49", "S025"],
            "s_demon": ["S50", "S050"],
            "unranked": [None, "", "S51", "S-1", "S1.0", "S１", "s1", "S1 ", "S+1", "S1x"],
        }
        expected = {key: {self.make_user(rank=rank).id for rank in ranks}
                    for key, ranks in groups.items()}
        self.db.commit()
        for key, users in expected.items():
            with self.subTest(rank=key):
                self.assertEqual(self.ids(rank=key), users)

    def test_student_verification_does_not_override_a_valid_rank(self):
        player = self.make_user(rank="A+", is_verified=False)
        unranked = self.make_user(rank=None, is_verified=True)
        self.db.commit()
        self.assertEqual(self.ids(rank="A+", identity="unknown"), {player.id})
        self.assertEqual(self.ids(rank="unranked"), {unranked.id})

    def test_identity_preserves_manual_priority_and_changes_at_year_boundary(self):
        boundary = self.make_user(student_id="251234567", identity=None)
        old = self.make_user(student_id="241234567", identity=None)
        future = self.make_user(student_id="991234567", identity=None)
        manual_new = self.make_user(student_id="221234567", identity="new_student")
        manual_old = self.make_user(student_id="261234567", identity="senior")
        master = self.make_user(student_id="graduate-format", identity="new_student")
        self.db.commit()
        with patch("app.services.auth_service.datetime") as clock:
            for year in (2026, 2027):
                with self.subTest(year=year):
                    clock.now.return_value = datetime(year, 1, 1)
                    new_ids = {future.id, manual_new.id, master.id}
                    old_ids = {old.id, manual_old.id}
                    (new_ids if year == 2026 else old_ids).add(boundary.id)
                    self.assertEqual(self.ids(identity="new_student"), new_ids)
                    self.assertEqual(self.ids(identity="senior"), old_ids)
                    self.assertEqual(self.ids(identity="unknown"), set())
                    for identity in ("new_student", "senior"):
                        self.assertTrue(all(p["identity"] == identity for p in
                                            hall.public_players(self.db, identity=identity)["items"]))

    def test_unknown_includes_non_ascii_non_nine_digit_and_unverified_identities(self):
        unknown_ids = {self.make_user(student_id=value, identity=None).id for value in
                       (None, "", "26123456", "2612345670", "２６１２３４５６７", "26abc4567", "26123456 ")}
        for manual in ("new_student", "senior", None):
            unknown_ids.add(self.make_user(is_verified=False, identity=manual).id)
        for manual in ("unknown", "NEW_STUDENT", "new_student "):
            unknown_ids.add(self.make_user(identity=manual).id)
        automatic = self.make_user(student_id="269876543", identity="")
        manual = self.make_user(student_id="non-standard", identity="senior")
        self.db.commit()
        with patch("app.services.auth_service.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 1)
            self.assertEqual(self.ids(identity="unknown"), unknown_ids)
            self.assertEqual(self.ids(identity="new_student"), {automatic.id})
            self.assertEqual(self.ids(identity="senior"), {manual.id})

    def test_filters_combine_with_search_before_pagination_and_keep_cursor_stable(self):
        expected = []
        for i in range(9):
            match = i % 3 == 0
            player = self.make_user(rank="A+" if i % 3 != 1 else "A",
                                    identity="new_student" if i % 3 != 2 else "senior",
                                    nickname="Forest" if i != 3 else "Other", game_id="Forest-AWP")
            if match:
                expected.append(player.id)
        # Newer users that match only some conditions must not consume the page.
        self.make_user(rank="A+", identity="new_student", nickname="Other", game_id="Other")
        self.db.commit()
        filters = dict(rank="A+", identity="new_student", keyword="Forest", limit=1)
        first = hall.public_players(self.db, **filters)
        self.assertEqual([p["id"] for p in first["items"]], [expected[-1]])
        self.assertTrue(first["has_more"])
        self.make_user(rank="A+", identity="new_student", nickname="Forest")
        self.db.commit()
        second = hall.public_players(self.db, before_id=first["next_cursor"], **filters)
        third = hall.public_players(self.db, before_id=second["next_cursor"], **filters)
        self.assertEqual([p["id"] for p in second["items"]], [expected[1]])
        self.assertEqual([p["id"] for p in third["items"]], [expected[0]])
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_cursor"])

    def test_filtered_directory_preserves_memberships_privacy_and_data(self):
        captain = self.make_user(rank="A+", identity="senior", verify_image="private-image",
                                 ai_student_id="private-candidate", user_description="Public intro")
        team = self.make_team(captain=captain, name="Visible team")
        hidden = self.make_user(rank="A+", identity="senior", is_hidden=True)
        hidden_team_captain = self.make_user(rank="A+", identity="senior")
        self.make_team(captain=hidden_team_captain, name="Private team", is_hidden=True)
        free = self.make_user(rank="A+", identity="senior")
        self.db.commit()
        before = [tuple(row) for row in self.db.execute(select(User.__table__).order_by(User.id))]
        result = hall.public_players(self.db, rank="A+", identity="senior")
        entries = {p["id"]: p for p in result["items"]}
        self.assertEqual(set(entries), {captain.id, hidden_team_captain.id, free.id})
        self.assertTrue(entries[captain.id]["has_team"])
        self.assertEqual(entries[captain.id]["team_name"], team.name)
        self.assertTrue(entries[hidden_team_captain.id]["has_team"])
        self.assertIsNone(entries[hidden_team_captain.id]["team_name"])
        self.assertFalse(entries[free.id]["has_team"])
        self.assertNotIn(hidden.id, entries)
        for item in entries.values():
            self.assertEqual(set(item), {"id", "nickname", "game_id", "avatar", "rank", "individual_rating",
                                         "is_verified", "identity", "user_description", "has_team", "team_name"})
        payload = json.dumps(result)
        for secret in (captain.student_id, "private-image", "private-candidate", "Private team"):
            self.assertNotIn(secret, payload)
        self.assertEqual(before, [tuple(row) for row in self.db.execute(select(User.__table__).order_by(User.id))])
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_empty_results_echo_filters_and_search_never_uses_student_id(self):
        self.make_user(rank="A+", identity="senior", student_id="261234567", nickname="100%")
        self.db.commit()
        empty = hall.public_players(self.db, keyword="261234567", rank="A+", identity="senior")
        self.assertEqual(empty, dict(items=[], has_more=False, next_cursor=None,
                                     filters={"rank": "A+", "identity": "senior"}))
        self.assertEqual(len(hall.public_players(self.db, keyword="%", rank="A+")["items"]), 1)
        self.assertEqual(hall.public_players(self.db)["filters"], {"rank": "", "identity": ""})

    def test_service_rejects_invalid_filters_without_querying_users(self):
        for kwargs in ({"rank": "A+++"}, {"rank": "A "}, {"rank": None},
                       {"identity": "freshman"}, {"identity": None}):
            with self.subTest(filters=kwargs), self.assertRaises(HTTPException) as error:
                hall.public_players(self.db, **kwargs)
            self.assertEqual(error.exception.status_code, 422)

    def test_filters_are_parameterized_and_applied_in_one_bounded_sql_query(self):
        self.make_user(rank="S10", identity="senior", nickname="100%")
        self.db.commit()
        statements = []

        def capture(state):
            if state.is_select:
                statements.append(state.statement)

        event.listen(self.db, "do_orm_execute", capture)
        try:
            result = hall.public_players(self.db, limit=1, rank="s_gold", identity="senior", keyword="100%")
        finally:
            event.remove(self.db, "do_orm_execute", capture)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(statements), 1)
        for dialect in (sqlite.dialect(), mysql.dialect(), MariaDBDialect()):
            with self.subTest(dialect=dialect.name):
                compiled = statements[0].compile(dialect=dialect)
                sql = str(compiled)
                self.assertIn("WHERE", sql)
                self.assertIn("LIMIT", sql)
                self.assertNotIn("100%", sql)
                self.assertNotIn("REGEXP", sql)
                self.assertIn(2, compiled.params.values())


class RecruitmentFilterRouteTests(DatabaseTestCase):
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

    def test_encoded_plus_filters_and_optional_parameters_reach_service(self):
        player = self.make_user(rank="A++", identity="senior")
        self.make_user(rank="A+", identity="senior")
        self.db.commit()
        query = urlencode(dict(rank="A++", identity="senior"))
        self.assertIn("%2B%2B", query)
        status, body = get(self.app, "/api/recruitment/players?" + query)
        self.assertEqual(status, 200, body)
        self.assertEqual([p["id"] for p in body["items"]], [player.id])
        self.assertEqual(body["filters"], {"rank": "A++", "identity": "senior"})
        for suffix in ("", "?rank=&identity="):
            status, body = get(self.app, "/api/recruitment/players" + suffix)
            self.assertEqual(status, 200)
            self.assertEqual(len(body["items"]), 2)
            self.assertEqual(body["filters"], {"rank": "", "identity": ""})

    def test_invalid_rank_identity_and_unencoded_plus_are_422(self):
        for query in ("rank=A+", "rank=S10", "rank=a", "rank=A%252B", "rank=all", "rank=null",
                      "identity=freshman", "identity=NEW_STUDENT", "identity=senior%20",
                      "rank=A%27%20OR%201%3D1", "limit=0", "limit=51", "before_id=0"):
            with self.subTest(query=query):
                status, body = get(self.app, "/api/recruitment/players?" + query)
                self.assertEqual(status, 422, body)
