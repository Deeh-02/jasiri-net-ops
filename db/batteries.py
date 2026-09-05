from db.connection import get_connection, utc_iso
from db import sites

# Movement lifecycle statuses that mean "away from anyone's hands" — charge
# is unknown and locked out of 'charging' for all of these, whether it's
# still literally moving or sitting at a site nobody's confirmed as usable
# yet.
AWAY_STATUSES = {"in_transit", "arrived", "site_still_down"}
# Physically arrived, but the site-down flow's confirm-online question
# hasn't been answered yet — battery status shows "Not Deployed" rather
# than "In Transit" (it's no longer moving) or "Deployed" (not confirmed
# usable there yet).
NOT_DEPLOYED_STATUSES = {"arrived", "site_still_down"}
# Terminal "it's there and confirmed" states — either path (straight
# completion, or the site-down flow's confirm-online) resolves here.
TERMINAL_STATUSES = {"completed", "site_confirmed_online"}


def add_battery(battery_number, serial_number=None, model=None, capacity=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO batteries (battery_number, serial_number, model, capacity)
        VALUES (%s, %s, %s, %s)
        RETURNING id;
        """,
        (battery_number, serial_number, model, capacity)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def update_charge_status(battery_id, charge_status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE batteries SET charge_status = %s WHERE id = %s;", (charge_status, battery_id))
    conn.commit()
    cur.close()
    conn.close()

def get_last_movement(battery_id):
    # NOTE: excludes cancelled movements, so a cancelled move doesn't leave
    # the battery looking like it's sitting at (or heading to) a destination
    # it never reached — it falls back to whatever the last legitimate
    # movement was. Fetches the last TWO (not just one): while the latest
    # movement is still 'pending', the battery hasn't actually left yet, so
    # location/moved_by/since must keep showing whatever the movement BEFORE
    # it left behind, not this new movement's (future) destination.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT battery_movements.status, battery_movements.moved_by,
               battery_movements.created_at, battery_movements.in_transit_at,
               to_loc.name
        FROM battery_movements
        JOIN locations AS to_loc ON battery_movements.to_location_id = to_loc.id
        WHERE battery_movements.battery_id = %s
          AND battery_movements.status != 'cancelled'
        ORDER BY battery_movements.created_at DESC
        LIMIT 2;
        """,
        (battery_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return None

    def to_dict(r):
        return {"status": r[0], "moved_by": r[1], "created_at": r[2], "in_transit_at": r[3], "to_location": r[4]}

    latest = to_dict(rows[0])
    prev = to_dict(rows[1]) if len(rows) > 1 else None
    status = latest["status"]

    if status == "pending":
        # Battery physically hasn't moved yet — surface the PRIOR movement's
        # resolved state (or the pre-any-movement baseline), not this one's.
        if prev:
            location = prev["to_location"]
            moved_by = prev["moved_by"]
            moved_at = prev["in_transit_at"] or prev["created_at"]
        else:
            location = "Unknown (no movements recorded)"
            moved_by = None
            moved_at = None
    elif status == "in_transit":
        # Genuinely en route — location is unknown until it lands somewhere.
        location = "—"
        moved_by = latest["moved_by"]
        # "Since" tracks when it actually left (marked in transit), not when
        # the movement was first created back in 'pending'.
        moved_at = latest["in_transit_at"]
    else:
        # arrived / site_still_down / completed / site_confirmed_online —
        # the battery has physically reached the destination even if it
        # isn't confirmed "Deployed" yet, so location shows where it
        # actually is rather than "—".
        location = latest["to_location"]
        moved_by = latest["moved_by"]
        moved_at = latest["in_transit_at"]

    return {
        "location": location,
        "moved_by": moved_by,
        "moved_at": moved_at,
        "movement_status": status,
    }

def _battery_status(last):
    """Battery Tracker status now tracks the linked movement's lifecycle:
    'Pending' while it's queued to move, 'In Transit' while it's actually
    moving, 'Not Deployed' once it's arrived but the site-down flow's
    confirm-online question is still unanswered, and 'Deployed' once that
    resolves (arrival for a straight move, or confirmed-online for a
    site-down move) — regardless of whether the destination is home base.
    'At Base' only remains as the baseline for a battery with no (live)
    movement history at all."""
    if last is None:
        return "At Base"
    status = last["movement_status"]
    if status == "pending":
        return "Pending"
    if status == "in_transit":
        return "In Transit"
    if status in NOT_DEPLOYED_STATUSES:
        return "Not Deployed"
    if status in TERMINAL_STATUSES:
        return "Deployed"
    return "At Base"

def is_locked_from_charging(battery_id):
    """A battery that's away from anyone's hands (in transit, or arrived and
    waiting on a site-check answer) can't be plugged in to charge — used to
    reject 'charging' server-side, not just hide it in the UI."""
    last = get_last_movement(battery_id)
    return last is not None and last["movement_status"] in AWAY_STATUSES

def get_current_location(battery_id):
    last = get_last_movement(battery_id)
    if last:
        return last["location"]
    return "Unknown (no movements recorded)"

def get_movement_history(battery_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            battery_movements.created_at,
            from_loc.name AS from_location,
            to_loc.name AS to_location,
            battery_movements.reason,
            battery_movements.moved_by
        FROM battery_movements
        LEFT JOIN locations AS from_loc ON battery_movements.from_location_id = from_loc.id
        JOIN locations AS to_loc ON battery_movements.to_location_id = to_loc.id
        WHERE battery_movements.battery_id = %s
        ORDER BY battery_movements.created_at DESC;
        """,
        (battery_id,)
    )
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results

def get_all_batteries():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, battery_number, model, capacity, charge_status
        FROM batteries
        WHERE status = 'active'
        ORDER BY battery_number;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    batteries = []
    for r in rows:
        battery_id = r[0]
        last = get_last_movement(battery_id)
        if last is None:
            current_location = "Unknown (no movements recorded)"
            moved_by = None
            moved_at = None
            movement_status = None
        else:
            current_location = last["location"]
            moved_by = last["moved_by"]
            moved_at = last["moved_at"]
            movement_status = last["movement_status"]

        batteries.append({
            "id": battery_id,
            "battery_number": r[1],
            "model": r[2],
            "capacity": r[3],
            "charge_status": r[4],
            "current_location": current_location,
            "status": _battery_status(last),
            "moved_by": moved_by,
            "moved_at": utc_iso(moved_at),
            # The raw movement lifecycle status (pending/in_transit/etc.) of
            # this battery's last movement — separate from `status` above,
            # which is the Pending/In Transit/Deployed/At Base label. Used
            # by the stat-card click-through detail to show what's actually
            # happening with each battery, not just where it physically is.
            "movement_status": movement_status,
        })
    return batteries

def deactivate_battery(battery_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE batteries SET status = 'inactive' WHERE id = %s;", (battery_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_battery_by_id(battery_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, battery_number, serial_number, model, capacity, charge_status, status
        FROM batteries
        WHERE id = %s;
    """, (battery_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        return None

    battery_id = row[0]
    last = get_last_movement(battery_id)
    if last is None:
        current_location = "Unknown (no movements recorded)"
        moved_by = None
        moved_at = None
    else:
        current_location = last["location"]
        moved_by = last["moved_by"]
        moved_at = last["moved_at"]

    return {
        "id": battery_id,
        "battery_number": row[1],
        "serial_number": row[2],
        "model": row[3],
        "capacity": row[4],
        "charge_status": row[5],
        "status": _battery_status(last),
        "current_location": current_location,
        "moved_by": moved_by,
        "moved_at": utc_iso(moved_at),
    }

def update_battery(battery_id, battery_number, serial_number=None, model=None, capacity=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE batteries
        SET battery_number = %s, serial_number = %s, model = %s, capacity = %s
        WHERE id = %s;
        """,
        (battery_number, serial_number, model, capacity, battery_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_movement_by_id(movement_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, battery_id, from_location_id, to_location_id, reason,
               status, created_at, arrived_at, confirmed_at, in_transit_at
        FROM battery_movements
        WHERE id = %s;
        """,
        (movement_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "battery_id": row[1],
        "from_location_id": row[2],
        "to_location_id": row[3],
        "reason": row[4],
        "status": row[5],
        "created_at": row[6],
        "arrived_at": row[7],
        "confirmed_at": row[8],
        "in_transit_at": row[9],
    }

def get_active_movements():
    """Default view for the Movements list — anything not yet fully resolved."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            battery_movements.id, batteries.battery_number,
            from_loc.name, to_loc.name,
            battery_movements.status, battery_movements.created_at,
            battery_movements.reason
        FROM battery_movements
        JOIN batteries ON battery_movements.battery_id = batteries.id
        LEFT JOIN locations AS from_loc ON battery_movements.from_location_id = from_loc.id
        JOIN locations AS to_loc ON battery_movements.to_location_id = to_loc.id
        WHERE battery_movements.status IN ('pending', 'in_transit', 'arrived', 'site_still_down')
        ORDER BY battery_movements.created_at DESC;
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0], "battery_number": r[1], "from_location": r[2],
            "to_location": r[3], "status": r[4],
            "created_at": utc_iso(r[5]),
            "reason": r[6],
        }
        for r in rows
    ]

def get_all_movements_history():
    """Everything, including cancelled / confirmed-online / completed — the 'show history' toggle."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            battery_movements.id, batteries.battery_number,
            from_loc.name, to_loc.name,
            battery_movements.status, battery_movements.created_at,
            battery_movements.reason
        FROM battery_movements
        JOIN batteries ON battery_movements.battery_id = batteries.id
        LEFT JOIN locations AS from_loc ON battery_movements.from_location_id = from_loc.id
        JOIN locations AS to_loc ON battery_movements.to_location_id = to_loc.id
        ORDER BY battery_movements.created_at DESC;
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0], "battery_number": r[1], "from_location": r[2],
            "to_location": r[3], "status": r[4],
            "created_at": utc_iso(r[5]),
            "reason": r[6],
        }
        for r in rows
    ]

def get_active_movement_count():
    """Badge count for the Movements nav/quick-link — mirrors
    get_active_movements()'s "not yet fully resolved" status set, so the
    badge and the default (non-history) Movements list always agree on
    what counts as still-open. Not time-gated, same as Check Sites'
    unconfirmed-count badge: it reflects what needs attention right now."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM battery_movements
        WHERE status IN ('pending', 'in_transit', 'arrived', 'site_still_down');
        """
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

def mark_movement_in_transit(movement_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE battery_movements SET status = 'in_transit', in_transit_at = NOW()
        WHERE id = %s
        RETURNING battery_id;
        """,
        (movement_id,)
    )
    row = cur.fetchone()
    battery_id = row[0] if row else None
    conn.commit()
    cur.close()
    conn.close()

    # Once a battery is actually in transit, nobody can plug it in to check
    # or charge it — charge becomes unknown right here (not at creation,
    # while it's still just 'pending' and physically sitting where it was)
    # and set_charge_status rejects 'charging' for as long as it stays in
    # one of the AWAY_STATUSES.
    if battery_id is not None:
        update_charge_status(battery_id, "unknown")

def mark_movement_arrived(movement_id):
    """Only used for the 'site_down' path — lands on 'arrived' and waits for
    the site-check answer."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE battery_movements SET status = 'arrived', arrived_at = NOW() WHERE id = %s;",
        (movement_id,)
    )
    conn.commit()
    cur.close()
    conn.close()

def complete_movement(movement_id):
    """Terminal state for any reason other than 'site_down' — once the battery
    has physically arrived there's nothing left to confirm, so it resolves
    straight to 'completed' instead of waiting on a site-check answer."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE battery_movements SET status = 'completed', arrived_at = NOW(), confirmed_at = NOW() WHERE id = %s;",
        (movement_id,)
    )
    conn.commit()
    cur.close()
    conn.close()

def cancel_movement(movement_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE battery_movements SET status = 'cancelled' WHERE id = %s;", (movement_id,))
    conn.commit()
    cur.close()
    conn.close()

def record_movement(battery_id, from_location_id, to_location_id, reason=None, moved_by=None, moved_by_user_id=None):
    conn = get_connection()
    cur = conn.cursor()

    if from_location_id is None:
        cur.execute(
            """
            SELECT to_location_id FROM battery_movements
            WHERE battery_id = %s AND status != 'cancelled'
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (battery_id,)
        )
        last = cur.fetchone()
        from_location_id = last[0] if last else None

    # New movement always starts life as 'pending' (DB column default handles this,
    # so we don't need to pass status explicitly here).
    cur.execute(
        """
        INSERT INTO battery_movements (battery_id, from_location_id, to_location_id, reason, moved_by, moved_by_user_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (battery_id, from_location_id, to_location_id, reason, moved_by, moved_by_user_id)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    # Charge/location/moved-by/since all stay exactly as they were while the
    # movement is 'pending' — the battery hasn't physically left yet. Charge
    # only flips to "unknown" once mark_movement_in_transit() actually moves
    # it, regardless of destination (this used to fire here and only for a
    # non-home-base destination — both of those were wrong once status
    # became lifecycle-driven).
    return new_id

def confirm_site_online(movement_id):
    """Movement -> Site Confirmed Online. Cross-feature link: also flips the
    destination site's is_online to TRUE and stamps verification_confirmed_at —
    this IS the hourly confirmation, not a separate write. If the site was
    sitting Offline in the Check Sites list, this brings it back Online there too."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE battery_movements
        SET status = 'site_confirmed_online', confirmed_at = NOW()
        WHERE id = %s
        RETURNING to_location_id;
        """,
        (movement_id,)
    )
    row = cur.fetchone()
    to_location_id = row[0] if row else None
    conn.commit()
    cur.close()
    conn.close()

    if to_location_id is not None:
        sites.set_location_online_status(to_location_id, True, stamp_confirmed=True)

def mark_site_still_down(movement_id):
    """Marks the destination site is_online = FALSE, so it shows Offline in the
    Check Sites list too. Deliberately does NOT stamp verification_confirmed_at —
    the site keeps getting flagged as needing a check every hour until someone
    reports it back online, rather than going quiet just because we know it's down."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE battery_movements
        SET status = 'site_still_down', confirmed_at = NOW()
        WHERE id = %s
        RETURNING to_location_id;
        """,
        (movement_id,)
    )
    row = cur.fetchone()
    to_location_id = row[0] if row else None
    conn.commit()
    cur.close()
    conn.close()

    if to_location_id is not None:
        sites.set_location_online_status(to_location_id, False, stamp_confirmed=False)