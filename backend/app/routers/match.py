"""
赛事相关API路由
Author: keill
Since: 2026-7-26
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.match import MatchCreateRequest, MatchInfo, RoundInfo, RoundUpdateRequest
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


@router.get("/{match_id}", response_model=MatchInfo)
def get_match(match_id: int, db: Session = Depends(get_db)):
    """获取赛事详情"""
    match = match_service.get_match_by_id(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return match


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
    """更新对阵结果"""
    match_round = match_service.update_round_result(
        db, round_id, request.team1_score, request.team2_score, request.winner_id
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
    """队伍报名"""
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
               group_names: list[str],
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
