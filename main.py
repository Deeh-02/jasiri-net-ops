from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from jose import jwt
from datetime import datetime, timedelta
import db

app = FastAPI()

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Auth config (local dev only — moves to env variables when we deploy for real) ---
SECRET_KEY = "temporary-dev-secret-change-this-before-deploy"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

def create_access_token(user_id, email, role, name, role_id=None):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "role": role, "name": name, "role_id": role_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/login")
def login(credentials: LoginRequest):
    user = db.get_user_by_email(credentials.email)
    if not user or user["status"] != "active":
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not db.verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"], user["email"], user["role"], user["name"], user["role_id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]},
    }


from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"id": int(payload["sub"]), "email": payload["email"], "role": payload["role"], "name": payload["name"], "role_id": payload.get("role_id")}

@app.get("/me")
def read_current_user(current_user: dict = Depends(get_current_user)):
    return current_user

@app.get("/me/permissions")
def get_my_permissions(current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "admin":
        return []
    role_id = current_user.get("role_id")
    if role_id is None:
        return []
    return db.get_role_permissions(role_id)

@app.get("/")
def serve_dashboard():
    return FileResponse("static/index.html")

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

class BatteryCreate(BaseModel):
    battery_number: str
    serial_number: Optional[str] = None
    model: Optional[str] = None
    capacity: Optional[str] = None

class BatteryUpdate(BaseModel):
    battery_number: str
    serial_number: Optional[str] = None
    model: Optional[str] = None
    capacity: Optional[str] = None

class MovementCreate(BaseModel):
    battery_id: int
    from_location_id: Optional[int] = None
    to_location_id: int
    reason: Optional[str] = None

class ChargeStatusUpdate(BaseModel):
    charge_status: str

# Seed value only — matches the spec's intent that new reasons (scheduled_swap,
# testing, battery_failure, redistribution, ...) can be added later by just adding
# to this set, no DB migration needed since `reason` is a plain text column.
MOVEMENT_REASONS = {"site_down", "storage"}

@app.post("/locations")
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

@app.patch("/locations/{location_id}")
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

@app.get("/roles")
def list_roles(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view roles")
    return db.get_all_roles()

@app.post("/roles")
def create_role(role: RoleCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create roles")
    new_id = db.add_role(role.name)
    return {"id": new_id, "name": role.name}

@app.patch("/roles/{role_id}")
def edit_role(role_id: int, role: RoleUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit roles")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    db.update_role(role_id, role.name)
    return {"id": role_id, "name": role.name}

@app.delete("/roles/{role_id}")
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

@app.get("/roles/{role_id}/permissions")
def get_permissions(role_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view permissions")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return db.get_role_permissions(role_id)

@app.put("/roles/{role_id}/permissions")
def set_permissions(role_id: int, update: RolePermissionsUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to set permissions")
    existing = db.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    permissions_list = [p.dict() for p in update.permissions]
    db.set_role_permissions(role_id, permissions_list)
    return {"role_id": role_id, "permissions": permissions_list}

@app.post("/locations/{location_id}/set-home-base")
def make_home_base(location_id: int, current_user: dict = Depends(get_current_user)):
    db.set_home_base(location_id)
    return {"id": location_id, "is_home_base": True}

@app.post("/batteries")
def create_battery(battery: BatteryCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "batteries", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to add batteries")
    new_id = db.add_battery(
        battery.battery_number,
        battery.serial_number,
        battery.model,
        battery.capacity,
    )
    return {"id": new_id, "battery_number": battery.battery_number}

@app.patch("/batteries/{battery_id}")
def edit_battery(battery_id: int, battery: BatteryUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "batteries", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit batteries")
    existing = db.get_battery_by_id(battery_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Battery not found")
    db.update_battery(
        battery_id,
        battery.battery_number,
        battery.serial_number,
        battery.model,
        battery.capacity,
    )
    return {
        "id": battery_id,
        "battery_number": battery.battery_number,
        "serial_number": battery.serial_number,
        "model": battery.model,
        "capacity": battery.capacity,
    }

@app.get("/batteries/{battery_id}")
def get_battery(battery_id: int, current_user: dict = Depends(get_current_user)):
    battery = db.get_battery_by_id(battery_id)
    if battery is None:
        raise HTTPException(status_code=404, detail="Battery not found")
    return battery

@app.get("/batteries/{battery_id}/movements")
def get_battery_movements(battery_id: int, current_user: dict = Depends(get_current_user)):
    battery = db.get_battery_by_id(battery_id)
    if battery is None:
        raise HTTPException(status_code=404, detail="Battery not found")
    history = db.get_movement_history(battery_id)
    return [
        {
            "created_at": row[0].isoformat() if row[0] else None,
            "from_location": row[1],
            "to_location": row[2],
            "reason": row[3],
            "moved_by": row[4],
        }
        for row in history
    ]

@app.patch("/batteries/{battery_id}/charge-status")
def set_charge_status(battery_id: int, update: ChargeStatusUpdate, current_user: dict = Depends(get_current_user)):
    valid = {"unknown", "charging", "charged", "low"}
    if update.charge_status not in valid:
        raise HTTPException(status_code=400, detail=f"charge_status must be one of {sorted(valid)}")
    db.update_charge_status(battery_id, update.charge_status)
    return {"id": battery_id, "charge_status": update.charge_status}


@app.post("/movements")
def create_movement(movement: MovementCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "manage"):
        raise HTTPException(status_code=403, detail="You don't have permission to move batteries")
    if movement.reason is not None and movement.reason not in MOVEMENT_REASONS:
        raise HTTPException(status_code=400, detail=f"reason must be one of {sorted(MOVEMENT_REASONS)}")
    new_id = db.record_movement(
        movement.battery_id,
        movement.from_location_id,
        movement.to_location_id,
        movement.reason,
        moved_by=current_user["name"],
        moved_by_user_id=current_user["id"],
    )
    return {"id": new_id}


# ============================================================
# NEW — Movement state machine actions
# ============================================================

@app.get("/movements")
def list_movements(history: bool = False, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view movements")
    if history:
        return db.get_all_movements_history()
    return db.get_active_movements()

@app.get("/movements/overdue-count")
def movements_overdue_count(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view movements")
    return {"count": db.get_overdue_movement_count(threshold_hours=1)}

@app.post("/movements/{movement_id}/mark-in-transit")
def movement_mark_in_transit(movement_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "manage"):
        raise HTTPException(status_code=403, detail="You don't have permission to move batteries")
    movement = db.get_movement_by_id(movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="Movement not found")
    if movement["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Can't mark in transit from status '{movement['status']}'")
    db.mark_movement_in_transit(movement_id)
    return {"id": movement_id, "status": "in_transit"}

@app.post("/movements/{movement_id}/mark-arrived")
def movement_mark_arrived(movement_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "manage"):
        raise HTTPException(status_code=403, detail="You don't have permission to move batteries")
    movement = db.get_movement_by_id(movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="Movement not found")
    if movement["status"] != "in_transit":
        raise HTTPException(status_code=400, detail=f"Can't mark arrived from status '{movement['status']}'")
    # Only a site-down move needs the follow-up "is it online?" question.
    # Anything else (storage, no reason given) is done the moment it arrives.
    if movement["reason"] == "site_down":
        db.mark_movement_arrived(movement_id)
        return {"id": movement_id, "status": "arrived"}
    db.complete_movement(movement_id)
    return {"id": movement_id, "status": "completed"}

class SiteOnlineAnswer(BaseModel):
    is_online: bool

@app.post("/movements/{movement_id}/confirm-online")
def movement_confirm_online(
    movement_id: int,
    answer: SiteOnlineAnswer,
    current_user: dict = Depends(get_current_user),
):
    if not user_has_permission(current_user, "movements", "manage"):
        raise HTTPException(status_code=403, detail="You don't have permission to move batteries")
    movement = db.get_movement_by_id(movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="Movement not found")
    if movement["status"] != "arrived":
        raise HTTPException(status_code=400, detail=f"Can't answer site-check from status '{movement['status']}'")
    if answer.is_online:
        db.confirm_site_online(movement_id)
        return {"id": movement_id, "status": "site_confirmed_online"}
    db.mark_site_still_down(movement_id)
    return {"id": movement_id, "status": "site_still_down"}

@app.post("/movements/{movement_id}/cancel")
def movement_cancel(movement_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "manage"):
        raise HTTPException(status_code=403, detail="You don't have permission to move batteries")
    movement = db.get_movement_by_id(movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="Movement not found")
    if movement["status"] not in ("pending", "in_transit"):
        raise HTTPException(status_code=400, detail=f"Can't cancel from status '{movement['status']}'")
    db.cancel_movement(movement_id)
    return {"id": movement_id, "status": "cancelled"}


# ============================================================
# NEW — Site (hourly) verification
# ============================================================

@app.get("/locations/verification")
def list_site_verification(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "site_checks", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view sites")
    return db.get_sites_with_verification_status()

class SiteVerificationAnswer(BaseModel):
    is_online: bool

@app.post("/locations/{location_id}/confirm")
def confirm_site_check(location_id: int, answer: SiteVerificationAnswer, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "site_checks", "confirm"):
        raise HTTPException(status_code=403, detail="You don't have permission to check sites")
    db.confirm_site(location_id, answer.is_online)
    return {"id": location_id, "is_online": answer.is_online}

@app.get("/locations/unconfirmed-count")
def locations_unconfirmed_count(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "site_checks", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view sites")
    return {"count": db.get_unconfirmed_site_count()}


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

@app.post("/users")
def create_user(user: UserCreate, current_user: dict = Depends(get_current_user)): 
    if not user_has_permission(current_user, "users", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create users")
    new_id = db.add_user(
        user.name,
        user.email,
        user.password,
        user.phone,
        user.role,
        user.role_id,
    )
    return {"id": new_id, "name": user.name, "email": user.email, "role": user.role, "role_id": user.role_id}

@app.get("/users")
def list_users(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view users")
    return db.get_all_users()


@app.patch("/users/{user_id}")
def edit_user(user_id: int, user: UserUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit users")
    db.update_user(user_id, user.name, user.email, user.phone, user.role, user.role_id)
    return {"id": user_id, "name": user.name, "email": user.email, "phone": user.phone, "role": user.role, "role_id": user.role_id}

@app.delete("/users/{user_id}")
def remove_user(user_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "users", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete users")
    db.deactivate_user(user_id)
    return {"id": user_id, "deactivated": True}

@app.delete("/batteries/{battery_id}")
def remove_battery(battery_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "batteries", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete batteries")
    db.deactivate_battery(battery_id)
    return {"id": battery_id, "deactivated": True}

@app.get("/batteries/{battery_id}/location")
def read_current_location(battery_id: int, current_user: dict = Depends(get_current_user)):
    location = db.get_current_location(battery_id)
    if location is None:
        raise HTTPException(status_code=404, detail="No location found for this battery")
    return {"battery_id": battery_id, "current_location": location}

@app.get("/batteries/{battery_id}/history")
def read_movement_history(battery_id: int, current_user: dict = Depends(get_current_user)):
    history = db.get_movement_history(battery_id)
    return {"battery_id": battery_id, "history": history}

@app.get("/locations")
def read_all_locations(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view sites")
    return db.get_all_locations()

@app.get("/batteries")
def read_all_batteries(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "batteries", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view batteries")
    return db.get_all_batteries()

@app.delete("/locations/{location_id}")
def remove_location(location_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete sites")
    db.delete_location(location_id)
    return {"id": location_id, "deactivated": True}