"""添加队长联系 QQ 字段；历史队伍保留空值，由队长补录。可重复执行。"""
from sqlalchemy import inspect

from app.database import engine
from app.models.team import Team


def upgrade(bind=engine):
    if bind.dialect.name not in ("sqlite", "mysql", "mariadb"):
        raise RuntimeError("队长 QQ 迁移仅支持 SQLite / MySQL / MariaDB")
    with bind.begin() as connection:
        if bind.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        if not inspect(connection).has_table("teams"):
            Team.__table__.create(connection)
            return {"captain_qq_added": True}
        if "captain_qq" in {column["name"] for column in inspect(connection).get_columns("teams")}:
            return {"captain_qq_added": False}
        connection.exec_driver_sql("ALTER TABLE teams ADD COLUMN captain_qq VARCHAR(12) NULL")
    return {"captain_qq_added": True}


if __name__ == "__main__":
    print(upgrade())
