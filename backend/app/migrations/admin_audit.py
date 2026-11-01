"""幂等创建管理员审计表，不回填无法证明的历史操作者。"""
from app.models.admin_audit import AdminAudit


def upgrade(engine):
    AdminAudit.__table__.create(engine, checkfirst=True)


if __name__ == "__main__":
    from app.database import engine
    upgrade(engine)
    print("管理员操作审计表已就绪")
