"""
赛事信息
Author: keill
Since: 2026-7-22
"""

from datetime import datetime
import enum

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum as SAEnum, Text
from sqlalchemy.orm import relationship

from app.database import Base

class MatchStatus(str, enum.Enum):
    """赛事状态"""
    DRAFT = "draft"                     # 未开始
    REGISTERING = "registering"         # 报名中
    IN_PROGRESS = "in_progress"         # 进行中
    FINISHED = "finished"               # 已结束


class Match(Base):
    """赛事信息表"""
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)  # 赛事编号ID
    name = Column(String(128), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    max_teams = Column(Integer, default=16)
    team_size = Column(Integer, default=5)
    status = Column(SAEnum(MatchStatus), default=MatchStatus.DRAFT)
    register_start = Column(DateTime, nullable=True)
    register_end = Column(DateTime, nullable=True)
    match_start = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)


class RoundStatus(str, enum.Enum):
    """对阵状态"""
    PENDING = "pending"             # 待开始
    IN_PROGRESS = "in_progress"     # 进行中
    FINISHED = "finished"           # 已结束


class MatchRound(Base):
    """对阵表"""
    __tablename__ = "match_rounds"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    team1_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team2_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team1_score = Column(Integer, default=0)
    team2_score = Column(Integer, default=0)
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    status = Column(SAEnum(RoundStatus), default=RoundStatus.PENDING)
    scheduled_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable = False)
    status = Column(SAEnum(RoundStatus), default = RoundStatus.PENDING)
    scheduled_time = Column(DateTime, nullable = True)
    created_at = Column(DateTime, default = datetime.now)
    