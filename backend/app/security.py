"""轻量认证：PBKDF2 口令哈希 + 库内不透明令牌（演示平台，可替换为统一身份）。"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import AuthToken, User

_PBKDF2_ROUNDS = 120_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def issue_token(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(token=token, user_id=user.id))
    db.commit()
    return token


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    token = authorization.split(" ", 1)[1].strip()
    row = db.query(AuthToken).filter_by(token=token).first()
    if not row:
        raise HTTPException(status_code=401, detail="令牌无效")
    # TTL 校验
    if datetime.utcnow() - row.created_at > timedelta(hours=settings.TOKEN_TTL_HOURS):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def scope_query(query, model, user: User):
    """按用户的统筹区/机构数据权限过滤。org 字段名不统一时在路由内另行处理。"""
    if "*" in user.region_scope or user.role == "admin":
        return query
    if hasattr(model, "region"):
        query = query.filter(model.region.in_(user.region_scope))
    return query


def can_access_case(user: User, region: str, org_codes: list[str]) -> bool:
    if user.role == "admin" or "*" in user.region_scope:
        return True
    if region and region in user.region_scope:
        return True
    allowed_orgs = set(user.org_scope or [])
    return bool(allowed_orgs and any(o in allowed_orgs for o in (org_codes or [])))
