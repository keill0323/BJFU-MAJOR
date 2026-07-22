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
    """队伍信息表

    Attributes:
        id: 队伍ID
        name: 队伍名，唯一
        captain_id: 队长用户ID，外键关联 users 表
        captain: 队长的 User 对象（通过 relationship 关联）
        description: 队伍简介
        status: 审核状态（pending/approved/rejected）
        created_at: 创建时间
        members: 关联的成员列表
    """
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)       #队伍id
    name = Column(String(64), unique=True, nullable=False)
    captain_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    captain = relationship("User", foreign_keys=[captain_id])
    description = Column(String(256), nullable=True)
    status = Column(SAEnum(TeamStatus), default=TeamStatus.PENDING, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    members = relationship("TeamMember", back_populates="team")


class MemberRole(str, enum.Enum):
    """队伍成员身份"""

    CAPTAIN = "captain"
    MEMBER = "member"


class TeamMember(Base):
    """队伍成员信息表

    Attributes:
        id: 入队记录ID
        team_id: 所属队伍ID，外键关联 teams 表
        user_id: 用户ID，外键关联 users 表
        user: 对应的 User 对象（通过 relationship 关联）
        role: 队内角色（captain/member）
        joined_at: 加入时间
        team: 对应的 Team 对象（通过 relationship 关联）
    """
    __tablename__ = "team_members"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)  # 入队记录ID
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="memberships")
    role = Column(SAEnum(MemberRole), default=MemberRole.MEMBER)
    joined_at = Column(DateTime, default=datetime.now, index=True)
    team = relationship("Team", back_populates="members")

