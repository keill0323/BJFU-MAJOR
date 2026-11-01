"""Management-only overview endpoints."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.admin import AdminAuditPage, AdminTodos
from app.services import admin_audit_service
from app.services.admin_service import get_todos
from app.services.auth_service import require_admin, require_admin_only

router = APIRouter(prefix="/api/admin", tags=["管理待办"])


@router.get("/todos", response_model=AdminTodos)
def todos(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return get_todos(db)


@router.get("/audit-logs", response_model=AdminAuditPage)
def audit_logs(
    actor_id: Optional[int] = Query(default=None, ge=1),
    target_user_id: Optional[int] = Query(default=None, ge=1),
    action: Optional[str] = None,
    application_id: Optional[int] = Query(default=None, ge=1),
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin=Depends(require_admin_only),
):
    """仅管理员可查看学号等受限审计；没有修改或删除审计的接口。"""
    return admin_audit_service.list_audits(
        db, actor_id=actor_id, target_user_id=target_user_id, action=action,
        application_id=application_id, created_from=created_from, created_to=created_to,
        page=page, page_size=page_size,
    )
