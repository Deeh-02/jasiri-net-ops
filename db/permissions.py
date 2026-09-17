from db.connection import db_cursor, utc_iso

def get_all_roles():
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, name, created_at FROM roles ORDER BY name;")
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "created_at": utc_iso(r[2]),
        }
        for r in rows
    ]

def get_role_by_id(role_id):
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, name, created_at FROM roles WHERE id = %s;", (role_id,))
        row = cur.fetchone()
    if row:
        return {
            "id": row[0],
            "name": row[1],
            "created_at": utc_iso(row[2]),
        }
    return None

def add_role(name):
    with db_cursor() as (conn, cur):
        cur.execute(
            "INSERT INTO roles (name) VALUES (%s) RETURNING id;",
            (name,)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id

def update_role(role_id, name):
    with db_cursor() as (conn, cur):
        cur.execute("UPDATE roles SET name = %s WHERE id = %s;", (name, role_id))
        conn.commit()

def delete_role(role_id):
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM roles WHERE id = %s;", (role_id,))
        conn.commit()

def check_role_permission(role_id, section, action):
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT allowed FROM role_permissions WHERE role_id = %s AND section = %s AND action = %s;",
            (role_id, section, action)
        )
        row = cur.fetchone()
    return bool(row and row[0])

def get_role_permissions(role_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT section, action, allowed FROM role_permissions WHERE role_id = %s;",
            (role_id,)
        )
        rows = cur.fetchall()
    return [
        {"section": r[0], "action": r[1], "allowed": r[2]}
        for r in rows
    ]

def set_role_permissions(role_id, permissions):
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM role_permissions WHERE role_id = %s;", (role_id,))
        for p in permissions:
            cur.execute(
                """
                INSERT INTO role_permissions (role_id, section, action, allowed)
                VALUES (%s, %s, %s, %s);
                """,
                (role_id, p["section"], p["action"], p["allowed"])
            )
        conn.commit()
