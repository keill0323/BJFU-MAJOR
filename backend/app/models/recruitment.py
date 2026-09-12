"""招募公告与微信订阅消息的持久化记录。"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from app.database import Base


class RecruitmentPost(Base):
    __tablename__ = "recruitment_posts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True)
    content = Column(String(500), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class WechatSubscription(Base):
    __tablename__ = "wechat_subscriptions"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    template_id = Column(String(128), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class WechatOutbox(Base):
    __tablename__ = "wechat_outbox"
    __table_args__ = (UniqueConstraint("kind", "source_id", name="uq_wechat_event"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(32), nullable=False)
    source_id = Column(Integer, nullable=False)
    template_id = Column(String(128), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    error_code = Column(String(48), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
