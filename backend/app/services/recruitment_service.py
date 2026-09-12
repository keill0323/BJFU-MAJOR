"""招募不依赖赛事报名；沿用队伍审核与队长权限。"""
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import func, or_
from app.models.team import Team, TeamMember, TeamStatus
from app.models.user import User
from app.models.recruitment import RecruitmentPost
from app.services.team_service import _team_transaction


def public_players(db, before_id=None, limit=20, keyword=""):
    """所有非隐藏用户；不按报名、认证或是否已有队伍筛除。只投影公开字段。"""
    from app.services.auth_service import effective_identity
    query = (db.query(User, TeamMember.team_id, Team.name, Team.is_hidden)
             .outerjoin(TeamMember, TeamMember.user_id == User.id)
             .outerjoin(Team, Team.id == TeamMember.team_id).filter(User.is_hidden.is_(False)))
    if keyword.strip():
        query = query.filter(or_(User.nickname.contains(keyword.strip(), autoescape=True),
                                 User.game_id.contains(keyword.strip(), autoescape=True)))
    if before_id is not None:
        query = query.filter(User.id < before_id)
    rows = query.order_by(User.id.desc()).limit(limit + 1).all()
    items = [dict(id=u.id, nickname=u.nickname, game_id=u.game_id, avatar=u.avatar,
                  rank=u.rank, individual_rating=u.individual_rating or 0,
                  is_verified=bool(u.is_verified), identity=effective_identity(u) if u.is_verified else None,
                  user_description=u.user_description, has_team=team_id is not None,
                  team_name=name if team_id and not hidden else None)
             for u, team_id, name, hidden in rows[:limit]]
    return dict(items=items, has_more=len(rows) > limit,
                next_cursor=items[-1]["id"] if len(rows) > limit else None)


def public_posts(db, before_id=None, limit=20):
    counts = db.query(TeamMember.team_id, func.count(TeamMember.id).label("count")).group_by(TeamMember.team_id).subquery()
    query = (db.query(RecruitmentPost, Team, func.coalesce(counts.c.count, 0))
             .join(Team, Team.id == RecruitmentPost.team_id)
             .join(User, User.id == Team.captain_id)
             .outerjoin(counts, counts.c.team_id == Team.id)
             .filter(RecruitmentPost.is_active.is_(True), Team.is_hidden.is_(False),
                     User.is_hidden.is_(False), Team.status == TeamStatus.APPROVED))
    if before_id is not None:
        query = query.filter(RecruitmentPost.id < before_id)
    rows = query.order_by(RecruitmentPost.id.desc()).limit(limit + 1).all()
    items = [dict(id=p.id, team_id=p.team_id, team_name=t.name, logo=t.logo, rating=t.rating,
                  member_count=count, content=p.content, updated_at=p.updated_at)
             for p, t, count in rows[:limit]]
    return dict(items=items, has_more=len(rows) > limit,
                next_cursor=items[-1]["id"] if len(rows) > limit else None)


def my_post(db, user_id):
    team = db.query(Team).filter(Team.captain_id == user_id, Team.is_hidden.is_(False)).first()
    if not team:
        return None
    post = db.query(RecruitmentPost).filter(RecruitmentPost.team_id == team.id).first()
    return dict(team_id=team.id, team_name=team.name, can_publish=team.status == TeamStatus.APPROVED,
                content=post.content if post else "", is_active=bool(post and post.is_active))


def save_post(db, team_id, user_id, content):
    content = content.strip()
    if not 1 <= len(content) <= 500:
        raise HTTPException(422, "招募内容需为 1–500 字")
    with _team_transaction(db, team_id) as team:
        if team.captain_id != user_id:
            raise HTTPException(403, "只有队长才能发布招募")
        if team.is_hidden or team.status != TeamStatus.APPROVED:
            raise HTTPException(400, "队伍审核通过后才能发布招募")
        post = db.query(RecruitmentPost).filter(RecruitmentPost.team_id == team_id).with_for_update().first()
        if not post:
            post = RecruitmentPost(team_id=team_id)
            db.add(post)
        post.content, post.is_active, post.updated_at = content, True, datetime.now()
    return my_post(db, user_id)


def close_post(db, team_id, user_id):
    with _team_transaction(db, team_id) as team:
        if team.captain_id != user_id:
            raise HTTPException(403, "只有队长才能关闭招募")
        post = db.query(RecruitmentPost).filter(RecruitmentPost.team_id == team_id).with_for_update().first()
        if post:
            post.is_active, post.updated_at = False, datetime.now()
    return {"message": "招募已关闭"}
