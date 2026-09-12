"""Public presentation filters. Never mutate stored tournament results or admin data."""
from app.models.team import Team
from app.models.user import User


HIDDEN_TEAM_NAME = "已隐藏测试队伍"


def public_rounds(db, rounds, hidden_ids=None):
    if hidden_ids is None:
        ids = {row.get(key) for row in rounds for key in ("team1_id", "team2_id")}
        hidden_ids = {row[0] for row in db.query(Team.id).filter(
            Team.id.in_(ids - {None}), Team.is_hidden.is_(True),
        ).all()}
    # Keep IDs, winners and scores so a hidden opponent never becomes a bye.
    return [{**row, **{
        f"{side}_name": HIDDEN_TEAM_NAME
        for side in ("team1", "team2") if row.get(f"{side}_id") in hidden_ids
    }} for row in rounds]


def public_match_detail(db, detail):
    ids = {row[0] for row in db.query(Team.id).filter(Team.is_hidden.is_(True)).all()}
    return {**detail,
            "teams": [row for row in detail["teams"] if row["team_id"] not in ids],
            "rounds": public_rounds(db, detail["rounds"], ids)}


def public_champions(db, items):
    user_ids = {member.get("user_id") for item in items for member in item["roster"]}
    hidden_users = {row[0] for row in db.query(User.id).filter(
        User.id.in_(user_ids - {None}), User.is_hidden.is_(True),
    ).all()}
    hidden_names = {row[0] for row in db.query(Team.name).filter(Team.is_hidden.is_(True)).all()}
    return [{**item,
             "roster": [member for member in item["roster"] if member.get("user_id") not in hidden_users],
             "runner_up_name": HIDDEN_TEAM_NAME if item["runner_up_name"] in hidden_names else item["runner_up_name"],
             } for item in items]
