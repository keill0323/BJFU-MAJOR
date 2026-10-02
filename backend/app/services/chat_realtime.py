"""单进程实时提示；离线补收以数据库为准，内存队列有界。"""
import asyncio
from contextlib import suppress
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from app.database import SessionLocal
from app.models.chat import ChatEvent, ChatConversation
from app.services import chat_service as service
from app.services.auth_service import get_current_user

logger = logging.getLogger(__name__)
_tickets = {}
_rates = {}
_guard = threading.Lock()
_connections = defaultdict(set)
_handshakes = 0


def throttle(uid, kind, maximum=30):
    tick = time.monotonic()
    with _guard:
        for key in [k for k, v in _rates.items() if not v or v[-1] < tick - 60]:
            _rates.pop(key, None)
        if len(_rates) >= 4096 and (uid, kind) not in _rates:
            raise HTTPException(503, "聊天繁忙，请稍后重试")
        queue = _rates.setdefault((uid, kind), deque())
        while queue and queue[0] < tick - 60:
            queue.popleft()
        if len(queue) >= maximum:
            raise HTTPException(429, "操作频繁，请稍后重试")
        queue.append(tick)


def ticket(uid, authorization):
    service.require_enabled(uid)
    throttle(uid, "ticket", 12)
    with _guard:
        tick = time.monotonic()
        for key in [k for k, (_, expiry) in _tickets.items() if expiry < tick]:
            _tickets.pop(key, None)
        if len(_tickets) >= 2048:
            raise HTTPException(503, "聊天连接繁忙")
        key = secrets.token_urlsafe(32)
        _tickets[key] = (authorization, tick + 30)
    return {"ticket": key, "expires_in": 30}


def authenticate(authorization):
    with SessionLocal() as db:
        user = get_current_user(db=db, authorization=authorization)
        service.require_enabled(user.id)
        service.active_user(db, user.id)
        return user.id


async def socket(websocket: WebSocket):
    global _handshakes
    if _handshakes >= 100 or sum(map(len, _connections.values())) >= 500:
        await websocket.close(code=1013)
        return
    _handshakes += 1
    awaiting_auth = True
    queue, uid, sender = None, None, None
    try:
        await websocket.accept()
        raw = await asyncio.wait_for(websocket.receive_text(), 5)
        if len(raw) > 256:
            raise ValueError("frame too large")
        import json
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            raise ValueError("invalid handshake")
        supplied = frame.get("ticket", "")
        with _guard:
            credential = _tickets.pop(supplied, None) if isinstance(supplied, str) else None
        if not credential or credential[1] < time.monotonic():
            raise ValueError("expired ticket")
        authorization = credential[0]
        uid = await asyncio.to_thread(authenticate, authorization)
        _handshakes -= 1
        awaiting_auth = False
        if sum(map(len, _connections.values())) >= 500 or len(_connections[uid]) >= 3:
            await websocket.close(code=4008)
            return
        queue = asyncio.Queue(maxsize=64)
        _connections[uid].add(queue)
        await websocket.send_json({"type": "ready"})

        async def drain():
            while True:
                payload = await queue.get()
                if payload is None:
                    await websocket.close(code=4008)
                    return
                await asyncio.wait_for(websocket.send_json(payload), 10)

        sender = asyncio.create_task(drain())
        while not sender.done():
            raw = await asyncio.wait_for(websocket.receive_text(), 65)
            if raw != "ping":
                raise ValueError("invalid heartbeat")
            await asyncio.to_thread(authenticate, authorization)
            queue.put_nowait({"type": "pong"})
    except (HTTPException, ValueError, KeyError, TypeError, asyncio.TimeoutError, asyncio.QueueFull, WebSocketDisconnect, RuntimeError):
        with suppress(Exception):
            await websocket.close(code=4001)
    finally:
        if awaiting_auth:
            _handshakes -= 1
        if queue is not None and uid is not None:
            _connections[uid].discard(queue)
            if not _connections[uid]:
                _connections.pop(uid, None)
        if sender:
            sender.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await sender


def pending():
    with SessionLocal() as db:
        rows = (db.query(ChatEvent, ChatConversation).join(ChatConversation, ChatConversation.id == ChatEvent.conversation_id)
                .filter(ChatEvent.notified.is_(False)).order_by(ChatEvent.id).limit(500).all())
        return [(e.id, room.id, str(e.seq), room.user_low, room.user_high) for e, room in rows]


def acknowledge(ids):
    if ids:
        with SessionLocal() as db:
            db.query(ChatEvent).filter(ChatEvent.id.in_(ids)).update({"notified": True}, synchronize_session=False)
            db.commit()


def clean():
    with SessionLocal() as db:
        return service.cleanup(db)


async def worker():
    last_clean = 0
    while True:
        try:
            if time.monotonic() - last_clean > 60:
                last_clean = time.monotonic()
                try:
                    await asyncio.to_thread(clean)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # 清理失败不丢正文、不阻断独立在线提示，下一分钟重试。
                    logger.warning("Chat cleanup failed; message content retained for retry")
            rows = await asyncio.to_thread(pending)
            # 一次已读可产生很多事件，同会话只排入一个最新水位提示。
            grouped = {}
            for _, cid, seq, low, high in rows:
                grouped[cid] = (seq, low, high)
            for cid, (seq, low, high) in grouped.items():
                for uid in (low, high):
                    for queue in tuple(_connections.get(uid, ())):
                        if queue.full():
                            while not queue.empty():
                                queue.get_nowait()
                            queue.put_nowait(None)
                        else:
                            queue.put_nowait({"type": "dirty", "conversation_id": cid, "seq": seq})
            # 这里只表示完成在线提醒尝试，不表示送达/已读；离线客户端从数据库补收。
            await asyncio.to_thread(acknowledge, [r[0] for r in rows])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Chat background work failed; persistent events remain available")
        await asyncio.sleep(1)
