"""管理员资料与认证操作审计；不存图片、凭据或账号标识。"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String

from app.database import Base


class AdminAudit(Base):
    __tablename__ = "admin_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 保留 ID 快照，不设置级联外键：用户删除后仍可追查历史操作。
    actor_id = Column(Integer, nullable=False)
    actor_role = Column(String(20), nullable=False)
    target_user_id = Column(Integer, nullable=False)
    action = Column(String(40), nullable=False)
    application_id = Column(Integer, nullable=True)
    before_values = Column(JSON, nullable=False)
    after_values = Column(JSON, nullable=False)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        Index("ix_admin_audits_created_id", "created_at", "id"),
        Index("ix_admin_audits_actor_created", "actor_id", "created_at"),
        Index("ix_admin_audits_target_created", "target_user_id", "created_at"),
        Index("ix_admin_audits_action_created", "action", "created_at"),
    )
