"""有界清理助手正文和过期预览；保留短期幂等账本，不触及用户私聊。"""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import and_, or_
from app.database import SessionLocal
from app.models.assistant_session import AssistantConversation
from app.models.assistant_action import AssistantAction

logger = logging.getLogger(__name__)


def cleanup_expired(db, *, at=None, limit=200):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('cleanup batch must be between 1 and 500')
    at = at or datetime.now(timezone.utc).replace(tzinfo=None)
    conversations = [row.id for row in db.query(AssistantConversation.id).filter(
        AssistantConversation.expires_at <= at,
        or_(AssistantConversation.lease_until.is_(None), AssistantConversation.lease_until <= at),
    ).order_by(AssistantConversation.expires_at).limit(limit)]
    if conversations:
        db.query(AssistantConversation).filter(AssistantConversation.id.in_(conversations)).delete(synchronize_session=False)
    # 未确认预览到期24h后清理；已完成的最小账本保存30天用于重复确认核查。
    actions = [row.id for row in db.query(AssistantAction.id).filter(or_(
        and_(AssistantAction.state != 'completed', AssistantAction.expires_at <= at - timedelta(hours=24)),
        and_(AssistantAction.state == 'completed', AssistantAction.completed_at <= at - timedelta(days=30)),
    )).order_by(AssistantAction.created_at).limit(limit)]
    if actions:
        db.query(AssistantAction).filter(AssistantAction.id.in_(actions)).delete(synchronize_session=False)
    db.commit()
    return {'conversations_deleted': len(conversations), 'actions_deleted': len(actions)}


def _run(session_factory):
    with session_factory() as db:
        cleanup_expired(db)


async def worker(session_factory=SessionLocal):
    # 开关关闭后仍清理先前的助手正文，不依赖用户再次登录。
    while True:
        try:
            await asyncio.to_thread(_run, session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Assistant maintenance unavailable; cleanup will retry without logging content')
        await asyncio.sleep(60)
