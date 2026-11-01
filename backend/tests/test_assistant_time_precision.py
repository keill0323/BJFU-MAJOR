"""MySQL精度迁移仅以方言编译/模拟连接验证，不连接实际MySQL。"""
from types import SimpleNamespace
from unittest.mock import Mock
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.engine import Connection
from tests.support import DatabaseTestCase
from app.models.assistant_session import AssistantTurn
from app.migrations.assistant import _upgrade_turn_time_precision


class AssistantTimePrecisionTests(DatabaseTestCase):
    def connection(self, precision):
        connection = Mock(spec=Connection)
        connection.dialect = SimpleNamespace(name='mysql')
        connection.execute.return_value.scalar.return_value = precision
        return connection

    def test_new_mysql_timestamp_keeps_microseconds_without_changing_sqlite_type(self):
        kind = AssistantTurn.__table__.c.created_at.type
        self.assertEqual(kind.compile(dialect=mysql.dialect()), 'DATETIME(6)')
        self.assertEqual(kind.compile(dialect=sqlite.dialect()), 'DATETIME')

    def test_existing_mysql_seconds_upgrade_once_and_precise_column_is_untouched(self):
        connection = self.connection(0)
        self.assertTrue(_upgrade_turn_time_precision(connection))
        connection.exec_driver_sql.assert_called_once_with(
            'ALTER TABLE assistant_turns MODIFY COLUMN created_at DATETIME(6) NOT NULL')
        connection.execute.return_value.scalar.return_value = 6
        self.assertFalse(_upgrade_turn_time_precision(connection))
        self.assertEqual(connection.exec_driver_sql.call_count, 1)
        self.assertEqual(connection.execute.call_args.args[1],
                         {'table_name': 'assistant_turns', 'column_name': 'created_at'})

    def test_non_mysql_does_not_query_or_alter_schema(self):
        connection = self.connection(0)
        connection.dialect = SimpleNamespace(name='sqlite')
        self.assertFalse(_upgrade_turn_time_precision(connection))
        connection.execute.assert_not_called()
        connection.exec_driver_sql.assert_not_called()

    def test_unknown_precision_stops_instead_of_blindly_altering(self):
        connection = self.connection(None)
        with self.assertRaises(RuntimeError):
            _upgrade_turn_time_precision(connection)
        connection.exec_driver_sql.assert_not_called()
