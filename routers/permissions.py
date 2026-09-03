from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from db import permissions as db
from routers.auth import get_current_user

router = APIRouter()

def user_has_permission(current_user: dict, section: str, action: str) -> bool:
    if current_user["role"] == "admin":
        return True
    role_id = current_user.get("role_id")
    if role_id is None:
        return False
    return db.check_role_permission(role_id, section, action)

class RoleCreate(BaseModel):
    name: str

class RoleUpdate(BaseModel):
    name: str

@router.get("/roles")
def list_roles(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view roles")
    return db.get_all_roles()

@router.post("/roles")
def create_role(role: RoleCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create roles")
    new_id = db.add_role(role.name)
    return {"id": new_id, "name": role.name}

@router.patch("/roles/{role_id}")
def edit_role(role_id: int, role: RoleUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit roles")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    db.update_role(role_id, role.name)
    return {"id": role_id, "name": role.name}

@router.delete("/roles/{role_id}")
def remove_role(role_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete roles")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    db.delete_role(role_id)
    return {"id": role_id, "deleted": True}

class PermissionEntry(BaseModel):
    section: str
    action: str
    allowed: bool

class RolePermissionsUpdate(BaseModel):
    permissions: list[PermissionEntry]

@router.get("/roles/{role_id}/permissions")
def get_permissions(role_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view permissions")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return db.get_role_permissions(role_id)

@router.put("/roles/{role_id}/permissions")
def set_permissions(role_id: int, update: RolePermissionsUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to set permissions")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    permissions_list = [p.dict() for p in update.permissions]
    db.set_role_permissions(role_id, permissions_list)
    return {"role_id": role_id, "permissions": permissions_list}
