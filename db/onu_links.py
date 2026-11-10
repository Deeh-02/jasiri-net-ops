"""Phase 6B: which customer each ONU belongs to (`onu_customer_links`).

OPS only ever SUGGESTS a link; a person confirms every one (the scope's rule:
"the system never links silently"). Suggestions come from two clues:
  1. pppoe_username: the ONU's PPPoE username in SmartOLT equals a customer's
     username (case ignored). On 2026-11-11 this matched 32 of 61 ONUs, each
     to exactly one customer. Only these, and only when unambiguous, can be
     accepted in bulk.
  2. name: a word of the ONU's name equals a customer's username (Mark_JS45
     -> JS45). Weaker; always accepted one by one.
A customer or ONU that shows up in more than one suggestion is marked
ambiguous and is never bulk-accepted.

Linking changes nothing in SmartOLT, the customer list or billing. Every
function that reads the links copes with the table not existing yet
(migration 0033 not run): the rest of the app keeps working without them.
"""
import logging
import re
from datetime import datetime, timezone

import psycopg2
from psycopg2 import errors as pg_errors

from db.connection import db_cursor, utc_iso
from db.smartolt import STATUS_STALE, _iso, _num

log = logging.getLogger(__name__)
TOKEN = re.compile(r"[\s_\-.,/()]+")


class LinkError(Exception):
    pass


# ---- Matching (pure) ----

def suggest(onus, customers, links):
    """onus: [{id, name, pppoe_username, ...}]; customers: [{id, username, ...}];
    links: [{onu_id, customer_id}]. Returns suggestions, best first."""
    linked_onus = {l["onu_id"] for l in links}
    linked_customers = {l["customer_id"] for l in links if l.get("customer_id")}
    by_user = {}
    for c in customers:
        if c["id"] in linked_customers or not c.get("username"):
            continue
        by_user.setdefault(c["username"].strip().lower(), []).append(c)

    out = []
    for o in onus:
        if o["id"] in linked_onus:
            continue
        found, how = [], None
        user = (o.get("pppoe_username") or "").strip().lower()
        if user and user in by_user:
            found, how = by_user[user], "pppoe_username"
        else:
            for token in TOKEN.split((o.get("name") or "").lower()):
                if token and token in by_user:
                    found += [c for c in by_user[token] if c not in found]
            how = "name" if found else None
        for c in found:
            out.append({"onu": o, "customer": c, "matched_by": how, "ambiguous": len(found) > 1})

    # The same customer suggested for two ONUs: neither is safe to bulk-accept.
    per_customer = {}
    for s in out:
        per_customer[s["customer"]["id"]] = per_customer.get(s["customer"]["id"], 0) + 1
    for s in out:
        if per_customer[s["customer"]["id"]] > 1:
            s["ambiguous"] = True
    out.sort(key=lambda s: (s["matched_by"] != "pppoe_username", s["ambiguous"], (s["onu"].get("name") or "").lower()))
    return out


def bulk_ok(s):
    return s["matched_by"] == "pppoe_username" and not s["ambiguous"]


# ---- Reading ----

def _onus(cur):
    cur.execute("""SELECT unique_external_id, sn, name, status, last_status_change, signal_1310_dbm, signal_1490_dbm,
                          signal_quality, board, port, onu, details, status_polled_at
                   FROM onu_state""")
    rows = []
    for r in cur.fetchall():
        d = r[11] or {}
        rows.append({"id": r[0], "sn": r[1], "name": r[2], "status": r[3], "last_status_change": _iso(r[4]),
                     "signal_1310_dbm": _num(r[5]), "signal_1490_dbm": _num(r[6]), "signal_quality": r[7],
                     "location": "/".join(str(v) for v in (r[8], r[9], r[10]) if v is not None) or None,
                     "zone": d.get("zone_name"), "wan_mode": d.get("wan_mode"),
                     "pppoe_username": d.get("pppoe_username"), "status_polled_at": r[12]})
    return rows


