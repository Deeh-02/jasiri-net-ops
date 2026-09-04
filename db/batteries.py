from db.connection import get_connection
from db import sites


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
    # NOTE: now excludes cancelled movements, so a cancelled move doesn't leave
    # the battery looking like it's sitting at a destination it never reached —
    # it falls back to whatever the last legitimate movement was.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT to_loc.name, to_loc.is_home_base, battery_movements.moved_by,
               battery_movements.created_at, battery_movements.status
        FROM battery_movements
        JOIN locations AS to_loc ON battery_movements.to_location_id = to_loc.id
        WHERE battery_movements.battery_id = %s
          AND battery_movements.status != 'cancelled'
        ORDER BY battery_movements.created_at DESC
        LIMIT 1;
        """,
        (battery_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {
            "location": row[0],
            "at_home_base": row[1],
            "moved_by": row[2],
            "moved_at": row[3],
            "movement_status": row[4],
        }
    return None

def _battery_status_label(last):
    """Battery Tracker status is ONLY ever 'At Base' or 'Deployed' — purely
    physical, based on whether the battery's last movement landed it at the
    home-base location. Movement lifecycle (Pending/In Transit/Arrived/etc.)
    is a separate vocabulary that lives only on the Movements page and must
    never leak in here, regardless of the movement's own status."""
    if last is None:
        return "At Base"
    return "At Base" if last["at_home_base"] else "Deployed"

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
        else:
            current_location = last["location"]
            moved_by = last["moved_by"]
            moved_at = last["moved_at"]

        batteries.append({
            "id": battery_id,
            "battery_number": r[1],
            "model": r[2],
            "capacity": r[3],
            "charge_status": r[4],
            "current_location": current_location,
            "status": _battery_status_label(last),
            "moved_by": moved_by,
            "moved_at": moved_at.isoformat() if moved_at else None,
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
        "status": _battery_status_label(last),
        "current_location": current_location,
        "moved_by": moved_by,
        "moved_at": moved_at.isoformat() if moved_at else None,
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
               status, created_at, arrived_at, confirmed_at
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
            battery_movements.reason, battery_movements.moved_by
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
            "created_at": r[5].isoformat() if r[5] else None,
            "reason": r[6],
            "moved_by": r[7],
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
            battery_movements.reason, battery_movements.moved_by
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
            "created_at": r[5].isoformat() if r[5] else None,
            "reason": r[6],
            "moved_by": r[7],
        }
        for r in rows
    ]

def get_overdue_movement_count(threshold_hours=1):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM battery_movements
        WHERE status IN ('pending', 'in_transit')
          AND created_at < NOW() - (%s || ' hours')::interval;
        """,
        (threshold_hours,)
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

def mark_movement_in_transit(movement_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE battery_movements SET status = 'in_transit' WHERE id = %s;", (movement_id,))
    conn.commit()
    cur.close()
    conn.close()

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

    # Once a battery leaves home base, we no longer know its real charge
    # level out in the field, so it resets to "unknown" automatically.
    # (Unchanged behavior — still fires at creation. Confirmed with Derrick:
    # not reverted on cancel either. Crosses into the sites domain to check
    # is_home_base, so it goes through db.sites's accessor rather than
    # querying locations directly from here.)
    went_to_home_base = sites.is_location_home_base(to_location_id)
    if not went_to_home_base:
        update_charge_status(battery_id, "unknown")

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