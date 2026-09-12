"""Private schedule events; deleted matches remain readable as historical notices."""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from app.database import Base


class ScheduleNotification(Base):
    __tablename__ = "schedule_notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    match_id = Column(Integer, nullable=False)
    round_id = Column(Integer, nullable=False)
    kind = Column(String(24), nullable=False)
    match_name = Column(String(128), nullable=False)
    team1_name = Column(String(64), nullable=False)
    team2_name = Column(String(64), nullable=False)
    scheduled_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)
    read_at = Column(DateTime, nullable=True)
    __table_args__ = (
        Index("ix_schedule_notice_user_id", "user_id", "id"),
        Index("ix_schedule_notice_user_read", "user_id", "read_at"),
    )
