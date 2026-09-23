from fastapi import APIRouter, Depends, HTTPException
from db import notifications as db
from routers.auth import get_current_user

router = APIRouter()


@router.get("/notifications")
def list_notifications(current_user: dict = Depends(get_current_user)):
    return db.list_for_user(current_user["id"])


@router.get("/notifications/unread-count")
def unread_count(current_user: dict = Depends(get_current_user)):
    return {"count": db.unread_count(current_user["id"])}


@router.post("/notifications/{notif_id}/read")
def mark_read(notif_id: int, current_user: dict = Depends(get_current_user)):
    if not db.mark_read(current_user["id"], notif_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(current_user: dict = Depends(get_current_user)):
    db.mark_all_read(current_user["id"])
    return {"ok": True}
