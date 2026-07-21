"""
队伍信息
Author: keill
Since: 2026-07-21
"""

from datetime import datetime
import enum

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.database import Base


class TeamStatus(str, enum.Enum):
    """队伍状态"""

    PENDING = "pending"       # 待审核
    APPROVED = "approved"     # 已通过
    REJECTED = "rejected"     # 已驳回


class Team(Base):
    """队伍信息表"""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)       #队伍id
    name = Column(String(64), unique=True, nullable=False)
    captain_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(String(256), nullable=True)
    status = Column(SAEnum(TeamStatus), default=TeamStatus.PENDING, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    members = relationship("TeamMember", back_populates="team")


class MemberRole(str, enum.Enum):
    """队伍成员身份"""

    CAPTAIN = "captain"
    MEMBER = "member"


class TeamMember(Base):
    """队伍成员信息表"""
    __tablename__ = "team_members"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)  # 入队记录ID
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(SAEnum(MemberRole), default=MemberRole.MEMBER)
    joined_at = Column(DateTime, default=datetime.now, index=True)
    team = relationship("Team", back_populates="members")

