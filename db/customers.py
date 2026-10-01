"""PPPoE customers loaded from the billing export, and the broadcast SMS sent
to them (Alerts > Customers / Broadcast). Parsing is in db/customer_import.py;
sending one SMS is db/alert_channels.send_sms. See migration 0017 for the
tables and the reasoning behind them.
"""
import logging
from datetime import datetime, time, timedelta

from psycopg2 import errors
from psycopg2.extras import Json, execute_values

from db.alert_channels import send_sms
from db.connection import db_cursor, utc_iso
from db.customer_import import EAT_OFFSET

log = logging.getLogger(__name__)

# Which customers a broadcast reaches. "active" is the paying customer:
# account Active AND not manually disabled in the billing system.
STATUS_FILTERS = {
    "active": "c.account_status = 'Active' AND c.enabled",
    "expired": "c.account_status = 'Expired'",
    "all": "true",
}

MAX_MESSAGE_LENGTH = 480
DUPLICATE_WINDOW = timedelta(minutes=5)
# A 'sending' broadcast older than this lost its worker (server restart).
STALE_SENDING = timedelta(hours=1)


class BroadcastRefused(Exception):
    """A rule stopped the send — the message is shown to the user."""


class CustomerConflict(Exception):
    """That router already has a customer with that username."""


def list_routers():
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, name FROM ppp_routers ORDER BY id")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]


def router_exists(router_id):
    with db_cursor() as (conn, cur):
        cur.execute("SELECT 1 FROM ppp_routers WHERE id = %s", (router_id,))
        return cur.fetchone() is not None


# On re-import an EXISTING customer gets only these four fields refreshed from
# the billing export. Name and phone are never overwritten — someone may have
# corrected them here — so a differing phone is reported for review instead.
_REFRESHED = ("plan", "expiry", "account_status", "enabled")


def import_customers(router_id, customers, commit):
    """Matches on (router, username). New customers are inserted with every
    field; existing ones get only _REFRESHED updated. With commit=False
    nothing is written — the counts are what a commit would do. Customers
    already stored but absent from the file are left alone and only counted:
    the export may simply be a partial one, and nothing is ever deleted.

    "phone_differs" lists existing customers whose export phone isn't the one
    stored here, with both numbers, for the user to review."""
    with db_cursor() as (conn, cur):
        cur.execute(
            f"SELECT username, name, phone, {', '.join(_REFRESHED)} FROM ppp_customers WHERE router_id = %s",
            (router_id,),
        )
        existing = {r[0]: {"name": r[1], "phone": r[2], **dict(zip(_REFRESHED, r[3:]))} for r in cur.fetchall()}

        to_insert, to_update, unchanged, phone_differs = [], [], 0, []
        for c in customers:
            old = existing.get(c["username"])
            if old is None:
                to_insert.append(c)
                continue
            if old["phone"] != c["phone"]:
                phone_differs.append({
                    "username": c["username"], "name": old["name"], "ops_phone": old["phone"],
                    "export_phone": c["phone"] or f"{c['phone_raw'] or 'blank'} (not a valid number)",
                })
            if all(old[k] == c[k] for k in _REFRESHED):
                unchanged += 1
            else:
                to_update.append(c)

        if commit:
            if to_insert:
                execute_values(
                    cur,
                    """
                    INSERT INTO ppp_customers
                        (router_id, username, external_id, name, phone, plan, expiry, account_status, enabled)
                    VALUES %s
                    ON CONFLICT (router_id, username) DO NOTHING
                    """,
                    [
                        (router_id, c["username"], c["external_id"], c["name"], c["phone"], c["plan"],
                         c["expiry"], c["account_status"], c["enabled"])
                        for c in to_insert
                    ],
                )
            for c in to_update:
                cur.execute(
                    """
                    UPDATE ppp_customers
                    SET plan = %s, expiry = %s, account_status = %s, enabled = %s, imported_at = now()
                    WHERE router_id = %s AND username = %s
                    """,
                    (c["plan"], c["expiry"], c["account_status"], c["enabled"], router_id, c["username"]),
                )
            conn.commit()
    in_file = {c["username"] for c in customers}
    return {
        "new": len(to_insert), "updated": len(to_update), "unchanged": unchanged,
        "missing": len([u for u in existing if u not in in_file]),
        "phone_differs": phone_differs,
    }


def _expiry_from_date(day):
    """A date typed into the form means "valid through that day" in Nairobi
    time; stored as UTC like every other timestamp."""
    return datetime.combine(day, time(23, 59, 59)) - EAT_OFFSET if day else None


