from fastapi import APIRouter, Depends, HTTPException, Query
from db import notifications as db
from routers.auth import get_current_user

router = APIRouter()


# The bell asks for the default 30; Alerts > Notifications asks for more.
@router.get("/notifications")
def list_notifications(limit: int = Query(db.DEFAULT_LIST_LIMIT, ge=1, le=200),
                       current_user: dict = Depends(get_current_user)):
    return db.list_for_user(current_user["id"], limit)


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
