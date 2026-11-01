"""微信一次性订阅通知。数据库出站队列与业务同事务，网络发送在提交后执行。

客户端记录仅表示用户曾同意订阅，实际可发送次数始终由微信校验。
发送超时/进程中断不能确定是否已送达，标为 unknown，不自动重发。
"""
from datetime import datetime, timedelta
import hashlib
import json
import logging
import re
import threading
import time
from urllib.request import Request, urlopen
from fastapi import HTTPException
from sqlalchemy import and_, or_
from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.models.team import TeamApplication, TeamInvitation, ApplicationStatus, Team
from app.models.recruitment import (WechatSubscription, WechatOutbox, WechatSubscriptionAttempt,
                                    WechatSubscriptionState, WechatSubscriptionDelivery)
from app.services.team_request_service import actionable
from app.services.locking import lock_row

logger = logging.getLogger(__name__)
KINDS = {"team_invitation": "入队邀请", "team_application": "入队申请"}
VALUES = {"team_name", "actor_name", "event", "time"}
_token_lock = threading.Lock()
_token = {"key": None, "value": "", "until": 0}


def templates():
    if not settings.WX_SUBSCRIBE_ENABLED or settings.WX_MOCK_LOGIN or not settings.WX_APPID or not settings.WX_SECRET:
        return {}
    try:
        raw = json.loads(settings.WX_SUBSCRIBE_TEMPLATES)
        if not isinstance(raw, dict) or settings.WX_SUBSCRIBE_ENV not in ("formal", "trial", "developer"):
            return {}
        result = {}
        for kind, item in raw.items():
            if kind not in KINDS or not isinstance(item, dict):
                continue
            tid, fields = item.get("template_id"), item.get("fields")
            if not isinstance(tid, str) or not re.fullmatch(r"[\w-]{10,128}", tid, flags=re.ASCII):
                continue
            if not isinstance(fields, dict) or not fields or len(fields) > 10:
                continue
            if not all(re.fullmatch(r"(thing|name|time|phrase)\d+", k) and v in VALUES for k, v in fields.items() if isinstance(k, str) and isinstance(v, str)):
                continue
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
                continue
            if any((k.startswith("time") != (v == "time")) for k, v in fields.items()):
                continue
            result[kind] = {"template_id": tid, "fields": fields}
        return result
    except (ValueError, TypeError):
        return {}


def configuration():
    items = [{"kind": k, "label": KINDS[k], "template_id": v["template_id"]} for k, v in templates().items()]
    return {"enabled": bool(items), "templates": items}


def _legacy_needs_resubscribe(db, user_id, template_id, subscription):
    if subscription is None or not subscription.enabled:
        return True
    used = db.query(WechatOutbox).filter(
        WechatOutbox.user_id == user_id, WechatOutbox.template_id == template_id,
        or_(WechatOutbox.status == "sent",
            and_(WechatOutbox.status == "failed", WechatOutbox.error_code == "43101")),
    ).order_by(WechatOutbox.updated_at.desc(), WechatOutbox.id.desc()).first()
    return bool(used and used.updated_at >= subscription.updated_at)


def _state_for_update(db, user_id, template_id, subscription):
    """调用方先锁用户；不把旧本地enabled当作微信剩余次数。"""
    state = db.query(WechatSubscriptionState).filter_by(
        user_id=user_id, template_id=template_id).populate_existing().with_for_update().first()
    if state is None:
        state = WechatSubscriptionState(
            user_id=user_id, template_id=template_id, grant_version=0,
            last_choice="accept" if subscription and subscription.enabled else None,
            accepted_at=subscription.updated_at if subscription and subscription.enabled else None,
            needs_resubscribe=(_legacy_needs_resubscribe(db, user_id, template_id, subscription)
                               if subscription else False),
        )
        db.add(state)
    return state


