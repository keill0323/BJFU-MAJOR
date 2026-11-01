"""Imported tournament map statistics; never an implicit link to local accounts."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PlayerStatsMap(Base):
    __tablename__ = "player_stats_maps"
    __table_args__ = (
        UniqueConstraint("provider", "external_match_id", name="uq_player_stats_provider_map"),
        Index("ix_player_stats_match_map", "match_id", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Historical source records do not block the existing event deletion workflow.
    # Public reads always require the event to still exist and be visible.
    match_id = Column(Integer, nullable=False)
    provider = Column(String(24), nullable=False)
    external_match_id = Column(String(32), nullable=False)
    source_url = Column(String(255), nullable=False)
    cup_name = Column(String(128), nullable=False)
    map_name = Column(String(64), nullable=False)
    source_team1_name = Column(String(128), nullable=False)
    source_team2_name = Column(String(128), nullable=False)
    team1_id = Column(Integer, nullable=True)
    team2_id = Column(Integer, nullable=True)
    team1_score = Column(Integer, nullable=False)
    team2_score = Column(Integer, nullable=False)
    round_id = Column(Integer, nullable=True)
    map_index = Column(Integer, nullable=True)
    payload_sha256 = Column(String(64), nullable=False)
    imported_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now)


class PlayerMapStats(Base):
    __tablename__ = "player_map_stats"
    __table_args__ = (
        UniqueConstraint("map_id", "steam_id", name="uq_player_stats_map_steam"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("player_stats_maps.id", ondelete="CASCADE"), nullable=False, index=True)
    steam_id = Column(String(17), nullable=False)
    nickname = Column(String(128), nullable=False)
    source_team_id = Column(String(64), nullable=True)
    team_slot = Column(Integer, nullable=False)
    kills = Column(Integer, nullable=False)
    deaths = Column(Integer, nullable=False)
    assists = Column(Integer, nullable=False)
    first_kills = Column(Integer, nullable=True)
    hit_percent = Column(Float, nullable=True)
    hs_percent = Column(Float, nullable=True)
    rws = Column(Float, nullable=True)
    rating = Column(Float, nullable=True)
    adr = Column(Float, nullable=True)
    reaction_ms = Column(Float, nullable=True)
    kills_2 = Column(Integer, nullable=True)
    kills_3 = Column(Integer, nullable=True)
    kills_4 = Column(Integer, nullable=True)
    kills_5 = Column(Integer, nullable=True)
    clutch_1v1 = Column(Integer, nullable=True)
    clutch_1v2 = Column(Integer, nullable=True)
    clutch_1v3 = Column(Integer, nullable=True)
