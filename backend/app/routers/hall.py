"""Read-only public honours and certified rank leaderboard."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.hall import AdminChampionInfo, ChampionPage, ManualChampionRequest, PlayerPage
from app.services import hall_service
from app.services.auth_service import require_admin_only

router = APIRouter(prefix="/api/hall", tags=["名人堂"])


@router.get("/champions", response_model=ChampionPage)
def champions(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
              db: Session = Depends(get_db)):
    return hall_service.list_champions(db, offset=offset, limit=limit)


@router.get("/players", response_model=PlayerPage)
def players(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
            db: Session = Depends(get_db)):
    return hall_service.list_players(db, offset=offset, limit=limit)


@router.get("/admin/champions/{match_id}", response_model=AdminChampionInfo)
def admin_champion(match_id: int, db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    return hall_service.get_admin_champion(db, match_id)


@router.put("/admin/champions/{match_id}", response_model=AdminChampionInfo)
def save_champion(match_id: int, request: ManualChampionRequest, db: Session = Depends(get_db),
                  admin=Depends(require_admin_only)):
    return hall_service.save_manual_champion(db, match_id, request, admin.id)
