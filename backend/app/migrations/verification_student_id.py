"""Add a separate, non-unique candidate student ID without rewriting user data.

Run explicitly from backend: python -m app.migrations.verification_student_id
MySQL DDL commits independently. This additive upgrade can be safely rerun and
must run before starting application code that reads User.ai_student_id.
"""
from sqlalchemy import inspect

from app.models.user import User


TABLE = "users"
COLUMN = "ai_student_id"


def upgrade(engine):
    """Use only the supplied engine, allowing isolated tests and explicit rollout."""
    if engine.dialect.name not in ("sqlite", "mysql", "mariadb"):
        raise RuntimeError("候选学号迁移仅支持 MySQL / MariaDB / SQLite")
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        schema = inspect(connection)
        if not schema.has_table(TABLE):
            User.__table__.create(connection)
            return {"created": True, "upgraded": False}
        if COLUMN in {column["name"] for column in schema.get_columns(TABLE)}:
            return {"created": False, "upgraded": False}
        quote = connection.dialect.identifier_preparer.quote
        column_type = User.__table__.c[COLUMN].type.compile(dialect=connection.dialect)
        # ADD COLUMN preserves existing values, indexes, references and any
        # custom fields. Existing rows deliberately keep a NULL candidate.
        connection.exec_driver_sql(
            f"ALTER TABLE {quote(TABLE)} ADD COLUMN {quote(COLUMN)} {column_type} NULL"
        )
        return {"created": False, "upgraded": True}


if __name__ == "__main__":
    from app.database import engine

    print(upgrade(engine))
