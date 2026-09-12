"""Candidate student IDs upgrade independently of verified IDs and user history."""
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import DatabaseTestCase

from pydantic import ValidationError
from sqlalchemy import MetaData, create_engine, event, inspect
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
from sqlalchemy.exc import IntegrityError

from app.migrations.verification_student_id import upgrade
from app.models.user import User, UserRole
from app.schemas.user import (
    AdminUpdateUserRequest, RecognizeStudentIdRequest, RecognizeStudentIdResponse,
    UserInfo, VerifyListItem,
)


class VerificationStudentIdMigrationTests(DatabaseTestCase):
    def test_empty_database_creates_user_table_and_repeated_upgrade_is_noop(self):
        engine = create_engine("sqlite://")
        try:
            self.assertEqual(upgrade(engine), {"created": True, "upgraded": False})
            self.assertEqual(upgrade(engine), {"created": False, "upgraded": False})
            column = next(c for c in inspect(engine).get_columns("users") if c["name"] == "ai_student_id")
            self.assertTrue(column["nullable"])
            self.assertEqual(column["type"].length, 64)
        finally:
            engine.dispose()

    def test_create_all_schema_needs_no_upgrade_and_candidates_do_not_reserve_verified_ids(self):
        self.assertEqual(upgrade(self.engine), {"created": False, "upgraded": False})
        owner = self.make_user(student_id="260123456")
        candidates = [self.make_user(student_id=None, is_verified=False, ai_student_id=owner.student_id)
                      for _ in range(2)]
        self.db.commit()
        ids = [user.id for user in candidates]
        self.assertEqual(upgrade(self.engine), {"created": False, "upgraded": False})
        self.db.expire_all()
        for user_id in ids:
            user = self.db.get(User, user_id)
            self.assertIsNone(user.student_id)
            self.assertEqual(user.ai_student_id, "260123456")
            self.assertFalse(user.is_verified)
        # Only the confirmed field retains its original uniqueness guarantee.
        with self.assertRaises(IntegrityError):
            self.make_user(student_id="260123456")
        self.db.rollback()
        self.assertEqual(self.db.query(User).count(), 3)

    def test_legacy_upgrade_preserves_all_user_values_indexes_and_references(self):
        engine = create_engine("sqlite://")

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        legacy = User.__table__.to_metadata(MetaData())
        legacy._columns.remove(legacy.c.ai_student_id)
        try:
            legacy.create(engine)
            with engine.begin() as connection:
                connection.execute(legacy.insert(), [
                    {"id": 11, "wx_openid": "legacy-one", "nickname": "已认证用户",
                     "student_id": "250123456", "is_verified": True, "role": UserRole.REVIEWER,
                     "rank": "A+", "individual_rating": 28, "identity": "senior",
                     "verify_image": "/uploads/old.png", "verify_reject_reason": None,
                     "ai_review_status": "manual_pass", "ai_review_reason": "旧审核记录",
                     "ai_review_confidence": 0.96, "created_at": datetime(2025, 10, 1, 14, 30)},
                    {"id": 12, "wx_openid": "legacy-two", "nickname": "待审核用户",
                     "student_id": None, "is_verified": False, "role": UserRole.USER,
                     "rank": None, "individual_rating": 0, "identity": None,
                     "verify_image": "/uploads/pending.png", "verify_reject_reason": "历史原因",
                     "ai_review_status": "pending", "ai_review_reason": "旧识别结果",
                     "ai_review_confidence": 0.42, "created_at": datetime(2026, 9, 11, 9, 30)},
                ])
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN custom_archive_note TEXT")
                connection.exec_driver_sql("UPDATE users SET custom_archive_note = 'custom history' WHERE id = 11")
                connection.exec_driver_sql("CREATE INDEX custom_user_nickname ON users(nickname)")
                connection.exec_driver_sql("CREATE TABLE user_reference (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id))")
                connection.exec_driver_sql("INSERT INTO user_reference (id, user_id) VALUES (1, 11)")
                original_rows = [dict(row) for row in connection.exec_driver_sql("SELECT * FROM users ORDER BY id").mappings()]
            original_indexes = inspect(engine).get_indexes("users")
            original_unique = inspect(engine).get_unique_constraints("users")
            self.assertEqual(upgrade(engine), {"created": False, "upgraded": True})
            self.assertEqual(inspect(engine).get_indexes("users"), original_indexes)
            self.assertEqual(inspect(engine).get_unique_constraints("users"), original_unique)
            with engine.begin() as connection:
                upgraded_rows = [dict(row) for row in connection.exec_driver_sql("SELECT * FROM users ORDER BY id").mappings()]
                self.assertTrue(all(row.pop("ai_student_id") is None for row in upgraded_rows))
                self.assertEqual(upgraded_rows, original_rows)
                self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
                self.assertEqual(connection.exec_driver_sql("SELECT user_id FROM user_reference").scalar_one(), 11)
                connection.exec_driver_sql("UPDATE users SET ai_student_id = '250123456' WHERE id = 12")
            self.assertEqual(upgrade(engine), {"created": False, "upgraded": False})
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("SELECT ai_student_id FROM users WHERE id = 12").scalar_one(), "250123456")
                self.assertIsNone(connection.exec_driver_sql("SELECT student_id FROM users WHERE id = 12").scalar_one())
        finally:
            engine.dispose()

    def test_mysql_and_mariadb_use_nullable_add_column_without_rebuilding_users(self):
        # Exercise each real SQL dialect without requiring a live database.
        for dialect in (mysql_dialect(), MariaDBDialect()):
            with self.subTest(dialect=dialect.name):
                statements = []
                columns = [{"name": "id"}, {"name": "student_id"}]
                schema = Mock()
                schema.has_table.return_value = True
                schema.get_columns.side_effect = lambda _table: columns

                def execute(sql):
                    statements.append(sql)
                    columns.append({"name": "ai_student_id"})

                connection = SimpleNamespace(dialect=dialect, exec_driver_sql=execute)
                engine = SimpleNamespace(dialect=dialect, begin=lambda: nullcontext(connection))
                with patch("app.migrations.verification_student_id.inspect", return_value=schema):
                    self.assertEqual(upgrade(engine), {"created": False, "upgraded": True})
                    self.assertEqual(upgrade(engine), {"created": False, "upgraded": False})
                self.assertEqual(statements, ["ALTER TABLE users ADD COLUMN ai_student_id VARCHAR(64) NULL"])

    def test_review_schemas_expose_candidate_separately_and_recognition_requires_image_version(self):
        user = self.make_user(student_id=None, ai_student_id="260123456", is_verified=False)
        for schema in (UserInfo, VerifyListItem):
            result = schema.model_validate(user).model_dump()
            self.assertIsNone(result["student_id"])
            self.assertEqual(result["ai_student_id"], "260123456")
            self.assertFalse(result["is_verified"])
        self.assertIsNone(AdminUpdateUserRequest(is_verified=True).expected_verify_image)
        request = RecognizeStudentIdRequest(expected_verify_image="/uploads/current.png")
        self.assertEqual(request.expected_verify_image, "/uploads/current.png")
        with self.assertRaises(ValidationError):
            RecognizeStudentIdRequest()
        response = RecognizeStudentIdResponse(student_id=None, confidence=0, reason="未识别到学号",
                                              verify_image=request.expected_verify_image)
        self.assertIsNone(response.student_id)
