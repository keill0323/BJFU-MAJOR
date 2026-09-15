"""管理员定向站内通知；发送批次与每位收件人的已读状态分开保存。"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index
from app.database import Base


class AdminNoticeBatch(Base):
    __tablename__ = "admin_notice_batches"
    __table_args__ = (UniqueConstraint("sender_id", "request_id", name="uq_admin_notice_request"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    request_id = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    title = Column(String(40), nullable=False)
    content = Column(String(500), nullable=False)
    recipient_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)


class AdminNotice(Base):
    __tablename__ = "admin_notices"
    __table_args__ = (
        UniqueConstraint("batch_id", "user_id", name="uq_admin_notice_recipient"),
        Index("ix_admin_notice_inbox", "user_id", "read_at", "id"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("admin_notice_batches.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(DateTime, nullable=True)
