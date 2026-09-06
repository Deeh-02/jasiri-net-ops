from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import inventory_locations as locations_db
from db import inventory_categories as categories_db
from db import inventory_items as items_db
from db import inventory_transactions as transactions_db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

# Fixed at the system level (not user-extensible) — plain text with no CHECK
# constraint per the codebase's enum-like-field convention (see
# MOVEMENT_REASONS in routers/batteries.py), validated here instead.
TRACKING_TYPES = {"asset_serialized", "inventory_quantity", "inventory_length"}
ASSET_STATUSES = {"Active", "Faulty", "In Repair", "Decommissioned", "Spare — In Storage"}
LENGTH_STATUSES = {"In Stock", "Out — Pending Reconciliation", "Depleted"}
# Out (issuing) is Milestone 5's issue_cart; Reconciled is Milestone 6's
# reconcile_cut. This endpoint only covers the single-line actions.
TRANSACTION_ACTIONS = {"In", "Transfer", "Adjustment", "Return", "Write-off"}
ACTIVITIES = {"Installation", "Expansion", "Maintenance", "Repair-Replacement", "Relocation", "Decommission"}

# Universal columns every item has regardless of tracking_type.
_UNIVERSAL_ITEM_FIELDS = {
    "sku", "name", "location_id", "unit_cost", "supplier", "unit_of_measure", "notes",
}
# Which additional columns apply to which tracking_type — anything a client
# sends outside its category's own set is silently dropped, never stored.
# This is what "never trust a client-supplied type" means in practice: the
# type comes from the category row looked up server-side, and it alone
# decides which columns get written.
_TYPE_FIELDS = {
    "asset_serialized": {"serial_number", "asset_status", "assigned_to_user_id", "make_model", "spec_capacity", "install_date"},
    "inventory_quantity": {"batch_lot", "expiry_date", "quantity_on_hand"},
    "inventory_length": {"cut_reel_id", "spec", "length_received", "length_remaining", "length_status"},
}

class InventoryLocationCreate(BaseModel):
    name: str
    is_store: bool = False
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None

class InventoryLocationUpdate(BaseModel):
    name: str
    is_store: bool = False
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None

@router.get("/inventory/locations")
def read_all_inventory_locations(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_locations", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory locations")
    return locations_db.get_all_locations()

@router.post("/inventory/locations")
def create_inventory_location(location: InventoryLocationCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_locations", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create inventory locations")
    new_id = locations_db.add_location(
        location.name, location.is_store, location.address,
        location.contact_name, location.contact_phone, location.notes,
    )
    return {"id": new_id, **location.dict()}

@router.patch("/inventory/locations/{location_id}")
def edit_inventory_location(location_id: int, location: InventoryLocationUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_locations", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit inventory locations")
    locations_db.update_location(
        location_id, location.name, location.is_store, location.address,
        location.contact_name, location.contact_phone, location.notes,
    )
    return {"id": location_id, **location.dict()}

@router.delete("/inventory/locations/{location_id}")
def remove_inventory_location(location_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_locations", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete inventory locations")
    locations_db.delete_location(location_id)
    return {"id": location_id, "deactivated": True}

class InventoryCategoryCreate(BaseModel):
    name: str
    tracking_type: str
    description: Optional[str] = None

class InventoryCategoryUpdate(BaseModel):
    name: str
    tracking_type: str
    description: Optional[str] = None

@router.get("/inventory/categories")
def read_all_inventory_categories(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_categories", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory categories")
    return categories_db.get_all_categories()

@router.post("/inventory/categories")
def create_inventory_category(category: InventoryCategoryCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_categories", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to create inventory categories")
    if category.tracking_type not in TRACKING_TYPES:
        raise HTTPException(status_code=400, detail=f"tracking_type must be one of {sorted(TRACKING_TYPES)}")
    new_id = categories_db.add_category(category.name, category.tracking_type, category.description)
    return {"id": new_id, **category.dict()}

