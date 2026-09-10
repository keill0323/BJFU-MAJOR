"""数据库事务内的行锁；调用方负责统一提交或回滚。"""

from sqlalchemy import update
from sqlalchemy.orm import Session


def lock_row(db: Session, model, row_id: int):
    """锁定并重新读取一行，锁一直保留到当前事务结束。

    SQLite 忽略 SELECT FOR UPDATE，先执行不改变主键的 UPDATE，
    通过数据库写锁串行化不同连接/进程，随后读取最新状态。
    MySQL 等数据库使用原生行锁。
    """
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(model)
            .where(model.id == row_id)
            .values(id=model.id)
            .execution_options(synchronize_session=False)
        )
    return (
        db.query(model)
        .filter(model.id == row_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
