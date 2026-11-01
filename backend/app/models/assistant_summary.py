"""助手工作上下文的可核查摘要；正文仍以短期会话原记录为准。"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.dialects.mysql import DATETIME
from app.database import Base

PRECISE_DATETIME = DateTime().with_variant(DATETIME(fsp=6), 'mysql', 'mariadb')

class AssistantSummary(Base):
    __tablename__ = 'assistant_summaries'
    conversation_id = Column(String(36), ForeignKey('assistant_conversations.id', ondelete='CASCADE'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    scope_json = Column(Text, nullable=False, default='{}')
    scope_epoch_at = Column(PRECISE_DATETIME, nullable=True)
    scope_epoch_turn_id = Column(String(36), nullable=True)
    summary_json = Column(Text, nullable=False, default='{}')
    source_created_at = Column(PRECISE_DATETIME, nullable=True)
    source_turn_id = Column(String(36), nullable=True)
    source_fingerprint = Column(String(64), nullable=False, default='')
    version = Column(Integer, nullable=False, default=0)
    created_at = Column(PRECISE_DATETIME, nullable=False, default=datetime.utcnow)
    updated_at = Column(PRECISE_DATETIME, nullable=False, default=datetime.utcnow)
