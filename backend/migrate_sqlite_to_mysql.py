"""迁移脚本：把 SQLite 旧库数据迁移到 MySQL
用法: python migrate_sqlite_to_mysql.py
"""
import sqlite3
from datetime import datetime

import pymysql

SQLITE_DB = "D:/vs code 库/北林major报名系统/backend/cs2_competition.db"
MYSQL_CFG = dict(
    host="127.0.0.1", port=3306, user="root",
    password="keill000323", database="cs2_competition", charset="utf8mb4",
)

# 表迁移顺序（先父表后子表，保证外键存在）
TABLES = [
    "users", "teams", "team_members",
    "team_applications", "team_invitations",
    "matches", "match_rounds", "team_progress", "registrations",
]

# 需要转大写统一的枚举列（SQLite 里可能存了小写，MySQL ENUM 只认大写 name）
ENUM_COLS = {
    "users": {"role"},
    "teams": {"status"},
    "team_members": {"role"},
    "team_applications": {"status"},
    "team_invitations": {"status"},
    "matches": {"status"},
    "match_rounds": {"status"},
    "team_progress": {"stage"},
    "registrations": {"status"},
}


def main():
    src = sqlite3.connect(SQLITE_DB)
    src.row_factory = sqlite3.Row
    dst = pymysql.connect(**MYSQL_CFG, autocommit=True)
    cur = dst.cursor()

    # 迁移前清空目标表，保证脚本可重复运行（先关外键检查）
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    for table in TABLES:
        cur.execute(f"DELETE FROM `{table}`")
    cur.execute("SET FOREIGN_KEY_CHECKS=1")

    total = 0
    for table in TABLES:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"[跳过] {table}: 0 行")
            continue
        cols = list(rows[0].keys())
        enum_cols = ENUM_COLS.get(table, set())
        # 列名加反引号：rank 是 MySQL 保留关键字
        quoted_cols = ",".join([f"`{c}`" for c in cols])
        placeholders = ",".join(["%s"] * len(cols))
        sql = f"INSERT INTO `{table}` ({quoted_cols}) VALUES ({placeholders})"

        for row in rows:
            values = []
            for c in cols:
                v = row[c]
                if c in enum_cols and isinstance(v, str):
                    v = v.upper()
                elif isinstance(v, datetime):
                    v = v.strftime("%Y-%m-%d %H:%M:%S")
                values.append(v)
            cur.execute(sql, values)

        print(f"[OK]   {table}: {len(rows)} 行")
        total += len(rows)

    src.close()
    dst.close()
    print(f"\n迁移完成，共 {total} 行")


if __name__ == "__main__":
    main()
