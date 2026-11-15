from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

from pydantic import BaseModel, Field
from jose import jwt, JWTError
from datetime import datetime, timedelta
import os
from db import users as db_users
from db import permissions as db_permissions

router = APIRouter()

SECRET_KEY = os.environ.get("SECRET_KEY", "temporary-dev-secret-change-this-before-deploy")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

def create_access_token(user_id, email, role, name, role_id=None):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "role": role, "name": name, "role_id": role_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/login")
def login(credentials: LoginRequest):
    user = db_users.verify_user_password(credentials.email, credentials.password)
    if not user or user["status"] != "active":
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"], user["email"], user["role"], user["name"], user["role_id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]},
    }

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"id": int(payload["sub"]), "email": payload["email"], "role": payload["role"], "name": payload["name"], "role_id": payload.get("role_id")}

@router.get("/me")
def read_current_user(current_user: dict = Depends(get_current_user)):
    # Name, email and phone as stored now (the token only has what was true
    # at login, and no phone), so Settings > Profile never saves a blank phone
    # over a real one.
    profile = db_users.get_profile(current_user["id"]) or {}
    return {**current_user, **profile}

@router.get("/me/permissions")
def get_my_permissions(current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "admin":
        return []
    role_id = current_user.get("role_id")
    if role_id is None:
        return []
    return db_permissions.get_role_permissions(role_id)

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/me/password")
def change_own_password(
    body: PasswordChangeRequest,
    current_user: dict = Depends(get_current_user),
):
    user = db_users.verify_user_password(current_user["email"], body.current_password)
    if user is None:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    db_users.update_user_password(current_user["id"], body.new_password)
    return {"success": True}

class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: Optional[str] = Field(default=None, max_length=30)


# Settings > Profile. The page always called PATCH /me but the route was never
# written, so saving answered 405 (fixed 2026-11-11). The token carries the
# name and email, so a fresh one comes back for the page to keep; otherwise a
# changed email would break "change password" until the next login.
@router.patch("/me")
def update_own_profile(body: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    name, email = body.name.strip(), body.email.strip()
    phone = (body.phone or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    other = db_users.get_user_by_email(email)
    if other is not None and other["id"] != current_user["id"]:
        raise HTTPException(status_code=400, detail="That email is already used by someone else")
    db_users.update_own_profile(current_user["id"], name, email, phone)
    token = create_access_token(current_user["id"], email, current_user["role"], name, current_user.get("role_id"))
    return {"access_token": token,
            "user": {"id": current_user["id"], "name": name, "email": email, "phone": phone, "role": current_user["role"]}}
