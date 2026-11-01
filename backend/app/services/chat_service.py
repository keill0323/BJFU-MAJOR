"""私聊事务、授权和清理；队列只保存消息引用，绝不复制正文。"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import base64
from fastapi import HTTPException
from sqlalchemy import and_, or_, func
from app.config import settings
from app.models.user import User, UserRole
from app.models.team import Team, TeamStatus
from app.models.match import MatchRound, Match
from app.models.chat import ChatConversation as Conversation, ChatMember as Member, ChatMessage as Message, ChatEvent as Event, ChatBlock as Block, ChatReport as Report, ChatSanction as Sanction, ChatAudit as Audit


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def enabled(user_id):
    allowed = {p.strip() for p in settings.CHAT_ALLOW_USERS.split(",") if p.strip()}
    return settings.CHAT_ENABLED and (not allowed or str(user_id) in allowed)


def require_enabled(user_id, write=False):
    if not enabled(user_id):
        raise HTTPException(503, "聊天暂未开放")
    if write and not settings.CHAT_WRITE_ENABLED:
        raise HTTPException(503, "聊天正在维护，暂时不能发送")


def public_user(user):
    if not user or user.is_hidden:
        return {"id": user.id if user else None, "nickname": "账号暂不可用", "avatar": None, "available": False}
    return {"id": user.id, "nickname": user.nickname or user.game_id or "选手" + str(user.id),
            "avatar": user.avatar, "available": True, "is_verified": bool(user.is_verified),
            "is_admin": user.role == UserRole.ADMIN}


def active_user(db, user_id, lock=False):
    query = db.query(User).filter_by(id=user_id).populate_existing()
    user = (query.with_for_update() if lock else query).first()
    if not user or user.is_hidden:
        raise HTTPException(403, "账号暂不可用")
    return user


def admin_user(db, user_id, lock=False):
    user = active_user(db, user_id, lock)
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "仅管理员可处理聊天举报和禁言")
    return user


def conversation(db, user_id, cid, lock=False):
    require_enabled(user_id)
    active_user(db, user_id, lock=lock)
    query = db.query(Conversation).filter(Conversation.id == cid, or_(Conversation.user_low == user_id, Conversation.user_high == user_id)).populate_existing()
    room = (query.with_for_update() if lock else query).first()
    if not room:
        raise HTTPException(404, "会话不存在")
    return room


def peer_id(room, user_id):
    return room.user_high if user_id == room.user_low else room.user_low


def blocked(db, a, b):
    query = db.query(Block).filter(or_(and_(Block.user_id == a, Block.target_id == b), and_(Block.user_id == b, Block.target_id == a))).populate_existing()
    return query.first() is not None


def audit(db, actor, action, target, reason=""):
    db.add(Audit(actor_id=actor, action=action, target_id=target, reason=reason, created_at=now()))


def append_event(db, room, kind, message_id=None):
    room.last_seq += 1
    event = Event(conversation_id=room.id, seq=room.last_seq, kind=kind, message_id=message_id, created_at=now(), notified=False)
    db.add(event)
    return event


def record_deletion(db, message, at, state):
    # 删除清单先持久化，再清正文；失败时保持正文和事务状态不变。
    from app.services.chat_deletions import append, LedgerError
    try:
        append(message, at, state)
    except LedgerError:
        db.rollback()
        raise HTTPException(503, "聊天清理存储暂不可用，请稍后重试") from None


def message_data(row):
    return {"id": str(row.id), "seq": str(row.seq), "sender_id": row.sender_id, "client_id": row.client_id,
            "kind": row.kind, "content": row.content if row.state == "active" else None,
            "state": row.state, "created_at": iso(row.created_at), "read_at": iso(row.read_at)}


def open_conversation(db, user_id, body):
    db.rollback()
    require_enabled(user_id, True)
    target = body.target_id
    if body.source == "team":
        team = db.query(Team).filter_by(id=target, is_hidden=False, status=TeamStatus.APPROVED).first()
        if not team:
            raise HTTPException(404, "队伍不存在或暂不可联系")
        target = team.captain_id
    elif body.source == "round":
        round_ = db.get(MatchRound, target)
        teams = [db.get(Team, tid) for tid in (round_.team1_id, round_.team2_id)] if round_ else []
        if len(teams) != 2 or any(not t or t.is_hidden for t in teams) or user_id not in [t.captain_id for t in teams]:
            raise HTTPException(403, "仅本场对阵的队长可以联系对方")
        target = next((t.captain_id for t in teams if t.captain_id != user_id), user_id)
    if target == user_id:
        raise HTTPException(400, "不能与自己创建私聊")
    low, high = sorted((user_id, target))
    # 来源解析不参与写事务；在用户锁后重新验证，避免认证快照影响限额/拉黑。
    db.rollback()
    try:
        users = {u.id: u for u in db.query(User).filter(User.id.in_([low, high])).order_by(User.id).populate_existing().with_for_update().all()}
        if len(users) != 2 or any(u.is_hidden for u in users.values()) or not enabled(target):
            raise HTTPException(403, "对方暂不可联系")
        if body.source == "team":
            team = db.query(Team).filter_by(id=body.target_id, is_hidden=False, status=TeamStatus.APPROVED).populate_existing().first()
            if not team or team.captain_id != target:
                raise HTTPException(409, "队长信息已更新，请重新打开队伍")
        elif body.source == "round":
            r = db.query(MatchRound).filter_by(id=body.target_id).populate_existing().first()
            teams = [db.query(Team).filter_by(id=tid).populate_existing().first() for tid in (r.team1_id, r.team2_id)] if r else []
            if len(teams) != 2 or any(not t or t.is_hidden for t in teams) or {t.captain_id for t in teams} != {user_id, target}:
                raise HTTPException(409, "对阵队长已更新，请重新打开对阵")
        # 此事务的快照在双方用户锁之后建立。建会话/拉黑都先锁用户，
        # 因此普通读也包含此前提交的会话和限额；缺失行无需再锁范围。
        # MySQL RR 的缺失会话/限额 FOR UPDATE 会让无关用户互持间隙锁，插入时死锁。
        room = db.query(Conversation).filter_by(user_low=low, user_high=high).populate_existing().first()
        if room:
            db.commit()
            return {"id": room.id}
        sender, receiver = users[user_id], users[target]
        if blocked(db, user_id, target):
            raise HTTPException(403, "当前无法建立私聊")
        if not sender.is_verified and sender.role != UserRole.ADMIN and receiver.role != UserRole.ADMIN:
            raise HTTPException(403, "完成在校认证后可发起私聊；认证问题可以联系管理员")
        day_start = (now() + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
        count = db.query(Conversation).filter(Conversation.initiator_id == user_id, Conversation.created_at >= day_start).count()
        if count >= 10:
            raise HTTPException(429, "今天新建私聊较多，请明天再试")
        room = Conversation(user_low=low, user_high=high, initiator_id=user_id, last_seq=0, event_floor=0, created_at=now(), updated_at=now())
        db.add(room)
        db.flush()
        db.add_all([Member(conversation_id=room.id, user_id=uid, read_seq=0) for uid in (low, high)])
        db.commit()
        return {"id": room.id}
    except Exception:
        db.rollback()
        raise


def contacts(db, user_id, keyword="", admins=False, before_id=None):
    require_enabled(user_id)
    active_user(db, user_id)
    query = db.query(User).filter(User.is_hidden.is_(False), User.id != user_id)
    if admins:
        query = query.filter(User.role == UserRole.ADMIN)
    elif keyword.strip():
        value = keyword.strip()
        matches = [User.nickname.contains(value, autoescape=True),
                   User.game_id.contains(value, autoescape=True)]
        if value.isascii() and value.isdecimal():
            digits = value.lstrip('0')
            # 系统ID精确匹配，不将主键转为字符串；超大输入仍可搜索昵称/游戏ID。
            if digits and len(digits) <= 10 and int(digits) <= 2147483647:
                matches.append(User.id == int(digits))
        query = query.filter(or_(*matches))
    if before_id:
        query = query.filter(User.id < before_id)
    rows = query.order_by(User.id.desc()).limit(31).all()
    return {"items": [public_user(u) for u in rows[:30]], "has_more": len(rows) > 30, "next_cursor": rows[29].id if len(rows) > 30 else None}


def unread_count(db, cid, user_id, read_seq):
    return db.query(Message).filter(Message.conversation_id == cid, Message.sender_id != user_id, Message.read_at.is_(None), Message.state == "active").count()


def room_info(db, room, user_id, context=None):
    other = peer_id(room, user_id)
    if context is None:
        member = db.get(Member, (room.id, user_id))
        peer = db.get(User, other)
        peer_member = db.get(Member, (room.id, other))
        latest = db.query(Message).filter_by(conversation_id=room.id, state="active").order_by(Message.seq.desc()).first()
        sanction = db.get(Sanction, user_id)
        is_blocked = blocked(db, user_id, other)
        own_block = db.get(Block, (user_id, other)) is not None
        unread = unread_count(db, room.id, user_id, member.read_seq)
    else:
        member = context["members"][(room.id, user_id)]
        peer_member = context["members"][(room.id, other)]
        peer, latest, sanction = context["users"].get(other), context["latest"].get(room.id), context["sanction"]
        is_blocked = (user_id, other) in context["blocks"] or (other, user_id) in context["blocks"]
        own_block = (user_id, other) in context["blocks"]
        unread = context["unread"].get(room.id, 0)
    return {"id": room.id, "peer": public_user(peer), "pinned": member.pinned, "muted": member.muted,
            "blocked": is_blocked, "blocked_by_me": own_block,
            "unread": unread, "read_seq": str(member.read_seq),
            "peer_read_seq": str(peer_member.read_seq), "last_event_seq": str(room.last_seq),
            "latest": message_data(latest) if latest else None, "updated_at": iso(room.updated_at),
            "write_enabled": settings.CHAT_WRITE_ENABLED and bool(peer and not peer.is_hidden) and enabled(other),
            "muted_until": iso(sanction.until) if sanction and sanction.until > now() else None}


def list_conversations(db, user_id, cursor=None):
    require_enabled(user_id)
    active_user(db, user_id)
    query = db.query(Conversation).join(Member, and_(Member.conversation_id == Conversation.id, Member.user_id == user_id))
    if cursor:
        try:
            pinned, stamp, last_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if type(pinned) is not bool or type(last_id) is not int or last_id <= 0:
                raise ValueError()
            updated = datetime.fromisoformat(stamp)
            if updated.tzinfo is not None:
                raise ValueError()
        except Exception:
            raise HTTPException(422, "会话分页位置无效") from None
        query = query.filter(or_((Member.pinned.is_(False) if pinned else Conversation.id < 0),
            and_(Member.pinned == pinned, Conversation.updated_at < updated),
            and_(Member.pinned == pinned, Conversation.updated_at == updated, Conversation.id < last_id)))
    rows = query.order_by(Member.pinned.desc(), Conversation.updated_at.desc(), Conversation.id.desc()).limit(31).all()
    # 会话分页整批取关联数据，避免 30 个会话产生数百条查询。
    ids = [r.id for r in rows[:30]]
    peers = [peer_id(r, user_id) for r in rows[:30]]
    members = db.query(Member).filter(Member.conversation_id.in_(ids)).all()
    users = db.query(User).filter(User.id.in_(peers)).all()
    last = db.query(Message.conversation_id, func.max(Message.seq).label("seq")).filter(Message.conversation_id.in_(ids), Message.state == "active").group_by(Message.conversation_id).subquery()
    latest = db.query(Message).join(last, and_(Message.conversation_id == last.c.conversation_id, Message.seq == last.c.seq)).all()
    unreads = db.query(Message.conversation_id, func.count(Message.id)).filter(Message.conversation_id.in_(ids), Message.sender_id != user_id, Message.state == "active", Message.read_at.is_(None)).group_by(Message.conversation_id).all()
    blocks = db.query(Block).filter(or_(and_(Block.user_id == user_id, Block.target_id.in_(peers)), and_(Block.target_id == user_id, Block.user_id.in_(peers)))).all()
    context = {"members": {(m.conversation_id, m.user_id): m for m in members}, "users": {u.id: u for u in users},
               "latest": {m.conversation_id: m for m in latest}, "unread": dict(unreads), "blocks": {(b.user_id, b.target_id) for b in blocks}, "sanction": db.get(Sanction, user_id)}
    items = [room_info(db, r, user_id, context) for r in rows[:30]]
    next_cursor = None
    if len(rows) > 30:
        last = rows[29]
        next_cursor = base64.urlsafe_b64encode(json.dumps([bool(items[-1]["pinned"]), last.updated_at.isoformat(), last.id]).encode()).decode().rstrip("=")
    return {"items": items, "has_more": len(rows) > 30, "next_cursor": next_cursor}


def summary(db, user_id):
    require_enabled(user_id)
    active_user(db, user_id)
    total = (db.query(func.count(Message.id)).join(Member, and_(Member.conversation_id == Message.conversation_id, Member.user_id == user_id))
             .filter(Member.muted.is_(False), Message.sender_id != user_id, Message.state == "active", Message.read_at.is_(None)).scalar())
    return {"unread": total or 0}


def history(db, user_id, cid, before_seq=None, limit=30):
    db.rollback()  # 结束认证 SELECT 的 MySQL RR 快照，再在会话锁后读取一致快照。
    room = conversation(db, user_id, cid, lock=True)
    query = db.query(Message).filter(Message.conversation_id == cid, Message.state != "purged")
    if before_seq:
        query = query.filter(Message.seq < before_seq)
    rows = query.order_by(Message.seq.desc()).limit(limit + 1).all()
    info = room_info(db, room, user_id)
    result = {"items": [message_data(m) for m in reversed(rows[:limit])], "has_more": len(rows) > limit,
              "next_cursor": str(rows[limit - 1].seq) if len(rows) > limit else None, "room": info,
              "cursor": str(room.last_seq)}
    db.commit()
    return result


def changes(db, user_id, cid, after_seq):
    db.rollback()
    room = conversation(db, user_id, cid, lock=True)
    if after_seq < room.event_floor or after_seq > room.last_seq:
        result = {"reset": True, "cursor": str(room.last_seq), "items": []}
    else:
        events = db.query(Event).filter(Event.conversation_id == cid, Event.seq > after_seq).order_by(Event.seq).limit(101).all()
        rows = events[:100]
        items = []
        for e in rows:
            msg = db.get(Message, e.message_id) if e.message_id else None
            items.append({"seq": str(e.seq), "kind": e.kind, "message_id": str(e.message_id) if e.message_id else None,
                          "message": message_data(msg) if msg and msg.state != "purged" else None})
        result = {"reset": False, "items": items, "cursor": str(rows[-1].seq if len(events) > 100 else room.last_seq),
                  "has_more": len(events) > 100, "room": room_info(db, room, user_id)}
    db.commit()
    return result


def check_content(openid, content):
    """检测失败关闭发送，不将令牌、正文或微信原始响应写入日志。"""
    if not settings.CHAT_MODERATION_ENABLED:
        if settings.WX_MOCK_LOGIN:
            return
        raise HTTPException(503, "聊天内容检测暂未配置")
    from app.services import wechat_service
    try:
        for attempt in range(2):
            token = wechat_service.access_token(refresh=bool(attempt))
            result = wechat_service._post("https://api.weixin.qq.com/wxa/msg_sec_check?access_token=" + token,
                {"openid": openid, "scene": 4, "version": 2, "content": content})
            if result.get("errcode") in (40001, 40014, 42001) and attempt == 0:
                continue
            if result.get("errcode") != 0:
                raise HTTPException(503, "内容检测暂不可用，请稍后重试")
            if result.get("result", {}).get("suggest") != "pass":
                raise HTTPException(422, "消息未通过内容检测，请修改后发送")
            return
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "内容检测暂不可用，请稍后重试") from None


def card_content(db, sender_id, receiver_id, body):
    if body.kind == "text":
        value = body.content.strip()
        if not value or len(value.encode("utf-8")) > 8192 or body.source_id is not None:
            raise HTTPException(422, "请发送 1～1000 字的文字")
        return value
    if not body.source_id or body.content:
        raise HTTPException(422, "卡片参数无效")
    if body.kind == "team":
        team = db.query(Team).filter_by(id=body.source_id, is_hidden=False, status=TeamStatus.APPROVED).first()
        if not team:
            raise HTTPException(404, "队伍不可分享")
        card = {"type": "team", "id": team.id, "title": team.name}
    else:
        r = db.get(MatchRound, body.source_id)
        teams = [db.get(Team, tid) for tid in (r.team1_id, r.team2_id)] if r else []
        if len(teams) != 2 or any(not t or t.is_hidden for t in teams) or {t.captain_id for t in teams} != {sender_id, receiver_id}:
            raise HTTPException(403, "仅可向本场对手队长分享对阵")
        match = db.get(Match, r.match_id)
        if not match:
            raise HTTPException(404, "赛事已不存在")
        card = {"type": "round", "id": r.id, "match_id": r.match_id, "title": match.name,
                "subtitle": teams[0].name + " vs " + teams[1].name}
    return json.dumps(card, ensure_ascii=False, separators=(",", ":"))


def _existing(db, cid, uid, client_id, digest):
    row = db.query(Message).filter_by(conversation_id=cid, sender_id=uid, client_id=client_id).populate_existing().first()
    if row and row.payload_hash != digest:
        raise HTTPException(409, "同一发送编号不能用于不同内容")
    return row


def send_message(db, user_id, cid, body):
    db.rollback()
    require_enabled(user_id, True)
    room = conversation(db, user_id, cid)
    other_id = peer_id(room, user_id)
    stamp = body.client_created_at
    stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo else stamp
    digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    old = _existing(db, cid, user_id, body.client_id, digest)
    if old:
        return {"message": message_data(old), "replayed": True}
    if stamp < now() - timedelta(days=1) or stamp > now() + timedelta(minutes=5):
        raise HTTPException(422, "待发送消息已过期，请确认内容后重新发送")
    if not enabled(other_id) or blocked(db, user_id, other_id):
        raise HTTPException(403, "当前无法向对方发送消息")
    if not public_user(db.get(User, other_id))["available"]:
        raise HTTPException(403, "对方暂不可联系")
    sanction_ = db.get(Sanction, user_id)
    if sanction_ and sanction_.until > now():
        raise HTTPException(403, "当前账号暂时不能发送聊天消息")
    recent = db.query(Message).filter(Message.sender_id == user_id, Message.created_at >= now() - timedelta(minutes=1))
    if recent.count() >= 30 or recent.filter(Message.created_at >= now() - timedelta(seconds=10)).count() >= 5:
        raise HTTPException(429, "发送太快了，请稍后再试")
    sender = active_user(db, user_id)
    content = card_content(db, user_id, other_id, body)
    openid = sender.wx_openid
    # 先释放认证依赖/读取启动的事务，再等待外部检测。
    db.rollback()
    check_content(openid, content)
    try:
        for uid in sorted((user_id, other_id)):
            active_user(db, uid, lock=True)
        room = conversation(db, user_id, cid, lock=True)
        old = _existing(db, cid, user_id, body.client_id, digest)
        if old:
            result = {"message": message_data(old), "replayed": True}
            db.commit()
            return result
        if not enabled(other_id) or blocked(db, user_id, other_id):
            raise HTTPException(403, "当前无法向对方发送消息")
        sanction = db.query(Sanction).filter_by(user_id=user_id).populate_existing().first()
        if sanction and sanction.until > now():
            raise HTTPException(403, "当前账号暂时不能发送聊天消息")
        minute = db.query(Message).filter(Message.sender_id == user_id, Message.created_at >= now() - timedelta(minutes=1))
        if minute.count() >= 30 or minute.filter(Message.created_at >= now() - timedelta(seconds=10)).count() >= 5:
            raise HTTPException(429, "发送太快了，请稍后再试")
        if card_content(db, user_id, other_id, body) != content:
            raise HTTPException(409, "卡片信息已更新，请重新发送")
        event = append_event(db, room, "message")
        msg = Message(conversation_id=cid, seq=event.seq, sender_id=user_id, client_id=body.client_id,
                      payload_hash=digest, kind=body.kind, content=content, state="active", created_at=now())
        db.add(msg)
        db.flush()
        event.message_id = msg.id
        room.updated_at = msg.created_at
        result = {"message": message_data(msg), "replayed": False}
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def mark_read(db, user_id, cid, seq=None, message_ids=None):
    db.rollback()
    try:
        room = conversation(db, user_id, cid, lock=True)
        member = db.query(Member).filter_by(conversation_id=cid, user_id=user_id).populate_existing().with_for_update().one()
        if seq is not None and (seq > room.last_seq or seq < 0):
            raise HTTPException(422, "已读位置无效")
        query = db.query(Message).filter(Message.conversation_id == cid, Message.sender_id != user_id, Message.state == "active")
        if message_ids:
            query = query.filter(Message.id.in_(message_ids))
        elif seq is not None:
            query = query.filter(Message.seq == seq)
        else:
            raise HTTPException(422, "请提供实际看到的消息")
        rows = query.populate_existing().with_for_update().all()
        for msg in rows:
            if msg.read_at is None:
                msg.read_at = now()
                member.read_seq = max(member.read_seq, msg.seq)
                append_event(db, room, "read", msg.id)
        db.commit()
        return {"read_seq": str(member.read_seq)}
    except Exception:
        db.rollback()
        raise


def preferences(db, user_id, cid, body):
    db.rollback()
    room = conversation(db, user_id, cid, lock=True)
    member = db.query(Member).filter_by(conversation_id=cid, user_id=user_id).populate_existing().with_for_update().one()
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(member, key, value)
    append_event(db, room, "preferences")
    db.commit()
    return {"message": "设置已保存"}


def recall(db, user_id, cid, message_id):
    db.rollback()
    room = conversation(db, user_id, cid, lock=True)
    row = db.query(Message).filter_by(id=message_id, conversation_id=cid, sender_id=user_id).populate_existing().with_for_update().first()
    if not row:
        raise HTTPException(404, "消息不存在")
    if row.state == "active":
        if row.created_at < now() - timedelta(minutes=2):
            raise HTTPException(400, "只能撤回两分钟内发送的消息")
        deleted_at = now()
        record_deletion(db, row, deleted_at, "recalled")
        row.content, row.state, row.purged_at = None, "recalled", deleted_at
        append_event(db, room, "recall", row.id)
    db.commit()
    return {"message": "消息已撤回"}


def set_block(db, user_id, target_id, value):
    db.rollback()
    require_enabled(user_id)
    if user_id == target_id:
        raise HTTPException(400, "不能拉黑自己")
    for uid in sorted((user_id, target_id)):
        active_user(db, uid, lock=True)
    # 双方用户锁已串行化同一拉黑关系；快照在加锁后建立，缺失行不加间隙锁。
    row = db.query(Block).filter_by(user_id=user_id, target_id=target_id).populate_existing().first()
    if value and not row:
        db.add(Block(user_id=user_id, target_id=target_id, created_at=now()))
    elif not value and row:
        db.delete(row)
    low, high = sorted((user_id, target_id))
    room = db.query(Conversation).filter_by(user_low=low, user_high=high).first()
    if room:
        # 已存在的会话仍需当前读锁，和清理/举报隐藏共同保护事件序号。
        room = db.query(Conversation).filter_by(id=room.id).populate_existing().with_for_update().one()
        append_event(db, room, "permissions")
    db.commit()
    return {"message": "设置已保存"}


def report(db, user_id, body):
    db.rollback()
    require_enabled(user_id)
    msg = db.get(Message, body.message_id)
    if not msg:
        raise HTTPException(404, "消息不存在")
    cid = msg.conversation_id
    db.rollback()
    room = conversation(db, user_id, cid, lock=True)
    msg = db.query(Message).filter_by(id=body.message_id, conversation_id=cid).populate_existing().with_for_update().first()
    if not msg:
        raise HTTPException(404, "消息不存在")
    if msg.sender_id == user_id or msg.state != "active":
        raise HTTPException(400, "这条消息暂不可举报")
    # 同会话举报由已持有的会话锁串行化；首次举报无需锁不存在的唯一键范围。
    existing = db.query(Report).filter_by(reporter_id=user_id, message_id=msg.id).populate_existing().first()
    if not existing:
        reason = body.reason.strip()
        if not reason:
            raise HTTPException(422, "请填写举报原因")
        db.add(Report(reporter_id=user_id, message_id=msg.id, conversation_id=room.id, reason=reason, created_at=now()))
    db.commit()
    return {"message": "举报已提交，管理员将核查"}


def admin_reports(db, actor_id, before_id=None):
    db.rollback()
    admin_user(db, actor_id)
    query = db.query(Report)
    if before_id:
        query = query.filter(Report.id < before_id)
    rows = query.order_by(Report.id.desc()).limit(31).all()
    items = []
    for r in rows[:30]:
        msg = db.get(Message, r.message_id)
        items.append({"id": r.id, "reason": r.reason, "status": r.status, "resolution": r.resolution,
                      "created_at": iso(r.created_at), "sender_id": msg.sender_id if msg else None,
                      "message": message_data(msg) if msg else None})
    audit(db, actor_id, "read_reports", rows[0].id if rows else 0)
    db.commit()
    return {"items": items, "has_more": len(rows) > 30, "next_cursor": rows[29].id if len(rows) > 30 else None,
            "pending": db.query(Report).filter_by(status="pending").count()}


def resolve_report(db, actor_id, report_id, body):
    db.rollback()
    admin_user(db, actor_id)
    row = db.query(Report).filter_by(id=report_id).first()
    if not row:
        raise HTTPException(404, "举报不存在")
    cid = row.conversation_id
    db.rollback()
    admin_user(db, actor_id, lock=True)
    # 与举报/清理一致：先会话，再举报及消息，避免相反锁序。
    room = db.query(Conversation).filter_by(id=cid).populate_existing().with_for_update().first()
    row = db.query(Report).filter_by(id=report_id).populate_existing().with_for_update().first()
    if row.status != "pending":
        return {"message": "该举报已处理"}
    msg = db.query(Message).filter_by(id=row.message_id).populate_existing().with_for_update().first()
    if body.action == "hide" and msg and msg.state == "active":
        deleted_at = now()
        record_deletion(db, msg, deleted_at, "hidden")
        msg.content, msg.state, msg.purged_at = None, "hidden", deleted_at
        append_event(db, room, "hide", msg.id)
    row.status, row.resolution, row.resolved_at = body.action, body.reason.strip(), now()
    audit(db, actor_id, body.action, report_id, body.reason)
    db.commit()
    return {"message": "举报已处理"}


def sanction(db, actor_id, body):
    db.rollback()
    users = {uid: active_user(db, uid, lock=True) for uid in sorted({actor_id, body.user_id})}
    if users[actor_id].role != UserRole.ADMIN:
        raise HTTPException(403, "仅管理员可处理聊天举报和禁言")
    target = users[body.user_id]
    if target.role == UserRole.ADMIN:
        raise HTTPException(400, "管理员账号不能在此禁言")
    # 禁言的所有写入均先锁目标用户；加锁后的快照保证读取最新已提交状态。
    row = db.query(Sanction).filter_by(user_id=body.user_id).populate_existing().first()
    if row is None:
        row = Sanction(user_id=body.user_id)
        db.add(row)
    row.until, row.reason, row.actor_id = now() + timedelta(hours=body.hours), body.reason.strip(), actor_id
    audit(db, actor_id, "sanction", body.user_id, body.reason)
    db.commit()
    return {"message": "禁言设置已更新"}


def cleanup(db, at=None, limit=200):
    """独立运行，不依赖前端在线；正文删除不会删除去重编号。"""
    at = at or now()
    candidates = (db.query(Message.id, Message.conversation_id).filter(Message.created_at <= at - timedelta(days=1), Message.state != "purged")
                  .filter(or_(Message.read_at.isnot(None), Message.state != "active"))
                  .order_by(Message.id).limit(limit).all())
    removed = 0
    for message_id, cid in candidates:
        room = db.query(Conversation).filter_by(id=cid).populate_existing().with_for_update().first()
        msg = db.query(Message).filter_by(id=message_id).populate_existing().with_for_update().first()
        if room and msg and msg.state != "purged" and msg.created_at <= at - timedelta(days=1) and (msg.state != "active" or msg.read_at is not None):
            if msg.content is not None:
                record_deletion(db, msg, at, "purged")
            msg.content, msg.state, msg.purged_at = None, "purged", at
            append_event(db, room, "delete", msg.id)
            removed += 1
        db.commit()
    # 更新同步水位后再删除过期事件，客户端旧游标会获得 reset。
    cids = [r[0] for r in db.query(Event.conversation_id).filter(Event.created_at < at - timedelta(days=7)).distinct().limit(limit)]
    for cid in cids:
        room = db.query(Conversation).filter_by(id=cid).populate_existing().with_for_update().one()
        rows = db.query(Event).filter(Event.conversation_id == cid, Event.created_at < at - timedelta(days=7)).order_by(Event.seq).limit(limit).all()
        if rows:
            room.event_floor = max(room.event_floor, rows[-1].seq)
            db.query(Event).filter(Event.id.in_([r.id for r in rows])).delete(synchronize_session=False)
        db.commit()
    expired = [r[0] for r in db.query(Message.id).filter(Message.state == "purged", Message.purged_at < at - timedelta(days=35)).limit(limit)]
    if expired:
        db.query(Message).filter(Message.id.in_(expired)).delete(synchronize_session=False)
        db.commit()
    return removed


if __name__ == "__main__":
    from app.database import SessionLocal
    with SessionLocal() as session:
        print("本批清理消息数：", cleanup(session))
