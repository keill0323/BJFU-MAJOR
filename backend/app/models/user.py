"""
用户数据模型
Author: keill
Since: 2026-07-21
"""

from datetime import datetime
import enum

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.database import Base


class UserRole(str, enum.Enum):
    """用户角色枚举类"""

    USER = "user"
    ADMIN = "admin"
    REVIEWER = "reviewer"
    COMMENTATOR = "commentator"


class User(Base):
    """用户表，记录所有注册用户信息

    Attributes:
        id: 主键，自增
        wx_openid: 微信小程序唯一标识
        nickname: 用户昵称
        game_id: 游戏ID
        student_id: 学号（需上传截图人工审核）
        verify_image: 学信网/教务系统截图URL
        is_verified: 管理员是否审核通过
        role: user/admin/reviewer/commentator
        created_at: 注册时间
        memberships: 用户加入的所有队伍记录（通过 relationship 关联）
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)  # 用户ID
    wx_openid = Column(String(64), unique=True, index=True, nullable=True)
    nickname = Column(String(64), nullable=True)
    game_id = Column(String(64), nullable=True)
    student_id = Column(String(64), unique=True, nullable=True)
    verify_image = Column(String(256), nullable=True)
    is_verified = Column(Boolean, default=False, index=True)
    role = Column(SAEnum(UserRole), default=UserRole.USER)
    created_at = Column(DateTime, default=datetime.now, index=True)
    memberships = relationship("TeamMember", back_populates="user")
