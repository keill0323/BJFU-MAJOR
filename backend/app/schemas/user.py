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
    verify_image: Optional[str] = None   # 学信网截图URL

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
    verify_image: Optional[str] = None   # 传空字符串表示清除截图


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

    class Config:
        from_attributes = True
    