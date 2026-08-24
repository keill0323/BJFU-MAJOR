"""
密码加密、JWT 令牌生成、 用户相关
Author: keill
Since: 2026-7-22
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import json
import urllib.request

from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import cast, String
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User, UserRole, RankApplication
from app.database import get_db
from app.services import team_service

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
    """根据学号自动识别身份（新生/老登）"""
    if not student_id or len(student_id) < 2 or not student_id[:2].isdigit():
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


def get_unverified_users(db: Session) -> list:
    """列出待认证审核的用户：已上传学信网截图但尚未通过"""
    return (
        db.query(User)
        .filter(User.verify_image.isnot(None), User.is_verified.is_(False))
        .order_by(User.created_at.asc())
        .all()
    )


def update_verify_image(db: Session, user_id: int, url: str) -> Optional[User]:
    """记录用户上传的学信网截图URL"""
    user = get_user_by_id(db, user_id)
    if user:
        user.verify_image = url
        db.commit()
        db.refresh(user)
    return user


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
                      identity: Optional[str] = None, verify_image: Optional[str] = None) -> Optional[User]:
    """管理员修改用户资料（学号、审核状态、段位、身份等）"""
    user = get_user_by_id(db, user_id)
    if user:
        if student_id is not None:
            user.student_id = student_id
        # 身份：仅管理员显式指定（研1/博1认证）时写入；设学号不写死，读取时动态算
        if identity is not None:
            user.identity = identity
        if is_verified is not None:
            user.is_verified = is_verified
        if rank is not None:
            user.rank = rank
            # 水平分与段位挂钩：设置段位时自动计算评分
            rating = _rank_rating(rank)
            if rating is not None:
                user.individual_rating = rating
        if individual_rating is not None:
            user.individual_rating = individual_rating
        if verify_image is not None:
            user.verify_image = verify_image or None   # 空字符串表示清除截图
        db.commit()
        db.refresh(user)
        team_service.recalc_user_team_rating(db, user_id)  # 用户段位/评分变动时，重新计算其所在队伍的 rating
    return user


def update_user_role(db: Session, user_id: int, role: str, operator: User) -> Optional[User]:
    """修改用户角色"""
    target = get_user_by_id(db,user_id)
    if not target:
        return None

    # 判断权限等级
    if operator.role == UserRole.REVIEWER and target.role == UserRole.ADMIN:
        raise ValueError("堂下何人竟敢状告本官")
    if operator.role == UserRole.REVIEWER and role == "admin":
        raise ValueError("大胆，竟敢篡你可莉叔叔的位")

    target.role = UserRole(role)
    db.commit()
    db.refresh(target)
    return target


def create_rank_application(db: Session, user_id: int, rank_image: Optional[str] = None,
                            ai_rank: Optional[str] = None, ai_confidence: float = 0,
                            ai_reason: Optional[str] = None) -> RankApplication:
    """创建段位更新申请（用户已有段位后再次上传截图）"""
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
    """
    app = db.query(RankApplication).filter(RankApplication.id == app_id).first()
    if not app:
        return None
    if app.status == "pending":
        user = get_user_by_id(db, app.user_id)
        if user:
            if app.rank_image:
                user.rank_image = app.rank_image
            final_rank = rank or app.ai_rank
            if final_rank:
                user.rank = final_rank
                rating = _rank_rating(final_rank)
                if rating is not None:
                    user.individual_rating = rating
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
    """要求管理员权限"""
    if current_user.role not in (UserRole.ADMIN, UserRole.REVIEWER):
        raise HTTPException(status_code=403, detail="权限不足")
    return current_user
