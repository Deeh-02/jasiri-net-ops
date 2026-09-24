import json

from db.connection import db_cursor, utc_iso

# Reports > SMS Status: a read-only view over alert_deliveries (written by
# db/monitoring_alerts.py). No schema of its own — this file only reads.

DEFAULT_LIMIT = 200
CHANNELS = ("sms", "whatsapp", "in_app")


def list_deliveries(since, until, channel=None, status=None, limit=DEFAULT_LIMIT):
    """Newest first. `since`/`until` are naive UTC datetimes (sent_at's own
    zone); `until` is exclusive."""
    where = ["d.sent_at >= %s", "d.sent_at < %s"]
    params = [since, until]
    if channel:
        where.append("d.channel = %s")
        params.append(channel)
    if status:
        where.append("d.status = %s")
        params.append(status)
    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT d.id, d.channel, d.kind, d.status, d.recipient, d.provider_response, d.sent_at,
                   u.name, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id)
            FROM alert_deliveries d
            LEFT JOIN users u ON u.id = d.user_id
            LEFT JOIN monitored_sites ms ON ms.id = d.monitored_site_id
            LEFT JOIN locations l ON l.id = ms.location_id
            WHERE {" AND ".join(where)}
            ORDER BY d.sent_at DESC, d.id DESC
            LIMIT %s
            """,
            (*params, limit),
        )
        rows = cur.fetchall()
        cur.execute(
            f"SELECT d.status, count(*) FROM alert_deliveries d WHERE {' AND '.join(where)} GROUP BY d.status",
            params,
        )
        counts = {r[0]: r[1] for r in cur.fetchall()}
    return {
        "counts": counts,
        "rows": [
            {
                "id": r[0], "channel": r[1], "kind": r[2], "status": r[3], "recipient": r[4],
                "provider_response": r[5] if not isinstance(r[5], str) else json.loads(r[5]),
                "sent_at": utc_iso(r[6]), "user_name": r[7], "site_name": r[8],
            }
            for r in rows
        ],
    }
