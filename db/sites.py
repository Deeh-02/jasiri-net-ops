from db.connection import db_cursor

def add_location(name, contact_name=None, contact_phone=None, address=None, is_home_base=False):
    with db_cursor() as (conn, cur):
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
    return new_id

def set_home_base(location_id):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE locations SET is_home_base = FALSE WHERE is_home_base = TRUE;")
        cur.execute("UPDATE locations SET is_home_base = TRUE WHERE id = %s;", (location_id,))
        conn.commit()

def update_location(location_id, name, contact_name=None, contact_phone=None, address=None, is_home_base=False):
    with db_cursor() as (conn, cur):
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

def delete_location(location_id):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE locations SET is_active = false WHERE id = %s;", (location_id,))
        conn.commit()

def get_all_locations():
    with db_cursor() as (conn, cur):
        cur.execute("""
            SELECT id, name, contact_name, contact_phone, address, is_home_base
            FROM locations
            WHERE is_active = true
            ORDER BY is_home_base DESC, name;
        """)
        rows = cur.fetchall()
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

def is_location_home_base(location_id):
    """Used by db/batteries.py's record_movement — a battery leaving home base
    resets its charge_status to 'unknown' since we lose visibility once it's
    out in the field."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT is_home_base FROM locations WHERE id = %s;", (location_id,))
        row = cur.fetchone()
    return row[0] if row else False


def set_location_online_status(location_id, is_online, stamp_confirmed):
    """Used by db/batteries.py's movement site-check actions. stamp_confirmed
    controls whether verification_confirmed_at also updates — mark_site_still_down
    deliberately leaves it stale, since nobody has confirmed the site is fine."""
    with db_cursor() as (conn, cur):
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
