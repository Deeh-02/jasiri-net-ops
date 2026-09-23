"""Phase 4.9 — reconciling the human confirm-online answer (movements
domain, entirely unchanged — see routers/batteries.py) against what
monitoring itself last saw for that site.

NO ISOLATION AMENDMENT NEEDED, unlike Task 3's battery read (see
migration 0014's header for the full reasoning). This file reads only
monitoring's own tables (monitored_sites, site_status_log, locations by
location_id — all already allowed) and writes site_confirmations, which
references monitored_sites and users — the same already-precedented
direction as alert_deliveries.user_id (0012). It never touches
battery_movements or writes to locations.

THE PREFILL ITSELF lives entirely on the frontend: movements.js already
has the arriving movement's `arrived_at` in hand, and fetches the existing
GET /monitoring/status (unchanged) to read each site's current state —
composed client-side, same pattern the Status page already uses for
revenue/sessions. Nothing new was needed on the read side for that; this
module is only the WRITE side, called once per human tap.
"""
from db.connection import db_cursor
from db.monitoring_alerts import load_recipients
from db.notifications import create as create_notification


def record_confirmation(site_id, human_says_online, confirmed_by):
    """Compares the human's answer to monitoring's own current state for
    this site, stores both (PHASES.md 4.9: "both answers stored"), and —
    a mismatch is exactly the "finding" PHASES.md calls out — raises one
    in-app notification per subscribed recipient. Reuses the same
    recipient list as down/recovered/flapping alerts (sites:receive_alerts)
    rather than inventing a second one; SMS/WhatsApp are deliberately not
    sent for this — a confirm-online mismatch is worth a look, not a page.

    Returns None if site_id isn't a real monitored site."""
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id), st.state
            FROM monitored_sites ms
            LEFT JOIN locations l ON l.id = ms.location_id
            LEFT JOIN LATERAL (
                SELECT state FROM site_status_log
                WHERE monitored_site_id = ms.id
                ORDER BY received_at DESC, id DESC LIMIT 1
            ) st ON true
            WHERE ms.id = %s
            """,
            (site_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        name, state = row

        # Only a plain online/offline assertion counts as an answer to
        # compare against. 'unknown'/'flapping'/no row yet all mean
        # "monitoring has nothing to say either way" — Correction 6 in
        # PHASES.md is exactly why an 'activity' site's silence is never
        # treated as proof of an outage, and the same caution applies here:
        # collapsing "no data" into "agrees" or "disagrees" would fabricate
        # a finding (or a false all-clear) monitoring never actually made.
        monitoring_says_online = {"online": True, "offline": False}.get(state)
        agrees = (monitoring_says_online == human_says_online) if monitoring_says_online is not None else None

        cur.execute(
            """
            INSERT INTO site_confirmations
                (monitored_site_id, human_says_online, monitoring_says_online, agrees, confirmed_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (site_id, human_says_online, monitoring_says_online, agrees, confirmed_by),
        )
        confirmation_id = cur.fetchone()[0]

        recipients = load_recipients(cur) if agrees is False else []
        conn.commit()

    if recipients:
        human_word = "online" if human_says_online else "still down"
        monitoring_word = "online" if monitoring_says_online else "down"
        title = f"{name}: confirm-online disagrees with monitoring"
        body = f"A technician says {name} is {human_word}, but monitoring last saw it {monitoring_word}. Worth a second look."
        for r in recipients:
            if r["in_app"]:
                create_notification(r["id"], "site_confirmation_mismatch", title, body, link=f"site-detail/{site_id}")

    return {"id": confirmation_id, "monitoring_says_online": monitoring_says_online, "agrees": agrees}
