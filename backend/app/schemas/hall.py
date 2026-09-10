"""Explicit allowlists for the two public Hall of Fame feeds."""
from datetime import datetime, timedelta, timezone
import re
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChampionMember(BaseModel):
    user_id: Optional[int] = None
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    rank: Optional[str] = None


class ChampionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match_id: int
    match_name: str
    match_type: Optional[str] = None
    match_available: bool = True
    event_date: Optional[datetime] = None
    awarded_at: Optional[datetime] = None
    champion_team_id: Optional[int] = None
    champion_name: str
    champion_logo: Optional[str] = None
    runner_up_name: Optional[str] = None
    champion_score: Optional[int] = None
    runner_up_score: Optional[int] = None
    roster: list[ChampionMember]
    snapshot_source: str


def _image_url(value):
    if value is None or value == "":
        return None
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError("图片地址不合法")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//"):
        return value
    if parsed.scheme.lower() in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
        return value
    raise ValueError("图片地址仅支持站内路径或 HTTP/HTTPS")


class ManualChampionMember(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: Optional[int] = Field(None, gt=0, strict=True)
    nickname: str = Field(min_length=1, max_length=64)
    avatar: Optional[str] = Field(None, max_length=256)
    rank: Optional[str] = Field(None, max_length=10)

    _validate_avatar = field_validator("avatar")(_image_url)

    @field_validator("rank")
    @classmethod
    def valid_rank(cls, value):
        if value in (None, ""):
            return None
        if not re.fullmatch(r"D|[CBA]\+{0,2}|S(?:[0-9]|[1-4][0-9]|50)?", value):
            raise ValueError("段位不合法")
        return value


class ManualChampionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    champion_team_id: Optional[int] = Field(None, gt=0, strict=True)
    champion_name: str = Field(min_length=1, max_length=64)
    champion_logo: Optional[str] = Field(None, max_length=255)
    event_date: datetime
    runner_up_name: Optional[str] = Field(None, max_length=64)
    champion_score: Optional[int] = Field(None, ge=0, le=999, strict=True)
    runner_up_score: Optional[int] = Field(None, ge=0, le=999, strict=True)
    roster: list[ManualChampionMember] = Field(default_factory=list, max_length=20)
    note: str = Field(min_length=1, max_length=500)
    expected_version: Optional[int] = Field(None, ge=0, strict=True)

    _validate_logo = field_validator("champion_logo")(_image_url)

    @field_validator("runner_up_name")
    @classmethod
    def optional_name(cls, value):
        return value or None

    @field_validator("event_date")
    @classmethod
    def local_event_date(cls, value):
        value = (value.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
                 if value.tzinfo else value)
        if value.year < 1000:
            raise ValueError("赛事日期超出支持范围")
        return value

    @model_validator(mode="after")
    def consistent_history(self):
        first, second = self.champion_score, self.runner_up_score
        if (first is None) != (second is None):
            raise ValueError("决赛比分需同时填写，未知时同时留空")
        if first is not None and first <= second:
            raise ValueError("冠军比分必须大于亚军比分")
        ids = [member.user_id for member in self.roster if member.user_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("冠军阵容不能重复填写同一选手")
        return self


class AdminChampionInfo(BaseModel):
    champion: Optional[ChampionInfo] = None
    note: Optional[str] = None
    updated_at: Optional[datetime] = None
    version: int = 0


class RankedPlayer(BaseModel):
    position: int
    user_id: int
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    rank: str
    individual_rating: Optional[int] = None


class ChampionPage(BaseModel):
    items: list[ChampionInfo]
    total: int
    offset: int
    limit: int
    has_more: bool


class PlayerPage(BaseModel):
    items: list[RankedPlayer]
    total: int
    offset: int
    limit: int
    has_more: bool
