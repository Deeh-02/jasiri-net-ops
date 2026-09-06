from db.connection import get_connection, utc_iso
from datetime import datetime, timezone
import uuid

# Same generic, type-agnostic column surface pattern as inventory_items.py —
# routers/inventory.py decides which columns apply to a given action/type
# and builds item_updates itself; this file just applies whatever it's given.
_TXN_COLUMNS = [
    "item_id", "category_id", "sku_or_spec", "action", "qty_or_length",
    "from_location_id", "to_location_id", "site_location_id", "activity",
    "issued_to_user_id", "logged_by_user_id", "status", "length_used",
    "length_returned", "event_group_id", "notes",
]

def _insert_txn_row(cur, fields):
    columns = [c for c in _TXN_COLUMNS if c in fields]
    placeholders = ", ".join(["%s"] * len(columns))
    cur.execute(
        f"INSERT INTO inventory_transactions ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id;",
        [fields[c] for c in columns]
    )
    return cur.fetchone()[0]

def _update_item_row(cur, item_id, updates):
    if not updates:
        return
    set_clause = ", ".join(f"{c} = %s" for c in updates)
    cur.execute(f"UPDATE inventory_items SET {set_clause} WHERE id = %s;", list(updates.values()) + [item_id])

def _insert_item_row(cur, fields):
    columns = list(fields.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    cur.execute(
        f"INSERT INTO inventory_items ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id;",
        [fields[c] for c in columns]
    )
    return cur.fetchone()[0]

def record_transaction(action, item_id, category_id, sku_or_spec, qty_or_length=None,
                        from_location_id=None, to_location_id=None, site_location_id=None,
                        activity=None, issued_to_user_id=None, logged_by_user_id=None, notes=None,
                        item_updates=None, split_new_item_fields=None):
    """Writes the item-side state change (if any) and the log row(s) in one
    connection/one commit — the write-through pattern from the phase plan,
    same shape as db/permissions.py's set_role_permissions(). All business
    judgment (which columns to touch, whether this is a split Transfer) is
    decided by routers/inventory.py before calling in here; this function
    just applies the plan it's given.

    split_new_item_fields set means: this is a partial-quantity Transfer.
    item_updates applies to the ORIGIN row (its quantity_on_hand decrement),
    a new destination item row is inserted from split_new_item_fields, and
    two linked log rows are written (one per item_id) sharing one
    event_group_id — the origin decrement and the new row each need their
    own row to explain them, per the plan's split-Transfer design.
    """
    conn = get_connection()
    cur = conn.cursor()

    base_txn_fields = {
        "category_id": category_id, "sku_or_spec": sku_or_spec, "action": action,
        "qty_or_length": qty_or_length, "from_location_id": from_location_id,
        "to_location_id": to_location_id, "site_location_id": site_location_id,
        "activity": activity, "issued_to_user_id": issued_to_user_id,
        "logged_by_user_id": logged_by_user_id, "notes": notes,
    }

    if split_new_item_fields:
        event_group_id = str(uuid.uuid4())
        _update_item_row(cur, item_id, item_updates)
        new_item_id = _insert_item_row(cur, split_new_item_fields)

        txn_a = _insert_txn_row(cur, {**base_txn_fields, "item_id": item_id, "event_group_id": event_group_id})
        txn_b = _insert_txn_row(cur, {**base_txn_fields, "item_id": new_item_id, "event_group_id": event_group_id})

        conn.commit()
        cur.close()
        conn.close()
        return {
            "transaction_ids": [txn_a, txn_b], "item_id": item_id,
            "new_item_id": new_item_id, "event_group_id": event_group_id,
        }

    _update_item_row(cur, item_id, item_updates)
    txn_id = _insert_txn_row(cur, {**base_txn_fields, "item_id": item_id})
    conn.commit()
    cur.close()
    conn.close()
    return {"transaction_id": txn_id, "item_id": item_id}

def issue_cart(prepared_lines, site_location_id, activity, issued_to_user_id, logged_by_user_id, notes):
    """One checkout of a mixed cart — N log rows plus N item updates, one
    connection, one commit, so a cart either lands whole or not at all.
    Every row shares one event_group_id, which is what makes "1 enclosure +
    2 packs of ties + 150m of cable" read back as a single event instead of
    three unrelated ones.

    Each prepared line arrives fully resolved by routers/inventory.py —
    including the item_updates appropriate to its tracking type, which is
    read from the item's category server-side and never from the client.
    """
    conn = get_connection()
    cur = conn.cursor()
    event_group_id = str(uuid.uuid4())
    transaction_ids = []

    for line in prepared_lines:
        _update_item_row(cur, line["item_id"], line["item_updates"])
        transaction_ids.append(_insert_txn_row(cur, {
            "item_id": line["item_id"],
            "category_id": line["category_id"],
            "sku_or_spec": line["sku_or_spec"],
            "action": "Out",
            "qty_or_length": line["qty_or_length"],
            "from_location_id": line["from_location_id"],
            "site_location_id": site_location_id,
            "activity": activity,
            "issued_to_user_id": issued_to_user_id,
            "logged_by_user_id": logged_by_user_id,
            "status": line["status"],
            "event_group_id": event_group_id,
            "notes": notes,
        }))

    conn.commit()
    cur.close()
    conn.close()
    return {"event_group_id": event_group_id, "transaction_ids": transaction_ids}

def reconcile_cut(item_id, category_id, sku_or_spec, length_used, length_returned,
                   logged_by_user_id, original_updates, new_cut_fields=None,
                   from_location_id=None, to_location_id=None, notes=None):
    """Stage 2 of the cable flow: the job closed, so actual usage is finally
    known. Closes out the Stage-1 `Out` row this answers, adjusts the
    original cut, and — when the returned remainder is long enough to be
    worth re-stocking — creates the new `-R` cut row before the log row that
    references it (the item has to exist before a row can point at it).

    A remainder that becomes its own cut is a second item-side change, so it
    gets its own log row rather than being implied by the first: two rows
    sharing one event_group_id, the same shape a split Transfer uses. Without
    it the new cut would appear in stock with nothing in the log accounting
    for where it came from.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE inventory_transactions SET status = 'closed' WHERE item_id = %s AND status = 'open_pending';",
        (item_id,)
    )
    _update_item_row(cur, item_id, original_updates)

    new_item_id = _insert_item_row(cur, new_cut_fields) if new_cut_fields else None
    event_group_id = str(uuid.uuid4()) if new_cut_fields else None

    base_txn_fields = {
        "category_id": category_id, "sku_or_spec": sku_or_spec, "action": "Reconciled",
        "from_location_id": from_location_id, "to_location_id": to_location_id,
        "logged_by_user_id": logged_by_user_id, "status": "closed",
        "event_group_id": event_group_id, "notes": notes,
    }

    transaction_ids = [_insert_txn_row(cur, {
        **base_txn_fields,
        "item_id": item_id,
        "qty_or_length": length_used,
        "length_used": length_used,
        "length_returned": length_returned,
    })]

    if new_item_id is not None:
        transaction_ids.append(_insert_txn_row(cur, {
            **base_txn_fields,
            "item_id": new_item_id,
            "qty_or_length": length_returned,
        }))

    conn.commit()
    cur.close()
    conn.close()
    return {
        "transaction_ids": transaction_ids, "item_id": item_id,
        "new_item_id": new_item_id, "event_group_id": event_group_id,
    }

def get_open_pending_cuts(aging_threshold_days):
    """Every cut that went out and hasn't been reconciled, oldest first, with
    its age so a job that's dragging doesn't leave stock unaccounted for
    indefinitely. Age is derived on read rather than stored — same approach
    as db/sites.py's needs_check."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT inventory_transactions.id, inventory_transactions.item_id,
               inventory_transactions.created_at, inventory_transactions.qty_or_length,
               inventory_items.cut_reel_id, inventory_items.spec,
               inventory_items.length_remaining, site_loc.name, inventory_categories.name
        FROM inventory_transactions
        JOIN inventory_items ON inventory_items.id = inventory_transactions.item_id
        JOIN inventory_categories ON inventory_categories.id = inventory_transactions.category_id
        LEFT JOIN inventory_locations AS site_loc ON site_loc.id = inventory_transactions.site_location_id
        WHERE inventory_transactions.status = 'open_pending'
        ORDER BY inventory_transactions.created_at;
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        issued_at = r[2].replace(tzinfo=timezone.utc) if r[2].tzinfo is None else r[2]
        days_out = (now - issued_at).days
        result.append({
            "transaction_id": r[0], "item_id": r[1], "issued_at": utc_iso(r[2]),
            "length_out": r[3], "cut_reel_id": r[4], "spec": r[5],
            "length_remaining": r[6], "site_location_name": r[7], "category_name": r[8],
            "days_out": days_out,
            "is_aging": days_out >= aging_threshold_days,
        })
    return result

_LOG_SELECT_COLUMNS = """
    inventory_transactions.id, inventory_transactions.item_id, inventory_transactions.category_id,
    inventory_categories.name, inventory_transactions.sku_or_spec, inventory_transactions.action,
    inventory_transactions.qty_or_length, inventory_transactions.from_location_id, from_loc.name,
    inventory_transactions.to_location_id, to_loc.name, inventory_transactions.site_location_id, site_loc.name,
    inventory_transactions.activity, inventory_transactions.issued_to_user_id, issued_to.name,
    inventory_transactions.logged_by_user_id, logged_by.name, inventory_transactions.status,
    inventory_transactions.length_used, inventory_transactions.length_returned,
    inventory_transactions.event_group_id, inventory_transactions.notes, inventory_transactions.created_at
"""

_LOG_FROM_JOINS = """
    FROM inventory_transactions
    JOIN inventory_categories ON inventory_categories.id = inventory_transactions.category_id
    LEFT JOIN inventory_locations AS from_loc ON from_loc.id = inventory_transactions.from_location_id
    LEFT JOIN inventory_locations AS to_loc ON to_loc.id = inventory_transactions.to_location_id
    LEFT JOIN inventory_locations AS site_loc ON site_loc.id = inventory_transactions.site_location_id
    LEFT JOIN users AS issued_to ON issued_to.id = inventory_transactions.issued_to_user_id
    JOIN users AS logged_by ON logged_by.id = inventory_transactions.logged_by_user_id
"""

def _log_row_to_dict(r):
    return {
        "id": r[0], "item_id": r[1], "category_id": r[2], "category_name": r[3],
        "sku_or_spec": r[4], "action": r[5], "qty_or_length": r[6],
        "from_location_id": r[7], "from_location_name": r[8],
        "to_location_id": r[9], "to_location_name": r[10],
        "site_location_id": r[11], "site_location_name": r[12],
        "activity": r[13], "issued_to_user_id": r[14], "issued_to_name": r[15],
        "logged_by_user_id": r[16], "logged_by_name": r[17], "status": r[18],
        "length_used": r[19], "length_returned": r[20], "event_group_id": r[21],
        "notes": r[22], "created_at": utc_iso(r[23]),
    }

def get_transaction_log(item_id=None, category_id=None, action=None, limit=200):
    conn = get_connection()
    cur = conn.cursor()
    where_clauses = []
    params = []
    if item_id is not None:
        where_clauses.append("inventory_transactions.item_id = %s")
        params.append(item_id)
    if category_id is not None:
        where_clauses.append("inventory_transactions.category_id = %s")
        params.append(category_id)
    if action is not None:
        where_clauses.append("inventory_transactions.action = %s")
        params.append(action)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)

    cur.execute(
        f"""
        SELECT {_LOG_SELECT_COLUMNS}
        {_LOG_FROM_JOINS}
        {where_sql}
        ORDER BY inventory_transactions.created_at DESC, inventory_transactions.id DESC
        LIMIT %s;
        """,
        params
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [_log_row_to_dict(r) for r in rows]

def get_transaction_by_id(transaction_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT {_LOG_SELECT_COLUMNS}
        {_LOG_FROM_JOINS}
        WHERE inventory_transactions.id = %s;
        """,
        (transaction_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return _log_row_to_dict(row) if row else None

# Admin-only historical correction — never touches the item row, only the
# log entry's own fields. action/item_id/category_id/logged_by_user_id/
# event_group_id/created_at stay immutable so a corrected row keeps its
# original identity and grouping.
_EDITABLE_LOG_COLUMNS = [
    "qty_or_length", "from_location_id", "to_location_id", "site_location_id",
    "activity", "issued_to_user_id", "status", "length_used", "length_returned", "notes",
]

def update_transaction(transaction_id, fields):
    conn = get_connection()
    cur = conn.cursor()
    columns = [c for c in _EDITABLE_LOG_COLUMNS if c in fields]
    set_clause = ", ".join(f"{c} = %s" for c in columns)
    cur.execute(
        f"UPDATE inventory_transactions SET {set_clause} WHERE id = %s;",
        [fields[c] for c in columns] + [transaction_id]
    )
    conn.commit()
    cur.close()
    conn.close()
