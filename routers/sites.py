from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import sites as db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

class LocationCreate(BaseModel):
    name: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    is_home_base: bool = False

class LocationUpdate(BaseModel):
    name: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    is_home_base: bool = False

@router.post("/locations")
def create_location(location: LocationCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create sites")
    new_id = db.add_location(
        location.name,
        location.contact_name,
        location.contact_phone,
        location.address,
        location.is_home_base,
    )
    return {
        "id": new_id,
        "name": location.name,
        "contact_name": location.contact_name,
        "contact_phone": location.contact_phone,
        "address": location.address,
        "is_home_base": location.is_home_base,
    }

@router.patch("/locations/{location_id}")
def edit_location(location_id: int, location: LocationUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit sites")
    db.update_location(
        location_id,
        location.name,
        location.contact_name,
        location.contact_phone,
        location.address,
        location.is_home_base,
    )
    return {
        "id": location_id,
        "name": location.name,
        "contact_name": location.contact_name,
        "contact_phone": location.contact_phone,
        "address": location.address,
        "is_home_base": location.is_home_base,
    }

@router.post("/locations/{location_id}/set-home-base")
def make_home_base(location_id: int, current_user: dict = Depends(get_current_user)):
    db.set_home_base(location_id)
    return {"id": location_id, "is_home_base": True}

@router.get("/locations")
def read_all_locations(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view sites")
    return db.get_all_locations()

@router.delete("/locations/{location_id}")
def remove_location(location_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete sites")
    db.delete_location(location_id)
    return {"id": location_id, "deactivated": True}