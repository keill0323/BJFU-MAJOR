"""
认证相关API路由
Author: keill
Since: 2026-7-24
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.schemas.user import UserLoginRequest, UserInfo, TokenResponse, UserUpdateRequest, AdminUpdateUserRequest, UpdateRoleRequest, VerifyListItem, RankApplicationInfo
from app.services import auth_service, ai_review_service, upload_service
from app.services.auth_service import get_current_user, require_admin, require_admin_only

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
        verify_reject_reason=request.verify_reject_reason,
    )
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.put("/admin/users/{user_id}/role")
def update_user_role(
    user_id: int,
    request: UpdateRoleRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin_only),
):
    """修改用户角色（仅超级管理员；reviewer 无此权限，防止权限复制）"""
    try:
        return auth_service.update_user_role(db, user_id, request.role, admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload-verify", response_model=UserInfo)
def upload_verify(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """上传学信网/教务系统截图（开启AI时自动预审）"""
    # 已认证用户无需重复上传，避免产生「已认证 + 审核中」的矛盾状态
    if current_user.is_verified:
        raise HTTPException(status_code=400, detail="你已通过在校认证，无需重复上传")

    # 统一校验 + 落盘（魔数校验 + 大小限制 + 随机文件名）
    url, file_path = upload_service.save_upload_image(file, f"verify_{current_user.id}", max_mb=10)

    user_id = current_user.id
    user = auth_service.update_verify_image(db, user_id, url)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    ai_result = None
    if settings.AI_REVIEW_ENABLED:
        try:
            ai_result = ai_review_service.validate_verify_result(
                ai_review_service.review_image(file_path)
            )
        except Exception:
            ai_result = None      # AI 调用失败不影响上传，转人工审核
    return auth_service.apply_verify_result(db, user_id, url, ai_result)


@router.post("/upload-avatar", response_model=UserInfo)
def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """上传头像"""
    url, _ = upload_service.save_upload_image(file, f"avatar_{current_user.id}", max_mb=5)
    current_user.avatar = url
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

    首次上传（当前无段位）：AI 高置信度自动更新，其余转人工审核；
    再次上传（已有段位）：生成段位更新申请，待管理员审批后生效。
    """
    user_id = current_user.id
    # 尽早拦截重复申请，避免重复落盘及不必要的 AI 调用。
    if auth_service.get_pending_rank_application(db, user_id):
        raise HTTPException(status_code=400, detail="你已有待审批的段位更新申请，请等待管理员处理")
    # 统一校验 + 落盘（魔数校验 + 大小限制 + 随机文件名）
    url, file_path = upload_service.save_upload_image(file, f"rank_{current_user.id}", max_mb=10)

    # AI 识别段位
    ai_result = None
    if settings.AI_REVIEW_ENABLED:
        try:
            ai_result = ai_review_service.validate_rank_result(
                ai_review_service.review_rank(file_path)
            )
        except Exception:
            ai_result = None

    return auth_service.apply_rank_upload(db, user_id, url, ai_result)


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
