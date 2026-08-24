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
from app.schemas.user import UserLoginRequest, UserInfo, TokenResponse, UserUpdateRequest, AdminUpdateUserRequest, UpdateRoleRequest, VerifyListItem, RankApplicationInfo
from app.services import auth_service, ai_review_service, team_service
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
    # 身份动态刷新：不写库，请求结束自动回滚
    current_user.identity = auth_service.effective_identity(current_user)
    return current_user


@router.get("/my-rank-applications", response_model=list[RankApplicationInfo])
def my_rank_applications(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """当前用户的段位更新申请（含驳回原因）"""
    return auth_service.get_my_rank_applications(db, current_user.id)


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
    users = auth_service.get_all_users(db, keyword)
    # 身份动态刷新：不写库
    for u in users:
        u.identity = auth_service.effective_identity(u)
    return users


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
        identity=request.identity,
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
def upload_verify(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """上传学信网/教务系统截图（开启AI时自动预审）"""
    # 仅允许图片类型
    allowed = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式图片")

    # 读取内容并限制大小 10MB
    content = file.file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    # 保存到上传目录，文件名带用户ID和时间戳防冲突
    ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[file.content_type]
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"verify_{current_user.id}_{int(time.time())}{ext}"
    file_path = upload_dir / filename          # ← 新增：存完整路径，AI 要用
    file_path.write_bytes(content)

    url = f"/uploads/{filename}"
    user = auth_service.update_verify_image(db, current_user.id, url)

    if settings.AI_REVIEW_ENABLED:
        try:
            ai_result = ai_review_service.review_image(str(file_path))
        except Exception:
            ai_result = None      # AI 调用失败不影响上传，转人工审核

        if ai_result:
            # 把 AI 结果写进数据库的 3 个新字段
            user.ai_review_reason = ai_result.get("reason")
            user.ai_review_confidence = ai_result.get("confidence", 0)

            # 置信度分级：≥0.9 高置信才自动通过/驳回，否则转人工
            conf = ai_result.get("confidence", 0)
            if ai_result.get("is_valid") is True and conf >= 0.9:
                user.ai_review_status = "auto_pass"     # 自动通过
                user.is_verified = True
                # AI 识别到学号则直接写入，无需管理员再填写
                sid = ai_result.get("student_id")
                if sid:
                    sid = str(sid).strip()
                    if sid:
                        # 学号唯一，若已被其他用户占用则不覆盖
                        from app.models.user import User
                        exists = db.query(User).filter(User.student_id == sid).first()
                        if not exists:
                            user.student_id = sid
            elif ai_result.get("is_valid") is False and conf >= 0.9:
                user.ai_review_status = "auto_reject"   # 自动驳回
            else:
                user.ai_review_status = "pending"       # 转人工

            db.commit()
            db.refresh(user)

    return user


@router.post("/upload-avatar", response_model=UserInfo)
def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """上传头像"""
    allowed = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式图片")

    content = file.file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 5MB")

    ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[file.content_type]
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"avatar_{current_user.id}_{int(time.time())}{ext}"
    file_path = upload_dir / filename
    file_path.write_bytes(content)

    current_user.avatar = f"/uploads/{filename}"
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/upload-rank")
def upload_rank(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """上传游戏段位截图（完美/5E平台）

    首次上传（当前无段位）：AI 识别后高置信度自动更新段位；
    再次上传（已有段位）：生成段位更新申请，待管理员审批后生效。
    """
    # 仅允许图片类型
    allowed = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式图片")

    # 读取内容并限制大小 10MB
    content = file.file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")

    # 保存截图
    ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[file.content_type]
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"rank_{current_user.id}_{int(time.time())}{ext}"
    file_path = upload_dir / filename
    file_path.write_bytes(content)

    url = f"/uploads/{filename}"
    user = auth_service.get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # AI 识别段位
    ai_result = None
    if settings.AI_REVIEW_ENABLED:
        try:
            ai_result = ai_review_service.review_rank(str(file_path))
        except Exception:
            ai_result = None

    rank = ai_result.get("rank") if ai_result else None
    conf = ai_result.get("confidence", 0) if ai_result else 0
    reason = ai_result.get("reason", "") if ai_result else ""

    # 首次上传（当前无段位）：AI 高置信度直接自动更新
    if not user.rank:
        if rank and conf >= 0.9:
            rating = auth_service._rank_rating(rank)
            user.rank = rank
            user.rank_image = url
            if rating is not None:
                user.individual_rating = rating
            db.commit()
            db.refresh(user)
            team_service.recalc_user_team_rating(db, user.id)  # 用户段位/评分变动时，重新计算其所在队伍的 rating
            return {"rank": rank, "confidence": conf, "auto_applied": True, "need_review": False, "reason": reason}
        # 识别失败/低置信度：保存截图，提示联系管理员
        user.rank_image = url
        db.commit()
        db.refresh(user)
        team_service.recalc_user_team_rating(db, user.id)  # 用户段位/评分变动时，重新计算其所在队伍的 rating
        return {"rank": rank, "confidence": conf, "auto_applied": False, "need_review": False,
                "reason": reason or "AI 未能识别段位，请联系管理员手动设置"}

    # 再次上传（已有段位）：生成段位更新申请，待管理员审批
    auth_service.create_rank_application(
        db, user.id, rank_image=url, ai_rank=rank,
        ai_confidence=conf, ai_reason=reason,
    )
    return {"rank": rank, "confidence": conf, "auto_applied": False, "need_review": True,
            "reason": reason or "已提交段位更新申请，等待管理员审批"}


@router.get("/admin/rank-applications", response_model=list[RankApplicationInfo])
def admin_rank_applications(
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员/审核员查看待审批的段位更新申请"""
    return auth_service.get_pending_rank_applications(db)


@router.post("/admin/rank-applications/{app_id}/approve", response_model=RankApplicationInfo)
def admin_approve_rank_application(
    app_id: int,
    rank: Optional[str] = None,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """审批通过段位更新申请（可手动指定段位覆盖 AI 结果）"""
    app = auth_service.approve_rank_application(db, app_id, rank)
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")
    return app


@router.post("/admin/rank-applications/{app_id}/reject", response_model=RankApplicationInfo)
def admin_reject_rank_application(
    app_id: int,
    reject_reason: Optional[str] = None,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """驳回段位更新申请（可填写驳回原因）"""
    app = auth_service.reject_rank_application(db, app_id, reject_reason)
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")
    return app


@router.get("/admin/verify-list", response_model=list[VerifyListItem])
def admin_verify_list(
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员/审核员查看待认证用户（已上传截图、未通过）"""
    return auth_service.get_unverified_users(db)