import json

from db.connection import db_cursor, utc_iso

# Reports > SMS Status: a read-only view over alert_deliveries (written by
# db/monitoring_alerts.py for sites and db/onu_alerts.py for ONU LOS). No
# schema of its own — this file only reads.

DEFAULT_LIMIT = 200
CHANNELS = ("sms", "whatsapp", "in_app")

# What an ONU LOS row is about: the whole PON port, else whoever the ONU is
# linked to on OLT > Links, else the ONU itself.
ONU_NAME = """CASE WHEN oa.pon IS NOT NULL THEN 'PON ' || oa.pon
     ELSE COALESCE(oc.name || ' (' || oc.username || ')', oc.username,
                   'Hotspot site ' || COALESCE(oml.name, om.name),
                   'ONU ' || COALESCE(os.name, os.sn, oa.unique_external_id)) END"""


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
                   u.name, COALESCE(l.name, ms.name, 'VLAN ' || ms.vlan_id, {ONU_NAME})
            FROM alert_deliveries d
            LEFT JOIN users u ON u.id = d.user_id
            LEFT JOIN monitored_sites ms ON ms.id = d.monitored_site_id
            LEFT JOIN locations l ON l.id = ms.location_id
            LEFT JOIN onu_alerts oa ON oa.id = d.onu_alert_id
            LEFT JOIN onu_state os ON os.unique_external_id = oa.unique_external_id
            LEFT JOIN onu_customer_links ol ON ol.unique_external_id = oa.unique_external_id
            LEFT JOIN ppp_customers oc ON oc.id = ol.customer_id
            LEFT JOIN monitored_sites om ON om.id = ol.site_id
            LEFT JOIN locations oml ON oml.id = om.location_id
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
