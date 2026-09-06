from db.connection import get_connection, utc_iso

# "Total On Hand" means available, not deployed — the same in-stock/out-in-
# the-field distinction Milestone 6's reconciliation already draws for
# Length. Each tracking type gets to that meaning differently because each
# one represents "out" differently:
#   - Asset: location_id moves to the site once issued (see issue_cart), so
#     on-hand = active rows still sitting at a store location.
#   - Quantity: issuing DRAWS DOWN quantity_on_hand in place rather than
#     moving the row, so the remaining quantity_on_hand already IS the
#     on-hand total, wherever it happens to sit.
#   - Length: a cut still "Out — Pending Reconciliation" keeps its full
#     length_remaining (Stage 1 doesn't deduct it) even though it's
#     physically gone, so only length_status = 'In Stock' rows count.
def get_sku_summary():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT inventory_items.category_id, inventory_categories.name,
               inventory_items.sku, COUNT(*)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'asset_serialized'
          AND inventory_locations.is_store = true
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_items.sku;
        """
    )
    asset_rows = cur.fetchall()

    cur.execute(
        """
        SELECT inventory_items.category_id, inventory_categories.name,
               inventory_items.sku, SUM(inventory_items.quantity_on_hand)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_quantity'
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_items.sku;
        """
    )
    quantity_rows = cur.fetchall()

    cur.execute(
        """
        SELECT inventory_items.category_id, inventory_categories.name,
               inventory_items.spec, SUM(inventory_items.length_remaining)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_length'
          AND inventory_items.length_status = 'In Stock'
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_items.spec;
        """
    )
    length_rows = cur.fetchall()

    cur.execute("SELECT category_id, sku_or_spec, reorder_level FROM inventory_sku_thresholds;")
    thresholds = {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}

    cur.close()
    conn.close()

    result = []
    for tracking_type, rows in (
        ("asset_serialized", asset_rows),
        ("inventory_quantity", quantity_rows),
        ("inventory_length", length_rows),
    ):
        for category_id, category_name, sku_or_spec, total_on_hand in rows:
            total_on_hand = float(total_on_hand or 0)
            reorder_level = thresholds.get((category_id, sku_or_spec))
            result.append({
                "category_id": category_id, "category_name": category_name,
                "tracking_type": tracking_type, "sku_or_spec": sku_or_spec,
                "total_on_hand": total_on_hand, "reorder_level": reorder_level,
                "below_threshold": reorder_level is not None and total_on_hand < reorder_level,
            })
    result.sort(key=lambda r: (r["category_name"], r["sku_or_spec"]))
    return result

def set_reorder_level(category_id, sku_or_spec, reorder_level):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO inventory_sku_thresholds (category_id, sku_or_spec, reorder_level, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (category_id, sku_or_spec)
        DO UPDATE SET reorder_level = EXCLUDED.reorder_level, updated_at = now();
        """,
        (category_id, sku_or_spec, reorder_level)
    )
    conn.commit()
    cur.close()
    conn.close()

# An "offcut" is a cut row created from a reconciliation's usable remainder
# (Milestone 6's <original>-R rows) rather than an original received length.
# There's no stored flag for this — it's derived the same way aging is
# (computed on read) — by finding the Reconciled log row that CREATED this
# item: the original cut's own Reconciled row always carries length_used,
# while the new remainder row's linked Reconciled row never does (see
# reconcile_cut in db/inventory_transactions.py), so length_used IS NULL
# uniquely identifies "this item is an offcut".
_OFFCUT_JOIN = """
    FROM inventory_items
    JOIN inventory_transactions ON inventory_transactions.item_id = inventory_items.id
        AND inventory_transactions.action = 'Reconciled'
        AND inventory_transactions.length_used IS NULL
"""

def get_offcut_summary_by_spec():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT inventory_items.spec, SUM(inventory_items.length_remaining), COUNT(*)
        {_OFFCUT_JOIN}
        WHERE inventory_items.is_active = true
          AND inventory_items.length_status = 'In Stock'
        GROUP BY inventory_items.spec
        ORDER BY inventory_items.spec;
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"spec": r[0], "total_length": float(r[1] or 0), "cut_count": r[2]} for r in rows]

def get_offcut_drill_down(spec):
    # Not filtered to length_status = 'In Stock' like the summary above —
    # this lists every offcut ever created for the spec, status included, so
    # someone investigating the total can see the full picture (including
    # ones already issued back out) rather than just re-deriving the same sum.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT inventory_items.id, inventory_items.cut_reel_id, inventory_items.length_remaining,
               inventory_items.length_status, loc.name, inventory_items.created_at
        {_OFFCUT_JOIN}
        LEFT JOIN inventory_locations AS loc ON loc.id = inventory_items.location_id
        WHERE inventory_items.is_active = true AND inventory_items.spec = %s
        ORDER BY inventory_items.created_at;
        """,
        (spec,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "item_id": r[0], "cut_reel_id": r[1], "length_remaining": float(r[2] or 0),
            "length_status": r[3], "location_name": r[4], "created_at": utc_iso(r[5]),
        }
        for r in rows
    ]
