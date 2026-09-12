"""Add explicit visibility flags. Does not guess BOT identities or hide any data.

Run before deploying the new models: python -m app.migrations.test_data_visibility
"""
from sqlalchemy import inspect


def upgrade(engine):
    if engine.dialect.name not in ("sqlite", "mysql", "mariadb"):
        raise RuntimeError("Visibility migration supports SQLite / MySQL / MariaDB only")
    changed = []
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        schema = inspect(connection)
        if not all(schema.has_table(table) for table in ("users", "teams")):
            raise RuntimeError("Initialize users and teams before running this migration")
        for table in ("users", "teams"):
            if "is_hidden" in {column["name"] for column in schema.get_columns(table)}:
                continue
            quote = connection.dialect.identifier_preparer.quote
            connection.exec_driver_sql(
                f"ALTER TABLE {quote(table)} ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT 0"
            )
            changed.append(table)
    return changed


if __name__ == "__main__":
    from app.database import engine
    print(upgrade(engine))
