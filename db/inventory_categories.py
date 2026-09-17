from db.connection import db_cursor

def add_category(name, tracking_type, custody_type="per_job", description=None):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO inventory_categories (name, tracking_type, custody_type, description)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """,
            (name, tracking_type, custody_type, description)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id

def update_category(category_id, name, tracking_type, custody_type="per_job", description=None):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE inventory_categories
            SET name = %s, tracking_type = %s, custody_type = %s, description = %s
            WHERE id = %s;
            """,
            (name, tracking_type, custody_type, description, category_id)
        )
        conn.commit()

def delete_category(category_id):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE inventory_categories SET is_active = false WHERE id = %s;", (category_id,))
        conn.commit()

def get_all_categories():
    with db_cursor() as (conn, cur):
        cur.execute("""
            SELECT id, name, tracking_type, custody_type, description
            FROM inventory_categories
            WHERE is_active = true
            ORDER BY name;
        """)
        rows = cur.fetchall()
    return [
        {"id": r[0], "name": r[1], "tracking_type": r[2], "custody_type": r[3], "description": r[4]}
        for r in rows
    ]

def get_category_by_id(category_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT id, name, tracking_type, custody_type, description FROM inventory_categories WHERE id = %s;",
            (category_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "tracking_type": row[2], "custody_type": row[3], "description": row[4]}

def count_active_items(category_id):
    """Backs the router's tracking_type lock: a category's tracking_type may
    only change while it has zero items, since every item column's meaning
    depends on which type it was created under. Returns a count (not just a
    boolean) so the 400 error can tell the caller exactly how many items are
    blocking the change."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) FROM inventory_items WHERE category_id = %s AND is_active = true;",
            (category_id,)
        )
        count = cur.fetchone()[0]
    return count
