"""微信一次性订阅通知。数据库出站队列与业务同事务，网络发送在提交后执行。

客户端记录仅表示用户曾同意订阅，实际可发送次数始终由微信校验。
发送超时/进程中断不能确定是否已送达，标为 unknown，不自动重发。
"""
from datetime import datetime, timedelta
import json
import logging
import re
import threading
import time
from urllib.request import Request, urlopen
from fastapi import HTTPException
from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.models.team import TeamApplication, TeamInvitation, ApplicationStatus, Team
from app.models.recruitment import WechatSubscription, WechatOutbox
from app.services.team_request_service import actionable

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


def record_choice(db, user_id, choices):
    configured = templates()
    allowed = {item["template_id"] for item in configured.values()}
    if not choices or any(tid not in allowed or choice not in ("accept", "reject", "ban") for tid, choice in choices.items()):
        raise HTTPException(422, "订阅配置已变化，请刷新后重试")
    # 锁用户，串行处理同一用户的授权回调；重复回调不会虚增发送次数。
    try:
        user = db.query(User).filter(User.id == user_id).with_for_update().one()
        for tid, choice in choices.items():
            row = db.get(WechatSubscription, (user_id, tid))
            if row is None:
                row = WechatSubscription(user_id=user_id, template_id=tid)
                db.add(row)
            row.enabled, row.updated_at = choice == "accept", datetime.now()
        # SessionLocal 关闭了 autoflush；入队前必须能查到本次新建的订阅记录。
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "订阅选择已记录；实际发送以微信授权为准"}


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


def deliver_pending(session_factory=SessionLocal, sender=send):
    if not templates():
        return
    with session_factory() as db:
        now = datetime.now()
        db.query(WechatOutbox).filter(WechatOutbox.status == "sending", WechatOutbox.updated_at < now - timedelta(minutes=5)).update(
            {"status": "unknown", "error_code": "interrupted"}, synchronize_session=False)
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
            recipient = db.get(User, row.user_id)
            subscription = db.get(WechatSubscription, (row.user_id, row.template_id))
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
            payload = dict(row.payload, touser=recipient.wx_openid, template_id=row.template_id, miniprogram_state=settings.WX_SUBSCRIBE_ENV, lang="zh_CN")
            db.commit()  # 网络期间不持有数据库事务/行锁。
            try:
                code = sender(payload)
                row.status, row.error_code = ("sent", None) if code == 0 else ("failed", str(code)[:48])
                # 43101 只影响本次通知；不能覆盖用户可能刚完成的新授权。
            except Exception:
                # 不记录异常正文，避免 HTTP 异常中的 access_token 泄露。
                row.status, row.error_code = "unknown", "transport_or_token_error"
            row.updated_at = datetime.now()
            db.commit()


def worker(stop):
    while not stop.is_set():
        try:
            deliver_pending()
        except Exception:
            logger.warning("WeChat outbox processing failed; queued events remain in database")
        stop.wait(10)
