from fastapi import APIRouter, Depends, Query, Header, WebSocket
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.schemas.chat import OpenConversation, SendMessage, ReadPosition, Preferences, ReportMessage, ResolveReport, SanctionUser
from app.services.auth_service import get_current_user, require_admin_only
from app.services import chat_service as service, chat_realtime as realtime

router = APIRouter(prefix="/api/chat", tags=["私聊"])


@router.get("/config")
def config(user=Depends(get_current_user)):
    available = service.enabled(user.id) and not user.is_hidden
    return {"enabled": available, "write_enabled": available and settings.CHAT_WRITE_ENABLED,
            "protocol": 1, "max_length": 1000, "retention_hours": 24, "unread_retention_days": None,
            "self_id": user.id, "is_admin": user.role.value == "admin"}


@router.get("/contacts")
def contacts(keyword: str = Query("", max_length=64), admins: bool = False, before_id: int | None = Query(None, gt=0),
             db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.contacts(db, user.id, keyword, admins, before_id)


@router.post("/conversations")
def open_conversation(body: OpenConversation, db: Session = Depends(get_db), user=Depends(get_current_user)):
    realtime.throttle(user.id, "open", 30)
    return service.open_conversation(db, user.id, body)


@router.get("/conversations")
def conversations(cursor: str | None = Query(None, max_length=256), db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.list_conversations(db, user.id, cursor)


@router.get("/summary")
def summary(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.summary(db, user.id)


@router.get("/conversations/{cid}/messages")
def history(cid: int, before_seq: int | None = Query(None, gt=0, le=9223372036854775807), limit: int = Query(30, ge=1, le=100),
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.history(db, user.id, cid, before_seq, limit)


@router.post("/conversations/{cid}/messages")
def send(cid: int, body: SendMessage, db: Session = Depends(get_db), user=Depends(get_current_user)):
    realtime.throttle(user.id, "send", 40)
    return service.send_message(db, user.id, cid, body)


@router.get("/conversations/{cid}/events")
def events(cid: int, after_seq: int = Query(0, ge=0, le=9223372036854775807), db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.changes(db, user.id, cid, after_seq)


@router.put("/conversations/{cid}/read")
def read(cid: int, body: ReadPosition, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.mark_read(db, user.id, cid, int(body.seq) if body.seq is not None else None, body.message_ids)


@router.patch("/conversations/{cid}/preferences")
def preferences(cid: int, body: Preferences, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.preferences(db, user.id, cid, body)


@router.post("/conversations/{cid}/messages/{message_id}/recall")
def recall(cid: int, message_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.recall(db, user.id, cid, message_id)


@router.put("/blocks/{target_id}")
def block(target_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.set_block(db, user.id, target_id, True)


@router.delete("/blocks/{target_id}")
def unblock(target_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return service.set_block(db, user.id, target_id, False)


@router.post("/reports")
def report(body: ReportMessage, db: Session = Depends(get_db), user=Depends(get_current_user)):
    realtime.throttle(user.id, "report", 10)
    return service.report(db, user.id, body)


@router.get("/admin/reports")
def reports(before_id: int | None = Query(None, gt=0), db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    service.active_user(db, admin.id)
    return service.admin_reports(db, admin.id, before_id)


@router.post("/admin/reports/{report_id}/resolve")
def resolve(report_id: int, body: ResolveReport, db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    service.active_user(db, admin.id)
    return service.resolve_report(db, admin.id, report_id, body)


@router.post("/admin/sanctions")
def sanction(body: SanctionUser, db: Session = Depends(get_db), admin=Depends(require_admin_only)):
    service.active_user(db, admin.id)
    return service.sanction(db, admin.id, body)


@router.post("/socket-ticket")
def ticket(authorization: str = Header(...), user=Depends(get_current_user)):
    if user.is_hidden:
        from fastapi import HTTPException
        raise HTTPException(403, "账号暂不可用")
    return realtime.ticket(user.id, authorization)


@router.websocket("/ws")
async def socket(websocket: WebSocket):
    await realtime.socket(websocket)
