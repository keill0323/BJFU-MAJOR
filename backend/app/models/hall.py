"""Persistent public tournament honours, independent of mutable team/user records."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class ChampionSnapshot(Base):
    __tablename__ = "champion_snapshots"

    # No foreign keys: deleting a team, player, or event must not erase its history.
    match_id = Column(Integer, primary_key=True, autoincrement=False)
    final_round_id = Column(Integer, nullable=True)
    match_name = Column(String(128), nullable=False)
    match_type = Column(String(20), nullable=True)
    event_date = Column(DateTime, nullable=True, index=True)
    awarded_at = Column(DateTime, nullable=True)
    champion_team_id = Column(Integer, nullable=True)
    champion_name = Column(String(64), nullable=False)
    champion_logo = Column(String(255), nullable=True)
    runner_up_name = Column(String(64), nullable=True)
    champion_score = Column(Integer, nullable=True)
    runner_up_score = Column(Integer, nullable=True)
    roster_json = Column(Text, nullable=False)
    # Freeze both finalists so a result correction can reverse the winner safely.
    finalists_json = Column(Text, nullable=False)
    snapshot_source = Column(String(20), nullable=False)
    is_valid = Column(Boolean, default=True, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    # Administrative provenance is deliberately excluded from public responses.
    manual_created_by = Column(Integer, nullable=True)
    manual_updated_by = Column(Integer, nullable=True)
    manual_created_at = Column(DateTime, nullable=True)
    manual_note = Column(String(500), nullable=True)
    manual_version = Column(Integer, default=0, nullable=False)
