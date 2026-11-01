"""助手幂等建表和MySQL轮次时间精度升级；不删除或回填业务数据。"""
from sqlalchemy import text
from sqlalchemy.engine import Connection
from app.database import engine
from app.models.assistant_action import AssistantAction
from app.models.assistant_summary import AssistantSummary
from app.models.assistant_session import (AssistantConversation, AssistantTurn,
                                           AssistantToolTrace, AssistantKnowledgeRevision)


def _upgrade_turn_time_precision(bind):
    """仅升级助手轮次时间精度；历史已丢失的微秒无法恢复。"""
    if bind.dialect.name not in ('mysql', 'mariadb'):
        return False

    def upgrade_connection(connection):
        precision = connection.execute(text('''SELECT DATETIME_PRECISION FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name AND COLUMN_NAME = :column_name'''),
            {'table_name': 'assistant_turns', 'column_name': 'created_at'}).scalar()
        if precision is None:
            raise RuntimeError('Assistant turn timestamp precision could not be verified')
        if int(precision) >= 6:
            return False
        # 标识符固定，不拼接外部输入；MySQL DDL有隐式提交，部署前备份。
        connection.exec_driver_sql('ALTER TABLE assistant_turns MODIFY COLUMN created_at DATETIME(6) NOT NULL')
        return True

    if isinstance(bind, Connection):
        return upgrade_connection(bind)
    with bind.begin() as connection:
        return upgrade_connection(connection)


def upgrade(bind=engine):
    for model in (AssistantAction, AssistantConversation, AssistantTurn,
                  AssistantToolTrace, AssistantKnowledgeRevision, AssistantSummary):
        model.__table__.create(bind, checkfirst=True)
    _upgrade_turn_time_precision(bind)


if __name__ == '__main__':
    upgrade()
    print('赛事助手数据表已就绪；功能开关未自动启用')
