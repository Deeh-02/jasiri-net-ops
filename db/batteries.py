from db.connection import db_cursor, utc_iso
from db import sites

# Movement lifecycle statuses that mean "away from anyone's hands, still
# literally moving or not yet confirmed usable" — used for the charge lock
# below. 'site_still_down' is kept for backward compatibility with any
# historical row written before mark_site_still_down started closing the
# movement out as 'completed' instead — going forward this only matches
# 'in_transit'/'arrived'.
AWAY_STATUSES = {"in_transit", "arrived", "site_still_down"}
# Arrived, but the site-down flow's confirm-online question hasn't been
# answered yet. Battery status still reads "Deployed" for this (see
# _battery_status) — it's purely here so is_locked_from_charging keeps
# rejecting 'charging' for it, same reasoning as AWAY_STATUSES above.
NOT_DEPLOYED_STATUSES = {"arrived", "site_still_down"}
# Terminal "it's there and the movement is closed" states. A site-down move
# that comes back "still down" also lands here now (see mark_site_still_down)
# — the MOVEMENT's lifecycle is done either way; whether the battery is
# actually usable there is tracked separately via the location's is_online,
# not by keeping the movement itself open.
TERMINAL_STATUSES = {"completed", "site_confirmed_online"}


def add_battery(battery_number, serial_number=None, model=None, capacity=None):
    with db_cursor() as (conn, cur):
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
    return new_id

def update_charge_status(battery_id, charge_status):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE batteries SET charge_status = %s WHERE id = %s;", (charge_status, battery_id))
        conn.commit()

def get_last_movement(battery_id):
    # NOTE: excludes cancelled movements, so a cancelled move doesn't leave
    # the battery looking like it's sitting at (or heading to) a destination
    # it never reached — it falls back to whatever the last legitimate
    # movement was. Fetches the last TWO (not just one): while the latest
    # movement is still 'pending', the battery hasn't actually left yet, so
    # location/moved_by/since must keep showing whatever the movement BEFORE
    # it left behind, not this new movement's (future) destination.
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT battery_movements.status, battery_movements.moved_by,
                   battery_movements.created_at, battery_movements.in_transit_at,
                   to_loc.name, to_loc.is_online, to_loc.is_home_base
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
    if not rows:
        return None

    def to_dict(r):
        return {
            "status": r[0], "moved_by": r[1], "created_at": r[2], "in_transit_at": r[3],
            "to_location": r[4], "is_online": r[5], "is_home_base": r[6],
        }

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
            site_online = prev["is_online"]
            is_home_base = prev["is_home_base"]
        else:
            location = "Unknown (no movements recorded)"
            moved_by = None
            moved_at = None
            site_online = None
            is_home_base = False
    elif status == "in_transit":
        # Genuinely en route — location (and its site's online state) is
        # unknown until it lands somewhere.
        location = "—"
        moved_by = latest["moved_by"]
        # "Since" tracks when it actually left (marked in transit), not when
        # the movement was first created back in 'pending'.
        moved_at = latest["in_transit_at"]
        site_online = None
        is_home_base = False
    else:
        # arrived / site_still_down / completed / site_confirmed_online —
        # the battery has physically reached the destination even if it
        # isn't confirmed "Deployed" yet, so location shows where it
        # actually is rather than "—".
        location = latest["to_location"]
        moved_by = latest["moved_by"]
        moved_at = latest["in_transit_at"]
        site_online = latest["is_online"]
        is_home_base = latest["is_home_base"]

    return {
        "location": location,
        "moved_by": moved_by,
        "moved_at": moved_at,
        "movement_status": status,
        # The destination site's real is_online state — None when there's
        # no meaningful site to check yet (in transit, or never moved).
        # Drives the battery table's "needs attention" row accent without
        # touching the Pending/In Transit/Deployed status label itself.
        "site_online": site_online,
        # Whether the battery has actually landed back at home base — a
        # movement resolving there means the battery is "At Base" again,
        # not "Deployed" (arriving anywhere else is what "Deployed" means).
        "is_home_base": is_home_base,
    }

def _battery_status(last):
    """Battery Tracker status tracks the linked movement's lifecycle:
    'Pending' while it's queued to move, 'In Transit' while it's actually
    moving, and 'Deployed' once it's arrived somewhere other than home
    base — whether that's a straight move, or the site-down flow
    (arrived-awaiting-confirmation, or answered either way). A movement
    that lands back at home base resolves to 'At Base' instead, since a
    battery sitting at home isn't "deployed" in any meaningful sense. A
    site confirmed still down does NOT get its own status label; it's
    surfaced instead as a row-level "needs attention" accent driven by
    site_online (see get_all_batteries), so the label/count stay simple.
    'At Base' also remains the baseline for a battery with no (live)
    movement history at all."""
    if last is None:
        return "At Base"
    status = last["movement_status"]
    if status == "pending":
        return "Pending"
    if status == "in_transit":
        return "In Transit"
    if status in NOT_DEPLOYED_STATUSES or status in TERMINAL_STATUSES:
        return "At Base" if last["is_home_base"] else "Deployed"
    return "At Base"

def is_locked_from_charging(battery_id):
    """A battery can't be plugged in to charge while it's away (in transit,
    or arrived and waiting on a site-check answer) OR while it's confirmed
    sitting at a site with no power — that second case stays locked even
    after the movement itself closes out as 'completed', since the
    restriction is really about the site, not the movement's own lifecycle.
    Enforced here (not just hidden in the UI) so it can't be bypassed via a
    direct API call."""
    last = get_last_movement(battery_id)
    if last is None:
        return False
    if last["movement_status"] in AWAY_STATUSES:
        return True
    return last["site_online"] is False

