"""认证与当前用户。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import current_user, issue_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(username=body.username).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_token(db, u)
    return {"token": token,
            "user": {"username": u.username, "display_name": u.display_name,
                     "role": u.role, "region_scope": u.region_scope,
                     "org_scope": u.org_scope}}


@router.get("/me")
def me(u: User = Depends(current_user)):
    return {"username": u.username, "display_name": u.display_name, "role": u.role,
            "region_scope": u.region_scope, "org_scope": u.org_scope}
