from db.connection import get_connection

def add_location(name, is_store=False, address=None, contact_name=None, contact_phone=None, notes=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO inventory_locations (name, is_store, address, contact_name, contact_phone, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (name, is_store, address, contact_name, contact_phone, notes)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def update_location(location_id, name, is_store=False, address=None, contact_name=None, contact_phone=None, notes=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE inventory_locations
        SET name = %s, is_store = %s, address = %s, contact_name = %s, contact_phone = %s, notes = %s
        WHERE id = %s;
        """,
        (name, is_store, address, contact_name, contact_phone, notes, location_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def delete_location(location_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE inventory_locations SET is_active = false WHERE id = %s;", (location_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_all_locations():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, is_store, address, contact_name, contact_phone, notes
        FROM inventory_locations
        WHERE is_active = true
        ORDER BY is_store DESC, name;
    """)
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
