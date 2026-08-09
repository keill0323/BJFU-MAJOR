"""
赛事信息
Author: keill
Since: 2026-7-22
"""

from datetime import datetime
import enum

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum as SAEnum, Text, UniqueConstraint
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

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="赛事ID")
    name = Column(String(128), unique=True, nullable=False, comment="赛事名称（唯一）")
    description = Column(Text, nullable=True, comment="赛事描述")
    max_teams = Column(Integer, default=16, comment="最大参赛队伍数")
    team_size = Column(Integer, default=5, comment="每队人数上限")
    status = Column(SAEnum(MatchStatus), default=MatchStatus.DRAFT, comment="赛事状态 draft/registering/in_progress/finished")
    register_start = Column(DateTime, nullable=True, comment="报名开始时间")
    register_end = Column(DateTime, nullable=True, comment="报名截止时间")
    match_start = Column(DateTime, nullable=True, comment="比赛开始时间")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="创建时间")


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

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="对阵记录ID")
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, comment="所属赛事ID")
    round_number = Column(Integer, nullable=False, comment="轮次")
    team1_id = Column(Integer, ForeignKey("teams.id"), nullable=True, comment="队伍1ID")
    team2_id = Column(Integer, ForeignKey("teams.id"), nullable=True, comment="队伍2ID")
    team1_score = Column(Integer, default=0, comment="队伍1得分")
    team2_score = Column(Integer, default=0, comment="队伍2得分")
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True, comment="胜者队伍ID")
    status = Column(SAEnum(RoundStatus), default=RoundStatus.PENDING, comment="对阵状态 pending/in_progress/finished")
    scheduled_time = Column(DateTime, nullable=True, comment="预定比赛时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    group_name = Column(String(20), nullable=True, comment="组别(A组/上半区等)")
    bo3_scores = Column(Text, nullable=True, comment="淘汰赛BO3三局小分(JSON数组字符串)")


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

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="进度记录ID")
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, comment="所属赛事ID")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, comment="队伍ID")
    stage = Column(SAEnum(StageStatus), default=StageStatus.CHALLENGER, comment="当前阶段 challenger/legend/playoff/eliminated")
    group_name = Column(String(20), nullable=True, comment="所在小组(A组/上半区等)")
    seed = Column(Integer, default=0, comment="种子排名")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


class RegistrationStatus(str, enum.Enum):
    """报名审核状态"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Registration(Base):
    """赛事报名表，支持队伍报名和个人报名

    Attributes:
        id: 报名记录ID
        match_id: 赛事ID
        team_id: 队伍ID（队伍报名时填写）
        user_id: 用户ID（个人报名时填写）
        status: 审核状态
        created_at: 报名时间
    """
    __tablename__ = "registrations"
    __table_args__ = (
        # 一人对同一赛事只能有一条报名记录，防止个人/队伍报名重复
        UniqueConstraint("match_id", "user_id", name="uq_registration_match_user"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="报名记录ID")
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, comment="赛事ID")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, comment="队伍ID(队伍报名时填写)")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="用户ID(个人报名时填写)")
    status = Column(SAEnum(RegistrationStatus), default=RegistrationStatus.PENDING, comment="审核状态 pending/approved/rejected")
    created_at = Column(DateTime, default=datetime.now, comment="报名时间")