def get_current_location(battery_id):
    last = get_last_movement(battery_id)
    if last:
        return last["location"]
    return "Unknown (no movements recorded)"

def get_movement_history(battery_id):
    with db_cursor() as (conn, cur):
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
    return results

def get_all_batteries():
    with db_cursor() as (conn, cur):
        cur.execute("""
            SELECT id, battery_number, model, capacity, charge_status
            FROM batteries
            WHERE status = 'active'
            ORDER BY battery_number;
        """)
        rows = cur.fetchall()

    batteries = []
    for r in rows:
        battery_id = r[0]
        last = get_last_movement(battery_id)
        if last is None:
            current_location = "Unknown (no movements recorded)"
            moved_by = None
            moved_at = None
            movement_status = None
            site_online = None
        else:
            current_location = last["location"]
            moved_by = last["moved_by"]
            moved_at = last["moved_at"]
            movement_status = last["movement_status"]
            site_online = last["site_online"]

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
            # The current location's real is_online state (None if there's
            # no site to check right now). The frontend uses this to flag a
            # "Deployed" battery sitting at a confirmed-offline site with a
            # row accent — it doesn't change `status` or the stat counts.
            "site_online": site_online,
        })
    return batteries

def deactivate_battery(battery_id):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE batteries SET status = 'inactive' WHERE id = %s;", (battery_id,))
        conn.commit()

def get_battery_by_id(battery_id):
    with db_cursor() as (conn, cur):
        cur.execute("""
            SELECT id, battery_number, serial_number, model, capacity, charge_status, status
            FROM batteries
            WHERE id = %s;
        """, (battery_id,))
        row = cur.fetchone()

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
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE batteries
            SET battery_number = %s, serial_number = %s, model = %s, capacity = %s
            WHERE id = %s;
            """,
            (battery_number, serial_number, model, capacity, battery_id)
        )
        conn.commit()

def get_movement_by_id(movement_id):
    with db_cursor() as (conn, cur):
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
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                battery_movements.id, batteries.battery_number,
                from_loc.name, to_loc.name, to_loc.id,
                battery_movements.status, battery_movements.created_at,
                battery_movements.reason, battery_movements.arrived_at
            FROM battery_movements
            JOIN batteries ON battery_movements.battery_id = batteries.id
            LEFT JOIN locations AS from_loc ON battery_movements.from_location_id = from_loc.id
            JOIN locations AS to_loc ON battery_movements.to_location_id = to_loc.id
            WHERE battery_movements.status IN ('pending', 'in_transit', 'arrived')
            ORDER BY battery_movements.created_at DESC;
            """
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0], "battery_number": r[1], "from_location": r[2],
            "to_location": r[3],
            # Phase 4.9: lets the frontend match this row against
            # GET /monitoring/status (keyed by location_id) to prefill the
            # site-check answer — this table still has no idea monitoring
            # exists, it's just returning its own location's id like any
            # other field here.
            "to_location_id": r[4],
            "status": r[5],
            "created_at": utc_iso(r[6]),
            "reason": r[7],
            "arrived_at": utc_iso(r[8]),
        }
        for r in rows
    ]

def get_all_movements_history():
    """Everything, including cancelled / confirmed-online / completed — the 'show history' toggle."""
    with db_cursor() as (conn, cur):
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
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT COUNT(*) FROM battery_movements
            WHERE status IN ('pending', 'in_transit', 'arrived');
            """
        )
        count = cur.fetchone()[0]
    return count

def mark_movement_in_transit(movement_id):
    with db_cursor() as (conn, cur):
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
    with db_cursor() as (conn, cur):
        cur.execute(
            "UPDATE battery_movements SET status = 'arrived', arrived_at = NOW() WHERE id = %s;",
            (movement_id,)
        )
        conn.commit()

def complete_movement(movement_id):
    """Terminal state for any reason other than 'site_down' — once the battery
    has physically arrived there's nothing left to confirm, so it resolves
    straight to 'completed' instead of waiting on a site-check answer."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "UPDATE battery_movements SET status = 'completed', arrived_at = NOW(), confirmed_at = NOW() WHERE id = %s;",
            (movement_id,)
        )
        conn.commit()

def cancel_movement(movement_id):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE battery_movements SET status = 'cancelled' WHERE id = %s;", (movement_id,))
        conn.commit()

def record_movement(battery_id, from_location_id, to_location_id, reason=None, moved_by=None, moved_by_user_id=None):
    with db_cursor() as (conn, cur):
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
    with db_cursor() as (conn, cur):
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

    if to_location_id is not None:
        sites.set_location_online_status(to_location_id, True, stamp_confirmed=True)

def mark_site_still_down(movement_id):
    """Answering a site-check as 'still down' closes the MOVEMENT out as
    'completed' — its lifecycle ends here, no further status changes are
    expected on this record. The battery's own real-world resolution (once
    the site is eventually confirmed back online, whether via Check Sites
    or a later movement) is tracked separately via the location's
    is_online, not by keeping this movement open.

    Marks the destination site is_online = FALSE, so it shows Offline in the
    Check Sites list too. Deliberately does NOT stamp verification_confirmed_at —
    the site keeps getting flagged as needing a check every hour until someone
    reports it back online, rather than going quiet just because we know it's down."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE battery_movements
            SET status = 'completed', confirmed_at = NOW()
            WHERE id = %s
            RETURNING to_location_id;
            """,
            (movement_id,)
        )
        row = cur.fetchone()
        to_location_id = row[0] if row else None
        conn.commit()

    if to_location_id is not None:
        sites.set_location_online_status(to_location_id, False, stamp_confirmed=False)