def _customers(cur):
    cur.execute("SELECT id, username, name, plan, expiry, account_status FROM ppp_customers")
    return [{"id": r[0], "username": r[1], "name": r[2], "plan": r[3], "expiry": utc_iso(r[4]), "account_status": r[5]}
            for r in cur.fetchall()]


def _links(cur):
    cur.execute("""SELECT l.unique_external_id, l.customer_id, l.not_a_customer, l.matched_by, u.name, l.confirmed_at
                   FROM onu_customer_links l LEFT JOIN users u ON u.id = l.confirmed_by""")
    return [{"onu_id": r[0], "customer_id": r[1], "not_a_customer": r[2], "matched_by": r[3], "by": r[4],
             "at": _iso(r[5])} for r in cur.fetchall()]


def _public(o):
    return {k: v for k, v in o.items() if k != "status_polled_at"}


def overview():
    """Everything the OLT page's Customers tab shows."""
    with db_cursor() as (_, cur):
        onus, customers, links = _onus(cur), _customers(cur), _links(cur)
    onu_by_id = {o["id"]: o for o in onus}
    cust_by_id = {c["id"]: c for c in customers}
    suggestions = suggest(onus, customers, links)
    suggested_onus = {s["onu"]["id"] for s in suggestions}
    linked_onus = {l["onu_id"] for l in links}
    linked_customers = {l["customer_id"] for l in links if l["customer_id"]}
    return {
        "suggestions": [{"onu": _public(s["onu"]), "customer": s["customer"], "matched_by": s["matched_by"],
                         "ambiguous": s["ambiguous"], "bulk_ok": bulk_ok(s)} for s in suggestions],
        "bulk_count": sum(1 for s in suggestions if bulk_ok(s)),
        # Unlinked ONUs with no suggestion at all (those with one are above).
        "onus_without_customer": [_public(o) for o in sorted(onus, key=lambda o: (o["name"] or "").lower())
                                  if o["id"] not in linked_onus and o["id"] not in suggested_onus],
        "customers_without_onu": sorted((c for c in customers if c["id"] not in linked_customers
                                         and c["account_status"] == "Active"),
                                        key=lambda c: (c["name"] or c["username"] or "").lower()),
        "linked": sorted(({**l, "onu": _public(onu_by_id[l["onu_id"]]) if l["onu_id"] in onu_by_id else None,
                           "customer": cust_by_id.get(l["customer_id"])} for l in links if not l["not_a_customer"]),
                         key=lambda l: ((l["customer"] or {}).get("name") or "").lower()),
        "not_customers": [{**l, "onu": _public(onu_by_id[l["onu_id"]]) if l["onu_id"] in onu_by_id else None}
                          for l in links if l["not_a_customer"]],
        "unlinked_onus": [{"id": o["id"], "name": o["name"], "sn": o["sn"]}
                          for o in sorted(onus, key=lambda o: (o["name"] or "").lower()) if o["id"] not in linked_onus],
    }


def customer_onus(customer_ids=None):
    """customer_id -> that customer's ONU (status, signal, last change), for the
    Customers page and the installation report. {} when 0033 isn't run yet."""
    try:
        with db_cursor() as (_, cur):
            cur.execute("""SELECT l.customer_id, s.unique_external_id, s.name, s.status, s.last_status_change,
                                  s.signal_1310_dbm, s.signal_1490_dbm, s.signal_quality, s.status_polled_at, s.signal_polled_at
                           FROM onu_customer_links l JOIN onu_state s ON s.unique_external_id = l.unique_external_id
                           WHERE l.customer_id IS NOT NULL AND (%s::int[] IS NULL OR l.customer_id = ANY(%s::int[]))""",
                        (customer_ids, customer_ids))
            rows = cur.fetchall()
    except (pg_errors.UndefinedTable, pg_errors.UndefinedColumn):
        return {}
    except psycopg2.Error:
        # Extra information only: the Customers page and the customer picker
        # must keep working without it.
        log.warning("onu_links: couldn't read customers' ONUs", exc_info=True)
        return {}
    now = datetime.now(timezone.utc)
    return {r[0]: {"id": r[1], "name": r[2], "status": r[3], "last_status_change": _iso(r[4]),
                   "signal_1310_dbm": _num(r[5]), "signal_1490_dbm": _num(r[6]), "signal_quality": r[7],
                   "signal_at": _iso(r[9]),
                   "stale": r[8] is None or now - r[8] > STATUS_STALE}
            for r in rows}


