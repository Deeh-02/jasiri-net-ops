"""Alert sending window — the one global setting that decides WHEN site
alerts may be sent (migration 0018). Edited in Alerts > SMS Templates.

Times are East Africa Time wall-clock, same as per-site quiet hours. A
window that runs past midnight (22:00 to 06:00) works; start == end is
rejected rather than guessed at.
"""
import logging
from datetime import time

import psycopg2

from db.connection import db_cursor, now_eat, utc_iso

log = logging.getLogger(__name__)

DEFAULTS = {"enabled": False, "start": time(8, 0), "end": time(18, 0)}


def parse_time(value):
    """'HH:MM' -> datetime.time, or None if it isn't one."""
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except (AttributeError, ValueError):
        return None


def get():
    """{'enabled', 'start', 'end'} (start/end are datetime.time). Runs on
    its own connection and falls back to "always send" on a database error,
    because it's called at the start of every alert pass — a missing table
    must never be what stops an outage being reported."""
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT send_window_enabled, send_window_start, send_window_end FROM alert_settings WHERE id = 1")
            row = cur.fetchone()
    except psycopg2.Error:
        log.exception("couldn't load alert settings — sending at any hour")
        return dict(DEFAULTS)
    if row is None:
        return dict(DEFAULTS)
    return {"enabled": row[0], "start": row[1], "end": row[2]}


def is_open(settings):
    """True when alerts may be sent right now."""
    if not settings["enabled"]:
        return True
    now = now_eat().time()
    start, end = settings["start"], settings["end"]
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # wraps midnight


def get_for_editor():
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT s.send_window_enabled, s.send_window_start, s.send_window_end, s.updated_at, u.name
            FROM alert_settings s LEFT JOIN users u ON u.id = s.updated_by WHERE s.id = 1
            """
        )
        row = cur.fetchone()
    if row is None:
        return {"enabled": False, "start": "08:00", "end": "18:00", "updated_at": None, "updated_by": None}
    return {
        "enabled": row[0], "start": row[1].strftime("%H:%M"), "end": row[2].strftime("%H:%M"),
        "updated_at": utc_iso(row[3]), "updated_by": row[4],
    }


def save(enabled, start, end, user_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO alert_settings (id, send_window_enabled, send_window_start, send_window_end, updated_by)
            VALUES (1, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                send_window_enabled = EXCLUDED.send_window_enabled,
                send_window_start = EXCLUDED.send_window_start,
                send_window_end = EXCLUDED.send_window_end,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """,
            (enabled, start, end, user_id),
        )
        conn.commit()
