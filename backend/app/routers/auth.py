"""
认证相关API路由
Author: keill
Since: 2026-7-24
"""

import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.schemas.user import UserLoginRequest, UserInfo, TokenResponse, UserUpdateRequest, AdminUpdateUserRequest, UpdateRoleRequest, VerifyListItem
from app.services import auth_service
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login", response_model=TokenResponse)
def login(request: UserLoginRequest, db: Session = Depends(get_db)):
    """微信小程序登录"""
    if settings.WX_MOCK_LOGIN:
        # 开发阶段: 用 code 模拟 wx_openid
        wx_openid = f"wx_mock_{request.code}"
    else:
        # 上线：用微信 code 换真实 openid
        wx_openid = auth_service.wx_code_to_openid(request.code)
        if not wx_openid:
            raise HTTPException(status_code=400, detail="微信登录失败，请重试")
    user = auth_service.get_user_by_wx_openid(db, wx_openid)
    if not user:
        user = auth_service.create_user(db, wx_openid)
    token = auth_service.create_access_token(user.id, user.role.value)
    return {"access_token": token, "user": user}


@router.get("/me", response_model=UserInfo)
def get_me(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """获取当前登录用户完整信息（含 id/学号/段位/评分/认证状态）"""
    return current_user


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


@router.get("/admin/users", response_model=list[UserInfo])
def admin_list_users(
    keyword: Optional[str] = None,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员获取用户列表，可按 id/昵称/游戏ID/学号 搜索"""
    return auth_service.get_all_users(db, keyword)


@router.put("/admin/users/{user_id}", response_model=UserInfo)
def admin_update_user(
    user_id: int,
    request: AdminUpdateUserRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin)
):
    """管理员修改用户学号、审核状态、段位"""
    user = auth_service.admin_update_user(
        db, user_id,
        student_id=request.student_id,
        is_verified=request.is_verified,
        rank=request.rank,
        individual_rating=request.individual_rating,
        verify_image=request.verify_image,
    )
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.put("/admin/users/{user_id}/role")
def update_user_role(
    user_id: int,
    request: UpdateRoleRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员修改用户角色"""
    try:
        return auth_service.update_user_role(db, user_id, request.role, admin)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/upload-verify", response_model=UserInfo)
async def upload_verify(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """上传学信网/教务系统截图（用于人工审核认证）"""
    # 仅允许图片类型
    allowed = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式图片")

    # 读取内容并限制大小 5MB
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 5MB")

    # 保存到上传目录，文件名带用户ID和时间戳防冲突
    ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[file.content_type]
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"verify_{current_user.id}_{int(time.time())}{ext}"
    (upload_dir / filename).write_bytes(content)

    url = f"/uploads/{filename}"
    user = auth_service.update_verify_image(db, current_user.id, url)
    return user


@router.get("/admin/verify-list", response_model=list[VerifyListItem])
def admin_verify_list(
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员/审核员查看待认证用户（已上传截图、未通过）"""
    return auth_service.get_unverified_users(db)