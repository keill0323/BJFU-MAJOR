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
        rating: 队伍水平分
    """
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="队伍ID")
    name = Column(String(64), unique=True, nullable=False, comment="队伍名（唯一）")
    captain_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="队长用户ID")
    captain = relationship("User", foreign_keys=[captain_id])
    description = Column(String(256), nullable=True, comment="队伍简介")
    status = Column(SAEnum(TeamStatus), default=TeamStatus.PENDING, index=True, comment="审核状态 pending/approved/rejected")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="创建时间")
    members = relationship("TeamMember", back_populates="team")
    rating = Column(Integer, default=1000, comment="队伍水平分")


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

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="入队记录ID")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, comment="所属队伍ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, comment="用户ID（一人只能在一支队伍）")
    user = relationship("User", back_populates="memberships")
    role = Column(SAEnum(MemberRole), default=MemberRole.MEMBER, comment="队内角色 captain/member")
    joined_at = Column(DateTime, default=datetime.now, index=True, comment="加入时间")
    team = relationship("Team", back_populates="members")

    # 非数据库字段：从关联的 user 取值，供序列化（TeamMemberInfo 需要 nickname/game_id）
    @property
    def nickname(self):
        return self.user.nickname if self.user else None

    @property
    def game_id(self):
        return self.user.game_id if self.user else None


class ApplicationStatus(str, enum.Enum):
    """申请状态"""

    PENDING = "pending"       # 待审核
    APPROVED = "approved"     # 已通过
    REJECTED = "rejected"     # 已拒绝


class TeamApplication(Base):
    """入队申请表

    用户主动申请加入队伍，队长审核通过后加入 team_members。

    Attributes:
        id: 申请记录ID
        team_id: 目标队伍ID
        user_id: 申请人ID
        message: 申请留言（可选）
        status: 申请状态（pending/approved/rejected）
        created_at: 申请时间
        team: 目标队伍对象（relationship）
        user: 申请人对象（relationship）
    """
    __tablename__ = "team_applications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="申请记录ID")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, comment="目标队伍ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="申请人ID")
    message = Column(String(200), nullable=True, comment="申请留言（可选）")
    status = Column(SAEnum(ApplicationStatus), default=ApplicationStatus.PENDING, index=True, comment="申请状态 pending/approved/rejected")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="申请时间")
    team = relationship("Team")
    user = relationship("User")


class TeamInvitation(Base):
    """入队邀请表

    队长主动邀请玩家入队，玩家同意后加入 team_members。
    与 TeamApplication 对称：申请是玩家发起，邀请是队长发起。

    Attributes:
        id: 邀请记录ID
        team_id: 发起邀请的队伍ID
        user_id: 被邀请人ID
        message: 邀请留言（可选）
        status: 邀请状态（pending/approved/rejected）
        created_at: 邀请时间
        team: 队伍对象（relationship）
        user: 被邀请人对象（relationship）
    """
    __tablename__ = "team_invitations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="邀请记录ID")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, comment="发起邀请的队伍ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="被邀请人ID")
    message = Column(String(200), nullable=True, comment="邀请留言（可选）")
    status = Column(SAEnum(ApplicationStatus), default=ApplicationStatus.PENDING, index=True, comment="邀请状态 pending/approved/rejected")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="邀请时间")
    team = relationship("Team")
    user = relationship("User")

