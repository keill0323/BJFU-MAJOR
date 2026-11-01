"""受限审计写入与查询，提交由业务调用方负责。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.admin_audit import AdminAudit
from app.models.user import User, UserRole


ACTION_REASONS = {
    "user.update": "管理员更新用户资料",
    "user.role.update": "管理员修改用户权限",
    "user.student_id.recognize": "管理员重新识别候选学号",
    "rank_application.approve": "管理员通过段位申请",
    "rank_application.reject": "管理员驳回段位申请",
    "round.schedule": "管理员统一安排比赛时间",
}
USER_FIELDS = frozenset({
    "student_id", "ai_student_id", "identity", "is_verified", "rank",
    "individual_rating", "ai_review_status", "verify_reject_reason", "role",
})
AUDIT_FIELDS = USER_FIELDS | {
    "verify_image_present", "verify_image_changed", "application_status", "reject_reason",
}


@dataclass(frozen=True)
class AuditActor:
    id: int
    role: str


def resolve_actor(db: Session, actor: Optional[User], *, admin_only=False) -> Optional[AuditActor]:
    """重新核对数据库权限；None 仅兼容既有内部调用，HTTP 始终传鉴权用户。"""
    if actor is None:
        return None
    current = (db.query(User).filter(User.id == actor.id).populate_existing().first())
    roles = (UserRole.ADMIN,) if admin_only else (UserRole.ADMIN, UserRole.REVIEWER)
    if current is None or current.role not in roles:
        raise HTTPException(status_code=403, detail="无权执行管理员操作")
    return AuditActor(current.id, current.role.value)


def user_snapshot(user: User) -> dict:
    values = {name: getattr(user, name) for name in USER_FIELDS}
    values["role"] = user.role.value
    values["verify_image_present"] = bool(user.verify_image)
    return values


def normalize_reason(reason: Optional[str], action: str) -> str:
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise HTTPException(status_code=400, detail="操作说明须为不超过500字的文本")
    return (reason or "").strip() or ACTION_REASONS[action]


def record_change(db: Session, actor: Optional[AuditActor], target_user_id: int,
                  action: str, before: dict, after: dict, *, reason=None,
                  application_id=None) -> Optional[AdminAudit]:
    """只保存白名单里有变化的值；不 commit，不写应用日志。"""
    if action not in ACTION_REASONS:
        raise ValueError("未知审计操作")
    keys = {key for key in before.keys() | after.keys()
            if key in AUDIT_FIELDS and before.get(key) != after.get(key)}
    if actor is None or not keys:
        return None
    entry = AdminAudit(
        actor_id=actor.id, actor_role=actor.role, target_user_id=target_user_id,
        action=action, application_id=application_id,
        before_values={key: before.get(key) for key in sorted(keys)},
        after_values={key: after.get(key) for key in sorted(keys)},
        reason=normalize_reason(reason, action),
    )
    db.add(entry)
    return entry


def record_round_schedule(db: Session, actor: AuditActor, captain_id: int,
                          match_id: int, round_id: int, before: dict, after: dict):
    """沿用按用户留痕的审计表，双方队长各一条；只保存排期及场次定位信息。"""
    allowed = ("scheduled_time", "team1_confirmed", "team2_confirmed")
    def snapshot(values):
        result = {"match_id": match_id, "round_id": round_id}
        for name in allowed:
            value = values[name]
            result[name] = value.isoformat() if isinstance(value, datetime) else value
        return result
    entry = AdminAudit(
        actor_id=actor.id, actor_role=actor.role, target_user_id=captain_id,
        action="round.schedule", before_values=snapshot(before), after_values=snapshot(after),
        reason=ACTION_REASONS["round.schedule"],
    )
    db.add(entry)
    return entry


def list_audits(db: Session, *, actor_id=None, target_user_id=None, action=None,
                application_id=None, created_from=None, created_to=None,
                page=1, page_size=20) -> dict:
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=400, detail="分页参数无效")
    if action is not None and action not in ACTION_REASONS:
        raise HTTPException(status_code=400, detail="未知审计操作")
    def utc_naive(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value
    created_from, created_to = utc_naive(created_from), utc_naive(created_to)
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=400, detail="开始时间不能晚于结束时间")
    query = db.query(AdminAudit)
    for field, value in (("actor_id", actor_id), ("target_user_id", target_user_id),
                         ("action", action), ("application_id", application_id)):
        if value is not None:
            query = query.filter(getattr(AdminAudit, field) == value)
    if created_from:
        query = query.filter(AdminAudit.created_at >= created_from)
    if created_to:
        query = query.filter(AdminAudit.created_at <= created_to)
    total = query.count()
    items = (query.order_by(AdminAudit.created_at.desc(), AdminAudit.id.desc())
             .offset((page - 1) * page_size).limit(page_size).all())
    return {"items": items, "total": total, "page": page, "page_size": page_size}
