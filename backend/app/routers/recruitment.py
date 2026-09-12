from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth_service import get_current_user
from app.services import recruitment_service as service

router = APIRouter(prefix="/api/recruitment", tags=["招募大厅"])


class PostRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)


@router.get("")
def list_posts(before_id: int = Query(None, ge=1), limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    return service.public_posts(db, before_id, limit)


@router.get("/my")
def my_post(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.my_post(db, user.id)


@router.get("/players")
def players(before_id: int = Query(None, ge=1), limit: int = Query(20, ge=1, le=50),
            keyword: str = Query("", max_length=64), rank: service.RankFilter = Query(""),
            identity: service.IdentityFilter = Query(""), db: Session = Depends(get_db)):
    return service.public_players(db, before_id, limit, keyword, rank=rank, identity=identity)


@router.put("/{team_id}")
def save_post(team_id: int, body: PostRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.save_post(db, team_id, user.id, body.content)


@router.delete("/{team_id}")
def close_post(team_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.close_post(db, team_id, user.id)
