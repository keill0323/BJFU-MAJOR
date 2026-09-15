"""定向通知仅写入站内收件箱，不调用微信发送或收集联系方式。"""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import or_, func
from app.models.user import User, UserRole
from app.models.admin_notice import AdminNoticeBatch, AdminNotice
from app.services.notification_service import now


def recipients(db, keyword="", before_id=None, limit=30):
    query = db.query(User).filter(User.is_hidden.is_(False))
    keyword = keyword.strip()
    if keyword:
        conditions = [User.nickname.contains(keyword, autoescape=True), User.game_id.contains(keyword, autoescape=True)]
        if keyword.isascii() and keyword.isdecimal() and len(keyword) <= 10:
            conditions.append(User.id == int(keyword))
        query = query.filter(or_(*conditions))
    if before_id is not None:
        query = query.filter(User.id < before_id)
    rows = query.order_by(User.id.desc()).limit(limit + 1).all()
    more, rows = len(rows) > limit, rows[:limit]
    return {"items": [{"id": u.id, "nickname": u.nickname, "game_id": u.game_id,
                       "rank": u.rank, "is_verified": u.is_verified} for u in rows],
            "has_more": more, "next_cursor": rows[-1].id if more else None}


def send_notice(db, sender_id, body):
    fingerprint = hashlib.sha256(json.dumps({"ids": body.recipient_ids, "title": body.title,
                                            "content": body.content}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    try:
        # 按 ID 统一锁定发送者和收件人，避免两个管理员互发时反向取锁。
        locked = db.query(User).filter(User.id.in_(sorted({sender_id, *body.recipient_ids}))).order_by(
            User.id).populate_existing().with_for_update().all()
        sender = next((u for u in locked if u.id == sender_id), None)
        if not sender or sender.is_hidden or sender.role != UserRole.ADMIN:
            raise HTTPException(403, "仅管理员可发送通知")
        existing = db.query(AdminNoticeBatch).filter_by(sender_id=sender_id, request_id=body.request_id).with_for_update().first()
        if existing:
            if existing.payload_hash != fingerprint:
                raise HTTPException(409, "发送请求已用于其他内容，请刷新发送页面")
            result = {"id": existing.id, "recipient_count": existing.recipient_count, "replayed": True}
            db.commit()
            return result
        users = [u for u in locked if u.id in body.recipient_ids and not u.is_hidden]
        if {row.id for row in users} != set(body.recipient_ids):
            raise HTTPException(422, "部分收件人已不存在或不可用，请重新选择；本次未发送")
        batch = AdminNoticeBatch(sender_id=sender_id, request_id=body.request_id, payload_hash=fingerprint,
                                 title=body.title, content=body.content, recipient_count=len(users), created_at=now())
        db.add(batch)
        db.flush()
        db.add_all([AdminNotice(batch_id=batch.id, user_id=row.id) for row in users])
        result = {"id": batch.id, "recipient_count": len(users), "replayed": False}
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def inbox(db, user_id, before_id=None, limit=30):
    unread = db.query(AdminNotice).filter_by(user_id=user_id, read_at=None).count()
    query = db.query(AdminNotice, AdminNoticeBatch).join(AdminNoticeBatch).filter(AdminNotice.user_id == user_id)
    if before_id is not None:
        query = query.filter(AdminNotice.id < before_id)
    rows = query.order_by(AdminNotice.id.desc()).limit(limit + 1).all()
    more, rows = len(rows) > limit, rows[:limit]
    return {"items": [{"id": n.id, "title": b.title, "content": b.content, "created_at": b.created_at,
                       "is_read": n.read_at is not None} for n, b in rows],
            "unread_count": unread, "has_more": more, "next_cursor": rows[-1][0].id if more else None}


def mark_read(db, user_id, notice_id):
    query = db.query(AdminNotice).filter_by(id=notice_id, user_id=user_id)
    if not query.first():
        raise HTTPException(404, "通知不存在")
    query.filter(AdminNotice.read_at.is_(None)).update({"read_at": now()})
    db.commit()
    return {"message": "已读"}


def sent(db):
    batches = db.query(AdminNoticeBatch).order_by(AdminNoticeBatch.id.desc()).limit(20).all()
    counts = dict(db.query(AdminNotice.batch_id, func.count(AdminNotice.id)).filter(
        AdminNotice.batch_id.in_([b.id for b in batches]), AdminNotice.read_at.is_not(None)
    ).group_by(AdminNotice.batch_id).all()) if batches else {}
    return {"items": [{"id": b.id, "title": b.title, "content": b.content, "created_at": b.created_at,
                       "recipient_count": b.recipient_count, "read_count": counts.get(b.id, 0)} for b in batches]}
