"""幂等建立定向通知表，不生成或发送历史消息。"""
from app.models.admin_notice import AdminNoticeBatch, AdminNotice


def upgrade(engine):
    AdminNoticeBatch.__table__.create(engine, checkfirst=True)
    AdminNotice.__table__.create(engine, checkfirst=True)


if __name__ == "__main__":
    from app.database import engine
    upgrade(engine)
    print("admin_notices ready")
