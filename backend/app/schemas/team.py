"""
队伍相关请求/响应模型
Author: keill
Since: 2026-7-22
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class TeamCreateRequest(BaseModel):
    """创建队伍请求"""
    name: str
    description: Optional[str] = None


class TeamMemberInfo(BaseModel):
    """队伍成员信息"""
    id: int
    user_id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    role: str

    class Config:
        from_attributes = True


class TeamInfo(BaseModel):
    """队伍信息响应"""
    id: int
    name: str
    captain_id: int
    description: Optional[str] = None
    status: str
    members: List[TeamMemberInfo] = []
    member_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class JoinTeamRequest(BaseModel):
    """加入队伍请求"""
    team_id: int
    user_id: int


class AssignMemberRequest(BaseModel):
    """管理员分配队员"""
    team_id: int
    user_id: int


class TalentMarketItem(BaseModel):
    """人才市场"""
    id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    individual_rating: Optional[int] = None
    rank: Optional[str] = None

    class Config:
        from_attributes = True


class UpdateMarketDescription(BaseModel):
    """人才市场自我介绍"""
    user_description: str