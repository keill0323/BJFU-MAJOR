"""Management-only overview endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.admin import AdminTodos
from app.services.admin_service import get_todos
from app.services.auth_service import require_admin

router = APIRouter(prefix="/api/admin", tags=["管理待办"])


@router.get("/todos", response_model=AdminTodos)
def todos(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return get_todos(db)
