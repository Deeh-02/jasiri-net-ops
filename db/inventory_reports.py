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
def get_sku_summary(category_id=None):
    conn = get_connection()
    cur = conn.cursor()
    category_filter = "AND inventory_items.category_id = %s" if category_id is not None else ""
    params = (category_id,) if category_id is not None else ()

    # avg_unit_cost is a live weighted average, not a hardcoded/stored figure
    # — Asset rows are each exactly 1 unit, so a plain AVG(unit_cost) across
    # the same in-store-active population already used for total_on_hand IS
    # the weighted average (every row's weight is equal). Quantity batches
    # differ in size, so weighting explicitly by quantity_on_hand is what
    # keeps a handful of expensive units from skewing the figure the same as
    # a large cheap batch. Length is left out — nothing in this round asked
    # for a blended cost-per-meter, and it's a separate feature (job costing
    # across cuts of one spec) from what enclosures/consumables need here —
    # its Total Value comes back null/"—" rather than a guessed number.
    cur.execute(
        f"""
        SELECT inventory_items.category_id, inventory_categories.name,
               inventory_items.sku, MIN(inventory_items.name), COUNT(*), AVG(inventory_items.unit_cost)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'asset_serialized'
          AND inventory_locations.is_store = true
          {category_filter}
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_items.sku;
        """,
        params
    )
    asset_rows = cur.fetchall()

    cur.execute(
        f"""
        SELECT inventory_items.category_id, inventory_categories.name,
               inventory_items.sku, MIN(inventory_items.name), SUM(inventory_items.quantity_on_hand),
               SUM(inventory_items.unit_cost * inventory_items.quantity_on_hand) / NULLIF(SUM(inventory_items.quantity_on_hand), 0)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_quantity'
          {category_filter}
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_items.sku;
        """,
        params
    )
    quantity_rows = cur.fetchall()

    cur.execute(
        f"""
        SELECT inventory_items.category_id, inventory_categories.name,
               inventory_items.spec, MIN(inventory_items.name), SUM(inventory_items.length_remaining)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_length'
          AND inventory_items.length_status = 'In Stock'
          {category_filter}
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_items.spec;
        """,
        params
    )
    length_rows = cur.fetchall()

    cur.execute("SELECT category_id, sku_or_spec, reorder_level FROM inventory_sku_thresholds;")
    thresholds = {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}

    cur.close()
    conn.close()

    def _summary_row(category_id_, category_name, sku_or_spec, name, tracking_type, total_on_hand, avg_unit_cost):
        total_on_hand = float(total_on_hand or 0)
        avg_unit_cost = float(avg_unit_cost) if avg_unit_cost is not None else None
        reorder_level = thresholds.get((category_id_, sku_or_spec))
        return {
            "category_id": category_id_, "category_name": category_name, "name": name,
            "tracking_type": tracking_type, "sku_or_spec": sku_or_spec,
            "total_on_hand": total_on_hand, "reorder_level": reorder_level,
            "below_threshold": reorder_level is not None and total_on_hand < reorder_level,
            "avg_unit_cost": avg_unit_cost,
            "total_value": avg_unit_cost * total_on_hand if avg_unit_cost is not None else None,
        }

    result = []
    for category_id_, category_name, sku, name, total_on_hand, avg_unit_cost in asset_rows:
        result.append(_summary_row(category_id_, category_name, sku, name, "asset_serialized", total_on_hand, avg_unit_cost))
    for category_id_, category_name, sku, name, total_on_hand, avg_unit_cost in quantity_rows:
        result.append(_summary_row(category_id_, category_name, sku, name, "inventory_quantity", total_on_hand, avg_unit_cost))
    for category_id_, category_name, spec, name, total_on_hand in length_rows:
        result.append(_summary_row(category_id_, category_name, spec, name, "inventory_length", total_on_hand, None))
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

# Cable Type Summary — every in-stock reel of a spec, not just offcuts (see
# get_offcut_summary_by_spec below for the narrower "unusable remainder"
# view). Answers "what cable do we have", grouped the same way SKU Summary
# groups Length rows, but with a reel count alongside the total length.
def get_cable_type_summary(category_id=None):
    conn = get_connection()
    cur = conn.cursor()
    category_filter = "AND inventory_items.category_id = %s" if category_id is not None else ""
    params = (category_id,) if category_id is not None else ()
    cur.execute(
        f"""
        SELECT inventory_items.spec, COUNT(*), SUM(inventory_items.length_remaining)
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_length'
          AND inventory_items.length_status = 'In Stock'
          {category_filter}
        GROUP BY inventory_items.spec
        ORDER BY inventory_items.spec;
        """,
        params
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"spec": r[0], "reels_in_stock": r[1], "total_length_remaining": float(r[2] or 0)}
        for r in rows
    ]


