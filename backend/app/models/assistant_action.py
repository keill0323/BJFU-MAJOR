"""用户确认卡片的持久记录；和报名在同一事务提交，不存模型正文。"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AssistantAction(Base):
    __tablename__ = "assistant_actions"
    __table_args__ = (Index("ix_assistant_action_owner_created", "user_id", "created_at"),)

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    mode = Column(String(16), nullable=False)
    state = Column(String(24), nullable=False, default="pending")
    snapshot_hash = Column(String(64), nullable=False)
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    expires_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
