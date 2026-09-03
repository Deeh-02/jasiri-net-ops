from db.connection import get_connection

def get_all_roles():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, created_at FROM roles ORDER BY name;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "created_at": r[2].isoformat() if r[2] else None,
        }
        for r in rows
    ]

def get_role_by_id(role_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, created_at FROM roles WHERE id = %s;", (role_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {
            "id": row[0],
            "name": row[1],
            "created_at": row[2].isoformat() if row[2] else None,
        }
    return None

def add_role(name):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO roles (name) VALUES (%s) RETURNING id;",
        (name,)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def update_role(role_id, name):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE roles SET name = %s WHERE id = %s;", (name, role_id))
    conn.commit()
    cur.close()
    conn.close()

def delete_role(role_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM roles WHERE id = %s;", (role_id,))
    conn.commit()
    cur.close()
    conn.close()

def check_role_permission(role_id, section, action):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT allowed FROM role_permissions WHERE role_id = %s AND section = %s AND action = %s;",
        (role_id, section, action)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return bool(row and row[0])

def get_role_permissions(role_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT section, action, allowed FROM role_permissions WHERE role_id = %s;",
        (role_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"section": r[0], "action": r[1], "allowed": r[2]}
        for r in rows
    ]

def set_role_permissions(role_id, permissions):
    conn = get_connection()
    cur = conn.cursor()
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
    cur.close()
    conn.close()