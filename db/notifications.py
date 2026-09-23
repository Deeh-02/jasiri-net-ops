from db.connection import db_cursor, utc_iso

# Generic in-app notification inbox — see migration 0012's header. Phase
# 4.8 (monitoring alerts) is the first writer; this module has no
# monitoring-specific knowledge, so a future phase can call create()
# without touching this file.

# How many rows the bell dropdown shows. Old ones are never deleted (they
# are a small audit trail, same reasoning as alert_deliveries) — just not
# fetched by default.
DEFAULT_LIST_LIMIT = 30


def create(user_id, type_, title, body, link=None):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO notifications (user_id, type, title, body, link)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, type_, title, body, link),
        )
        notif_id = cur.fetchone()[0]
        conn.commit()
    return notif_id


def list_for_user(user_id, limit=DEFAULT_LIST_LIMIT):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT id, type, title, body, link, read_at, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0], "type": r[1], "title": r[2], "body": r[3], "link": r[4],
            "read": r[5] is not None, "created_at": utc_iso(r[6]),
        }
        for r in rows
    ]


def unread_count(user_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            "SELECT count(*) FROM notifications WHERE user_id = %s AND read_at IS NULL",
            (user_id,),
        )
        return cur.fetchone()[0]


def mark_read(user_id, notif_id):
    """False if that notification isn't this user's — a 404, not someone
    else's inbox leaking through an off-by-one id."""
    with db_cursor() as (conn, cur):
        cur.execute(
            "UPDATE notifications SET read_at = now() WHERE id = %s AND user_id = %s AND read_at IS NULL",
            (notif_id, user_id),
        )
        found = cur.rowcount > 0
        conn.commit()
    return found


def mark_all_read(user_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            "UPDATE notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL",
            (user_id,),
        )
        conn.commit()