def onu_customer(onu_id):
    """The customer linked to one ONU, for the View page, or None."""
    try:
        with db_cursor() as (_, cur):
            cur.execute("""SELECT l.not_a_customer, c.id, c.username, c.name, c.plan, c.expiry, c.account_status
                           FROM onu_customer_links l LEFT JOIN ppp_customers c ON c.id = l.customer_id
                           WHERE l.unique_external_id = %s""", (onu_id,))
            r = cur.fetchone()
    except pg_errors.UndefinedTable:
        return None
    if not r:
        return None
    if r[0]:
        return {"not_a_customer": True}
    return {"id": r[1], "username": r[2], "name": r[3], "plan": r[4], "expiry": utc_iso(r[5]), "account_status": r[6]}


# ---- Writing (admin only; the router checks) ----

def link(onu_id, customer_id, matched_by, user_id):
    if matched_by not in ("pppoe_username", "name", "manual"):
        matched_by = "manual"
    with db_cursor() as (conn, cur):
        cur.execute("SELECT 1 FROM onu_state WHERE unique_external_id = %s", (onu_id,))
        if not cur.fetchone():
            raise LinkError("That ONU isn't in OPS's list.")
        cur.execute("SELECT 1 FROM ppp_customers WHERE id = %s", (customer_id,))
        if not cur.fetchone():
            raise LinkError("That customer doesn't exist.")
        try:
            cur.execute("""INSERT INTO onu_customer_links (unique_external_id, customer_id, matched_by, confirmed_by)
                           VALUES (%s, %s, %s, %s)""", (onu_id, customer_id, matched_by, user_id))
        except pg_errors.UniqueViolation:
            conn.rollback()
            raise LinkError("That ONU or that customer is already linked. Unlink it first.")
        conn.commit()


def mark_not_customer(onu_id, user_id):
    with db_cursor() as (conn, cur):
        cur.execute("SELECT 1 FROM onu_state WHERE unique_external_id = %s", (onu_id,))
        if not cur.fetchone():
            raise LinkError("That ONU isn't in OPS's list.")
        try:
            cur.execute("""INSERT INTO onu_customer_links (unique_external_id, not_a_customer, matched_by, confirmed_by)
                           VALUES (%s, true, 'manual', %s)""", (onu_id, user_id))
        except pg_errors.UniqueViolation:
            conn.rollback()
            raise LinkError("That ONU is already linked. Unlink it first.")
        conn.commit()


def unlink(onu_id):
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM onu_customer_links WHERE unique_external_id = %s", (onu_id,))
        removed = cur.rowcount
        conn.commit()
    if not removed:
        raise LinkError("That ONU isn't linked.")


def accept_username_matches(user_id):
    """Links every unambiguous PPPoE-username suggestion, recomputed here (not
    taken from the browser), in one transaction. Returns how many."""
    with db_cursor() as (conn, cur):
        cur.execute("LOCK TABLE onu_customer_links IN SHARE ROW EXCLUSIVE MODE")
        picks = [s for s in suggest(_onus(cur), _customers(cur), _links(cur)) if bulk_ok(s)]
        for s in picks:
            cur.execute("""INSERT INTO onu_customer_links (unique_external_id, customer_id, matched_by, confirmed_by)
                           VALUES (%s, %s, 'pppoe_username', %s)""", (s["onu"]["id"], s["customer"]["id"], user_id))
        conn.commit()
    return len(picks)