def subscription_state(db, user_id):
    """仅返回当前账号及当前配置模板。GET不写入，也不推测真实剩余额度。"""
    config = configuration()
    result, cache = [], {}
    for item in config["templates"]:
        tid = item["template_id"]
        if tid not in cache:
            subscription = db.get(WechatSubscription, (user_id, tid))
            state = db.get(WechatSubscriptionState, (user_id, tid))
            latest = db.query(WechatOutbox).filter_by(user_id=user_id, template_id=tid).order_by(
                WechatOutbox.updated_at.desc(), WechatOutbox.id.desc()).first()
            successes = db.query(WechatOutbox.id).filter_by(user_id=user_id, template_id=tid, status="sent").count()
            enabled = bool(subscription and subscription.enabled)
            needs = (not enabled or bool(state.needs_resubscribe)) if state else _legacy_needs_resubscribe(
                db, user_id, tid, subscription)
            delivery = latest.status if latest else None
            if not enabled:
                message = "尚未记录允许接收该提醒，请点击订阅；实际发送次数由微信校验"
            elif delivery == "unknown":
                message = "上次发送结果待确认，不会自动重发；剩余次数未知，可主动补充订阅"
            elif delivery == "failed" and latest.error_code != "43101":
                message = "上次微信提醒未发送成功，请查看站内消息；可联系管理员检查配置"
            elif needs:
                message = ("微信未接受上次提醒，建议重新订阅；不影响站内消息" if latest and latest.error_code == "43101" else
                           "已使用一次提醒，建议补充订阅；不代表全部次数已耗尽" if successes else
                           "建议再次订阅，以确认接收提醒；剩余次数未知")
            else:
                message = "已记录允许接收提醒；剩余次数未知，实际发送以微信校验为准"
            cache[tid] = {"enabled": enabled, "has_accepted": bool(enabled or (state and state.accepted_at)),
                          "actual_remaining": "unknown", "last_delivery_status": delivery,
                          "last_error_code": _public_error(latest.error_code) if latest else None,
                          "successful_send_count": successes, "needs_resubscribe": needs,
                          "grant_version": state.grant_version if state else 0,
                          "authorization_attempts_tracked": bool(state and state.last_attempt_id),
                          "message": message}
        result.append(dict(item, **cache[tid]))
    return {"enabled": config["enabled"], "templates": result}


def _public_error(value):
    if value is None:
        return None
    if value in ("interrupted", "transport_or_token_error", "provider_response_invalid"):
        return value
    return str(value) if re.fullmatch(r"-?[0-9]{1,12}", str(value)) else "delivery_error"


