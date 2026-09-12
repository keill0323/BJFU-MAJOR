"""招募不依赖赛事报名；沿用队伍审核与队长权限。"""
from datetime import datetime
from typing import Literal, get_args
from fastapi import HTTPException
from sqlalchemy import Integer, and_, case, cast, func, or_
from app.models.team import Team, TeamMember, TeamStatus
from app.models.user import User
from app.models.recruitment import RecruitmentPost
from app.services.team_service import _team_transaction


RankFilter = Literal["", "D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++",
                     "s", "s_gold", "s_diamond", "s_demon", "unranked"]
IdentityFilter = Literal["", "new_student", "senior", "unknown"]
_STANDARD_RANKS = ("D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++")


def _ascii_digits(value):
    """Portable ASCII validation; unlike casts, rejects Unicode digits and suffixes."""
    remaining = value
    for digit in "0123456789":
        remaining = func.replace(remaining, digit, "")
    return and_(func.length(value) > 0, func.length(remaining) == 0)


def _rank_condition(selected):
    rank = func.coalesce(User.rank, "")
    # HEX keeps case and trailing spaces exact even under MySQL's default collation.
    encoded = func.hex(rank)
    if selected in _STANDARD_RANKS:
        return encoded == selected.encode("ascii").hex().upper()
    tail = func.substr(rank, 2)
    digits = _ascii_digits(tail)
    stars = cast(case((digits, tail), else_="-1"), Integer)
    numbered_s = and_(func.hex(func.substr(rank, 1, 1)) == "53", digits,
                      stars.between(0, 50))
    legacy_s = encoded == "53"
    if selected == "s":
        return or_(legacy_s, and_(numbered_s, stars < 10))
    if selected == "s_gold":
        return and_(numbered_s, stars.between(10, 24))
    if selected == "s_diamond":
        return and_(numbered_s, stars.between(25, 49))
    if selected == "s_demon":
        return and_(numbered_s, stars == 50)
    valid_standard = encoded.in_([r.encode("ascii").hex().upper() for r in _STANDARD_RANKS])
    return ~or_(valid_standard, legacy_s, numbered_s)


def _identity_condition(selected):
    from app.services import auth_service

    manual = func.coalesce(User.identity, "")
    student_id = func.coalesce(User.student_id, "")
    standard_id = and_(func.length(student_id) == 9, _ascii_digits(student_id))
    enrollment_year = 2000 + cast(func.substr(student_id, 1, 2), Integer)
    # Keep auth_service.effective_identity's manual priority and dynamic year rule.
    automatic = case((enrollment_year >= auth_service.datetime.now().year - 1,
                      "new_student"), else_="senior")
    manual_identity = case(
        (func.hex(manual) == "new_student".encode("ascii").hex().upper(), "new_student"),
        (func.hex(manual) == "senior".encode("ascii").hex().upper(), "senior"),
        else_="unknown")
    identity = case((func.length(manual) > 0, manual_identity),
                    (standard_id, automatic), else_="unknown")
    return case((User.is_verified.is_(True), identity), else_="unknown") == selected


def public_players(db, before_id=None, limit=20, keyword="", *, rank="", identity=""):
    """所有非隐藏用户；不按报名、认证或是否已有队伍筛除。只投影公开字段。"""
    from app.services.auth_service import effective_identity
    if rank not in get_args(RankFilter) or identity not in get_args(IdentityFilter):
        raise HTTPException(422, "不支持的段位或身份筛选")
    query = (db.query(User, TeamMember.team_id, Team.name, Team.is_hidden)
             .outerjoin(TeamMember, TeamMember.user_id == User.id)
             .outerjoin(Team, Team.id == TeamMember.team_id).filter(User.is_hidden.is_(False)))
    if rank:
        query = query.filter(_rank_condition(rank))
    if identity:
        query = query.filter(_identity_condition(identity))
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
                next_cursor=items[-1]["id"] if len(rows) > limit else None,
                filters=dict(rank=rank, identity=identity))


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
