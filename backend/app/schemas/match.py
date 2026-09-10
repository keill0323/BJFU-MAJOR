"""
赛事相关请求/响应模型
Author: keill
Since: 2026-7-22
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class MatchCreateRequest(BaseModel):
    """赛事创建"""
    name: str
    description: Optional[str] = None
    max_teams: int = 16
    team_size: int = 5
    match_type: str = "major"   # freshman新生赛 / major大赛
    register_start: Optional[datetime] = None
    register_end: Optional[datetime] = None
    match_start: Optional[datetime] = None


class MatchInfo(BaseModel):
    """赛事信息响应"""
    id: int
    name: str
    description: Optional[str] = None
    max_teams: int
    team_size: int
    match_type: str = "major"
    status: str
    register_start: Optional[datetime] = None
    register_end: Optional[datetime] = None
    match_start: Optional[datetime] = None
    registered_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class MatchDetailInfo(MatchInfo):
    """详情增加参赛名单是否已锁定；状态由实际编排记录计算，无需新增数据库列。"""
    roster_locked: bool = False


class RoundInfo(BaseModel):
    """对阵信息响应"""
    id: int
    match_id: int
    round_number: int
    team1_id: Optional[int] = None
    team2_id: Optional[int] = None
    team1_name: Optional[str] = None
    team2_name: Optional[str] = None
    team1_score: int
    team2_score: int
    winner_id: Optional[int] = None
    status: str
    group_name: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    bo3_scores: Optional[List[dict]] = None   # 淘汰赛 BO3 三局小分
    # 时间协商
    team1_confirmed: bool = False
    team2_confirmed: bool = False
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    schedule_status: str = "unconfirmed"   # unconfirmed未约定 / pending待确认 / confirmed已确定

    class Config:
        from_attributes = True


class RoundUpdateRequest(BaseModel):
    """更新对阵结果请求"""
    team1_score: Optional[int] = None
    team2_score: Optional[int] = None
    winner_id: Optional[int] = None
    bo3_scores: Optional[List[dict]] = None   # 淘汰赛 BO3 三局小分（每局 {t1, t2}）


class MatchStatusUpdateRequest(BaseModel):
    """更新赛事状态请求"""
    status: str  # draft/registering/in_progress/finished


class RegistrationWindowUpdateRequest(BaseModel):
    """完整替换报名时间限制；null 表示不限制该端时间，不改变赛事状态。"""
    register_start: Optional[datetime]
    register_end: Optional[datetime]


class LeaderboardItem(BaseModel):
    """排行榜条目"""
    rank: int
    team_id: int
    team_name: str
    wins: int
    losses: int
    total_score: int


class RegistrationDetail(BaseModel):
    """赛事报名队伍详情（含进度：阶段/分组/种子）"""
    team_id: int
    team_name: str
    rating: int = 0
    registration_status: str
    stage: Optional[str] = None
    group_name: Optional[str] = None
    seed: int = 0
    captain_name: Optional[str] = None
    member_count: int = 0
    wins: int = 0      # 当前阶段胜场
    losses: int = 0    # 当前阶段负场
    diff: int = 0      # 当前阶段净胜分


class MatchAdminDetail(BaseModel):
    """管理后台赛事详情：赛事信息 + 报名队伍进度 + 对阵列表"""
    match: MatchDetailInfo
    teams: List[RegistrationDetail] = []
    rounds: List[RoundInfo] = []


class StageWindowInfo(BaseModel):
    """阶段时间窗口"""
    id: int
    match_id: int
    group_name: str
    window_start: datetime
    window_end: datetime

    class Config:
        from_attributes = True


class StageWindowUpdate(BaseModel):
    """设置/更新阶段时间窗口请求"""
    window_start: datetime
    window_end: datetime


class RoundScheduleRequest(BaseModel):
    """队长提交约定比赛时间请求"""
    scheduled_time: datetime


class RoundScheduleActionRequest(BaseModel):
    """确认/拒绝约定时间请求（带客户端所见的提议时间，用于防竞态）"""
    expected_time: Optional[datetime] = None
