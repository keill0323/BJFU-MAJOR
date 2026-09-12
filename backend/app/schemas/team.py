"""
队伍相关请求/响应模型
Author: keill
Since: 2026-7-22
"""

from datetime import datetime
import re
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_captain_qq(value) -> str:
    """只接受符合格式的 QQ 字符串；接口与服务入口共用此校验。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("请填写队长 QQ 号")
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,11}", value.strip()):
        raise ValueError("QQ 号须为 5–12 位数字，且不能以 0 开头")
    return value.strip()


class TeamContactRequest(BaseModel):
    """队长补录或更新用于队伍联系的 QQ，不允许清空。"""
    captain_qq: str = Field(..., description="队长 QQ 号（5–12 位数字，不以 0 开头）")

    @model_validator(mode="before")
    @classmethod
    def require_captain_qq(cls, values):
        if isinstance(values, dict) and "captain_qq" not in values:
            raise ValueError("请填写队长 QQ 号")
        return values

    @field_validator("captain_qq", mode="before")
    @classmethod
    def validate_captain_qq(cls, value):
        return normalize_captain_qq(value)


class TeamCreateRequest(TeamContactRequest):
    """创建队伍请求"""
    name: str = Field(..., min_length=1, max_length=20, description="队伍名（1-20字符）")
    description: Optional[str] = None


class TeamMemberInfo(BaseModel):
    """队伍成员信息"""
    id: int
    user_id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    avatar: Optional[str] = None
    rating: Optional[int] = None
    rank: Optional[str] = None
    identity: Optional[str] = None
    is_verified: bool = False
    role: str

    class Config:
        from_attributes = True


class TeamListInfo(BaseModel):
    """队伍列表项（列表页用，不带成员详情）"""
    id: int
    name: str
    captain_id: int
    captain_name: Optional[str] = None
    description: Optional[str] = None
    status: str
    member_count: int = 0
    rating: Optional[int] = None
    logo: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TeamInfo(BaseModel):
    """队伍信息响应"""
    id: int
    name: str
    captain_id: int
    captain_qq: Optional[str] = None
    description: Optional[str] = None
    status: str
    members: List[TeamMemberInfo] = []
    member_count: int = 0
    logo: Optional[str] = None
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
    user_description: Optional[str] = None   # 个人介绍（人才市场自由人详情）

    class Config:
        from_attributes = True


class UpdateMarketDescription(BaseModel):
    """人才市场自我介绍"""
    user_description: str


class ApplyJoinRequest(BaseModel):
    """申请加入队伍请求"""
    team_id: int
    message: Optional[str] = Field(None, max_length=200)


class RecruitByStudentRequest(BaseModel):
    """队长按学号拉人入队请求"""
    team_id: int
    student_id: str


class TeamApplicationInfo(BaseModel):
    """入队申请信息"""
    id: int
    team_id: int
    user_id: int
    nickname: Optional[str] = None
    game_id: Optional[str] = None
    avatar: Optional[str] = None
    rank: Optional[str] = None
    individual_rating: int = 0
    identity: Optional[str] = None
    is_verified: bool = False
    user_description: Optional[str] = None
    message: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class InviteRequest(BaseModel):
    """队长邀请入队请求"""
    user_id: int
    message: Optional[str] = Field(None, max_length=200)


class InvitationInfo(BaseModel):
    """入队邀请信息（我收到的）"""
    id: int
    team_id: int
    team_name: Optional[str] = None
    captain_name: Optional[str] = None
    message: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
