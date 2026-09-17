import re

from db.connection import db_cursor

# Columns are a plain, generic insert/update surface — routers/inventory.py
# is the layer that decides which of these apply to a given item's
# tracking_type and zeroes out the rest before calling in here. This file
# doesn't know or care what a tracking_type even is.
_ITEM_COLUMNS = [
    "category_id", "sku", "name", "location_id", "unit_cost", "supplier",
    "unit_of_measure", "notes",
    "serial_number", "asset_status", "assigned_to_user_id", "make_model",
    "spec_capacity", "install_date",
    "batch_lot", "expiry_date", "quantity_on_hand",
    "cut_reel_id", "spec", "length_received", "length_remaining", "length_status",
]

def add_item(fields):
    with db_cursor() as (conn, cur):
        columns = [c for c in _ITEM_COLUMNS if c in fields]
        placeholders = ", ".join(["%s"] * len(columns))
        cur.execute(
            f"""
            INSERT INTO inventory_items ({", ".join(columns)})
            VALUES ({placeholders})
            RETURNING id;
            """,
            [fields[c] for c in columns]
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id

def update_item(item_id, fields):
    with db_cursor() as (conn, cur):
        columns = [c for c in _ITEM_COLUMNS if c in fields]
        set_clause = ", ".join(f"{c} = %s" for c in columns)
        cur.execute(
            f"UPDATE inventory_items SET {set_clause} WHERE id = %s;",
            [fields[c] for c in columns] + [item_id]
        )
        conn.commit()

def deactivate_item(item_id):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE inventory_items SET is_active = false WHERE id = %s;", (item_id,))
        conn.commit()

# Product-level fields (sku, name, category_id, make_model, spec_capacity,
# spec, supplier, unit_of_measure) are denormalized across every unit row of
# a SKU/spec group — there's no separate products table (see
# architecture.md). Editing "the product" from the Items table's aggregate
# row means applying the same values to every active unit in that group at
# once, matched the same way get_all_items' sku param already does: sku for
# Asset/Quantity, spec for Length, via the same (sku = %s OR spec = %s) test.
def update_product(category_id, sku_or_spec, fields):
    with db_cursor() as (conn, cur):
        columns = [c for c in _ITEM_COLUMNS if c in fields]
        set_clause = ", ".join(f"{c} = %s" for c in columns)
        cur.execute(
            f"""
            UPDATE inventory_items SET {set_clause}
            WHERE category_id = %s AND (sku = %s OR spec = %s) AND is_active = true;
            """,
            [fields[c] for c in columns] + [category_id, sku_or_spec, sku_or_spec]
        )
        updated = cur.rowcount
        conn.commit()
    return updated

def next_serial_seq(category_id, sku):
    """Smallest N such that '{sku}-{N:02d}' doesn't collide with any existing
    serial_number under this SKU — checked against every row regardless of
    is_active (a deactivated unit's identifier still shouldn't be reissued).
    Powers the Add Unit batch flow's auto-assigned internal placeholder
    (e.g. SPL-001-01, SPL-001-02, ...) for adding several units of an
    already-known Asset Core product in one sitting. Only ever matches
    serials shaped exactly like the placeholder pattern — a manually-typed
    manufacturer serial that happens to look different doesn't collide."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT serial_number FROM inventory_items WHERE category_id = %s AND sku = %s AND serial_number IS NOT NULL;",
            (category_id, sku)
        )
        rows = cur.fetchall()

    pattern = re.compile(rf"^{re.escape(sku)}-(\d+)$")
    max_seq = 0
    for (serial,) in rows:
        m = pattern.match(serial)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1

def count_active_units(category_id, sku_or_spec):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT COUNT(*) FROM inventory_items
            WHERE category_id = %s AND (sku = %s OR spec = %s) AND is_active = true;
            """,
            (category_id, sku_or_spec, sku_or_spec)
        )
        count = cur.fetchone()[0]
    return count

_SELECT_COLUMNS = """
    inventory_items.id, inventory_items.category_id, inventory_categories.name,
    inventory_categories.tracking_type, inventory_items.sku, inventory_items.name,
    inventory_items.location_id, inventory_locations.name, inventory_items.unit_cost,
    inventory_items.supplier, inventory_items.unit_of_measure, inventory_items.notes,
    inventory_items.serial_number, inventory_items.asset_status,
    inventory_items.assigned_to_user_id, inventory_items.make_model,
    inventory_items.spec_capacity, inventory_items.install_date,
    inventory_items.batch_lot, inventory_items.expiry_date, inventory_items.quantity_on_hand,
    inventory_items.cut_reel_id, inventory_items.spec, inventory_items.length_received,
    inventory_items.length_remaining, inventory_items.length_status
"""

def _row_to_dict(r):
    return {
        "id": r[0], "category_id": r[1], "category_name": r[2], "tracking_type": r[3],
        "sku": r[4], "name": r[5], "location_id": r[6], "location_name": r[7],
        "unit_cost": r[8], "supplier": r[9], "unit_of_measure": r[10], "notes": r[11],
        "serial_number": r[12], "asset_status": r[13], "assigned_to_user_id": r[14],
        "make_model": r[15], "spec_capacity": r[16],
        "install_date": r[17].isoformat() if r[17] else None,
        "batch_lot": r[18], "expiry_date": r[19].isoformat() if r[19] else None,
        "quantity_on_hand": r[20],
        "cut_reel_id": r[21], "spec": r[22], "length_received": r[23],
        "length_remaining": r[24], "length_status": r[25],
    }

def get_all_items(category_id=None, sku=None):
    """sku matches either the sku column (Asset/Quantity) or the spec column
    (Length, whose SKU surrogate is its spec) — the caller (the Items
    table's per-SKU unit drill-down) has one string and doesn't need to know
    which column it maps to for this item's type."""
    with db_cursor() as (conn, cur):
        where_clauses = ["inventory_items.is_active = true"]
        params = []
        if category_id is not None:
            where_clauses.append("inventory_items.category_id = %s")
            params.append(category_id)
        if sku is not None:
            where_clauses.append("(inventory_items.sku = %s OR inventory_items.spec = %s)")
            params.extend([sku, sku])
        cur.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM inventory_items
            JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
            LEFT JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY inventory_items.name, inventory_items.id;
            """,
            # id is a tiebreaker, not an afterthought — every unit under one
            # SKU/spec shares the same name, and Postgres gives no guaranteed
            # order among rows that tie on the ORDER BY clause. Without this,
            # which serial lands "first"/"last" in this list (and therefore in
            # Issue/Return Materials' Quick Issue/Quick Return auto-select,
            # which picks off one end of this exact order) could shift between
            # requests with no visible cause.
            params
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]

def get_item_by_id(item_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM inventory_items
            JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
            LEFT JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
            WHERE inventory_items.id = %s;
            """,
            (item_id,)
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None
