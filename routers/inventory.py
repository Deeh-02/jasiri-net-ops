from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from db import inventory_locations as locations_db
from db import inventory_categories as categories_db
from db import inventory_items as items_db
from db import inventory_transactions as transactions_db
from db import inventory_reports as reports_db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

# Fixed at the system level (not user-extensible) — plain text with no CHECK
# constraint per the codebase's enum-like-field convention (see
# MOVEMENT_REASONS in routers/batteries.py), validated here instead.
TRACKING_TYPES = {"asset_serialized", "inventory_quantity", "inventory_length"}
# Display-only "Core" naming for error messages — must match
# TRACKING_TYPE_LABELS in static/js/inventory-common.js exactly (that file's
# own comment is the source-of-truth note; this is the one other place that
# needs updating if the labels ever change).
TRACKING_TYPE_LABELS = {
    "asset_serialized": "Asset Core",
    "inventory_quantity": "Consumables Core",
    "inventory_length": "Cable Core",
}
CUSTODY_TYPES = {"per_job", "custody"}
ASSET_STATUSES = {"Active", "Deployed", "Faulty", "In Repair", "Decommissioned"}
LENGTH_STATUSES = {"In Stock", "Out — Pending Reconciliation", "Depleted"}
# Out (issuing) is Milestone 5's issue_cart; Reconciled is Milestone 6's
# reconcile_cut. This endpoint only covers the single-line actions on things
# that already exist — "In" is deliberately NOT one of them: receiving new
# stock only ever happens through Add Product/Add Unit (which auto-generates
# serials/reel IDs, supports batch quantities, and writes its own "In" row
# via add_unit/add_unit_batch below). Allowing a second, manual "In" path
# here would let the same event be recorded two divergent ways — one with
# consistent auto-generated IDs and batch support, one without.
TRANSACTION_ACTIONS = {"Transfer", "Adjustment", "Return", "Write-off"}
ACTIVITIES = {"Installation", "Expansion", "Maintenance", "Repair-Replacement", "Relocation", "Decommission"}

# A returned cable remainder at least this long is worth re-stocking as its
# own cut; anything shorter is scrap that stays logged against the original.
USABLE_LENGTH_THRESHOLD_M = 20

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

# Locations have no management screen any more — every free-typed location
# field (Issue/Return Materials' Site, Add/Edit Unit's Location) creates
# rows implicitly (see locations_db.get_or_create_location_by_name), so
# there's no user-facing add/edit/delete action left to gate. GET stays: it
# backs both autocompletes' suggestion lists — Add/Edit Unit's Location
# filters to is_store=true, Site's suggests every location, store or job
# site.
@router.get("/inventory/locations")
def read_all_inventory_locations(is_store: Optional[bool] = None, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory locations")
    return locations_db.get_all_locations(is_store)

class InventoryCategoryCreate(BaseModel):
    name: str
    tracking_type: str
    custody_type: str = "per_job"
    description: Optional[str] = None

class InventoryCategoryUpdate(BaseModel):
    name: str
    tracking_type: str
    custody_type: str = "per_job"
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
    if category.custody_type not in CUSTODY_TYPES:
        raise HTTPException(status_code=400, detail=f"custody_type must be one of {sorted(CUSTODY_TYPES)}")
    new_id = categories_db.add_category(category.name, category.tracking_type, category.custody_type, category.description)
    return {"id": new_id, **category.dict()}

@router.patch("/inventory/categories/{category_id}")
def edit_inventory_category(category_id: int, category: InventoryCategoryUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_categories", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit inventory categories")
    if category.tracking_type not in TRACKING_TYPES:
        raise HTTPException(status_code=400, detail=f"tracking_type must be one of {sorted(TRACKING_TYPES)}")
    if category.custody_type not in CUSTODY_TYPES:
        raise HTTPException(status_code=400, detail=f"custody_type must be one of {sorted(CUSTODY_TYPES)}")
    existing = categories_db.get_category_by_id(category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if category.tracking_type != existing["tracking_type"]:
        item_count = categories_db.count_active_items(category_id)
        if item_count > 0:
            core_label = TRACKING_TYPE_LABELS.get(existing["tracking_type"], existing["tracking_type"])
            item_word = "item" if item_count == 1 else "items"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This category has {item_count} {item_word} using {core_label}-specific fields, "
                    f"so its Core can't be changed. Create a new category with the Core you want instead."
                ),
            )
    categories_db.update_category(category_id, category.name, category.tracking_type, category.custody_type, category.description)
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
    # Free-typed, same pattern as Issue/Return Materials' Site field —
    # resolved server-side via locations_db.get_or_create_location_by_name
    # (is_store=True) by _resolve_unit_location_id below, which takes
    # precedence over location_id when both a name and an id could apply.
    # Add/Edit Unit send this; location_id stays for any other caller that
    # already has a resolved id (e.g. Batch Review's per-row correction).
    location_name: Optional[str] = None
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

