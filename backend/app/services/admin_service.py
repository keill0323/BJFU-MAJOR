"""Aggregate pending work without loading user images or event detail graphs."""
from sqlalchemy import and_, case, exists, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models.match import Match, MatchRound, Registration, RegistrationStatus, StageStatus, TeamProgress
from app.models.team import Team, TeamStatus
from app.models.user import RankApplication, User


def get_todos(db: Session):
    # Match the existing review-list predicates, including unverified submissions
    # that were flagged/rejected by AI and still need a human decision.
    counts = db.execute(select(
        select(func.count(User.id)).where(User.verify_image.isnot(None), User.is_verified.is_(False)).scalar_subquery(),
        select(func.count(RankApplication.id)).where(RankApplication.status == "pending").scalar_subquery(),
        select(func.count(Team.id)).where(Team.status == TeamStatus.PENDING).scalar_subquery(),
    )).one()
    # Exactly mirror get_team_registration_status: all-approved without a team
    # progress is still pending; an all-rejected team is rejected only when it
    # has no progress. Count a team once regardless of how many members it has.
    teams = db.query(
        Registration.match_id, Registration.team_id,
        func.count(Registration.id).label("members"),
        func.sum(case((Registration.status == RegistrationStatus.APPROVED, 1), else_=0)).label("approved"),
        func.sum(case((Registration.status == RegistrationStatus.REJECTED, 1), else_=0)).label("rejected"),
    ).join(Team, Team.id == Registration.team_id).group_by(Registration.match_id, Registration.team_id).subquery()
    progressed = exists().where(TeamProgress.match_id == teams.c.match_id, TeamProgress.team_id == teams.c.team_id)
    pending = db.query(teams.c.match_id, func.count().label("count")).filter(not_(or_(
        and_(progressed, teams.c.approved == teams.c.members),
        and_(not_(progressed), teams.c.rejected == teams.c.members),
    ))).group_by(teams.c.match_id).subquery()
    locked_progress = exists().where(TeamProgress.match_id == Match.id, or_(
        TeamProgress.seed > 0,
        and_(TeamProgress.group_name.isnot(None), TeamProgress.group_name != ""),
        TeamProgress.stage.in_((StageStatus.LEGEND, StageStatus.PLAYOFF, StageStatus.ELIMINATED)),
    ))
    locked_rounds = exists().where(MatchRound.match_id == Match.id)
    rows = db.query(
        Match.id.label("match_id"), Match.name.label("match_name"), pending.c.count,
        or_(locked_progress, locked_rounds).label("roster_locked"),
    ).join(pending, pending.c.match_id == Match.id).order_by(Match.created_at.desc(), Match.id.desc()).all()
    registration_matches = [dict(row._mapping) for row in rows]
    registration_count = sum(row["count"] for row in registration_matches)
    return {
        "verification_count": counts[0], "rank_application_count": counts[1],
        "team_count": counts[2], "registration_count": registration_count,
        "total": sum(counts) + registration_count,
        "registration_matches": registration_matches,
    }
