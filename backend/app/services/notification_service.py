"""Write notices in the scheduling transaction; expose only the recipient's inbox."""
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from app.models.match import Match, MatchRound
from app.models.team import Team, TeamApplication, TeamInvitation, ApplicationStatus
from app.models.user import User
from app.models.notification import ScheduleNotification
from app.services.team_request_service import actionable


def _applications(db, user_id):
    return (db.query(TeamApplication).join(Team, Team.id == TeamApplication.team_id)
            .join(User, User.id == TeamApplication.user_id)
            .filter(Team.captain_id == user_id, Team.is_hidden.is_(False), User.is_hidden.is_(False),
                    *actionable(db, TeamApplication)))


def team_applications(db, user_id):
    from sqlalchemy.orm import joinedload
    from app.services.team_service import application_info
    rows = _applications(db, user_id).options(joinedload(TeamApplication.user), joinedload(TeamApplication.team)).order_by(TeamApplication.id.desc()).all()
    return [dict(application_info(a), team_name=a.team.name) for a in rows]


def personal_summary(db, user_id):
    invitations = (db.query(TeamInvitation).join(Team, Team.id == TeamInvitation.team_id)
                   .filter(TeamInvitation.user_id == user_id, *actionable(db, TeamInvitation),
                           Team.is_hidden.is_(False)).count())
    applications = _applications(db, user_id).count()
    schedules = db.query(ScheduleNotification).filter(ScheduleNotification.user_id == user_id, ScheduleNotification.read_at.is_(None)).count()
    return dict(invitation_count=invitations, application_count=applications, schedule_count=schedules,
                total=invitations + applications + schedules)


def now():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def notify_schedule(db, round_, kind, scheduled_time, actor_id=None):
    """No commit here: a failed notice must also roll back the schedule change."""
    match = db.get(Match, round_.match_id)
    # Read current captains after the event lock, including under MySQL REPEATABLE READ.
    team_ids = [tid for tid in (round_.team1_id, round_.team2_id) if tid]
    current_teams = {team.id: team for team in db.query(Team).filter(Team.id.in_(team_ids))
                     .order_by(Team.id).populate_existing().with_for_update().all()}
    teams = [current_teams.get(tid) for tid in (round_.team1_id, round_.team2_id)]
    recipients = {team.captain_id for team in teams if team and team.captain_id != actor_id}
    for user_id in recipients:
        db.add(ScheduleNotification(
            user_id=user_id, match_id=round_.match_id, round_id=round_.id,
            kind=kind, scheduled_time=scheduled_time, created_at=now(),
            match_name=match.name, team1_name=teams[0].name if teams[0] else "轮空",
            team2_name=teams[1].name if teams[1] else "轮空",
        ))


def list_notifications(db, user_id, *, before_id=None, limit=30):
    query = db.query(ScheduleNotification).filter_by(user_id=user_id)
    unread = query.filter(ScheduleNotification.read_at.is_(None)).count()
    if before_id is not None:
        query = query.filter(ScheduleNotification.id < before_id)
    rows = query.order_by(ScheduleNotification.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    available = {r[0] for r in db.query(MatchRound.id).filter(
        MatchRound.id.in_([row.round_id for row in rows])).all()} if rows else set()
    return {"items": [{
        "id": row.id, "match_id": row.match_id, "round_id": row.round_id,
        "kind": row.kind, "match_name": row.match_name,
        "team1_name": row.team1_name, "team2_name": row.team2_name,
        "scheduled_time": row.scheduled_time, "created_at": row.created_at,
        "is_read": row.read_at is not None, "round_available": row.round_id in available,
    } for row in rows], "unread_count": unread, "has_more": has_more,
        "next_cursor": rows[-1].id if has_more else None}


def mark_read(db, user_id, notice_id):
    query = db.query(ScheduleNotification).filter_by(id=notice_id, user_id=user_id)
    if not query.first():
        raise HTTPException(404, "通知不存在")
    query.filter(ScheduleNotification.read_at.is_(None)).update({"read_at": now()})
    db.commit()
    return {"message": "已读"}