def add_customer(router_id, username, name, phone, plan, account_status, enabled, expiry_day):
    try:
        with db_cursor() as (conn, cur):
            cur.execute(
                """
                INSERT INTO ppp_customers (router_id, username, name, phone, plan, expiry, account_status, enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (router_id, username, name, phone, plan, _expiry_from_date(expiry_day), account_status, enabled),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
    except errors.UniqueViolation:
        raise CustomerConflict()
    return new_id


def update_customer(customer_id, name, phone, plan, account_status, enabled, expiry_day):
    """Replaces every editable field. Username and router are the import key,
    so they never change here. Returns False if there is no such customer."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE ppp_customers
            SET name = %s, phone = %s, plan = %s, expiry = %s, account_status = %s, enabled = %s
            WHERE id = %s
            """,
            (name, phone, plan, _expiry_from_date(expiry_day), account_status, enabled, customer_id),
        )
        found = cur.rowcount == 1
        conn.commit()
    return found


def list_customers(router_id=None):
    where, params = "", []
    if router_id:
        where, params = "WHERE c.router_id = %s", [router_id]
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT c.id, r.name, c.username, c.name, c.phone, c.plan, c.expiry, c.account_status, c.enabled
            FROM ppp_customers c JOIN ppp_routers r ON r.id = c.router_id
            {where}
            ORDER BY r.id, c.username
            """,
            params,
        )
        return [
            {"id": r[0], "router": r[1], "username": r[2], "name": r[3], "phone": r[4], "plan": r[5],
             "expiry": utc_iso(r[6]), "account_status": r[7], "enabled": r[8]}
            for r in cur.fetchall()
        ]


def _audience(cur, router_ids, status_filter):
    """One row per DISTINCT phone (a person with two accounts is texted once),
    plus how many matching customers have no usable phone."""
    cond = STATUS_FILTERS[status_filter]
    cur.execute(
        f"""
        SELECT DISTINCT ON (c.phone) c.id, c.phone, c.name
        FROM ppp_customers c
        WHERE c.router_id = ANY(%s) AND c.phone IS NOT NULL AND {cond}
        ORDER BY c.phone, c.id
        """,
        (router_ids,),
    )
    recipients = [{"customer_id": r[0], "phone": r[1], "name": r[2]} for r in cur.fetchall()]
    cur.execute(
        f"SELECT count(*) FROM ppp_customers c WHERE c.router_id = ANY(%s) AND c.phone IS NULL AND {cond}",
        (router_ids,),
    )
    return recipients, cur.fetchone()[0]


def audience_preview(router_ids, status_filter):
    with db_cursor() as (conn, cur):
        recipients, no_phone = _audience(cur, router_ids, status_filter)
    return {"count": len(recipients), "no_phone": no_phone}


def render_message(template, name):
    return template.replace("{{NAME}}", (name or "").strip() or "Customer")


def start_broadcast(user_id, message, router_ids, status_filter, expected_count):
    """Validates, snapshots the audience and records the broadcast. Returns
    (broadcast_id, recipients). expected_count is what the user saw in the
    preview — if the audience has changed since (a re-import in between), the
    send is refused rather than going to a different number of people."""
    with db_cursor() as (conn, cur):
        recipients, _ = _audience(cur, router_ids, status_filter)
        if not recipients:
            raise BroadcastRefused("Nobody matches that audience")
        if len(recipients) != expected_count:
            raise BroadcastRefused(
                f"The audience changed: it is now {len(recipients)} people, not {expected_count}. "
                "Preview again to confirm."
            )
        cur.execute(
            "SELECT 1 FROM customer_broadcasts WHERE message = %s AND created_at > now() - %s",
            (message, DUPLICATE_WINDOW),
        )
        if cur.fetchone():
            raise BroadcastRefused("That exact message was already sent in the last 5 minutes")
        cur.execute(
            """
            INSERT INTO customer_broadcasts (message, router_ids, status_filter, recipient_count, sent_by)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (message, router_ids, status_filter, len(recipients), user_id),
        )
        broadcast_id = cur.fetchone()[0]
        conn.commit()
    return broadcast_id, recipients


def run_broadcast(broadcast_id, message, recipients):
    """Background task: one SMS per recipient, one delivery row each, counters
    updated as it goes. send_sms never raises; anything unexpected still ends
    in state 'done' so the broadcast isn't left looking stuck."""
    sent = failed = 0
    try:
        with db_cursor() as (conn, cur):
            for r in recipients:
                ok, resp = send_sms(r["phone"], render_message(message, r["name"]))
                sent, failed = sent + ok, failed + (not ok)
                cur.execute(
                    """
                    INSERT INTO customer_broadcast_deliveries (broadcast_id, customer_id, phone, status, provider_response)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (broadcast_id, r["customer_id"], r["phone"], "sent" if ok else "failed", Json(resp)),
                )
                cur.execute(
                    "UPDATE customer_broadcasts SET sent_count = %s, failed_count = %s WHERE id = %s",
                    (sent, failed, broadcast_id),
                )
                conn.commit()
    except Exception:
        log.exception("Broadcast %s stopped early", broadcast_id)
    finally:
        with db_cursor() as (conn, cur):
            cur.execute("UPDATE customer_broadcasts SET state = 'done' WHERE id = %s", (broadcast_id,))
            conn.commit()


def list_broadcasts(limit=30):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT b.id, b.message, b.router_ids, b.status_filter, b.recipient_count, b.sent_count,
                   b.failed_count,
                   CASE WHEN b.state = 'sending' AND b.created_at < now() - %s THEN 'interrupted' ELSE b.state END,
                   u.name, b.created_at
            FROM customer_broadcasts b LEFT JOIN users u ON u.id = b.sent_by
            ORDER BY b.id DESC LIMIT %s
            """,
            (STALE_SENDING, limit),
        )
        return [
            {"id": r[0], "message": r[1], "router_ids": r[2], "status_filter": r[3],
             "recipient_count": r[4], "sent_count": r[5], "failed_count": r[6], "state": r[7],
             "sent_by": r[8], "created_at": utc_iso(r[9])}
            for r in cur.fetchall()
        ]
