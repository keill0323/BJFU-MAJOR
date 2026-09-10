"""
用户相关请求/响应模型
Author: keill
Since: 2026-7-22
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class UserLoginRequest(BaseModel):
    """微信登录请求"""
    code: str       # 微信临时登录凭证


class UserInfo(BaseModel):
    """用户基本信息响应"""
    id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    student_id: Optional[str] = None
    is_verified: bool = False
    rank: Optional[str] = None
    individual_rating: int = 0
    role: str
    identity: Optional[str] = None    # new_student新生/senior老登
    verify_image: Optional[str] = None   # 学信网截图URL
    verify_reject_reason: Optional[str] = None   # 认证驳回原因
    avatar: Optional[str] = None         # 头像URL
    rank_image: Optional[str] = None     # 段位截图URL（认证页缩略图回显）
    user_description: Optional[str] = None   # 个人介绍（人才市场自我介绍）
    # AI 审核结果（auto_pass 自动通过 / auto_reject 自动驳回 / pending 待人工复核）
    ai_review_status: Optional[str] = None
    ai_review_reason: Optional[str] = None

    class Config:
        from_attributes = True


class UserUpdateRequest(BaseModel):
    """用户更新资料请求"""
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    

class AdminUpdateUserRequest(BaseModel):
    """管理员修改用户资料"""
    student_id: Optional[str] = None
    is_verified: Optional[bool] = None
    rank: Optional[str] = None
    individual_rating: Optional[int] = None
    identity: Optional[str] = None       # new_student新生/senior老登（研1/博1由管理员认证）
    verify_image: Optional[str] = None   # 传空字符串表示清除截图
    verify_reject_reason: Optional[str] = None   # 认证驳回原因（传空字符串表示清除）


class UpdateRoleRequest(BaseModel):
    """修改用户角色请求"""
    role: str


class TokenResponse(BaseModel):
    """登录响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class VerifyListItem(BaseModel):
    """待认证审核用户项"""
    id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    student_id: Optional[str] = None
    verify_image: Optional[str] = None
    is_verified: bool = False
    created_at: Optional[datetime] = None
    # AI 审核结果（管理后台可见，含置信度）
    ai_review_status: Optional[str] = None
    ai_review_reason: Optional[str] = None
    ai_review_confidence: Optional[float] = None

    class Config:
        from_attributes = True


class RankApplicationInfo(BaseModel):
    """段位更新申请"""
    id: int
    user_id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    current_rank: Optional[str] = None   # 当前段位
    rank_image: Optional[str] = None     # 新段位截图
    ai_rank: Optional[str] = None        # AI 识别段位
    ai_confidence: Optional[float] = None
    ai_reason: Optional[str] = None
    reject_reason: Optional[str] = None   # 驳回原因
    status: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
    