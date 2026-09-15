from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth_service import get_current_user, require_admin_only
from app.services import admin_notice_service
from app.schemas.admin_notice import SendAdminNotice
from app.services import notification_service
from app.services import wechat_service
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/notifications", tags=["消息"])


@router.get("/admin/recipients")
def notice_recipients(keyword: str = Query("", max_length=50), before_id: int = Query(None, ge=1),
                      db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    return admin_notice_service.recipients(db, keyword, before_id)


@router.post("/admin/send")
def send_admin_notice(body: SendAdminNotice, db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    return admin_notice_service.send_notice(db, admin.id, body)


@router.get("/admin/sent")
def sent_admin_notices(db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    return admin_notice_service.sent(db)


@router.get("/admin/inbox")
def admin_notice_inbox(before_id: int = Query(None, ge=1), limit: int = Query(30, ge=1, le=100),
                      db: Session = Depends(get_db), user=Depends(get_current_user)):
    return admin_notice_service.inbox(db, user.id, before_id, limit)


@router.put("/admin/inbox/{notice_id}/read")
def read_admin_notice(notice_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return admin_notice_service.mark_read(db, user.id, notice_id)


@router.get("/summary")
def summary(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return notification_service.personal_summary(db, user.id)


@router.get("/team-applications")
def applications(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return notification_service.team_applications(db, user.id)


class SubscriptionChoice(BaseModel):
    choices: dict[str, str] = Field(min_length=1, max_length=3)


@router.get("/wechat/config")
def wechat_config():
    return wechat_service.configuration()


@router.post("/wechat/subscriptions")
def subscribe(body: SubscriptionChoice, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return wechat_service.record_choice(db, user.id, body.choices)


@router.get("/schedules")
def list_schedules(before_id: int = Query(None, ge=1), limit: int = Query(30, ge=1, le=100),
                   db: Session = Depends(get_db), user=Depends(get_current_user)):
    return notification_service.list_notifications(db, user.id, before_id=before_id, limit=limit)


@router.put("/schedules/{notice_id}/read")
def read_schedule(notice_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return notification_service.mark_read(db, user.id, notice_id)
