"""
认证相关API路由
Author: keill
Since: 2026-7-24
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.user import UserLoginRequest, UserInfo, TokenResponse, UserUpdateRequest, AdminUpdateUserRequest, UpdateRoleRequest
from app.services import auth_service
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login", response_model=TokenResponse)
def login(request: UserLoginRequest, db: Session = Depends(get_db)):
    """微信小程序登录"""
    # 开发阶段: 用code 模拟 wx_openid
    wx_openid = f"wx_mock_{request.code}"
    user = auth_service.get_user_by_wx_openid(db, wx_openid)
    if not user:
        user = auth_service.create_user(db, wx_openid)
    token = auth_service.create_access_token(user.id, user.role.value)
    return {"access_token": token, "user": user}


@router.put("/profile", response_model=UserInfo)
def update_profile(
    request: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """用户自行修改昵称和游戏ID"""
    return auth_service.update_self_profile(
        db, current_user.id, request.nickname, request.game_id
    )


@router.put("/admin/users/{user_id}", response_model=UserInfo)
def admin_update_user(
    user_id: int,
    request: AdminUpdateUserRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin)
):
    """管理员修改用户学号、审核状态、段位"""
    return auth_service.admin_update_user(
        db, user_id,
        student_id=request.student_id,
        is_verified=request.is_verified,
        rank=request.rank,
        individual_rating=request.individual_rating,
    )


@router.put("/admin/users/{user_id}/role")
def update_user_role(
    user_id: int,
    request: UpdateRoleRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员修改用户角色"""
    return auth_service.update_user_role(db, user_id, request.role, admin)