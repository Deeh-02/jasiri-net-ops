from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
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
    return current_user

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