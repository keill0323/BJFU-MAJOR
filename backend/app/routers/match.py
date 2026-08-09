"""
赛事相关API路由
Author: keill
Since: 2026-7-26
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.match import MatchCreateRequest, MatchInfo, RoundInfo, RoundUpdateRequest, MatchStatusUpdateRequest, MatchAdminDetail
from app.models.match import MatchStatus
from app.services import match_service
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/api/matches", tags=["赛事"])


@router.post("", response_model=MatchInfo, status_code=201)
def create_match(request: MatchCreateRequest,
                 db=Depends(get_db),
                 admin=Depends(require_admin)
                 ):
    """创建赛事"""
    return match_service.create_match(
        db, request.name,
        description=request.description,
        max_teams=request.max_teams,
        team_size=request.team_size,
        register_start=request.register_start,
        register_end=request.register_end,
        match_start=request.match_start
    )


@router.get("", response_model=list[MatchInfo])
def list_matches(db: Session = Depends(get_db)):
    """获取所有赛事"""
    return match_service.get_all_matches(db)


@router.get("/search", response_model=list[MatchInfo])
def search_matches(keyword: str, db: Session = Depends(get_db)):
    """模糊搜索赛事"""
    return match_service.search_matches(db, keyword)


@router.put("/{match_id}/status", response_model=MatchInfo)
def update_match_status(
    match_id: int,
    request: MatchStatusUpdateRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员更新赛事状态（草稿/报名中/进行中/已结束）"""
    # 校验状态值合法
    try:
        status = MatchStatus(request.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的赛事状态")
    match = match_service.update_match_status(db, match_id, status)
    if not match:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return match


@router.get("/{match_id}", response_model=MatchInfo)
def get_match(match_id: int, db: Session = Depends(get_db)):
    """获取赛事详情"""
    match = match_service.get_match_by_id(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return match


@router.get("/{match_id}/detail", response_model=MatchAdminDetail)
def get_match_detail_public(
    match_id: int,
    db: Session = Depends(get_db),
):
    """普通用户查看赛事详情（队伍进度 + 对阵，含 BO3 小分），用于用户端赛事页展示"""
    detail = match_service.get_match_admin_detail(db, match_id)
    if not detail:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return detail


@router.get("/{match_id}/admin-detail", response_model=MatchAdminDetail)
def get_match_admin_detail(
    match_id: int,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理后台查看赛事详情（含报名队伍进度与对阵）"""
    detail = match_service.get_match_admin_detail(db, match_id)
    if not detail:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return detail


@router.get("/{match_id}/rounds", response_model=list[RoundInfo])
def get_rounds(match_id: int, db: Session = Depends(get_db)):
    """获取赛事所有对阵"""
    return match_service.get_rounds_by_match(db, match_id)


@router.post("/{match_id}/rounds", response_model=RoundInfo, status_code=201)
def add_round(
    match_id: int,
    round_number: int,
    team1_id: int,
    team2_id: int = None,
    group_name: str = None,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """手动添加对阵"""
    return match_service.add_round(db, match_id, round_number, team1_id, team2_id, group_name)


@router.put("/rounds/{round_id}", response_model=RoundInfo)
def update_round_result(
    round_id: int,
    request: RoundUpdateRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """更新对阵结果（淘汰赛传 bo3_scores 支持三局两胜）"""
    match_round = match_service.update_round_result(
        db, round_id, request.team1_score, request.team2_score, request.winner_id,
        request.bo3_scores
    )
    if not match_round:
        raise HTTPException(status_code=404, detail="对阵不存在")
    return match_round


@router.post("/{match_id}/register/team")
def register_team(
    match_id: int,
    team_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队伍报名（只有队长能发起）"""
    from app.models.team import Team
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != current_user.id:
        raise HTTPException(status_code=403, detail="只有队长才能发起队伍报名")

    match_service.register_team(db, match_id, team_id)
    return {"message": "报名成功"}


@router.post("/{match_id}/register/user")
def register_user(
    match_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """个人报名"""
    match_service.register_user(db, match_id, current_user.id)
    return {"message": "报名成功"}


@router.get("/{match_id}/my-registration")
def get_my_registration(
    match_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """查询当前用户是否已报名该赛事"""
    reg = match_service.get_my_registration(db, match_id, current_user.id)
    if not reg:
        return {"registered": False, "registration": None}
    return {
        "registered": True,
        "registration": {
            "id": reg.id,
            "team_id": reg.team_id,
            "status": reg.status.value if hasattr(reg.status, "value") else reg.status,
        },
    }


@router.post("/registerations/{reg_id}/approve")
def approve_registration(
    reg_id: int,
    db: Session = Depends(get_db),
    admin = Depends(require_admin)
):
    """审核通过报名"""
    reg = match_service.approve_registration(db, reg_id)
    if not reg:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    return {"message": "审核通过"}


@router.post("/{match_id}/auto-seeds")
def auto_seeds(match_id: int,
               db=Depends(get_db),
               admin=Depends(require_admin)
):
    """自动分配种子，前四进传奇组"""
    match_service.auto_assign_seeds(db, match_id)
    return {"message": "种子分配完成"}


@router.post("/{match_id}/auto-group")
def auto_group(match_id: int,
               group_names: list[str] = Query(...),
               db=Depends(get_db),
               admin=Depends(require_admin)
):
    """蛇形分组"""
    match_service.auto_group_teams(db, match_id, group_names)
    return {"message": "分组完成"}


@router.post("/{match_id}/auto-matches/double")
def auto_double_matches(match_id: int,
                        group_name: str,
                        db=Depends(get_db),
                        admin=Depends(require_admin)
):
    """为小组生成双循环对阵"""
    match_service.auto_generate_group_matches(db, match_id, group_name)
    return {"message": "对阵生成完成"}


@router.post("/{match_id}/auto-matches/single")
def auto_single_matches(match_id: int, group_name: str, db=Depends(get_db), admin=Depends(require_admin)):
    """为小组生成单循环对阵"""
    match_service.auto_generate_single_round_matches(db, match_id, group_name)
    return {"message": "对阵生成完成"}


@router.post("/{match_id}/finish-group")
def finish_group(match_id: int, db=Depends(get_db), admin=Depends(require_admin)):
    """结束小组赛：每组第1晋级，每组第2进附加赛单循环"""
    result = match_service.finish_group_stage(db, match_id)
    return {"message": "小组赛结束", "data": result}


@router.post("/{match_id}/finish-playoff")
def finish_playoff(match_id: int, db=Depends(get_db), admin=Depends(require_admin)):
    """结束附加赛：附加赛第1晋级"""
    result = match_service.finish_playoff_stage(db, match_id)
    return {"message": "附加赛结束", "data": result}


@router.post("/{match_id}/generate-knockout")
def gen_knockout(match_id: int, db=Depends(get_db), admin=Depends(require_admin)):
    """生成6强淘汰赛1/4决赛"""
    result = match_service.generate_knockout(db, match_id)
    return {"message": "淘汰赛已生成", "data": result}


@router.post("/{match_id}/divide-legend")
def divide_legend(match_id: int, db=Depends(get_db), admin=Depends(require_admin)):
    """传奇组8队分上下区，生成各区循环赛"""
    result = match_service.divide_legend(db, match_id)
    return {"message": "传奇组已分区", "data": result}


@router.post("/{match_id}/finish-legend")
def finish_legend(match_id: int, db=Depends(get_db), admin=Depends(require_admin)):
    """结束传奇组循环赛：每区前3晋级淘汰赛"""
    result = match_service.finish_legend_stage(db, match_id)
    return {"message": "传奇组结束", "data": result}


@router.post("/{match_id}/advance-knockout")
def advance_knockout(match_id: int, db=Depends(get_db), admin=Depends(require_admin)):
    """推进淘汰赛：1/4打完生成半决赛，半决赛打完生成决赛"""
    result = match_service.advance_knockout(db, match_id)
    return {"message": "淘汰赛已推进", "data": result}
