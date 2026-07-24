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
    """赛事信息表

    Attributes:
        id: 赛事ID
        name: 赛事名称，唯一
        description: 赛事描述
        max_teams: 最大参赛队伍数
        team_size: 每队人数上限
        status: 赛事状态（draft/registering/in_progress/finished）
        register_start: 报名开始时间
        register_end: 报名截止时间
        match_start: 比赛开始时间
        created_at: 创建时间
    """
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
    """对阵表

    Attributes:
        id: 对阵记录ID
        match_id: 所属赛事ID，外键关联 matches 表
        round_number: 第几轮（1=淘汰赛第一轮，2=第二轮...
        team1_id: 队伍1 ID
        team2_id: 队伍2 ID
        team1_score: 队伍1得分
        team2_score: 队伍2得分
        winner_id: 胜者队伍ID
        status: 对阵状态（pending/in_progress/finished）
        scheduled_time: 预定比赛时间
        created_at: 创建时间
        group_name: 组别(A组/上半区等)
    """
    __tablename__ = "match_rounds"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    round_number = Column(Integer, nullable=False)
    team1_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team2_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team1_score = Column(Integer, default=0)
    team2_score = Column(Integer, default=0)
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    status = Column(SAEnum(RoundStatus), default=RoundStatus.PENDING)
    scheduled_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    group_name = Column(String(20), nullable=True)


class StageStatus(str, enum.Enum):
    """队伍赛事阶段"""
    CHALLENGER = "challenger"       # 挑战者组
    LEGEND = "legend"               # 传奇组
    PLAYOFF = "playoff"             # 淘汰赛
    ELIMINATED = "eliminated"       # 已淘汰


class TeamProgress(Base):
    """队伍赛事进度表

    Attributes:
        id: 进度记录ID
        match_id: 所属赛事ID，外键关联 matches 表
        team_id: 队伍ID，外键关联 teams 表
        stage: 当前阶段（challenger/legend/playoff/eliminated）
        group_name: 所在小组（A组/上半区等）
        seed: 种子排名
        created_at: 创建时间
    """

    __tablename__ = "team_progress"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    stage = Column(SAEnum(StageStatus), default=StageStatus.CHALLENGER)
    group_name = Column(String(20), nullable=True)
    seed = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    