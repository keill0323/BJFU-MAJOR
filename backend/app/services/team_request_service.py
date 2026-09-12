"""入队申请、邀请的有效性与自动失效规则，所有列表/计数共用。"""
from datetime import datetime
from app.models.team import TeamMember, TeamApplication, TeamInvitation, ApplicationStatus


def actionable(db, model):
    membership = db.query(TeamMember.id).filter(TeamMember.user_id == model.user_id).exists()
    return (model.status == ApplicationStatus.PENDING, model.invalidated_at.is_(None), ~membership)


def invalidate_pending(db, user_id, *, keep_application=None, keep_invitation=None):
    """调用方持有用户锁；只更新标记、不提交，不修改已完成或其他用户的记录。"""
    now = datetime.now()
    for model, keep_id in ((TeamApplication, keep_application), (TeamInvitation, keep_invitation)):
        query = db.query(model).filter(model.user_id == user_id,
            model.status == ApplicationStatus.PENDING, model.invalidated_at.is_(None))
        if keep_id is not None:
            query = query.filter(model.id != keep_id)
        query.update({model.invalidated_at: now}, synchronize_session="fetch")
