"""Validated, credential-free internal imports and explicit public response fields."""
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator


COUNT_FIELDS = ("kills", "deaths", "assists", "first_kills", "kills_2", "kills_3", "kills_4", "kills_5",
                "clutch_1v1", "clutch_1v2", "clutch_1v3")
METRIC_FIELDS = ("hit_percent", "hs_percent", "rws", "rating", "adr", "reaction_ms")


class ImportedPlayerStats(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    steam_id: StrictStr = Field(pattern=r"^[0-9]{17}$")
    nickname: StrictStr = Field(min_length=1, max_length=128)
    source_team_id: Optional[StrictStr] = Field(default=None, min_length=1, max_length=64)
    team_slot: Literal[1, 2]
    kills: StrictInt = Field(ge=0, le=1000000)
    deaths: StrictInt = Field(ge=0, le=1000000)
    assists: StrictInt = Field(ge=0, le=1000000)
    first_kills: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    hit_percent: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    hs_percent: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    rws: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    rating: Optional[float] = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    adr: Optional[float] = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    reaction_ms: Optional[float] = Field(default=None, ge=0, le=100000, allow_inf_nan=False)
    kills_2: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    kills_3: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    kills_4: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    kills_5: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    clutch_1v1: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    clutch_1v2: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)
    clutch_1v3: Optional[StrictInt] = Field(default=None, ge=0, le=1000000)

    @field_validator("team_slot", *METRIC_FIELDS, mode="before")
    @classmethod
    def reject_boolean_metrics(cls, value):
        if isinstance(value, bool):
            raise ValueError("统计数字不能是布尔值")
        return value

    @model_validator(mode="after")
    def check_first_kills(self):
        if self.first_kills is not None and self.first_kills > self.kills:
            raise ValueError("首杀不能多于击杀")
        return self


class ImportedStatsMap(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal["perfect_match"] = "perfect_match"
    external_match_id: StrictStr = Field(pattern=r"^[1-9][0-9]{0,31}$")
    source_url: StrictStr = Field(min_length=1, max_length=255)
    cup_name: StrictStr = Field(min_length=1, max_length=128)
    map_name: StrictStr = Field(min_length=1, max_length=64)
    source_team1_name: StrictStr = Field(min_length=1, max_length=128)
    source_team2_name: StrictStr = Field(min_length=1, max_length=128)
    team1_id: Optional[StrictInt] = Field(default=None, gt=0)
    team2_id: Optional[StrictInt] = Field(default=None, gt=0)
    team1_score: StrictInt = Field(ge=0, le=1000)
    team2_score: StrictInt = Field(ge=0, le=1000)
    round_id: Optional[StrictInt] = Field(default=None, gt=0)
    map_index: Optional[StrictInt] = Field(default=None, ge=1, le=3)
    players: list[ImportedPlayerStats] = Field(min_length=2, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def source_alias(cls, value):
        if isinstance(value, dict) and "source" in value:
            value = dict(value)
            source = value.pop("source")
            if "provider" in value and value["provider"] != source:
                raise ValueError("source与provider不一致")
            value["provider"] = source
        return value

    @model_validator(mode="after")
    def check_source_and_roster(self):
        source = urlsplit(self.source_url)
        if (source.scheme != "https" or source.netloc != "match.wmpvp.com" or source.username or source.password
                or source.query or source.fragment or source.path != "/csgo/detail/" + self.external_match_id):
            raise ValueError("来源必须是对应的完美赛事地图详情链接")
        if self.team1_score == self.team2_score:
            raise ValueError("只接受已结束且有实际回合的地图，弃权不生成地图统计")
        if self.team1_id is not None and self.team1_id == self.team2_id:
            raise ValueError("地图双方不能是同一队伍")
        steam_ids = [player.steam_id for player in self.players]
        if len(set(steam_ids)) != len(steam_ids):
            raise ValueError("同一地图SteamID重复")
        if {player.team_slot for player in self.players} != {1, 2}:
            raise ValueError("地图必须包含双方选手")
        by_slot, by_source = {}, {}
        for player in self.players:
            if player.source_team_id is not None:
                by_slot.setdefault(player.team_slot, set()).add(player.source_team_id)
                by_source.setdefault(player.source_team_id, set()).add(player.team_slot)
        if (any(len(values) > 1 for values in by_slot.values())
                or any(len(values) > 1 for values in by_source.values())):
            raise ValueError("同一地图的外部队伍ID必须与双方位置一一对应")
        return self


class PublicStatsTeam(BaseModel):
    team_id: Optional[int] = None
    team_name: str


class PublicPlayerStats(BaseModel):
    position: int
    player_key: str
    nickname: str
    teams: list[PublicStatsTeam]
    maps_played: int
    kills: int
    deaths: int
    assists: int
    kd: Optional[float] = None
    adr: Optional[float] = None
    adr_maps: int
    average_rating: Optional[float] = None
    rating_maps: int
    first_kills: Optional[int] = None
    first_kill_maps: int


class PublicStatsSourceMap(BaseModel):
    external_match_id: str
    source_url: str
    map_name: str
    source_team1_name: str
    source_team2_name: str
    team1_id: Optional[int] = None
    team2_id: Optional[int] = None
    team1_score: int
    team2_score: int
    round_id: Optional[int] = None
    map_index: Optional[int] = None
    player_count: int


class PublicPlayerStatsPage(BaseModel):
    match_id: int
    source: Literal["perfect_match"] = "perfect_match"
    source_label: str = "完美赛事平台"
    map_count: int
    player_count: int
    total: int
    offset: int
    limit: int
    has_more: bool
    updated_at: Optional[str] = None
    items: list[PublicPlayerStats]
    source_maps: list[PublicStatsSourceMap]
    notices: list[str]
