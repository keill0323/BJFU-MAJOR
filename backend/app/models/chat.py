"""双人私聊：正文、同步事件与已读位置分别持久化。"""
from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.mysql import DATETIME
from app.database import Base

Seq = BigInteger().with_variant(Integer, "sqlite")
Timestamp = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    __table_args__ = (UniqueConstraint("user_low", "user_high", name="uq_chat_pair"),)
    id = Column(Integer, primary_key=True)
    user_low = Column(Integer, nullable=False, index=True)
    user_high = Column(Integer, nullable=False, index=True)
    initiator_id = Column(Integer, nullable=False, index=True)
    last_seq = Column(Seq, nullable=False, default=0)
    event_floor = Column(Seq, nullable=False, default=0)
    created_at = Column(Timestamp, nullable=False)
    updated_at = Column(Timestamp, nullable=False, index=True)


class ChatMember(Base):
    __tablename__ = "chat_members"
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, primary_key=True)
    read_seq = Column(Seq, nullable=False, default=0)
    pinned = Column(Boolean, nullable=False, default=False)
    muted = Column(Boolean, nullable=False, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_chat_message_seq"),
        UniqueConstraint("conversation_id", "sender_id", "client_id", name="uq_chat_client_message"),
        Index("ix_chat_sender_time", "sender_id", "created_at"),
        Index("ix_chat_cleanup", "state", "created_at"),
        Index("ix_chat_unread", "conversation_id", "state", "read_at", "sender_id"),
    )
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Seq, nullable=False)
    sender_id = Column(Integer, nullable=False)
    client_id = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="text")
    content = Column(Text, nullable=True)
    state = Column(String(16), nullable=False, default="active")
    created_at = Column(Timestamp, nullable=False)
    purged_at = Column(Timestamp, nullable=True)
    read_at = Column(Timestamp, nullable=True)


class ChatEvent(Base):
    __tablename__ = "chat_events"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_chat_event_seq"),
        Index("ix_chat_pending_event", "notified", "id"),
    )
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Seq, nullable=False)
    kind = Column(String(24), nullable=False)
    message_id = Column(Integer, nullable=True)  # 无正文；清理消息后事件仍可补收。
    created_at = Column(Timestamp, nullable=False)
    notified = Column(Boolean, nullable=False, default=False)


class ChatBlock(Base):
    __tablename__ = "chat_blocks"
    user_id = Column(Integer, primary_key=True)
    target_id = Column(Integer, primary_key=True)
    created_at = Column(Timestamp, nullable=False)


class ChatReport(Base):
    __tablename__ = "chat_reports"
    __table_args__ = (UniqueConstraint("reporter_id", "message_id", name="uq_chat_report"),)
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    conversation_id = Column(Integer, nullable=False)
    reason = Column(String(300), nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    resolution = Column(String(300), nullable=True)
    created_at = Column(Timestamp, nullable=False)
    resolved_at = Column(Timestamp, nullable=True)


class ChatSanction(Base):
    __tablename__ = "chat_sanctions"
    user_id = Column(Integer, primary_key=True)
    until = Column(Timestamp, nullable=False)
    reason = Column(String(300), nullable=False)
    actor_id = Column(Integer, nullable=False)


class ChatAudit(Base):
    __tablename__ = "chat_audit_logs"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False)
    target_id = Column(Integer, nullable=False)
    reason = Column(String(300), nullable=False, default="")
    created_at = Column(Timestamp, nullable=False, index=True)
