from datetime import datetime
from db.connection import get_connection

def add_location(name, contact_name=None, contact_phone=None, address=None, is_home_base=False):
    conn = get_connection()
    cur = conn.cursor()
    if is_home_base:
        cur.execute("UPDATE locations SET is_home_base = FALSE WHERE is_home_base = TRUE;")
    cur.execute(
        """
        INSERT INTO locations (name, contact_name, contact_phone, address, is_home_base)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (name, contact_name, contact_phone, address, is_home_base)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def set_home_base(location_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE locations SET is_home_base = FALSE WHERE is_home_base = TRUE;")
    cur.execute("UPDATE locations SET is_home_base = TRUE WHERE id = %s;", (location_id,))
    conn.commit()
    cur.close()
    conn.close()

def update_location(location_id, name, contact_name=None, contact_phone=None, address=None, is_home_base=False):
    conn = get_connection()
    cur = conn.cursor()
    if is_home_base:
        cur.execute("UPDATE locations SET is_home_base = FALSE WHERE is_home_base = TRUE;")
    cur.execute(
        """
        UPDATE locations
        SET name = %s, contact_name = %s, contact_phone = %s, address = %s, is_home_base = %s
        WHERE id = %s;
        """,
        (name, contact_name, contact_phone, address, is_home_base, location_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def delete_location(location_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE locations SET is_active = false WHERE id = %s;", (location_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_all_locations():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, contact_name, contact_phone, address, is_home_base
        FROM locations
        WHERE is_active = true
        ORDER BY is_home_base DESC, name;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "contact_name": r[2],
            "contact_phone": r[3],
            "address": r[4],
            "is_home_base": r[5],
        }
        for r in rows
    ]

def confirm_site(location_id, is_online):
    """One-tap check-in from the Check Sites list — now records the actual
    online/offline state, not just that someone looked."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE locations SET is_online = %s, verification_confirmed_at = NOW() WHERE id = %s;",
        (is_online, location_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_sites_with_verification_status():
    """is_online is the real, persistent state of the site (only changes when
    someone explicitly answers online/offline, from here or from a movement's
    site-check). needs_check is just the hourly nag, derived on read by
    comparing verification_confirmed_at's hour to the current hour — separate
    concept from the actual online/offline value."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, is_online, verification_confirmed_at
        FROM locations
        WHERE is_active = true
        ORDER BY name;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    now = datetime.now()
    result = []
    for r in rows:
        confirmed_at = r[3]
        needs_check = not (
            confirmed_at is not None
            and confirmed_at.date() == now.date()
            and confirmed_at.hour == now.hour
        )
        result.append({
            "id": r[0],
            "name": r[1],
            "is_online": r[2],
            "needs_check": needs_check,
            "verification_confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
        })
    return result

def get_unconfirmed_site_count():
    """Outside the 8am-8pm active window, nothing is flagged — badge shows 0."""
    now = datetime.now()
    if not (8 <= now.hour < 20):
        return 0
    sites = get_sites_with_verification_status()
    return sum(1 for s in sites if s["needs_check"])

def is_location_home_base(location_id):
    """Used by db/batteries.py's record_movement — a battery leaving home base
    resets its charge_status to 'unknown' since we lose visibility once it's
    out in the field."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_home_base FROM locations WHERE id = %s;", (location_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else False


def set_location_online_status(location_id, is_online, stamp_confirmed):
    """Used by db/batteries.py's movement site-check actions. stamp_confirmed
    controls whether verification_confirmed_at also updates — mark_site_still_down
    deliberately leaves it stale so the hourly check keeps flagging the site
    until someone reports it back online."""
    conn = get_connection()
    cur = conn.cursor()
    if stamp_confirmed:
        cur.execute(
            "UPDATE locations SET is_online = %s, verification_confirmed_at = NOW() WHERE id = %s;",
            (is_online, location_id)
        )
    else:
        cur.execute(
            "UPDATE locations SET is_online = %s WHERE id = %s;",
            (is_online, location_id)
        )
    conn.commit()
    cur.close()
    conn.close()