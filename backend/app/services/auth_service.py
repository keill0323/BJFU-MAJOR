"""
密码加密、JWT 令牌生成、 用户相关
Author: keill
Since: 2026-7-22
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path
import json
import re
import urllib.request

from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import and_, cast, func, or_, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User, UserRole, RankApplication
from app.database import get_db
from app.services import team_service
from app.services.locking import lock_row

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def wx_code_to_openid(code: str) -> Optional[str]:
    """调用微信 jscode2session 接口，用小程序登录 code 换取 openid（上线真实登录用）"""
    url = (
        "https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={settings.WX_APPID}&secret={settings.WX_SECRET}"
        f"&js_code={code}&grant_type=authorization_code"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if data.get("openid"):
        return data["openid"]
    return None

# 段位体系：D + C/C+/C++ + B/B+/B++ + A/A+/A++ + S 段50星（总分100，段位越高水平分越高）
RANK_ORDER = ["D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++"]


def _rank_rating(rank: str) -> Optional[int]:
    """根据段位计算水平分（0-100）：D~A++ 每档 +3（4~31），S 段从 40 升到 100。"""
    if not rank:
        return None
    # 归一化防御：去掉空白 + 转大写，与 ai_review_service.normalize_rank 规则一致（安全审查 #20）
    rank = str(rank).strip().upper().replace(" ", "")
    if rank in RANK_ORDER:
        return 4 + RANK_ORDER.index(rank) * 3
    if rank.startswith("S"):
        try:
            n = int(rank[1:])
        except ValueError:
            return None
        if 1 <= n <= 50:
            return round(40 + (n - 1) * 60 / 49)
    return None


def _auto_identity(student_id: Optional[str]) -> Optional[str]:
    """仅对九位数字学号按入学年份识别；其他格式交管理员核验。"""
    if not isinstance(student_id, str) or not re.fullmatch(r"[0-9]{9}", student_id):
        return None
    enroll_year = 2000 + int(student_id[:2])
    current_year = datetime.now().year
    if enroll_year >= current_year - 1:
        return "new_student"   # 新生
    return "senior"            # 老登


def effective_identity(user) -> Optional[str]:
    """获取用户有效身份（动态刷新）。

    管理员手动设置的 identity（如研1/博1认证）优先；
    否则按学号现场计算，跨年自动变化（大二新生→大三老登）。
    """
    if user.identity:
        return user.identity
    return _auto_identity(user.student_id)


MANUAL_STUDENT_ID_HINT = "非9位数字学号需人工认证，请联系管理员（QQ：3761215994）核验学号及新老生身份。"


def student_id_review_hint(user) -> Optional[str]:
    """为非标准学号提供现有报名错误提示；不把未知格式猜成硕士或博士。"""
    sid = (user.ai_student_id or user.student_id) if not user.is_verified else user.student_id
    if sid and not re.fullmatch(r"[0-9]{9}", sid):
        return MANUAL_STUDENT_ID_HINT
    return None


def hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希加密"""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, role: str) -> str:
    """生成 JWT 登陆令牌，有效期由配置文件决定"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """根据用户ID查询用户"""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_student_id(db: Session, student_id: str) -> Optional[User]:
    """根据学号查询用户"""
    return db.query(User).filter(User.student_id == student_id).first()


def get_user_by_wx_openid(db: Session, wx_openid: str) -> Optional[User]:
    """根据微信openid查询用户"""
    return db.query(User).filter(User.wx_openid == wx_openid).first()


def create_user(db: Session, wx_openid: str, nickname: Optional[str] = None) -> User:
    """创建新用户"""
    user = User(wx_openid=wx_openid, nickname=nickname)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_all_users(db: Session, keyword: Optional[str] = None) -> list:
    """获取全部用户（管理员用），可按 id/昵称/游戏ID/学号 模糊搜索"""
    query = db.query(User)
    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            cast(User.id, String).like(kw)
            | User.nickname.like(kw)
            | User.game_id.like(kw)
            | User.student_id.like(kw)
        )
    return query.order_by(User.id.asc()).all()


def pending_verification_condition():
    """人工待审条件；人工驳回已完成，AI 驳回仍允许人工复核。"""
    return and_(
        User.verify_image.isnot(None),
        func.length(func.trim(User.verify_image)) > 0,
        User.is_verified.is_(False),
        or_(User.ai_review_status.is_(None), User.ai_review_status != "manual_reject"),
    )


def get_unverified_users(db: Session) -> list:
    """列出有效凭证尚待人工审核的用户，重新上传后可再次进入队列。"""
    return (
        db.query(User)
        .filter(pending_verification_condition())
        .order_by(User.created_at.asc())
        .all()
    )


def update_verify_image(db: Session, user_id: int, url: str) -> Optional[User]:
    """提交新凭证，重置上一轮审核结果。"""
    user = _lock_user(db, user_id)
    if user:
        if user.is_verified:
            raise HTTPException(status_code=400, detail="你已通过在校认证，无需重复上传")
        user.verify_image = url
        user.verify_reject_reason = None
        user.ai_review_status = "pending"
        user.ai_review_reason = None
        user.ai_review_confidence = 0
        user.ai_student_id = None
        db.commit()
        db.refresh(user)
    return user


def _lock_user(db: Session, user_id: int) -> Optional[User]:
    return lock_row(db, User, user_id)


def apply_verify_result(db: Session, user_id: int, image_url: str, result) -> Optional[User]:
    """仅将 AI 结果应用到仍待审核的同一张凭证，学号冲突转人工。"""
    # AI 调用期间不持锁；结束旧读取事务后读取最新上传及人工审核状态。
    db.rollback()
    user = _lock_user(db, user_id)
    if (not user or user.verify_image != image_url or user.is_verified
            or user.ai_review_status != "pending" or result is None):
        return user

    user.ai_student_id = result["student_id"]
    user.ai_review_reason = result["reason"]
    user.ai_review_confidence = result["confidence"]
    if result["student_id"] and not re.fullmatch(r"[0-9]{9}", result["student_id"]):
        # 候选学号保留供管理员核验；非本科格式不自动通过，也不因此驳回。
        user.ai_review_status = "pending"
        user.ai_review_reason = MANUAL_STUDENT_ID_HINT
    elif result["confidence"] >= 0.9 and result["is_valid"] is True:
        sid = result["student_id"]
        exists = (db.query(User.id).filter(User.student_id == sid, User.id != user_id)
                  .first()) if sid else None
        if sid and not exists:
            user.student_id = sid
            user.is_verified = True
            user.ai_review_status = "auto_pass"
        else:
            user.ai_review_reason = ("该学号已被其他用户使用，待人工核验" if exists
                                     else "未识别到有效学号，待人工核验")
    elif result["confidence"] >= 0.9 and result["is_valid"] is False:
        user.ai_review_status = "auto_reject"

    try:
        db.commit()
    except IntegrityError:
        # 两个账号可能同时通过预检查，最终以数据库学号唯一约束为准。
        db.rollback()
        user = _lock_user(db, user_id)
        if (user and user.verify_image == image_url and not user.is_verified
                and user.ai_review_status == "pending"):
            user.ai_review_reason = "该学号已被其他用户使用，待人工核验"
            user.ai_review_confidence = result["confidence"]
            user.ai_student_id = result["student_id"]
            db.commit()
    if user:
        db.refresh(user)
    return user


def recognize_student_id(db: Session, user_id: int, expected_verify_image: str) -> dict:
    """管理员重读既有凭证，只保存待确认学号，不改变人工审核结论。"""
    from app.services import ai_review_service

    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    image_url = user.verify_image
    if not image_url:
        raise HTTPException(status_code=400, detail="该用户没有在校认证图片，请手动填写学号")
    if image_url != expected_verify_image:
        raise HTTPException(status_code=409, detail="认证图片已更新，请刷新后重新核对")
    if not settings.AI_REVIEW_ENABLED:
        raise HTTPException(status_code=400, detail="学号识别暂未开启，可对照图片手动填写")
    if not image_url.startswith("/uploads/") or "\\" in image_url:
        raise HTTPException(status_code=400, detail="该凭证不支持自动识别，请对照图片填写学号")
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    image_path = (upload_root / image_url[len("/uploads/"):]).resolve()
    if not image_path.is_relative_to(upload_root):
        raise HTTPException(status_code=400, detail="认证图片路径无效")
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="认证图片已不存在，请手动填写学号或重新提交凭证")
    snapshot = (user.student_id, user.is_verified, user.ai_review_status)
    # Do not hold a database transaction while waiting for the vision service.
    db.rollback()
    try:
        result = ai_review_service.validate_verify_result(ai_review_service.review_image(str(image_path)))
    except Exception:
        raise HTTPException(status_code=502, detail="学号识别暂时失败，请重试或对照图片填写") from None
    user = _lock_user(db, user_id)
    if (not user or user.verify_image != image_url
            or (user.student_id, user.is_verified, user.ai_review_status) != snapshot):
        raise HTTPException(status_code=409, detail="认证资料或审核状态已更新，请刷新后重新核对")
    user.ai_student_id = result["student_id"]
    db.commit()
    return {"student_id": result["student_id"], "confidence": result["confidence"],
            "reason": result["reason"], "verify_image": image_url}


def update_self_profile(db: Session, user_id: int, nickname: Optional[str] = None, game_id: Optional[str] = None) -> Optional[User]:
    """用户自行修改资料"""
    user = get_user_by_id(db, user_id)
    if user:
        if nickname is not None:
            user.nickname = nickname
        if game_id is not None:
            user.game_id = game_id
        db.commit()
        db.refresh(user)
    return user


def admin_update_user(db: Session, user_id: int, student_id: Optional[str] = None, is_verified: Optional[bool] = None,
                      rank: Optional[str] = None, individual_rating: Optional[int] = None,
                      identity: Optional[str] = None, verify_image: Optional[str] = None,
                      verify_reject_reason: Optional[str] = None,
                      expected_verify_image: Optional[str] = None) -> Optional[User]:
    """管理员修改用户资料（学号、审核状态、段位、身份等）"""
    from app.services.ai_review_service import normalize_rank, normalize_student_id
    from sqlalchemy.exc import IntegrityError

    user = _lock_user(db, user_id)
    if user:
        if expected_verify_image is not None and user.verify_image != expected_verify_image:
            raise HTTPException(status_code=409, detail="认证图片已更新，请刷新后重新核对")
        if student_id is not None:
            student_id = normalize_student_id(student_id)
            if not student_id:
                raise HTTPException(status_code=400, detail="学号须为 6–20 位数字，请对照图片确认")
        if is_verified is True and not normalize_student_id(student_id if student_id is not None else user.student_id):
            raise HTTPException(status_code=400, detail="请先确认并填写学号，再通过在校认证")
        if student_id is not None:
            user.student_id = student_id
        # 身份：仅管理员显式指定（研1/博1认证）时写入；设学号不写死，读取时动态算
        if identity is not None:
            user.identity = identity
        if is_verified is not None:
            user.is_verified = is_verified
            # 人工审核同步 AI 状态，避免出现「已认证但状态仍为初审中」的矛盾
            user.ai_review_status = "manual_pass" if is_verified else "manual_reject"
            if is_verified:
                user.verify_reject_reason = None
        if rank is not None:
            # 段位写入统一归一化（安全审查 #11：防止 s10/王者 等非法值入库）
            normalized = normalize_rank(rank)
            if not normalized:
                raise HTTPException(status_code=400, detail=f"无效的段位：{rank}")
            user.rank = normalized
            # 水平分与段位挂钩：设置段位时自动计算评分
            rating = _rank_rating(normalized)
            if rating is not None:
                user.individual_rating = rating
        if individual_rating is not None:
            user.individual_rating = individual_rating
        if verify_image is not None:
            if user.verify_image != (verify_image or None):
                user.ai_student_id = None
            user.verify_image = verify_image or None   # 空字符串表示清除截图
        if verify_reject_reason is not None:
            user.verify_reject_reason = verify_reject_reason or None   # 空字符串表示清除驳回原因
        try:
            db.commit()
        except IntegrityError:
            # 学号唯一冲突（安全审查 #19）
            db.rollback()
            raise HTTPException(status_code=400, detail="该学号已被其他用户使用")
        db.refresh(user)
        team_service.recalc_user_team_rating(db, user_id)  # 用户段位/评分变动时，重新计算其所在队伍的 rating
    return user


def update_user_role(db: Session, user_id: int, role: str, operator: User) -> Optional[User]:
    """修改用户角色（仅 admin 可调用，路由层已用 require_admin_only 拦截）

    注意：审核员（reviewer）不再能修改角色，
    防止审核员互相提升/复制权限（安全审查 #8）。
    """
    target = get_user_by_id(db,user_id)
    if not target:
        return None

    # 防御：即使被绕过 require_admin_only，也拒绝非 admin 操作者
    if operator.role != UserRole.ADMIN:
        raise ValueError("仅管理员可以修改用户角色")

    target.role = UserRole(role)
    db.commit()
    db.refresh(target)
    return target


def create_rank_application(db: Session, user_id: int, rank_image: Optional[str] = None,
                            ai_rank: Optional[str] = None, ai_confidence: float = 0,
                            ai_reason: Optional[str] = None) -> RankApplication:
    """创建首次人工段位认证或段位更新申请。

    同一用户同时只允许一条 pending 申请（安全审查 #22：防止无限刷申请轰炸管理员）。
    """
    # 锁定用户行，将同一用户的检查和创建串行化。
    user = _lock_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    pending = get_pending_rank_application(db, user_id, for_update=True)
    if pending:
        raise HTTPException(status_code=400, detail="你已有待审批的段位更新申请，请等待管理员处理")
    app = RankApplication(
        user_id=user_id,
        rank_image=rank_image,
        ai_rank=ai_rank,
        ai_confidence=ai_confidence,
        ai_reason=ai_reason,
        status="pending",
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def get_pending_rank_application(db: Session, user_id: int, for_update: bool = False):
    query = db.query(RankApplication).filter(
        RankApplication.user_id == user_id, RankApplication.status == "pending",
    )
    if for_update:
        query = query.populate_existing().with_for_update()
    return query.first()


def apply_rank_upload(db: Session, user_id: int, image_url: str, result) -> dict:
    """AI 未能自动认证时也进入现有段位人工审核队列。"""
    db.rollback()
    user = _lock_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if get_pending_rank_application(db, user_id, for_update=True):
        raise HTTPException(status_code=400, detail="你已有待审批的段位更新申请，请等待管理员处理")
    rank = result["rank"] if result else None
    confidence = result["confidence"] if result else 0
    reason = result["reason"] if result else ""
    if not user.rank and rank and confidence >= 0.9:
        user.rank = rank
        user.rank_image = image_url
        user.individual_rating = _rank_rating(rank)
        db.commit()
        team_service.recalc_user_team_rating(db, user_id)
        return {"rank": rank, "confidence": confidence, "auto_applied": True,
                "need_review": False, "reason": reason}

    if not user.rank:
        user.rank_image = image_url
        # create_rank_application 会刷新用户锁，先在同一事务中写入截图。
        db.flush()
    create_rank_application(db, user_id, rank_image=image_url, ai_rank=rank,
                            ai_confidence=confidence, ai_reason=reason)
    return {"rank": rank, "confidence": confidence, "auto_applied": False,
            "need_review": True, "reason": reason or "已提交段位认证申请，等待管理员审批"}


def get_pending_rank_applications(db: Session) -> list:
    """列出待审批的段位更新申请"""
    return (
        db.query(RankApplication)
        .filter(RankApplication.status == "pending")
        .order_by(RankApplication.created_at.asc())
        .all()
    )


def get_my_rank_applications(db: Session, user_id: int) -> list:
    """查询某用户的段位更新申请（按时间倒序，含驳回原因）"""
    return (
        db.query(RankApplication)
        .filter(RankApplication.user_id == user_id)
        .order_by(RankApplication.created_at.desc())
        .all()
    )


def approve_rank_application(db: Session, app_id: int, rank: Optional[str] = None) -> Optional[RankApplication]:
    """审批通过段位更新申请：更新用户段位 + 同步水平分

    rank 为管理员手动指定的段位（AI 识别错误时可覆盖），不传则用 AI 识别结果。
    管理员指定的段位同样走归一化（安全审查 #11），非法值拒绝。
    """
    from app.services.ai_review_service import normalize_rank

    app = db.query(RankApplication).filter(RankApplication.id == app_id).first()
    if not app:
        return None
    user = _lock_user(db, app.user_id)
    app = (db.query(RankApplication).filter(RankApplication.id == app_id)
           .populate_existing().with_for_update().first())
    if app.status == "pending":
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        final_rank = rank if rank is not None else app.ai_rank
        normalized = normalize_rank(final_rank)
        if not normalized:
            raise HTTPException(status_code=400, detail="请指定有效段位后再通过审核")
        if app.rank_image:
            user.rank_image = app.rank_image
        user.rank = normalized
        user.individual_rating = _rank_rating(normalized)
        app.status = "approved"
        db.commit()
        db.refresh(app)
        team_service.recalc_user_team_rating(db, app.user_id)  # 用户段位/评分变动时，重新计算其所在队伍的 rating
    return app


def reject_rank_application(db: Session, app_id: int, reject_reason: Optional[str] = None) -> Optional[RankApplication]:
    """驳回段位更新申请（可填写驳回原因）"""
    app = db.query(RankApplication).filter(RankApplication.id == app_id).first()
    if not app:
        return None
    _lock_user(db, app.user_id)
    app = (db.query(RankApplication).filter(RankApplication.id == app_id)
           .populate_existing().with_for_update().first())
    if app.status == "pending":
        app.status = "rejected"
        if reject_reason:
            app.reject_reason = reject_reason
        db.commit()
        db.refresh(app)
    return app


def get_current_user(db: Session = Depends(get_db), authorization: str = Header(None)) -> User:
    """从请求头Authorization中解析JWT，返回当前用户"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[7:]   # 去掉"Bearer "
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(payload["sub"])
    except JWTError:
        raise HTTPException(status_code=401, detail="令牌无效")
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求管理员权限（admin / reviewer 均视为管理端用户）"""
    if current_user.role not in (UserRole.ADMIN, UserRole.REVIEWER):
        raise HTTPException(status_code=403, detail="权限不足")
    return current_user


def require_admin_only(current_user: User = Depends(get_current_user)) -> User:
    """要求超级管理员权限（仅 admin，reviewer 不通过）

    用于角色变更等敏感操作，防止审核员（reviewer）复制审核员权限。
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可执行该操作")
    return current_user