# Unlike get_cable_type_summary's top-level rows (in-stock only, answering
# "what do we have"), this backs the Reports drill-down and deliberately
# includes every reel ever created for the spec — no is_active or
# length_status filter — so a fully-depleted reel (used up and reconciled to
# 0, or written off) still shows up here with its actual length_status and
# wherever its location_id last pointed before it closed out. That's a
# different question from the Units view (openUnitListModal in
# inventory-common.js), which excludes depleted reels by default since it
# answers "what's currently usable stock" — this one answers "what happened
# to every reel we've ever had".
def get_cable_drill_down(spec):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT inventory_items.id, inventory_items.cut_reel_id, inventory_items.length_remaining,
               inventory_items.length_status, loc.name, inventory_items.unit_cost, inventory_items.created_at
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        LEFT JOIN inventory_locations AS loc ON loc.id = inventory_items.location_id
        WHERE inventory_categories.tracking_type = 'inventory_length'
          AND inventory_items.spec = %s
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
            "length_status": r[3], "location_name": r[4], "unit_cost": r[5], "created_at": utc_iso(r[6]),
        }
        for r in rows
    ]

# The Items table's rollup — one row per (category, sku/spec) group, same
# grouping as get_sku_summary but answering a different question: total
# OWNED (on-hand + deployed combined), not just what's available to issue
# right now. A deployed asset is still owned and its value shouldn't vanish
# from the table just because it's out on a job — that's why this is a
# separate function from get_sku_summary rather than a shared one: SKU
# Summary's reorder-level flagging genuinely needs on-hand-only, and
# conflating the two would make one of them wrong.
#
# Each row carries both the on-hand/deployed split AND the combined total, so
# the frontend's Status filter (All/In Store/Deployed) can switch which
# number is displayed without a re-fetch.
def get_items_summary(category_id=None):
    conn = get_connection()
    cur = conn.cursor()
    category_filter = "AND inventory_items.category_id = %s" if category_id is not None else ""
    params = (category_id,) if category_id is not None else ()

    # Asset: "deployed" is asset_status = 'Deployed' specifically (the
    # issue-time transition — see routers/inventory.py's _plan_issue_line),
    # not a location check — that's what the status actually encodes.
    # avg_unit_cost is a plain AVG across ALL active rows (deployed
    # included), same "equal-weight rows = weighted average" reasoning as
    # get_sku_summary, just without that function's is_store filter.
    # location_names is a display convenience for the Items table's Location
    # column: distinct store locations among the on-hand population only
    # (a deployed asset's location_id, if any, isn't "where this SKU is
    # shelved" the way an on-hand one's is). Comma-joined since a SKU's
    # on-hand units routinely span more than one store.
    cur.execute(
        f"""
        SELECT inventory_items.category_id, inventory_categories.name, inventory_categories.custody_type,
               inventory_items.sku, MIN(inventory_items.name),
               COUNT(*) FILTER (WHERE inventory_items.asset_status != 'Deployed'),
               COUNT(*) FILTER (WHERE inventory_items.asset_status = 'Deployed'),
               AVG(inventory_items.unit_cost),
               STRING_AGG(DISTINCT inventory_locations.name, ', ') FILTER (WHERE inventory_items.asset_status != 'Deployed')
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        LEFT JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'asset_serialized'
          {category_filter}
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_categories.custody_type, inventory_items.sku;
        """,
        params
    )
    asset_rows = cur.fetchall()

    # Quantity: issuing draws down quantity_on_hand in place rather than
    # moving stock to a tracked "deployed" bucket (confirmed in
    # _plan_issue_line — there is no such bucket for this type), so on-hand
    # IS the total; deployed is always 0. Keeps the already-correct weighted
    # average (SUM(cost*qty)/SUM(qty)) rather than a flat number — the
    # underlying batch-cost-variance problem is identical to Assets', the
    # math already exists and is tested, so there's no reason to downgrade it.
    cur.execute(
        f"""
        SELECT inventory_items.category_id, inventory_categories.name, inventory_categories.custody_type,
               inventory_items.sku, MIN(inventory_items.name),
               SUM(inventory_items.quantity_on_hand),
               SUM(inventory_items.unit_cost * inventory_items.quantity_on_hand) / NULLIF(SUM(inventory_items.quantity_on_hand), 0),
               STRING_AGG(DISTINCT inventory_locations.name, ', ')
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        LEFT JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_quantity'
          {category_filter}
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_categories.custody_type, inventory_items.sku;
        """,
        params
    )
    quantity_rows = cur.fetchall()

    # Length: on-hand vs deployed mirrors length_status, same convention as
    # get_cable_type_summary — but with NO status filter on the total, unlike
    # that report (which stays scoped to in-stock only, since it answers "what's
    # on the shelf" rather than this table's "what do we own").
    #
    # Value is SUM(unit_cost) across the actual reels, NOT qty * unit_cost —
    # unlike Asset/Quantity, where qty is a count of individually-priced
    # units (so qty * cost correctly reconstructs total spend), Cable's "Qty"
    # is a length in metres while unit_cost prices a whole reel regardless of
    # length. Multiplying those together (as the generic _row() below does
    # for the other two types) previously priced a drum per metre instead of
    # per reel — e.g. two 7,000/reel Drop Cable drums summing 2,001m priced
    # out at 7,000 x 2,001 instead of 7,000 x 2 reels.
    cur.execute(
        f"""
        SELECT inventory_items.category_id, inventory_categories.name, inventory_categories.custody_type,
               inventory_items.spec, MIN(inventory_items.name),
               SUM(inventory_items.length_remaining) FILTER (WHERE inventory_items.length_status = 'In Stock'),
               SUM(inventory_items.length_remaining) FILTER (WHERE inventory_items.length_status = 'Out — Pending Reconciliation'),
               SUM(inventory_items.unit_cost) FILTER (WHERE inventory_items.length_status = 'In Stock'),
               SUM(inventory_items.unit_cost) FILTER (WHERE inventory_items.length_status = 'Out — Pending Reconciliation'),
               STRING_AGG(DISTINCT inventory_locations.name, ', ') FILTER (WHERE inventory_items.length_status = 'In Stock')
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        LEFT JOIN inventory_locations ON inventory_locations.id = inventory_items.location_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_length'
          {category_filter}
        GROUP BY inventory_items.category_id, inventory_categories.name, inventory_categories.custody_type, inventory_items.spec;
        """,
        params
    )
    length_rows = cur.fetchall()

    # Cable's unit cost is a single real value ("what we're currently
    # paying"), not an average — the most recently received active reel of
    # that spec, per spec.
    cur.execute(
        f"""
        SELECT DISTINCT ON (inventory_items.category_id, inventory_items.spec)
               inventory_items.category_id, inventory_items.spec, inventory_items.unit_cost
        FROM inventory_items
        JOIN inventory_categories ON inventory_categories.id = inventory_items.category_id
        WHERE inventory_items.is_active = true
          AND inventory_categories.tracking_type = 'inventory_length'
          {category_filter}
        ORDER BY inventory_items.category_id, inventory_items.spec, inventory_items.created_at DESC;
        """,
        params
    )
    length_unit_cost = {(r[0], r[1]): r[2] for r in cur.fetchall()}

    # Reorder Level is edited from this table now (Reports' own copy of the
    # same threshold stayed read-only) — same thresholds table, same
    # (category_id, sku_or_spec) key get_sku_summary already uses.
    cur.execute("SELECT category_id, sku_or_spec, reorder_level FROM inventory_sku_thresholds;")
    thresholds = {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}

    cur.close()
    conn.close()

    def _row(category_id, category_name, custody_type, tracking_type, sku_or_spec, name, on_hand_qty, deployed_qty, avg_unit_cost, location_names, value_override=None):
        on_hand_qty = float(on_hand_qty or 0)
        deployed_qty = float(deployed_qty or 0)
        total_qty = on_hand_qty + deployed_qty
        unit_cost = float(avg_unit_cost) if avg_unit_cost is not None else None
        reorder_level = thresholds.get((category_id, sku_or_spec))
        if value_override is not None:
            # Cable: each reel is priced as a whole unit regardless of its
            # length, so value is the real SUM(unit_cost) passed in here —
            # never qty (metres) * unit_cost (per reel).
            on_hand_value, deployed_value = value_override
        else:
            cost_for_value = unit_cost or 0
            on_hand_value = on_hand_qty * cost_for_value
            deployed_value = deployed_qty * cost_for_value
        return {
            "category_id": category_id, "category_name": category_name, "custody_type": custody_type,
            "tracking_type": tracking_type, "sku_or_spec": sku_or_spec, "name": name,
            "on_hand_qty": on_hand_qty, "deployed_qty": deployed_qty, "total_qty": total_qty,
            "avg_unit_cost": unit_cost,
            "on_hand_value": on_hand_value,
            "deployed_value": deployed_value,
            "total_value": on_hand_value + deployed_value,
            "reorder_level": reorder_level,
            "location_names": location_names,
        }

    result = []
    for category_id_, category_name, custody_type, sku, name, on_hand, deployed, avg_cost, location_names in asset_rows:
        result.append(_row(category_id_, category_name, custody_type, "asset_serialized", sku, name, on_hand, deployed, avg_cost, location_names))
    for category_id_, category_name, custody_type, sku, name, on_hand, avg_cost, location_names in quantity_rows:
        result.append(_row(category_id_, category_name, custody_type, "inventory_quantity", sku, name, on_hand, 0, avg_cost, location_names))
    for category_id_, category_name, custody_type, spec, name, on_hand, deployed, on_hand_value, deployed_value, location_names in length_rows:
        avg_cost = length_unit_cost.get((category_id_, spec))
        result.append(_row(
            category_id_, category_name, custody_type, "inventory_length", spec, name, on_hand, deployed, avg_cost, location_names,
            value_override=(float(on_hand_value or 0), float(deployed_value or 0)),
        ))

    result.sort(key=lambda r: (r["category_name"], r["sku_or_spec"]))
    return result

