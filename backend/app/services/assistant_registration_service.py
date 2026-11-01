"""报名工具只能准备确认卡；确认端点独立鉴权并原子提交业务与幂等记录。

预览不持行锁，也不调用真实报名来模拟检查。确认的 pending→executing
认领、原报名服务的 flush 和 completed 结果在同一短事务提交；没有可见的
“executing 已提交、报名未提交”间隙，进程中断由数据库回滚。
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from app.models.assistant_action import AssistantAction
from app.models.match import Match, Registration
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services import match_service
from app.services.auth_service import effective_identity


ACTION_TTL_SECONDS = 600


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _value(value):
    return getattr(value, "value", value)


def _fail(code, message, status=409):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _safe_reason(error):
    detail = error.detail
    message = detail.get("message", "暂时无法报名") if isinstance(detail, dict) else str(detail)
    # 旧资格提示含管理员个人QQ，不把它自动变成助手的公开通讯录。
    message = re.sub(r"[（(]\s*QQ\s*[：:]\s*[0-9]+\s*[）)]", "", message, flags=re.I)
    return message[:500]


def _actor(db, actor, *, for_update=False):
    actor_id = getattr(actor, "id", None)
    if type(actor_id) is not int or actor_id <= 0:
        _fail("unauthenticated", "请先登录", 401)
    query = db.query(User).filter(User.id == actor_id).populate_existing()
    current = (query.with_for_update() if for_update else query).first()
    if current is None or current.is_hidden:
        _fail("account_unavailable", "当前账号不可使用赛事助手报名", 403)
    return current


def _positive_id(value, name):
    if type(value) is not int or value <= 0:
        _fail("invalid_arguments", name + "必须是正整数", 422)


def _team(db, user_id, team_id, *, for_update=False):
    if team_id is None:
        teams = db.query(Team).filter(Team.captain_id == user_id, Team.is_hidden.is_(False)).all()
        if len(teams) != 1:
            _fail("team_selection_required", "请先选择由你担任队长的队伍", 409)
        team = teams[0]
    else:
        _positive_id(team_id, "队伍ID")
        team = (match_service.lock_row(db, Team, team_id) if for_update else
                db.query(Team).filter(Team.id == team_id).populate_existing().first())
    if team is None or team.is_hidden:
        _fail("team_unavailable", "队伍不存在或不可用", 404)
    if team.captain_id != user_id:
        _fail("captain_required", "只有队长才能发起队伍报名", 403)
    return team


def _inspect(db, actor, match_id, mode, team_id=None, *, for_update=False):
    """调用共享原规则。返回的条件快照仅用于哈希，不进入模型或卡片。"""
    _positive_id(match_id, "赛事ID")
    if mode not in ("personal", "team") or (mode == "personal" and team_id is not None):
        _fail("invalid_arguments", "报名方式应为personal或team，个人报名不能指定队伍", 422)
    current = _actor(db, actor)
    if mode == "personal":
        match, current = match_service._validate_user_registration(
            db, match_id, current.id, for_update=for_update)
        members = [current]
        team = None
    else:
        match = (match_service._lock_match(db, match_id) if for_update else
                 db.query(Match).filter(Match.id == match_id).populate_existing().first())
        if match is None:
            _fail("match_not_found", "赛事不存在", 404)
        team = _team(db, current.id, team_id, for_update=for_update)
        if match_service.is_roster_locked(db, match.id, for_update=for_update):
            raise HTTPException(400, "赛事已开始编排，参赛名单已锁定，不能新增队伍报名")
        membership = match_service._validate_team_registration(
            db, match, team.id, for_update=for_update)
        match_service._check_match_registerable(db, match, for_update=for_update)
        query = db.query(Registration.id).filter(Registration.match_id == match.id,
                                                  Registration.team_id == team.id)
        if (query.with_for_update() if for_update else query).first():
            raise HTTPException(400, "该队伍已报名此赛事，请勿重复报名")
        query = db.query(User).filter(User.id.in_([m.user_id for m in membership])).populate_existing()
        members = (query.with_for_update() if for_update else query).all()
        query = db.query(Registration.id).filter(
            Registration.match_id == match.id,
            Registration.user_id.in_([m.id for m in members]),
            Registration.team_id.isnot(None), Registration.team_id != team.id)
        conflicting = (query.with_for_update() if for_update else query).first()
        if conflicting:
            raise HTTPException(400, "队员在该赛事中已有其他队伍报名，请先处理原报名")

    if for_update:
        current = _actor(db, actor, for_update=True)

    snapshot = {
        "match": [match.id, match.name, _value(match.status), match.match_type, match.max_teams,
                  match.team_size, str(match.register_start), str(match.register_end)],
        "mode": mode, "user_id": current.id,
        "team": [team.id, team.name, team.captain_id, _value(team.status)] if team else None,
        "members": sorted([[u.id, bool(u.is_verified), u.rank, effective_identity(u)] for u in members]),
    }
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=True, sort_keys=True).encode()).hexdigest()
    return {"match_id": match.id, "match_name": match.name, "team_id": team.id if team else None,
            "team_name": team.name if team else None, "mode": mode, "member_count": len(members),
            "snapshot_hash": digest, "user_id": current.id}


def _diagnostic(data, *, eligible, message, code="eligible"):
    public = {k: v for k, v in data.items() if k not in ("snapshot_hash", "user_id")}
    public.update(eligible=eligible, code=code)
    return {"status": "ok" if eligible else "blocked", "message": message, "data": public, "cards": []}


def check_registration(db, actor, match_id, mode, team_id=None):
    """纯只读资格诊断，无加锁/flush/commit，也不尝试真实报名。"""
    try:
        info = _inspect(db, actor, match_id, mode, team_id)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        return _diagnostic({"match_id": match_id, "mode": mode, "team_id": team_id},
                           eligible=False, message=_safe_reason(error),
                           code=detail.get("code", "registration_ineligible"))
    return _diagnostic(info, eligible=True, message="当前条件满足，可准备报名确认卡；尚未提交报名")


def preview_registration(db, actor, match_id, mode, team_id=None):
    """只持久化限时确认意图；用户尚未确认，不能创建Registration。"""
    try:
        info = _inspect(db, actor, match_id, mode, team_id)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        return _diagnostic({"match_id": match_id, "mode": mode, "team_id": team_id},
                           eligible=False, message=_safe_reason(error),
                           code=detail.get("code", "registration_ineligible"))
    action_id = secrets.token_urlsafe(32)
    expires = _now() + timedelta(seconds=ACTION_TTL_SECONDS)
    db.add(AssistantAction(id=action_id, user_id=info["user_id"], match_id=match_id,
                           team_id=info["team_id"], mode=mode, state="pending",
                           snapshot_hash=info["snapshot_hash"], expires_at=expires))
    db.commit()  # No LLM/network wait holds this transaction.
    message = ("确认后提交队伍报名，状态为待管理员审核；尚未取得参赛资格" if mode == "team" else
               "确认后提交个人报名，沿用现有个人报名通过规则；这不是队伍参赛资格")
    response = _diagnostic(info, eligible=True, message="请核对卡片后点击确认；聊天回复不视为确认")
    response["data"].update(action_id=action_id, state="pending", expires_at=_iso(expires))
    response["cards"] = [{"type": "registration_preview", "mode": mode, "title": "报名确认",
                          "summary": message, "expires_at": _iso(expires),
                          "items": [{"title": info["match_name"],
                                     "subtitle": info["team_name"] or "个人报名",
                                     "detail": f"本次报名人数：{info['member_count']}"}],
                          "action": {"kind": "confirm_registration", "action_id": action_id}}]
    return response


def _completed(db, action, *, replayed):
    saved = json.loads(action.result_json)
    records = db.query(Registration).filter(Registration.id.in_(saved["registration_ids"])).all()
    statuses = sorted({_value(r.status) for r in records})
    intact = len(records) == len(saved["registration_ids"])
    status = statuses[0] if intact and len(statuses) == 1 else "changed"
    title = {"pending": "报名已提交，待管理员审核", "approved": "报名已通过",
             "rejected": "当前报名已被驳回", "changed": "报名已提交，当前记录已发生变化"}[status]
    if action.mode == "personal" and status == "approved":
        title = "个人报名已通过；队伍参赛仍需队伍报名审核"
    data = {"action_id": action.id, "state": "completed", "mode": action.mode,
            "match_id": action.match_id, "team_id": action.team_id,
            "registration_status": status, "registration_statuses": statuses,
            "record_count": len(records), "replayed": replayed}
    return {"status": "ok", "message": title, "data": data,
            "cards": [{"type": "registration_result", "mode": action.mode, "title": "报名结果", "summary": title,
                       "items": [{"title": title, "badge": status}]}]}


def _invalidate(db, action, code, message, state="invalidated", *, status=409):
    action.state = state
    db.commit()
    _fail(code, message, status)


def confirm_registration(db, actor, action_id):
    """仅供独立用户确认端点调用；绝不能注册为模型可执行工具。"""
    current = _actor(db, actor)
    actor_id = current.id
    if not isinstance(action_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", action_id):
        _fail("action_not_found", "确认操作不存在", 404)
    try:
        # UPDATE is the first locking operation. It serializes concurrent
        # connections on SQLite as well as MySQL without a process-local lock.
        claimed = db.execute(update(AssistantAction).where(
            AssistantAction.id == action_id, AssistantAction.user_id == actor_id,
            AssistantAction.state == "pending",
        ).values(state="executing").execution_options(synchronize_session=False)).rowcount
        action = db.query(AssistantAction).filter(AssistantAction.id == action_id,
                                                   AssistantAction.user_id == actor_id).populate_existing().first()
        if action is None:
            db.rollback()
            _fail("action_not_found", "确认操作不存在", 404)
        if not claimed:
            if action.state == "completed":
                if action.mode == "team":
                    _team(db, actor_id, action.team_id, for_update=True)
                response = _completed(db, action, replayed=True)
                db.commit()
                return response
            db.rollback()
            _fail("action_not_pending", "该确认已失效，请重新准备报名预览")
        if action.expires_at <= _now():
            _invalidate(db, action, "action_expired", "确认已过期，请重新准备报名预览", "expired")
        try:
            info = _inspect(db, current, action.match_id, action.mode, action.team_id, for_update=True)
        except HTTPException as error:
            code = error.detail.get("code") if isinstance(error.detail, dict) else None
            _invalidate(db, action, code if error.status_code == 403 and code else "preconditions_changed",
                        "报名条件已变化，请重新预览：" + _safe_reason(error),
                        status=403 if error.status_code == 403 else 409)
        if info["snapshot_hash"] != action.snapshot_hash:
            _invalidate(db, action, "preconditions_changed", "报名对象、阵容或资格条件已变化，请重新准备报名预览")
        try:
            if action.mode == "team":
                records = match_service.register_team(db, action.match_id, action.team_id, commit=False)
            else:
                records = [match_service.register_user(db, action.match_id, actor_id, commit=False)]
        except HTTPException as error:
            _invalidate(db, action, "preconditions_changed", "报名校验未通过，请重新预览：" + _safe_reason(error))
        action.state = "completed"
        action.completed_at = _now()
        action.result_json = json.dumps({"registration_ids": [r.id for r in records]})
        db.flush()
        response = _completed(db, action, replayed=False)
        db.commit()  # Business writes and completed ledger become visible together.
        return response
    except HTTPException:
        db.rollback()
        raise
    except DBAPIError:
        db.rollback()
        # An ambiguous commit response may already have succeeded. Re-read the
        # durable record before telling the user to retry, never resubmit blindly.
        try:
            action = db.query(AssistantAction).filter(AssistantAction.id == action_id,
                                                       AssistantAction.user_id == actor_id).populate_existing().first()
            if action is not None and action.state == "completed":
                response = _completed(db, action, replayed=True)
                db.rollback()
                return response
        except DBAPIError:
            db.rollback()
        _fail("confirmation_result_unknown", "暂时无法确认报名结果，请用同一确认操作重试核查", 503)
