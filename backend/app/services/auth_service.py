"""
密码加密、JWT 令牌生成、 用户相关
Author: keill
Since: 2026-7-22
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User,UserRole
from app.database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
                      rank: Optional[str] = None, individual_rating: Optional[int] = None) -> Optional[User]:
    """管理员修改用户资料（学号、审核状态、段位等）"""
    user = get_user_by_id(db, user_id)
    if user:
        if student_id is not None:
            user.student_id = student_id
        if is_verified is not None:
            user.is_verified = is_verified
        if rank is not None:
            user.rank = rank
        if individual_rating is not None:
            user.individual_rating = individual_rating
        db.commit()
        db.refresh(user)
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
