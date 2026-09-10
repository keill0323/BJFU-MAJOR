"""Upgrade champion snapshots for manual history, preserving existing honours.

Run explicitly from the backend directory: python -m app.migrations.manual_champions
Deployments should stop API writers and take a database backup before this schema
upgrade. MySQL DDL commits independently; each change is safe to retry.
"""
from sqlalchemy import MetaData, inspect

from app.models.hall import ChampionSnapshot


TABLE = "champion_snapshots"
TEMP_TABLE = "champion_snapshots_manual_upgrade"
NULLABLE_FIELDS = ("final_round_id", "champion_team_id", "runner_up_name", "champion_score", "runner_up_score")
ADDED_FIELDS = ("manual_created_by", "manual_updated_by", "manual_created_at", "manual_note", "manual_version")


def upgrade(engine):
    """Accept an explicit engine so tests and maintenance use isolated databases."""
    if engine.dialect.name not in ("sqlite", "mysql", "mariadb"):
        raise RuntimeError("手动冠军迁移仅支持 MySQL / MariaDB / SQLite")
    table = ChampionSnapshot.__table__
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        schema = inspect(connection)
        if not schema.has_table(TABLE):
            table.create(connection)
            return {"created": True, "upgraded": False}
        columns = {column["name"]: column for column in schema.get_columns(TABLE)}
        missing = [name for name in ADDED_FIELDS if name not in columns]
        nonnullable = [name for name in NULLABLE_FIELDS if not columns[name]["nullable"]]
        if not missing and not nonnullable:
            return {"created": False, "upgraded": False}
        quote = connection.dialect.identifier_preparer.quote
        if engine.dialect.name == "sqlite":
            # SQLite cannot remove NOT NULL in place. Copy under a write lock and
            # preserve private notes, IDs, custom indexes and triggers on reruns.
            unknown = set(columns) - set(table.columns.keys())
            if unknown or schema.has_table(TEMP_TABLE):
                raise RuntimeError("冠军表含未知字段或迁移临时表，请先检查数据库，未修改原表")
            extras = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND type IN ('index', 'trigger') AND sql IS NOT NULL",
                (TABLE,),
            ).scalars().all()
            temporary = table.to_metadata(MetaData(), name=TEMP_TABLE)
            temporary.indexes.clear()
            temporary.create(connection)
            names = list(table.columns.keys())
            values = [quote(name) if name in columns else ("0" if name == "manual_version" else "NULL") for name in names]
            connection.exec_driver_sql(
                f"INSERT INTO {quote(TEMP_TABLE)} ({', '.join(map(quote, names))}) "
                f"SELECT {', '.join(values)} FROM {quote(TABLE)}"
            )
            connection.exec_driver_sql(f"DROP TABLE {quote(TABLE)}")
            connection.exec_driver_sql(f"ALTER TABLE {quote(TEMP_TABLE)} RENAME TO {quote(TABLE)}")
            for statement in extras:
                connection.exec_driver_sql(statement)
            for index in table.indexes:
                index.create(connection, checkfirst=True)
        else:
            for name in missing:
                column_type = table.columns[name].type.compile(dialect=connection.dialect)
                nullable = "NOT NULL DEFAULT 0" if name == "manual_version" else "NULL"
                connection.exec_driver_sql(
                    f"ALTER TABLE {quote(TABLE)} ADD COLUMN {quote(name)} {column_type} {nullable}"
                )
            for name in nonnullable:
                column_type = table.columns[name].type.compile(dialect=connection.dialect)
                connection.exec_driver_sql(
                    f"ALTER TABLE {quote(TABLE)} MODIFY COLUMN {quote(name)} {column_type} NULL"
                )
        return {"created": False, "upgraded": True}


if __name__ == "__main__":
    from app.database import engine

    print(upgrade(engine))
