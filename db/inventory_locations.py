from db.connection import get_connection

# add_location/update_location/delete_location were removed along with
# Milestone H — Locations has no management screen any more, so nothing
# creates/edits/deactivates a row by hand any more (see
# get_or_create_location_by_name below for how rows are created now).

def get_all_locations(is_store=None):
    conn = get_connection()
    cur = conn.cursor()
    if is_store is None:
        cur.execute("""
            SELECT id, name, is_store, address, contact_name, contact_phone, notes
            FROM inventory_locations
            WHERE is_active = true
            ORDER BY is_store DESC, name;
        """)
    else:
        cur.execute("""
            SELECT id, name, is_store, address, contact_name, contact_phone, notes
            FROM inventory_locations
            WHERE is_active = true AND is_store = %s
            ORDER BY name;
        """, (is_store,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "is_store": r[2],
            "address": r[3],
            "contact_name": r[4],
            "contact_phone": r[5],
            "notes": r[6],
        }
        for r in rows
    ]

# Locations no longer have a management screen — every free-typed location
# field (Issue/Return Materials' Site, and Add/Edit Unit's Location)
# autocompletes against existing names but must be able to create a
# brand-new one with zero pre-configuration. Case-insensitive match keeps
# "Nairobi Site" and "nairobi site" from becoming two rows just because of
# how someone typed it that day.
#
# is_store draws the same line GET /inventory/locations?is_store= already
# draws: Site (is_store=False, the default) matches/creates job sites,
# Add/Edit Unit's Location (is_store=True) matches/creates stores/
# warehouses. It's part of the match, not just the insert — without that, a
# job site and a store that happen to share a name would resolve to the
# same row, letting a Unit's Location silently point at a job site (or vice
# versa), which is exactly the ambiguity the two fields need to stay clear
# of.
def get_or_create_location_by_name(name, is_store=False):
    name = name.strip()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM inventory_locations WHERE is_active = true AND is_store = %s AND lower(name) = lower(%s) LIMIT 1;",
        (is_store, name),
    )
    row = cur.fetchone()
    if row:
        cur.close()
        conn.close()
        return row[0]

    cur.execute(
        "INSERT INTO inventory_locations (name, is_store) VALUES (%s, %s) RETURNING id;",
        (name, is_store),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

# The single location Return Materials sends stock back to, since that flow
# collects no destination from the user. Exactly one inventory_locations row
# is expected to carry is_store = true as this default — set via direct DB
# update, the same "named constant, no settings UI" precedent the 20m/14d
# thresholds use, since this is foundational setup data that changes rarely.
def get_default_store_location():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name FROM inventory_locations
        WHERE is_active = true AND is_store = true
        ORDER BY id LIMIT 1;
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return {"id": row[0], "name": row[1]} if row else None
