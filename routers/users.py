from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import users as db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    phone: Optional[str] = None
    role: str = "technician"
    role_id: Optional[int] = None

class UserUpdate(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    role: str
    role_id: Optional[int] = None

@router.post("/users")
def create_user(user: UserCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create users")
    if not user.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not user.email.strip():
        raise HTTPException(status_code=400, detail="Email is required")
    if not user.password:
        raise HTTPException(status_code=400, detail="Password is required")
    if db.get_user_by_email(user.email) is not None:
        raise HTTPException(status_code=400, detail="Email is already in use")

    new_id = db.add_user(
        user.name,
        user.email,
        user.password,
        user.phone,
        user.role,
        user.role_id,
    )
    return {"id": new_id, "name": user.name, "email": user.email, "role": user.role, "role_id": user.role_id}

@router.get("/users")
def list_users(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view users")
    return db.get_all_users()

@router.patch("/users/{user_id}")
def edit_user(user_id: int, user: UserUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit users")
    db.update_user(user_id, user.name, user.email, user.phone, user.role, user.role_id)
    return {"id": user_id, "name": user.name, "email": user.email, "phone": user.phone, "role": user.role, "role_id": user.role_id}

@router.delete("/users/{user_id}")
def remove_user(user_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete users")
    db.deactivate_user(user_id)
    return {"id": user_id, "deactivated": True}