# Consumable Core's "View" action has no individual units to drill into, so
# it shows a flat running-balance history instead — one Quantity SKU can span
# several batch-lot rows (item_ids), so this joins across all of them rather
# than reading one item's history in isolation.
#
# The running balance's sign convention is derived, not stored: In and a
# positive Adjustment add, Out and Write-off subtract, Return contributes 0
# for this type (an existing, unchanged behavior — Return only relocates a
# Quantity item, it never restores quantity_on_hand), and Transfer contributes
# 0 at the SKU-aggregate level regardless of whether a given row is a whole-
# row move or one half of a split (a split's two linked rows carry equal and
# opposite real effects, so scoring both as 0 nets to the same correct total
# without needing to know which row is the origin vs the new destination row).
def get_sku_transaction_history(category_id, sku):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT inventory_transactions.id, inventory_transactions.created_at, inventory_transactions.action,
               inventory_transactions.qty_or_length,
               SUM(
                   CASE inventory_transactions.action
                       WHEN 'In' THEN COALESCE(inventory_transactions.qty_or_length, 0)
                       WHEN 'Adjustment' THEN COALESCE(inventory_transactions.qty_or_length, 0)
                       WHEN 'Out' THEN -COALESCE(inventory_transactions.qty_or_length, 0)
                       WHEN 'Write-off' THEN -COALESCE(inventory_transactions.qty_or_length, 0)
                       ELSE 0
                   END
               ) OVER (ORDER BY inventory_transactions.created_at, inventory_transactions.id) AS balance
        FROM inventory_transactions
        JOIN inventory_items ON inventory_items.id = inventory_transactions.item_id
        WHERE inventory_items.category_id = %s AND inventory_items.sku = %s
        ORDER BY inventory_transactions.created_at, inventory_transactions.id;
        """,
        (category_id, sku)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    history = [
        {
            "id": r[0], "created_at": utc_iso(r[1]), "action": r[2],
            "qty_or_length": r[3], "balance": float(r[4]),
        }
        for r in rows
    ]
    history.reverse()  # newest first, matching every other log table
    return history

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

def get_offcut_summary_by_spec(category_id=None):
    conn = get_connection()
    cur = conn.cursor()
    category_filter = "AND inventory_items.category_id = %s" if category_id is not None else ""
    params = (category_id,) if category_id is not None else ()
    cur.execute(
        f"""
        SELECT inventory_items.spec, SUM(inventory_items.length_remaining), COUNT(*)
        {_OFFCUT_JOIN}
        WHERE inventory_items.is_active = true
          AND inventory_items.length_status = 'In Stock'
          {category_filter}
        GROUP BY inventory_items.spec
        ORDER BY inventory_items.spec;
        """,
        params
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
