from db.connection import get_connection

def add_category(name, tracking_type, description=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO inventory_categories (name, tracking_type, description)
        VALUES (%s, %s, %s)
        RETURNING id;
        """,
        (name, tracking_type, description)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def update_category(category_id, name, tracking_type, description=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE inventory_categories
        SET name = %s, tracking_type = %s, description = %s
        WHERE id = %s;
        """,
        (name, tracking_type, description, category_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def delete_category(category_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE inventory_categories SET is_active = false WHERE id = %s;", (category_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_all_categories():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, tracking_type, description
        FROM inventory_categories
        WHERE is_active = true
        ORDER BY name;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "tracking_type": r[2], "description": r[3]}
        for r in rows
    ]

def get_category_by_id(category_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, tracking_type, description FROM inventory_categories WHERE id = %s;",
        (category_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "tracking_type": row[2], "description": row[3]}

def category_has_items(category_id):
    """Backs the router's tracking_type lock: a category's tracking_type may
    only change while it has zero items, since every item column's meaning
    depends on which type it was created under."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT EXISTS(SELECT 1 FROM inventory_items WHERE category_id = %s AND is_active = true);",
        (category_id,)
    )
    exists = cur.fetchone()[0]
    cur.close()
    conn.close()
    return exists
