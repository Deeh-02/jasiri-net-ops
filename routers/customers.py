from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from db import customers as db
from db.customer_import import ImportFileError, normalize_phone, parse_customer_file
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


# One permission (Roles > Sites > Site Status > Customer Broadcasts) gates
# the whole feature: the customer list holds phone numbers, and importing one
# is how a broadcast's audience is defined, so there's no useful "view but
# not send" split.
def require_broadcast_access(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "send_broadcasts"):
        raise HTTPException(status_code=403, detail="You don't have permission to manage customer broadcasts")
    return current_user


def _check_router_ids(router_ids):
    known = {r["id"] for r in db.list_routers()}
    if not router_ids or not set(router_ids) <= known:
        raise HTTPException(status_code=400, detail="Pick at least one router")


@router.get("/customers/routers")
def get_routers(current_user: dict = Depends(require_broadcast_access)):
    return db.list_routers()


@router.get("/customers")
def get_customers(router_id: int | None = None, current_user: dict = Depends(require_broadcast_access)):
    return db.list_customers(router_id)


class CustomerFields(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = None
    plan: str | None = Field(default=None, max_length=80)
    account_status: Literal["Active", "Expired", "Inactive"]
    enabled: bool = True
    expiry: date | None = None


class CustomerCreate(CustomerFields):
    router_id: int
    username: str = Field(min_length=1, max_length=120)


def _clean(fields):
    """Blank text -> None, and a typed phone must be a real Kenyan mobile —
    a customer you can't text is allowed, a mistyped number is not."""
    phone = (fields.phone or "").strip()
    normalized = normalize_phone(phone) if phone else None
    if phone and not normalized:
        raise HTTPException(status_code=400, detail="That isn't a Kenyan mobile number (e.g. 0712345678)")
    return (fields.name or "").strip() or None, normalized, (fields.plan or "").strip() or None


@router.post("/customers")
def add_customer(body: CustomerCreate, current_user: dict = Depends(require_broadcast_access)):
    if not db.router_exists(body.router_id):
        raise HTTPException(status_code=400, detail="Unknown router")
    name, phone, plan = _clean(body)
    try:
        new_id = db.add_customer(
            body.router_id, body.username.strip(), name, phone, plan, body.account_status, body.enabled, body.expiry
        )
    except db.CustomerConflict:
        raise HTTPException(status_code=409, detail="That router already has a customer with that username")
    return {"id": new_id}


@router.patch("/customers/{customer_id}")
def edit_customer(customer_id: int, body: CustomerFields, current_user: dict = Depends(require_broadcast_access)):
    name, phone, plan = _clean(body)
    if not db.update_customer(customer_id, name, phone, plan, body.account_status, body.enabled, body.expiry):
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"id": customer_id}


@router.post("/customers/import")
async def import_file(
    router_id: int = Form(...),
    commit: bool = Form(False),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_broadcast_access),
):
    """Dry run unless commit=true: the same call, so what the preview showed is
    exactly what a commit does."""
    if not db.router_exists(router_id):
        raise HTTPException(status_code=400, detail="Unknown router")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="That file is over 5 MB — is it the right one?")
    try:
        parsed = parse_customer_file(file.filename, content)
    except ImportFileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    counts = db.import_customers(router_id, parsed["customers"], commit)
    return {
        **counts,
        "committed": commit,
        "total": len(parsed["customers"]),
        "bad_phone": parsed["bad_phone"],
        "duplicates": parsed["duplicates"],
    }


class AudienceQuery(BaseModel):
    router_ids: list[int]
    status_filter: str = "active"


def _check_status(status_filter):
    if status_filter not in db.STATUS_FILTERS:
        raise HTTPException(status_code=400, detail="Unknown customer status")


@router.post("/customers/broadcasts/preview")
def preview(body: AudienceQuery, current_user: dict = Depends(require_broadcast_access)):
    _check_router_ids(body.router_ids)
    _check_status(body.status_filter)
    return db.audience_preview(body.router_ids, body.status_filter)


class BroadcastCreate(AudienceQuery):
    message: str = Field(min_length=1, max_length=db.MAX_MESSAGE_LENGTH)
    customer_ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/customers/broadcasts")
def send_broadcast(
    body: BroadcastCreate,
    background: BackgroundTasks,
    current_user: dict = Depends(require_broadcast_access),
):
    _check_router_ids(body.router_ids)
    _check_status(body.status_filter)
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Write a message first")
    try:
        broadcast_id, recipients = db.start_broadcast(
            current_user["id"], message, body.router_ids, body.status_filter, body.customer_ids
        )
    except db.BroadcastRefused as e:
        raise HTTPException(status_code=409, detail=str(e))
    background.add_task(db.run_broadcast, broadcast_id, message, recipients)
    return {"id": broadcast_id, "recipient_count": len(recipients)}


@router.get("/customers/broadcasts")
def get_broadcasts(current_user: dict = Depends(require_broadcast_access)):
    return db.list_broadcasts()