@router.patch("/inventory/categories/{category_id}")
def edit_inventory_category(category_id: int, category: InventoryCategoryUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_categories", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit inventory categories")
    if category.tracking_type not in TRACKING_TYPES:
        raise HTTPException(status_code=400, detail=f"tracking_type must be one of {sorted(TRACKING_TYPES)}")
    existing = categories_db.get_category_by_id(category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if category.tracking_type != existing["tracking_type"] and categories_db.category_has_items(category_id):
        raise HTTPException(
            status_code=400,
            detail="Tracking type can't change — this category already has items."
        )
    categories_db.update_category(category_id, category.name, category.tracking_type, category.description)
    return {"id": category_id, **category.dict()}

@router.delete("/inventory/categories/{category_id}")
def remove_inventory_category(category_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_categories", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete inventory categories")
    categories_db.delete_category(category_id)
    return {"id": category_id, "deactivated": True}

class InventoryItemCreate(BaseModel):
    category_id: int
    sku: str
    name: str
    location_id: Optional[int] = None
    unit_cost: Optional[float] = None
    supplier: Optional[str] = None
    unit_of_measure: Optional[str] = None
    notes: Optional[str] = None
    # Asset (Serialized)
    serial_number: Optional[str] = None
    asset_status: Optional[str] = None
    assigned_to_user_id: Optional[int] = None
    make_model: Optional[str] = None
    spec_capacity: Optional[str] = None
    install_date: Optional[str] = None
    # Inventory (Quantity)
    batch_lot: Optional[str] = None
    expiry_date: Optional[str] = None
    quantity_on_hand: Optional[float] = None
    # Inventory (Length)
    cut_reel_id: Optional[str] = None
    spec: Optional[str] = None
    length_received: Optional[float] = None
    length_remaining: Optional[float] = None
    length_status: Optional[str] = None

class InventoryItemUpdate(InventoryItemCreate):
    category_id: Optional[int] = None  # ignored — an item's category/tracking_type never changes after creation

def _fields_for_type(payload: dict, tracking_type: str) -> dict:
    """Keeps only the universal columns plus whichever type-specific columns
    apply to tracking_type — anything else the client sent (fields from a
    different tracking_type, or category_id on an update) is dropped here,
    not written."""
    allowed = _UNIVERSAL_ITEM_FIELDS | _TYPE_FIELDS[tracking_type]
    return {k: v for k, v in payload.items() if k in allowed}

def _validate_type_fields(tracking_type: str, fields: dict):
    # dict.setdefault doesn't help here — pydantic's .dict() already sets
    # every unset optional field's key to None, so the key always "exists".
    if tracking_type == "asset_serialized":
        if not fields.get("serial_number"):
            raise HTTPException(status_code=400, detail="serial_number is required for an Asset (Serialized) item")
        if fields.get("asset_status") is None:
            fields["asset_status"] = "Active"
        if fields["asset_status"] not in ASSET_STATUSES:
            raise HTTPException(status_code=400, detail=f"asset_status must be one of {sorted(ASSET_STATUSES)}")
    elif tracking_type == "inventory_quantity":
        if fields.get("quantity_on_hand") is None:
            raise HTTPException(status_code=400, detail="quantity_on_hand is required for an Inventory (Quantity) item")
    elif tracking_type == "inventory_length":
        if not fields.get("cut_reel_id"):
            raise HTTPException(status_code=400, detail="cut_reel_id is required for an Inventory (Length) item")
        if fields.get("length_received") is None:
            raise HTTPException(status_code=400, detail="length_received is required for an Inventory (Length) item")
        if fields.get("length_remaining") is None:
            fields["length_remaining"] = fields["length_received"]
        if fields.get("length_status") is None:
            fields["length_status"] = "In Stock"
        if fields["length_status"] not in LENGTH_STATUSES:
            raise HTTPException(status_code=400, detail=f"length_status must be one of {sorted(LENGTH_STATUSES)}")

@router.get("/inventory/items")
def read_all_inventory_items(category_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory items")
    return items_db.get_all_items(category_id)

@router.post("/inventory/items")
def create_inventory_item(item: InventoryItemCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to add inventory items")
    category = categories_db.get_category_by_id(item.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    fields = _fields_for_type(item.dict(), category["tracking_type"])
    _validate_type_fields(category["tracking_type"], fields)
    fields["category_id"] = item.category_id

    new_id = items_db.add_item(fields)
    return items_db.get_item_by_id(new_id)

@router.patch("/inventory/items/{item_id}")
def edit_inventory_item(item_id: int, item: InventoryItemUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit inventory items")
    existing = items_db.get_item_by_id(item_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Item not found")

    # tracking_type is resolved from the item's own (unchangeable) category —
    # never from anything the client sends.
    fields = _fields_for_type(item.dict(), existing["tracking_type"])
    _validate_type_fields(existing["tracking_type"], fields)

    items_db.update_item(item_id, fields)
    return items_db.get_item_by_id(item_id)

@router.delete("/inventory/items/{item_id}")
def remove_inventory_item(item_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete inventory items")
    items_db.deactivate_item(item_id)
    return {"id": item_id, "deactivated": True}

class InventoryTransactionCreate(BaseModel):
    action: str
    item_id: int
    qty_or_length: Optional[float] = None
    from_location_id: Optional[int] = None
    to_location_id: Optional[int] = None
    site_location_id: Optional[int] = None
    activity: Optional[str] = None
    issued_to_user_id: Optional[int] = None
    notes: Optional[str] = None

def _plan_transaction(item: dict, txn: InventoryTransactionCreate):
    """The single place every single-line action's item-side effect is
    decided — see the phase plan's Action Semantics table. Returns
    (item_updates, split_new_item_fields, resolved qty_or_length,
    resolved from_location_id, resolved to_location_id). Raises
    HTTPException on anything that doesn't make sense for this item's
    tracking_type or current state."""
    tracking_type = item["tracking_type"]
    to_location_id = txn.to_location_id
    from_location_id = txn.from_location_id
    qty_or_length = txn.qty_or_length
    item_updates = None
    split_new_item_fields = None

    if txn.action == "In":
        # Pure log entry — the item already reflects its arrival, having
        # been created (Milestone 3's add_item) with that state. No item
        # write here; a new batch/cut/serial with a different cost or
        # expiry is a NEW row, never a top-up of an existing one.
        to_location_id = to_location_id or item["location_id"]
        if qty_or_length is None:
            qty_or_length = item["quantity_on_hand"] if tracking_type == "inventory_quantity" else item["length_received"]

    elif txn.action == "Transfer":
        if to_location_id is None:
            raise HTTPException(status_code=400, detail="to_location_id is required for a Transfer")
        from_location_id = from_location_id or item["location_id"]
        if tracking_type == "inventory_quantity":
            current_qty = float(item["quantity_on_hand"] or 0)
            transfer_qty = qty_or_length if qty_or_length is not None else current_qty
            if transfer_qty <= 0 or transfer_qty > current_qty:
                raise HTTPException(status_code=400, detail="qty_or_length must be greater than 0 and no more than the item's current quantity_on_hand")
            qty_or_length = transfer_qty
            if transfer_qty == current_qty:
                item_updates = {"location_id": to_location_id}
            else:
                item_updates = {"quantity_on_hand": current_qty - transfer_qty}
                split_new_item_fields = {
                    "category_id": item["category_id"], "sku": item["sku"], "name": item["name"],
                    "location_id": to_location_id, "unit_cost": item["unit_cost"], "supplier": item["supplier"],
                    "unit_of_measure": item["unit_of_measure"], "batch_lot": item["batch_lot"],
                    "expiry_date": item["expiry_date"], "quantity_on_hand": transfer_qty,
                }
        else:
            # Asset (one serialized row) and Length (not split by Transfer,
            # only by Milestone 6's reconciliation) both move wholesale.
            item_updates = {"location_id": to_location_id}

    elif txn.action == "Adjustment":
        if tracking_type == "asset_serialized":
            raise HTTPException(status_code=400, detail="Adjustment does not apply to a serialized asset")
        if qty_or_length is None or qty_or_length == 0:
            raise HTTPException(status_code=400, detail="qty_or_length (the signed correction) is required for an Adjustment")
        if tracking_type == "inventory_quantity":
            new_value = float(item["quantity_on_hand"] or 0) + qty_or_length
            if new_value < 0:
                raise HTTPException(status_code=400, detail="This adjustment would take quantity_on_hand below zero")
            item_updates = {"quantity_on_hand": new_value}
        else:
            new_value = float(item["length_remaining"] or 0) + qty_or_length
            if new_value < 0:
                raise HTTPException(status_code=400, detail="This adjustment would take length_remaining below zero")
            item_updates = {"length_remaining": new_value}

    elif txn.action == "Return":
        if to_location_id is None:
            raise HTTPException(status_code=400, detail="to_location_id is required for a Return")
        from_location_id = from_location_id or item["location_id"]
        item_updates = {"location_id": to_location_id}
        if tracking_type == "asset_serialized":
            item_updates["assigned_to_user_id"] = None

    elif txn.action == "Write-off":
        from_location_id = from_location_id or item["location_id"]
        if tracking_type == "asset_serialized":
            item_updates = {"is_active": False, "asset_status": "Decommissioned"}
        elif tracking_type == "inventory_quantity":
            if qty_or_length is None:
                qty_or_length = item["quantity_on_hand"]
            item_updates = {"quantity_on_hand": 0, "is_active": False}
        else:
            if qty_or_length is None:
                qty_or_length = item["length_remaining"]
            item_updates = {"length_remaining": 0, "length_status": "Depleted", "is_active": False}

    return item_updates, split_new_item_fields, qty_or_length, from_location_id, to_location_id

@router.post("/inventory/transactions")
def create_inventory_transaction(txn: InventoryTransactionCreate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_transactions", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to log inventory transactions")
    if txn.action not in TRANSACTION_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(TRANSACTION_ACTIONS)}")
    if txn.activity is not None and txn.activity not in ACTIVITIES:
        raise HTTPException(status_code=400, detail=f"activity must be one of {sorted(ACTIVITIES)}")

    item = items_db.get_item_by_id(txn.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item_updates, split_new_item_fields, qty_or_length, from_location_id, to_location_id = _plan_transaction(item, txn)

    sku_or_spec = item["spec"] if item["tracking_type"] == "inventory_length" else item["sku"]

    result = transactions_db.record_transaction(
        action=txn.action, item_id=txn.item_id, category_id=item["category_id"], sku_or_spec=sku_or_spec,
        qty_or_length=qty_or_length, from_location_id=from_location_id, to_location_id=to_location_id,
        site_location_id=txn.site_location_id, activity=txn.activity, issued_to_user_id=txn.issued_to_user_id,
        logged_by_user_id=current_user["id"], notes=txn.notes,
        item_updates=item_updates, split_new_item_fields=split_new_item_fields,
    )
    return result

@router.get("/inventory/transactions")
def read_transaction_log(
    item_id: Optional[int] = None, category_id: Optional[int] = None, action: Optional[str] = None,
    limit: int = 200, current_user: dict = Depends(get_current_user),
):
    if not user_has_permission(current_user, "inventory_transactions", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory transactions")
    return transactions_db.get_transaction_log(item_id, category_id, action, limit)

class IssueCartLine(BaseModel):
    item_id: int
    # Quantity lines only. An Asset line is the serial itself, and a Length
    # line always sends the whole cut out (how much was actually used isn't
    # known until reconciliation), so neither carries a number here.
    qty_or_length: Optional[float] = None

class IssueCart(BaseModel):
    lines: list[IssueCartLine]
    site_location_id: int
    activity: Optional[str] = None
    issued_to_user_id: Optional[int] = None
    notes: Optional[str] = None

def _plan_issue_line(item: dict, line: IssueCartLine, site_location_id: int, issued_to_user_id):
    """One cart line's item-side effect, decided by the tracking_type read
    off the item's own category. Returns (item_updates, qty_or_length,
    status) — status is only set for Length lines, which stay open until
    Milestone 6's reconciliation closes them out."""
    tracking_type = item["tracking_type"]

    if tracking_type == "asset_serialized":
        # The asset is now in service at the site, in someone's hands.
        return (
            {
                "location_id": site_location_id,
                "assigned_to_user_id": issued_to_user_id,
                "asset_status": "Active",
            },
            None,
            None,
        )

    if tracking_type == "inventory_quantity":
        on_hand = float(item["quantity_on_hand"] or 0)
        issued = line.qty_or_length
        if issued is None or issued <= 0:
            raise HTTPException(status_code=400, detail=f"A quantity is required for '{item['name']}'")
        if issued > on_hand:
            raise HTTPException(status_code=400, detail=f"Only {on_hand} of '{item['name']}' on hand, can't issue {issued}")
        # Consumables are drawn down from stock rather than moved — once
        # they're issued to a job, what matters is how many are left.
        return ({"quantity_on_hand": on_hand - issued}, issued, None)

    # Length: the whole cut physically goes out now, but nothing is deducted
    # yet — actual metres used aren't known until the job closes, which is
    # what makes this two-stage. The row stays open/pending until then.
    if item["length_status"] == "Out — Pending Reconciliation":
        raise HTTPException(status_code=400, detail=f"Cut '{item['cut_reel_id']}' is already out and awaiting reconciliation")
    return (
        {"length_status": "Out — Pending Reconciliation", "location_id": site_location_id},
        item["length_remaining"],
        "open_pending",
    )

@router.post("/inventory/issue")
def issue_materials(cart: IssueCart, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_transactions", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to issue materials")
    if not cart.lines:
        raise HTTPException(status_code=400, detail="Nothing to issue — the cart is empty")
    if cart.activity is not None and cart.activity not in ACTIVITIES:
        raise HTTPException(status_code=400, detail=f"activity must be one of {sorted(ACTIVITIES)}")

    # Every line is resolved and validated before anything is written, so a
    # bad line rejects the whole cart rather than half-issuing it.
    prepared_lines = []
    for line in cart.lines:
        item = items_db.get_item_by_id(line.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Item {line.item_id} not found")

        item_updates, qty_or_length, status = _plan_issue_line(
            item, line, cart.site_location_id, cart.issued_to_user_id
        )

        prepared_lines.append({
            "item_id": line.item_id,
            "category_id": item["category_id"],
            "sku_or_spec": item["spec"] if item["tracking_type"] == "inventory_length" else item["sku"],
            "qty_or_length": qty_or_length,
            "from_location_id": item["location_id"],
            "status": status,
            "item_updates": item_updates,
        })

    return transactions_db.issue_cart(
        prepared_lines, cart.site_location_id, cart.activity,
        cart.issued_to_user_id, current_user["id"], cart.notes,
    )

class InventoryTransactionEdit(BaseModel):
    qty_or_length: Optional[float] = None
    from_location_id: Optional[int] = None
    to_location_id: Optional[int] = None
    site_location_id: Optional[int] = None
    activity: Optional[str] = None
    issued_to_user_id: Optional[int] = None
    notes: Optional[str] = None

@router.patch("/inventory/transactions/{transaction_id}")
def edit_transaction(transaction_id: int, edit: InventoryTransactionEdit, current_user: dict = Depends(get_current_user)):
    # Admin-only, full stop — not gated through user_has_permission's
    # grantable section/action mechanism, since editing history should never
    # be something a role can be granted; only the built-in admin bypass.
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can edit a historical transaction")
    existing = transactions_db.get_transaction_by_id(transaction_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    # exclude_unset, not the full dict: unlike the rest of this router's PATCH
    # endpoints (which always get a full form resend from their own edit
    # modal), this one has no UI guaranteeing that — a caller correcting just
    # `notes` must not silently null out every other field on the row.
    transactions_db.update_transaction(transaction_id, edit.dict(exclude_unset=True))
    return transactions_db.get_transaction_by_id(transaction_id)
