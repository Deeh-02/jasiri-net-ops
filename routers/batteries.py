from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import batteries as db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()


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

@router.post("/batteries")
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

@router.patch("/batteries/{battery_id}")
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

@router.get("/batteries/{battery_id}")
def get_battery(battery_id: int, current_user: dict = Depends(get_current_user)):
    battery = db.get_battery_by_id(battery_id)
    if battery is None:
        raise HTTPException(status_code=404, detail="Battery not found")
    return battery

@router.get("/batteries/{battery_id}/movements")
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

@router.patch("/batteries/{battery_id}/charge-status")
def set_charge_status(battery_id: int, update: ChargeStatusUpdate, current_user: dict = Depends(get_current_user)):
    valid = {"unknown", "charging", "charged", "low"}
    if update.charge_status not in valid:
        raise HTTPException(status_code=400, detail=f"charge_status must be one of {sorted(valid)}")
    db.update_charge_status(battery_id, update.charge_status)
    return {"id": battery_id, "charge_status": update.charge_status}

@router.post("/movements")
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

@router.get("/movements")
def list_movements(history: bool = False, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view movements")
    if history:
        return db.get_all_movements_history()
    return db.get_active_movements()

@router.get("/movements/overdue-count")
def movements_overdue_count(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view movements")
    return {"count": db.get_overdue_movement_count(threshold_hours=1)}

@router.post("/movements/{movement_id}/mark-in-transit")
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

@router.post("/movements/{movement_id}/mark-arrived")
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

@router.post("/movements/{movement_id}/confirm-online")
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

@router.post("/movements/{movement_id}/cancel")
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

@router.delete("/batteries/{battery_id}")
def remove_battery(battery_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "batteries", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete batteries")
    db.deactivate_battery(battery_id)
    return {"id": battery_id, "deactivated": True}

@router.get("/batteries/{battery_id}/location")
def read_current_location(battery_id: int, current_user: dict = Depends(get_current_user)):
    location = db.get_current_location(battery_id)
    if location is None:
        raise HTTPException(status_code=404, detail="No location found for this battery")
    return {"battery_id": battery_id, "current_location": location}

@router.get("/batteries/{battery_id}/history")
def read_movement_history(battery_id: int, current_user: dict = Depends(get_current_user)):
    history = db.get_movement_history(battery_id)
    return {"battery_id": battery_id, "history": history}

@router.get("/batteries")
def read_all_batteries(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "batteries", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view batteries")
    return db.get_all_batteries()