"""Read-only tournament player statistics. Importing is a controlled internal operation."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.player_stats import PublicPlayerStatsPage
from app.services.player_stats_service import list_player_stats


router = APIRouter(prefix="/api/matches", tags=["赛事选手数据"])


@router.get("/{match_id}/player-stats", response_model=PublicPlayerStatsPage)
def get_player_stats(match_id: int, keyword: str = Query("", max_length=100),
                     team_id: int | None = Query(None, gt=0), offset: int = Query(0, ge=0),
                     limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    return list_player_stats(db, match_id, keyword=keyword, team_id=team_id, offset=offset, limit=limit)
