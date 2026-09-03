from db.connection import get_connection
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain_password):
    return pwd_context.hash(plain_password)

def verify_password(plain_password, password_hash):
    return pwd_context.verify(plain_password, password_hash)

def add_user(name, email, password, phone=None, role="technician", role_id=None):
    conn = get_connection()
    cur = conn.cursor()
    password_hash = hash_password(password)
    cur.execute(
        """
        INSERT INTO users (name, email, phone, password_hash, role, role_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (name, email, phone, password_hash, role, role_id)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def get_user_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, email, phone, password_hash, role, status, role_id FROM users WHERE email = %s;",
        (email,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "phone": row[3],
            "password_hash": row[4],
            "role": row[5],
            "status": row[6],
            "role_id": row[7],
        }
    return None

def get_all_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, email, phone, role, status, created_at, role_id
        FROM users
        ORDER BY name;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "email": r[2],
            "phone": r[3],
            "role": r[4],
            "status": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "role_id": r[7],
        }
        for r in rows
    ]

def update_user(user_id, name, email, phone, role, role_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET name = %s, email = %s, phone = %s, role = %s, role_id = %s
        WHERE id = %s;
        """,
        (name, email, phone, role, role_id, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()

def deactivate_user(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = 'inactive' WHERE id = %s;", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def verify_user_password(email, plain_password):
    """Look up a user by email and check their password.
    Returns the full user record on match, None otherwise."""
    user = get_user_by_email(email)
    if user is None:
        return None
    if not verify_password(plain_password, user["password_hash"]):
        return None
    return user

def update_user_password(user_id, new_password):
    conn = get_connection()
    cur = conn.cursor()
    new_hash = hash_password(new_password)
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s;", (new_hash, user_id))
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return updated