def _resolve_unit_location_id(location_id, location_name):
    """Add/Edit Unit's Location field is free-typed (mirrors Issue/Return's
    Site field), matching or creating against is_store=true rows only —
    never job sites, even if one happens to share the name (see the
    is_store note on get_or_create_location_by_name). A plain location_id
    is still honored when no name was sent, for callers that already have
    a resolved id."""
    if location_name is not None and location_name.strip():
        return locations_db.get_or_create_location_by_name(location_name, is_store=True)
    return location_id

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
        if not fields.get("spec"):
            raise HTTPException(status_code=400, detail="Spec is required for a Cable Core item")
        # Cable Core has no separate SKU concept — Spec is the sole
        # identifying field (this is exactly what sku_or_spec already
        # computes server-side throughout reporting/grouping). sku stays a
        # NOT NULL column, so it's forced to mirror spec here rather than
        # left as whatever (if anything) the client sent — the frontend no
        # longer even shows a SKU input for this Core, but this holds
        # regardless of what any caller sends.
        fields["sku"] = fields["spec"]

@router.get("/inventory/items")
def read_all_inventory_items(category_id: Optional[int] = None, sku: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory items")
    return items_db.get_all_items(category_id, sku)

# Registered before /inventory/items/{item_id} — otherwise FastAPI's
# first-match routing would try to parse "summary" as that route's int
# item_id and 422 before ever reaching this handler.
@router.get("/inventory/items/summary")
def read_items_summary(category_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    # Backs both the Items page (inventory.js) and the Stock page (stock.js) —
    # each now independently permissioned (view_items / view_stock), and this
    # one shared endpoint has no way to tell which page is asking, so either
    # is enough to call it. A role with only Stock access still needs this to
    # load Stock's table, and likewise for Items-only.
    if not (user_has_permission(current_user, "inventory_items", "view_items")
            or user_has_permission(current_user, "inventory_items", "view_stock")):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory items")
    return reports_db.get_items_summary(category_id)

@router.get("/inventory/items/{item_id}")
def read_inventory_item(item_id: int, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view inventory items")
    item = items_db.get_item_by_id(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

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

class InventoryUnitCreate(InventoryItemCreate):
    # Threaded through by the frontend across a "+ Add another unit" sitting
    # — the first call leaves this unset (the response hands back a fresh
    # one), every following call in the same sitting passes that same value
    # back in, so every unit added lands under one event_group_id.
    event_group_id: Optional[str] = None

@router.post("/inventory/units")
def create_inventory_unit(unit: InventoryUnitCreate, current_user: dict = Depends(get_current_user)):
    """Add Item's Step 2 ("Add Unit") — unlike plain POST /inventory/items,
    this also writes the unit's arrival as an In transaction row in the same
    commit, so receiving stock never needs a separate manual log-the-arrival
    step. Kept as its own endpoint (not folded into POST /inventory/items)
    since the two are genuinely different operations — this one always
    produces a log row, the other never does.

    Gated on add — creating a brand-new product/SKU row (POST
    /inventory/items) and adding a physical unit of one (here) share the
    single "Add item" permission; restocking is no longer a separately
    grantable capability."""
    if not user_has_permission(current_user, "inventory_items", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to add stock")
    category = categories_db.get_category_by_id(unit.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    tracking_type = category["tracking_type"]

    unit.location_id = _resolve_unit_location_id(unit.location_id, unit.location_name)
    fields = _fields_for_type(unit.dict(), tracking_type)
    _validate_type_fields(tracking_type, fields)
    fields["category_id"] = unit.category_id

    sku_or_spec = fields["spec"] if tracking_type == "inventory_length" else fields["sku"]
    qty_or_length = None
    if tracking_type == "inventory_quantity":
        qty_or_length = fields.get("quantity_on_hand")
    elif tracking_type == "inventory_length":
        qty_or_length = fields.get("length_received")

    result = transactions_db.add_unit(
        fields, qty_or_length, sku_or_spec, fields.get("location_id"),
        current_user["id"], unit.notes, unit.event_group_id,
    )
    return {
        "item": items_db.get_item_by_id(result["item_id"]),
        "event_group_id": result["event_group_id"],
    }

class InventoryUnitBatchCreate(BaseModel):
    category_id: int
    sku: str
    name: str
    make_model: Optional[str] = None
    spec_capacity: Optional[str] = None
    supplier: Optional[str] = None
    unit_of_measure: Optional[str] = None
    quantity: int
    location_id: Optional[int] = None
    # Same free-typed pattern as InventoryItemCreate.location_name above —
    # shared across this whole batch sitting.
    location_name: Optional[str] = None
    unit_cost: Optional[float] = None
    asset_status: Optional[str] = None
    assigned_to_user_id: Optional[int] = None
    install_date: Optional[str] = None
    notes: Optional[str] = None
    # Same threading convention as InventoryUnitCreate.event_group_id above.
    event_group_id: Optional[str] = None

@router.post("/inventory/units/batch")
def create_inventory_unit_batch(batch: InventoryUnitBatchCreate, current_user: dict = Depends(get_current_user)):
    """Add Item's Step 2 ("Add Unit"), Existing-product batch variant —
    lets several units of an already-known product be added in one action
    (e.g. 5 splicing machines arriving together) instead of repeating the
    single-unit form N times. Asset Core only: Consumables Core's "batch"
    quantity already lives in one row's quantity_on_hand (no need for N
    rows), and Cable Core's units are individually distinct cuts/reels with
    their own lengths, so both still go through POST /inventory/units one at
    a time. New product creation is unaffected — this endpoint is never
    reached from that path. Gated on add — see POST /inventory/units'
    docstring."""
    if not user_has_permission(current_user, "inventory_items", "add"):
        raise HTTPException(status_code=403, detail="You don't have permission to add stock")
    category = categories_db.get_category_by_id(batch.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if category["tracking_type"] != "asset_serialized":
        raise HTTPException(status_code=400, detail="Batch quantity add is only available for Asset Core products")
    if batch.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")

    asset_status = batch.asset_status or "Active"
    if asset_status not in ASSET_STATUSES:
        raise HTTPException(status_code=400, detail=f"asset_status must be one of {sorted(ASSET_STATUSES)}")

    batch.location_id = _resolve_unit_location_id(batch.location_id, batch.location_name)
    item_fields = {
        "category_id": batch.category_id, "sku": batch.sku, "name": batch.name,
        "make_model": batch.make_model, "spec_capacity": batch.spec_capacity,
        "supplier": batch.supplier, "unit_of_measure": batch.unit_of_measure,
        "location_id": batch.location_id, "unit_cost": batch.unit_cost,
        "asset_status": asset_status, "assigned_to_user_id": batch.assigned_to_user_id,
        "install_date": batch.install_date,
    }
    start_seq = items_db.next_serial_seq(batch.category_id, batch.sku)
    result = transactions_db.add_unit_batch(
        item_fields, batch.quantity, batch.sku, batch.location_id,
        current_user["id"], batch.notes, start_seq, batch.event_group_id,
    )
    return {
        "items": [items_db.get_item_by_id(i) for i in result["item_ids"]],
        "event_group_id": result["event_group_id"],
    }

@router.patch("/inventory/items/{item_id}")
def edit_inventory_item(item_id: int, item: InventoryItemUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "inventory_items", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit inventory items")
    existing = items_db.get_item_by_id(item_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Item not found")

    item.location_id = _resolve_unit_location_id(item.location_id, item.location_name)

    # tracking_type is resolved from the item's own (unchangeable) category —
    # never from anything the client sends.
    fields = _fields_for_type(item.dict(), existing["tracking_type"])
    _validate_type_fields(existing["tracking_type"], fields)

    items_db.update_item(item_id, fields)
    return items_db.get_item_by_id(item_id)

class ProductUpdate(BaseModel):
    # Identifies the current SKU/spec group to update — the "primary key"
    # of a product that has no table of its own.
    category_id: int
    sku_or_spec: str
    # New values. new_category_id is only allowed to move within the same
    # Core (tracking_type) — reassigning across Cores would corrupt every
    # unit's type-specific columns, which only make sense for one type.
    new_category_id: int
    sku: str
    name: str
    spec_capacity: Optional[str] = None
    spec: Optional[str] = None

@router.patch("/inventory/products")
def update_product(payload: ProductUpdate, current_user: dict = Depends(get_current_user)):
    """Edits a SKU/spec group's product-level fields across every active
    unit under it at once — the Items table's aggregate-row Edit action.
    Deliberately separate from PATCH /inventory/items/{id}, which is
    unit-level only (see that form's own fields for the split)."""
    if not user_has_permission(current_user, "inventory_items", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit inventory items")
    old_category = categories_db.get_category_by_id(payload.category_id)
    if old_category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    new_category = categories_db.get_category_by_id(payload.new_category_id)
    if new_category is None:
        raise HTTPException(status_code=404, detail="New category not found")
    if new_category["tracking_type"] != old_category["tracking_type"]:
        raise HTTPException(status_code=400, detail="Can't move a product to a category of a different Core")

    tracking_type = old_category["tracking_type"]
    fields = {
        "sku": payload.sku, "name": payload.name, "category_id": payload.new_category_id,
    }
    if tracking_type == "asset_serialized":
        fields["spec_capacity"] = payload.spec_capacity
    elif tracking_type == "inventory_length":
        if not payload.spec:
            raise HTTPException(status_code=400, detail="Spec is required for a Cable Core product")
        fields["spec"] = payload.spec
        # Same sku-mirrors-spec invariant as _validate_type_fields — Cable
        # Core has no separate SKU concept, so this overrides whatever
        # payload.sku held (the frontend no longer collects it for this
        # Core at all).
        fields["sku"] = payload.spec

    updated_count = items_db.update_product(payload.category_id, payload.sku_or_spec, fields)
    if updated_count == 0:
        raise HTTPException(status_code=404, detail="No units found under this product")
    return {"updated_count": updated_count}

@router.delete("/inventory/products")
def delete_product(category_id: int, sku_or_spec: str, current_user: dict = Depends(get_current_user)):
    """Deletes a whole product/SKU — blocked outright (never a cascade) if
    any unit still exists under it. There's no separate product row to
    remove once that count hits zero; this just confirms there's nothing
    left standing in the way."""
    if not user_has_permission(current_user, "inventory_items", "delete"):
        raise HTTPException(status_code=403, detail="You don't have permission to delete inventory items")
    count = items_db.count_active_units(category_id, sku_or_spec)
    if count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Can't delete — {count} unit{'s' if count != 1 else ''} still exist under this product, remove those first"
        )
    return {"deleted": True}

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
    # Free-typed, same pattern as Add/Edit Unit's Location field — resolved
    # server-side via _resolve_unit_location_id (is_store=True) below, taking
    # precedence over a plain id when both are sent. Only meaningful for
    # Transfer; Return always resolves to the single default store instead
    # (see _plan_return) and the other two actions have no location fields.
    from_location_name: Optional[str] = None
    to_location_id: Optional[int] = None
    to_location_name: Optional[str] = None
    site_location_id: Optional[int] = None
    activity: Optional[str] = None
    issued_to_user_id: Optional[int] = None
    # Required (and validated) only for a Return of an asset_serialized item —
    # see _plan_return. Must be explicitly chosen by the user; never inferred
    # from the asset's prior state.
    asset_status: Optional[str] = None
    notes: Optional[str] = None

def _plan_return(item: dict, asset_status: Optional[str], qty_or_length: Optional[float] = None):
    """Shared by the generic transaction-log Return option and the Return
    Materials cart. Returns (item_updates, to_location_id,
    split_new_item_fields, resolved_qty_or_length). Unlike every other
    action, Return never takes a destination from the caller — neither entry
    point collects one any more, so it always resolves to the single
    configured default store. An Asset's status must be explicitly chosen by
    the user (never inferred from what it was before), while
    assigned_to_user_id always clears regardless of the chosen status.

    A Quantity return splits exactly like a partial Transfer when
    qty_or_length is less than the item's full quantity_on_hand: the
    original row (wherever it was — e.g. still out at a site) decrements by
    the returned amount, and a new row is created at the default store
    holding the returned quantity — same split_new_item_fields shape
    _plan_transaction's Transfer branch already produces, consumed the same
    way by record_transaction/return_cart. qty_or_length of None (the
    generic Log Transaction form doesn't collect one) means "return
    everything," matching the previous behavior exactly — the whole row's
    location just moves, nothing splits."""
    # Cable/Length items never go through Return — every outcome (fully
    # used, partially used, never touched) is already expressible via
    # Reconciliation's Length Used/Length Returned fields (a Length
    # Returned equal to the full issued amount just closes the cut out
    # clean, no new remainder reel). Guarded here rather than only in the
    # frontend since both Return entry points (the dedicated Return
    # Materials cart and the generic Log Transaction form's Return option)
    # call into this same function.
    if item["tracking_type"] == "inventory_length":
        raise HTTPException(
            status_code=400,
            detail="Cable/Length items can't be returned — reconcile the cut instead (Transaction Log)",
        )

    default_store = locations_db.get_default_store_location()
    if default_store is None:
        raise HTTPException(status_code=500, detail="No default store location is configured for Returns")
    to_location_id = default_store["id"]
    split_new_item_fields = None
    resolved_qty = qty_or_length

    if item["tracking_type"] == "inventory_quantity":
        current_qty = float(item["quantity_on_hand"] or 0)
        return_qty = qty_or_length if qty_or_length is not None else current_qty
        if return_qty <= 0 or return_qty > current_qty:
            raise HTTPException(status_code=400, detail="qty_or_length must be greater than 0 and no more than the item's current quantity_on_hand")
        resolved_qty = return_qty
        if return_qty == current_qty:
            item_updates = {"location_id": to_location_id}
        else:
            item_updates = {"quantity_on_hand": current_qty - return_qty}
            split_new_item_fields = {
                "category_id": item["category_id"], "sku": item["sku"], "name": item["name"],
                "location_id": to_location_id, "unit_cost": item["unit_cost"], "supplier": item["supplier"],
                "unit_of_measure": item["unit_of_measure"], "batch_lot": item["batch_lot"],
                "expiry_date": item["expiry_date"], "quantity_on_hand": return_qty,
            }
    else:
        item_updates = {"location_id": to_location_id}
        if item["tracking_type"] == "asset_serialized":
            item_updates["assigned_to_user_id"] = None
            if asset_status is None:
                raise HTTPException(status_code=400, detail=f"A status is required to return '{item['name']}'")
            if asset_status not in ASSET_STATUSES:
                raise HTTPException(status_code=400, detail=f"asset_status must be one of {sorted(ASSET_STATUSES)}")
            item_updates["asset_status"] = asset_status

    return item_updates, to_location_id, split_new_item_fields, resolved_qty

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

    if txn.action == "Transfer":
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
        from_location_id = from_location_id or item["location_id"]
        item_updates, to_location_id, split_new_item_fields, qty_or_length = _plan_return(item, txn.asset_status, qty_or_length)

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

    # Transfer's From/To are free-typed autocomplete fields, same resolution
    # as Add/Edit Unit's Location field (is_store=True) — a name takes
    # precedence over a plain id when both are sent.
    txn.to_location_id = _resolve_unit_location_id(txn.to_location_id, txn.to_location_name)
    txn.from_location_id = _resolve_unit_location_id(txn.from_location_id, txn.from_location_name)

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
    # This one endpoint backs two different UI surfaces with two different
    # permissions: called with item_id, it's the per-unit history tab inside
    # Asset/Cable Core's detail modal — the same "View Stock History"
    # capability sku-history above gates, not the full Transaction Log page.
    # Called without it (from the Inventory Log page itself), it's gated by
    # that page's own inventory_transactions:view as before.
    if item_id is not None:
        if not user_has_permission(current_user, "inventory_items", "view_history"):
            raise HTTPException(status_code=403, detail="You don't have permission to view stock history")
    elif not user_has_permission(current_user, "inventory_transactions", "view"):
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
    # Free-typed — resolved to an id server-side via
    # locations_db.get_or_create_location_by_name, so a brand-new site needs
    # zero pre-configuration.
    site_location_name: str
    activity: Optional[str] = None
    issued_to_user_id: Optional[int] = None
    notes: str

def _plan_issue_line(item: dict, line: IssueCartLine, site_location_id: int, issued_to_user_id):
    """One cart line's item-side effect, decided by the tracking_type read
    off the item's own category. Returns (item_updates, qty_or_length,
    status) — status is only set for Length lines, which stay open until
    Milestone 6's reconciliation closes them out."""
    tracking_type = item["tracking_type"]

    if tracking_type == "asset_serialized":
        # The asset is now in service at the site, in someone's hands —
        # "Deployed" is the location-state half of the lifecycle (In Stock →
        # Deployed → back to Active on Return), independent of the asset's
        # condition (Faulty/In Repair/Decommissioned), which a Return leaves
        # untouched (see the Return branch in _plan_transaction).
        return (
            {
                "location_id": site_location_id,
                "assigned_to_user_id": issued_to_user_id,
                "asset_status": "Deployed",
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
    # Its own permission, independent of the generic Log Transaction form's
    # inventory_transactions:add — a role can issue materials without being
    # able to log arbitrary Transfer/Adjustment/Write-off rows, or vice versa.
    if not user_has_permission(current_user, "inventory_transactions", "issue"):
        raise HTTPException(status_code=403, detail="You don't have permission to issue materials")
    if not cart.lines:
        raise HTTPException(status_code=400, detail="Nothing to issue — the cart is empty")
    if not cart.site_location_name or not cart.site_location_name.strip():
        raise HTTPException(status_code=400, detail="A site/destination is required")
    if not cart.notes or not cart.notes.strip():
        raise HTTPException(status_code=400, detail="Notes are required")
    if cart.activity is not None and cart.activity not in ACTIVITIES:
        raise HTTPException(status_code=400, detail=f"activity must be one of {sorted(ACTIVITIES)}")

    site_location_id = locations_db.get_or_create_location_by_name(cart.site_location_name)

    # Every line is resolved and validated before anything is written, so a
    # bad line rejects the whole cart rather than half-issuing it.
    prepared_lines = []
    for line in cart.lines:
        item = items_db.get_item_by_id(line.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Item {line.item_id} not found")

        item_updates, qty_or_length, status = _plan_issue_line(
            item, line, site_location_id, cart.issued_to_user_id
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
        prepared_lines, site_location_id, cart.activity,
        cart.issued_to_user_id, current_user["id"], cart.notes,
    )

class ReturnCartLine(BaseModel):
    item_id: int
    # Required for an asset_serialized line, ignored otherwise — see
    # _plan_return. Never inferred from the item's prior state.
    asset_status: Optional[str] = None
    # Only meaningful for inventory_quantity — how much of this line's
    # quantity_on_hand is being returned. None returns the whole row (see
    # _plan_return); less than the full amount splits it. Ignored for
    # asset_serialized; Cable/Length never reaches here at all (see
    # _plan_return's guard).
    qty_or_length: Optional[float] = None

class ReturnCart(BaseModel):
    lines: list[ReturnCartLine]
    notes: str

@router.post("/inventory/return")
def return_materials(cart: ReturnCart, current_user: dict = Depends(get_current_user)):
    # Its own permission, independent of Issue Materials and of the generic
    # Log Transaction form's inventory_transactions:add — see issue_materials
    # above for the same reasoning.
    if not user_has_permission(current_user, "inventory_transactions", "return"):
        raise HTTPException(status_code=403, detail="You don't have permission to return materials")
    if not cart.lines:
        raise HTTPException(status_code=400, detail="Nothing to return — the cart is empty")
    if not cart.notes or not cart.notes.strip():
        raise HTTPException(status_code=400, detail="Notes are required")

    # Every line resolves and validates before anything is written — a bad
    # line (e.g. a missing asset status) rejects the whole cart rather than
    # half-returning it. Any active asset is returnable this way regardless
    # of its current status/location — an unused, never-deployed unit goes
    # through the same flow as a deployed one.
    prepared_lines = []
    for line in cart.lines:
        item = items_db.get_item_by_id(line.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Item {line.item_id} not found")

        item_updates, to_location_id, split_new_item_fields, resolved_qty = _plan_return(item, line.asset_status, line.qty_or_length)

        prepared_lines.append({
            "item_id": line.item_id,
            "category_id": item["category_id"],
            "sku_or_spec": item["spec"] if item["tracking_type"] == "inventory_length" else item["sku"],
            "from_location_id": item["location_id"],
            "to_location_id": to_location_id,
            "item_updates": item_updates,
            "split_new_item_fields": split_new_item_fields,
            "qty_or_length": resolved_qty,
        })

    return transactions_db.return_cart(prepared_lines, current_user["id"], cart.notes)

class ReconcileCut(BaseModel):
    item_id: int
    length_used: float
    length_returned: float
    # Free-text, editable — the frontend auto-suggests "<original>-R" but an
    # odd existing physical label shouldn't be forced into that shape.
    new_cut_reel_id: Optional[str] = None
    notes: Optional[str] = None

@router.post("/inventory/reconcile")
def reconcile_cut(reconcile: ReconcileCut, current_user: dict = Depends(get_current_user)):
    # Closes out a job, so it's gated Manager-level per the phase plan — its
    # own grantable action, distinct from just adding a line to the log.
    if not user_has_permission(current_user, "inventory_transactions", "reconcile"):
        raise HTTPException(status_code=403, detail="You don't have permission to reconcile a cable cut")

    item = items_db.get_item_by_id(reconcile.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if item["tracking_type"] != "inventory_length":
        raise HTTPException(status_code=400, detail="Only a Length-based item can be reconciled")
    if item["length_status"] != "Out — Pending Reconciliation":
        raise HTTPException(status_code=400, detail="This cut isn't out and pending reconciliation")
    if reconcile.length_used < 0 or reconcile.length_returned < 0:
        raise HTTPException(status_code=400, detail="length_used and length_returned can't be negative")

    # The full cut went out at Stage 1 with nothing deducted yet (see
    # issue_cart) — length_remaining still holds that full amount, and
    # Stage 2's two numbers must account for all of it.
    length_out = float(item["length_remaining"] or 0)
    if abs((reconcile.length_used + reconcile.length_returned) - length_out) > 0.01:
        raise HTTPException(status_code=400, detail=f"length_used + length_returned must add up to the {length_out}m that went out")

    # The original row is fully spoken for either way: consumed, or its
    # remainder is now accounted for by a new row (usable case) or as scrap
    # logged against this same row (sub-threshold case) — never both.
    original_updates = {"length_remaining": 0, "length_status": "Depleted"}

    new_cut_fields = None
    if reconcile.length_returned >= USABLE_LENGTH_THRESHOLD_M:
        new_cut_fields = {
            "category_id": item["category_id"], "sku": item["sku"], "name": item["name"],
            "location_id": item["location_id"], "unit_cost": item["unit_cost"], "supplier": item["supplier"],
            "unit_of_measure": item["unit_of_measure"],
            "cut_reel_id": reconcile.new_cut_reel_id or f"{item['cut_reel_id']}-R",
            "spec": item["spec"], "length_received": reconcile.length_returned,
            "length_remaining": reconcile.length_returned, "length_status": "In Stock",
        }

    return transactions_db.reconcile_cut(
        item_id=reconcile.item_id, category_id=item["category_id"], sku_or_spec=item["spec"],
        length_used=reconcile.length_used, length_returned=reconcile.length_returned,
        logged_by_user_id=current_user["id"], original_updates=original_updates,
        new_cut_fields=new_cut_fields, from_location_id=item["location_id"], notes=reconcile.notes,
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

@router.get("/inventory/sku-summary")
def read_sku_summary(category_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    # Reports is its own grantable capability (reports:view), independent of
    # inventory_items:view — a role can browse the item catalog without
    # seeing rollup reports, or the reverse. Backs every panel on the
    # Reports page (this, cable-summary, offcuts below), matching the
    # dedicated "Reports" nav toggle (see NAV_GROUP_MASTER_PERMISSION-style
    # gating in common.js's ROUTE_PERMISSION_MAP for "inventory-reports").
    if not user_has_permission(current_user, "reports", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view reports")
    return reports_db.get_sku_summary(category_id)

@router.get("/inventory/sku-history")
def read_sku_transaction_history(category_id: int, sku: str, current_user: dict = Depends(get_current_user)):
    # This backs Consumables Core's flat stock-history table specifically
    # (the "View" action on Stock/Items rows) — its own permission,
    # independent of inventory_items:view, so a role can browse the item
    # catalog without seeing its movement history, or the reverse.
    if not user_has_permission(current_user, "inventory_items", "view_history"):
        raise HTTPException(status_code=403, detail="You don't have permission to view stock history")
    return reports_db.get_sku_transaction_history(category_id, sku)

class ReorderLevelSet(BaseModel):
    category_id: int
    sku_or_spec: str
    reorder_level: float

@router.patch("/inventory/reorder-level")
def set_inventory_reorder_level(payload: ReorderLevelSet, current_user: dict = Depends(get_current_user)):
    # Its own grantable capability, independent of inventory_items:edit — a
    # role can manage reorder thresholds without full item-edit rights, or
    # the reverse. Note this endpoint is also called (fire-and-forget, no
    # error surfaced on failure — a pre-existing gap, not introduced by this
    # permission split) from the Add Item wizard's New Product step when a
    # reorder level is set at creation time, so a role with "Add item" but
    # not this permission will silently have that one optional field not
    # take, while the rest of product creation still succeeds.
    if not user_has_permission(current_user, "inventory_items", "edit_reorder_level"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit reorder levels")
    if payload.reorder_level < 0:
        raise HTTPException(status_code=400, detail="reorder_level can't be negative")
    reports_db.set_reorder_level(payload.category_id, payload.sku_or_spec, payload.reorder_level)
    return payload.dict()

@router.get("/inventory/cable-summary")
def read_cable_type_summary(category_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "reports", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view reports")
    return reports_db.get_cable_type_summary(category_id)

@router.get("/inventory/cable-summary/drill-down")
def read_cable_drill_down(spec: str, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "reports", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view reports")
    return reports_db.get_cable_drill_down(spec)

@router.get("/inventory/offcuts")
def read_offcut_summary(category_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "reports", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view reports")
    return reports_db.get_offcut_summary_by_spec(category_id)

@router.get("/inventory/offcuts/drill-down")
def read_offcut_drill_down(spec: str, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "reports", "view"):
        raise HTTPException(status_code=403, detail="You don't have permission to view reports")
    return reports_db.get_offcut_drill_down(spec)
