"""添加自动失效标记，并使当前已入队用户的历史 pending 记录失效。可重复执行。"""
from datetime import datetime
from sqlalchemy import inspect, text
from app.database import engine
from app.models.team import TeamApplication, TeamInvitation


def upgrade(bind=engine):
    if bind.dialect.name not in ("sqlite", "mysql", "mariadb"):
        raise RuntimeError("入队申请迁移仅支持 SQLite / MySQL / MariaDB")
    changed = {}
    with bind.begin() as connection:
        if bind.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        for model in (TeamApplication, TeamInvitation):
            table = model.__tablename__
            if not inspect(connection).has_table(table):
                model.__table__.create(connection)
            elif "invalidated_at" not in {c["name"] for c in inspect(connection).get_columns(table)}:
                quote = connection.dialect.identifier_preparer.quote
                connection.exec_driver_sql(f"ALTER TABLE {quote(table)} ADD COLUMN invalidated_at DATETIME NULL")
            # 表名固定来自模型；不修改有效自由人的申请，不触及成员和赛事数据。
            result = connection.execute(text(f"UPDATE {table} SET invalidated_at=:now "
                f"WHERE invalidated_at IS NULL AND status='PENDING' AND EXISTS "
                f"(SELECT 1 FROM team_members WHERE team_members.user_id={table}.user_id)"), {"now": datetime.now()})
            changed[table] = result.rowcount
    return changed


if __name__ == "__main__":
    print(upgrade())
