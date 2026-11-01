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


class WechatSubscriptionAttempt(Base):
    """客户端一次手势授权回调的去重记录；不是微信剩余次数账本。"""
    __tablename__ = "wechat_subscription_attempts"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    attempt_id = Column(String(96), primary_key=True)
    choices_hash = Column(String(64), nullable=False)
    choices = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class WechatSubscriptionState(Base):
    """跨设备保存补订阅建议；grant_version仅用于防竞态，不表示额度。"""
    __tablename__ = "wechat_subscription_states"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    template_id = Column(String(128), primary_key=True)
    grant_version = Column(Integer, nullable=False, default=0)
    last_choice = Column(String(16), nullable=True)
    last_attempt_id = Column(String(96), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    needs_resubscribe = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class WechatSubscriptionDelivery(Base):
    """每条Outbox最多一份发送回执；发送前冻结授权版本，成功才记使用。"""
    __tablename__ = "wechat_subscription_deliveries"
    outbox_id = Column(Integer, ForeignKey("wechat_outbox.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = Column(String(128), nullable=False)
    grant_version = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="sending")
    error_code = Column(String(48), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
