"""
用户数据模型
Author: keill
Since: 2026-07-21
"""

from datetime import datetime
import enum

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, Enum as SAEnum
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
        individual_rating: 个人水平得分()
        rank: 个人段位
        user_description: 个人介绍
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="主键，自增")
    wx_openid = Column(String(64), unique=True, index=True, nullable=True, comment="微信小程序唯一标识")
    nickname = Column(String(64), nullable=True, comment="用户昵称")
    game_id = Column(String(64), nullable=True, comment="游戏ID")
    student_id = Column(String(64), unique=True, nullable=True, comment="学号（需上传截图人工审核）")
    verify_image = Column(String(256), nullable=True, comment="学信网/教务系统截图URL")
    verify_reject_reason = Column(String(200), nullable=True, comment="认证驳回原因（用户可见）")
    is_verified = Column(Boolean, default=False, index=True, comment="管理员是否审核通过")
    role = Column(SAEnum(UserRole), default=UserRole.USER, comment="用户角色 user/admin/reviewer/commentator")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="注册时间")
    memberships = relationship("TeamMember", back_populates="user")
    individual_rating = Column(Integer, default=0, comment="个人水平得分")
    rank = Column(String(10), nullable=True, comment="个人段位")
    identity = Column(String(20), nullable=True, comment="用户身份 new_student新生/senior老登（新生由学号自动识别，研1博1由管理员认证）")
    user_description = Column(String(200), nullable=True, comment="个人介绍")
    ai_review_status = Column(String(20), nullable=True, comment="AI审核状态 auto_pass/auto_reject/pending")
    ai_review_reason = Column(String(500), nullable=True, comment="AI判断理由")
    ai_review_confidence = Column(Float, default=0, nullable=True, comment="AI置信度 0~1")
    rank_image = Column(String(256), nullable=True, comment="段位截图URL（完美/5E平台）")
    avatar = Column(String(256), nullable=True, comment="头像URL")


class RankApplication(Base):
    """段位更新申请表

    用户已有段位后，再次上传段位截图申请更新段位，需管理员审批后生效。
    首次上传（无段位）不走此表，由 upload-rank 接口直接自动更新。

    Attributes:
        id: 申请ID
        user_id: 申请人ID
        rank_image: 新段位截图URL
        ai_rank: AI 识别的段位
        ai_confidence: AI 置信度 0~1
        ai_reason: AI 判断理由
        status: 申请状态（pending/approved/rejected）
        created_at: 申请时间
        user: 申请人对象
    """
    __tablename__ = "rank_applications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, comment="申请ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="申请人ID")
    rank_image = Column(String(256), nullable=True, comment="新段位截图URL")
    ai_rank = Column(String(10), nullable=True, comment="AI识别的段位")
    ai_confidence = Column(Float, default=0, nullable=True, comment="AI置信度 0~1")
    ai_reason = Column(String(500), nullable=True, comment="AI判断理由")
    status = Column(String(20), default="pending", index=True, comment="申请状态 pending/approved/rejected")
    reject_reason = Column(String(200), nullable=True, comment="驳回原因")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="申请时间")
    user = relationship("User")

    # 非数据库字段：从关联 user 取值，供序列化
    @property
    def nickname(self):
        return self.user.nickname if self.user else None

    @property
    def game_id(self):
        return self.user.game_id if self.user else None

    @property
    def current_rank(self):
        return self.user.rank if self.user else None
