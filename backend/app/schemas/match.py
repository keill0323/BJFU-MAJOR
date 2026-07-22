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
    status: str
    register_start: Optional[datetime] = None
    register_end: Optional[datetime] = None
    match_start: Optional[datetime] = None
    registered_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


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
    scheduled_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class RoundUpdateRequest(BaseModel):
    """更新对阵结果请求"""
    team1_score: Optional[int] = None
    team2_score: Optional[int] = None
    winner_id: Optional[int] = None


class LeaderboardItem(BaseModel):
    """排行榜条目"""
    rank: int
    team_id: int
    team_name: str
    wins: int
    losses: int
    total_score: int
    