def record_choice(db, user_id, choices, *, attempt_id=None):
    if not isinstance(choices, dict) or not 1 <= len(choices) <= 3 or any(
            not isinstance(tid, str) or choice not in ("accept", "reject", "ban") for tid, choice in choices.items()):
        raise HTTPException(422, "订阅选择无效")
    if attempt_id is not None and (not isinstance(attempt_id, str) or
                                   not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", attempt_id)):
        raise HTTPException(422, "订阅尝试标识无效")
    digest = hashlib.sha256(json.dumps(choices, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    configured = templates()
    allowed = {item["template_id"] for item in configured.values()}
    try:
        # 与发送回执使用相同用户锁；SQLite也使用真实写锁，防重复回调并发插入。
        if lock_row(db, User, user_id) is None:
            raise HTTPException(404, "用户不存在")
        if attempt_id is not None:
            attempt = db.query(WechatSubscriptionAttempt).filter_by(
                user_id=user_id, attempt_id=attempt_id).populate_existing().with_for_update().first()
            if attempt is not None:
                if attempt.choices_hash != digest:
                    raise HTTPException(409, "同一次订阅尝试的回调内容不一致，请刷新后重试")
                db.commit()
                return {"message": "该订阅回调已记录；实际发送以微信授权为准", "idempotent": True,
                        "attempt_id": attempt_id, "attempt_recorded": True}
        if any(tid not in allowed for tid in choices):
            raise HTTPException(422, "订阅配置已变化，请刷新后重试")
        now = datetime.now()
        for tid, choice in choices.items():
            row = db.query(WechatSubscription).filter_by(user_id=user_id, template_id=tid).populate_existing().with_for_update().first()
            state = _state_for_update(db, user_id, tid, row)
            if row is None:
                row = WechatSubscription(user_id=user_id, template_id=tid)
                db.add(row)
            row.enabled, row.updated_at = choice == "accept", now
            state.last_choice, state.updated_at = choice, now
            if choice == "accept":
                state.accepted_at = now
                if attempt_id is not None:
                    # 这是客户端报告的不同授权尝试版本，不是微信额度计数。
                    state.grant_version += 1
                    state.needs_resubscribe = False
            else:
                state.needs_resubscribe = True
            if attempt_id is not None:
                state.last_attempt_id = attempt_id
        if attempt_id is not None:
            db.add(WechatSubscriptionAttempt(user_id=user_id, attempt_id=attempt_id,
                                              choices_hash=digest, choices=dict(choices)))
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "订阅选择已记录；实际发送以微信授权为准", "idempotent": False,
            "attempt_id": attempt_id, "attempt_recorded": attempt_id is not None}


def _can_receive(user):
    return bool(user and not user.is_hidden and user.wx_openid and
                not user.wx_openid.startswith(("bot:", "mock_", "test-")))


def enqueue(db, kind, source_id, recipient_id, team, actor):
    spec = templates().get(kind)
    if not spec:
        return
    recipient = db.get(User, recipient_id)
    if not _can_receive(recipient):
        return
    subscription = db.get(WechatSubscription, (recipient_id, spec["template_id"]))
    if not subscription or not subscription.enabled:
        return
    values = {"team_name": team.name, "actor_name": actor.nickname or actor.game_id or "选手",
              "event": KINDS[kind], "time": datetime.now().strftime("%Y年%m月%d日 %H:%M")}
    data = {}
    for key, semantic in spec["fields"].items():
        value = re.sub(r"[\r\n\t]", " ", values[semantic])
        maximum = 5 if key.startswith("phrase") else 10 if key.startswith("name") else 20
        data[key] = {"value": value if key.startswith("time") else value[:maximum]}
    db.add(WechatOutbox(user_id=recipient_id, kind=kind, source_id=source_id, template_id=spec["template_id"],
                       payload={"data": data, "page": "pages/messages/messages" if kind == "team_invitation" else "pages/team/team"}))


def _post(url, body):
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read(65536))


def access_token(refresh=False):
    with _token_lock:
        key = (settings.WX_APPID, settings.WX_SECRET)
        if not refresh and _token["key"] == key and time.monotonic() < _token["until"]:
            return _token["value"]
        result = _post("https://api.weixin.qq.com/cgi-bin/stable_token", {
            "grant_type": "client_credential", "appid": settings.WX_APPID, "secret": settings.WX_SECRET,
            "force_refresh": False})
        if not result.get("access_token"):
            raise RuntimeError("微信 access_token 获取失败")
        _token.update(key=key, value=result["access_token"], until=time.monotonic() + max(0, int(result.get("expires_in", 7200)) - 120))
        return _token["value"]


def send(payload):
    for attempt in range(2):
        token = access_token(refresh=bool(attempt))
        result = _post("https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=" + token, payload)
        code = result.get("errcode")
        if code in (40001, 40014, 42001) and attempt == 0:
            continue  # 明确被拒绝才刷新令牌重试，不重试超时。
        return code


def _capture_delivery(db, row, subscription):
    """调用方持有用户锁；版本在网络发送前持久化，旧发送不能覆盖新授权。"""
    receipt = db.query(WechatSubscriptionDelivery).filter_by(
        outbox_id=row.id).populate_existing().with_for_update().first()
    if receipt is not None:
        # An outbox was manually reset or interrupted. A durable attempt must
        # never become a second automatic send merely by changing pending.
        row.status = receipt.status if receipt.status != "sending" else "unknown"
        row.error_code = receipt.error_code if receipt.status != "sending" else "interrupted"
        row.updated_at = datetime.now()
        if receipt.status == "sending":
            receipt.status, receipt.error_code = "unknown", "interrupted"
            receipt.updated_at = datetime.now()
        db.commit()
        return None
    state = _state_for_update(db, row.user_id, row.template_id, subscription)
    version = state.grant_version
    db.add(WechatSubscriptionDelivery(outbox_id=row.id, user_id=row.user_id,
                                     template_id=row.template_id, grant_version=version, status="sending"))
    return version


def _finish_delivery(db, notice_id, version, status, error_code):
    row = db.query(WechatOutbox).filter_by(id=notice_id).first()
    if row is None:
        return
    # Same lock order as record_choice; no network operation occurs in here.
    if lock_row(db, User, row.user_id) is None:
        db.rollback()
        return
    row = lock_row(db, WechatOutbox, notice_id)
    receipt = db.query(WechatSubscriptionDelivery).filter_by(
        outbox_id=notice_id).populate_existing().with_for_update().first()
    if row is None or row.status != "sending" or (receipt and receipt.status != "sending"):
        db.rollback()
        return  # A late worker cannot turn an interrupted/unknown receipt into a new send.
    subscription = db.query(WechatSubscription).filter_by(
        user_id=row.user_id, template_id=row.template_id).populate_existing().with_for_update().first()
    state = _state_for_update(db, row.user_id, row.template_id, subscription)
    if receipt is None:
        receipt = WechatSubscriptionDelivery(outbox_id=row.id, user_id=row.user_id,
                                             template_id=row.template_id, grant_version=version)
        db.add(receipt)
    now = datetime.now()
    row.status, row.error_code, row.updated_at = status, error_code, now
    receipt.status, receipt.error_code, receipt.updated_at = status, error_code, now
    # Success means one confirmed use, not "all grants consumed". A newer
    # callback is preserved even when an older send finishes later.
    if state.grant_version == version and (status == "sent" or (status == "failed" and error_code == "43101")):
        state.needs_resubscribe, state.updated_at = True, now
    db.commit()  # Receipt and outbox terminal state commit atomically.


def deliver_pending(session_factory=SessionLocal, sender=send):
    if not templates():
        return
    with session_factory() as db:
        now = datetime.now()
        db.query(WechatOutbox).filter(WechatOutbox.status == "sending", WechatOutbox.updated_at < now - timedelta(minutes=5)).update(
            {"status": "unknown", "error_code": "interrupted"}, synchronize_session=False)
        db.query(WechatSubscriptionDelivery).filter(
            WechatSubscriptionDelivery.status == "sending",
            WechatSubscriptionDelivery.outbox_id.in_(
                db.query(WechatOutbox.id).filter(WechatOutbox.status == "unknown")),
        ).update({"status": "unknown", "error_code": "interrupted", "updated_at": now}, synchronize_session=False)
        db.query(WechatOutbox).filter(WechatOutbox.status == "pending", WechatOutbox.created_at < now - timedelta(days=1)).update(
            {"status": "expired"}, synchronize_session=False)
        db.commit()
        ids = [row.id for row in db.query(WechatOutbox.id).filter(WechatOutbox.status == "pending").order_by(WechatOutbox.id).limit(20)]
    for notice_id in ids:
        with session_factory() as db:
            # 条件更新领取，多个进程只能有一个发送者。
            claimed = db.query(WechatOutbox).filter(WechatOutbox.id == notice_id, WechatOutbox.status == "pending").update(
                {"status": "sending", "updated_at": datetime.now()}, synchronize_session=False)
            db.commit()
            if not claimed:
                continue
            row = db.get(WechatOutbox, notice_id)
            recipient = lock_row(db, User, row.user_id)
            subscription = db.query(WechatSubscription).filter_by(
                user_id=row.user_id, template_id=row.template_id).populate_existing().with_for_update().first()
            if row.kind in ("team_invitation", "team_application"):
                source_model = TeamInvitation if row.kind == "team_invitation" else TeamApplication
                source = db.query(source_model).filter(source_model.id == row.source_id, *actionable(db, source_model)).first()
                team = db.get(Team, source.team_id) if source else None
                source_valid = bool(source and source.status == ApplicationStatus.PENDING and team and
                                    (row.kind != "team_application" or team.captain_id == row.user_id))
            else:
                team, source_valid = None, False
            spec = templates().get(row.kind)
            if (not _can_receive(recipient) or not subscription or not subscription.enabled or
                    not source_valid or not team or team.is_hidden or
                    not spec or spec["template_id"] != row.template_id):
                row.status = "skipped"
                db.commit()
                continue
            version = _capture_delivery(db, row, subscription)
            if version is None:
                continue
            payload = dict(row.payload, touser=recipient.wx_openid, template_id=row.template_id, miniprogram_state=settings.WX_SUBSCRIBE_ENV, lang="zh_CN")
            db.commit()  # 网络期间不持有数据库事务/行锁。
            try:
                code = sender(payload)
                if type(code) is not int:
                    status, error = "unknown", "provider_response_invalid"
                else:
                    status, error = ("sent", None) if code == 0 else ("failed", str(code)[:48])
            except Exception:
                # 不记录异常正文，避免 HTTP 异常中的 access_token 泄露。
                status, error = "unknown", "transport_or_token_error"
            _finish_delivery(db, notice_id, version, status, error)


def worker(stop):
    while not stop.is_set():
        try:
            deliver_pending()
        except Exception:
            logger.warning("WeChat outbox processing failed; queued events remain in database")
        stop.wait(10)
