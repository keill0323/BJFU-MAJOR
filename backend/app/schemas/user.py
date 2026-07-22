"""
用户相关请求/响应模型
Author: keill
Since: 2026-7-22
"""

from pydantic import BaseModel
from typing import Optional


class UserLoginRequest(BaseModel):
    """微信登录请求"""
    code: str       # 微信临时登录凭证


class UserInfo(BaseModel):
    """用户基本信息响应"""
    id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    student_id: Optional[str] = None
    role: str

    class Config:
        from_attributes = True
        