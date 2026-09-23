"""Editable alert message text (Alerts > SMS Templates in the app).

Each alert kind has a DEFAULT here in code; a row in alert_message_templates
(migration 0016) overrides it, and deleting that row ("Reset to default")
goes back to the code default. So an untouched install needs no rows at
all, and a default improved in code reaches everyone who never customised
that message.

The same rendered text goes out on every channel — SMS, WhatsApp and the
in-app bell — so editing a template here changes all three.

PLACEHOLDERS are {{NAME}} tokens, filled in by db/monitoring_alerts.py at
send time. Each template only accepts its own list — saving one with an
unknown or misspelt token is rejected rather than letting "{{SITE_NAM}}"
reach a phone. There is deliberately no revenue placeholder anywhere:
PHASES.md 4.4 keeps revenue figures out of every alert body, and revenue
only ever affects the ORDER sites appear in within BATTERY_PLAN / PRIORITY.
"""
import logging
import re

import psycopg2

from db.connection import db_cursor, utc_iso

log = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
MAX_LENGTH = 1000

# Insertion order is the order the editor page lists them in.
TEMPLATES = {
    "down": {
        "label": "Site goes down",
        "description": "First alert, sent once a site has been offline for 5 minutes.",
        "default": "⚠ {{SITE_NAME}} is DOWN, {{PEOPLE_ONLINE}} people online at the time. Down since {{DOWN_SINCE}} EAT.",
        "placeholders": {
            "SITE_NAME": "Site name",
            "PEOPLE_ONLINE": "People connected when it dropped",
            "DOWN_SINCE": "Time it went down (HH:MM)",
        },
        "sample": {"SITE_NAME": "Kasarani Site", "PEOPLE_ONLINE": "14", "DOWN_SINCE": "17:53"},
    },
    "still_down": {
        "label": "Site still down",
        "description": "Reminder at 15 min, 30 min, 1 h, then every 30–60 min up to 12 h. Exactly "
                       "one of {{BATTERY_RECOMMENDED}} / {{BATTERY_NONE}} is filled in, and only at "
                       "the 15-minute reminder — the other always stays empty that send.",
        "default": "⚠ {{SITE_NAME}} is STILL DOWN — {{DURATION}} so far (since {{DOWN_SINCE}} EAT). {{BATTERY_RECOMMENDED}}{{BATTERY_NONE}}",
        "placeholders": {
            "SITE_NAME": "Site name",
            "DURATION": "How long it has been down",
            "DOWN_SINCE": "Time it went down (HH:MM)",
            "BATTERY_RECOMMENDED": "Battery recommendation sentence (15-min reminder only, when one is available)",
            "BATTERY_NONE": "No-battery-available sentence (15-min reminder only, when none is available)",
        },
        "sample": {"SITE_NAME": "Kasarani Site", "DURATION": "15m", "DOWN_SINCE": "17:53",
                   "BATTERY_RECOMMENDED": "Recommended battery: BAT-014 (priority 1 of 3).", "BATTERY_NONE": ""},
    },
    "battery_recommended": {
        "label": "Battery recommendation",
        "description": "Fills {{BATTERY_RECOMMENDED}} in the message above when a charged battery is "
                       "at base. {{PRIORITY}} only appears when several sites are competing for batteries.",
        "default": "Recommended battery: {{BATTERY}} {{PRIORITY}}.",
        "placeholders": {
            "BATTERY": "Battery number",
            "PRIORITY": "e.g. (priority 1 of 3)",
        },
        "sample": {"BATTERY": "BAT-014", "PRIORITY": "(priority 1 of 3)"},
    },
    "battery_none": {
        "label": "No battery available",
        "description": "Fills {{BATTERY_NONE}} in the message above when no charged battery is at base.",
        "default": "No charged battery currently available {{PRIORITY}}.",
        "placeholders": {
            "PRIORITY": "e.g. (priority 2 of 3)",
        },
        "sample": {"PRIORITY": "(priority 2 of 3)"},
    },
    "recovered": {
        "label": "Site back online",
        "description": "Sent once when a site that was alerted on comes back.",
        "default": "✅ {{SITE_NAME}} is back ONLINE. Was down for {{DURATION}}.",
        "placeholders": {
            "SITE_NAME": "Site name",
            "DURATION": "How long it was down",
        },
        "sample": {"SITE_NAME": "Kasarani Site", "DURATION": "1h 23m"},
    },
    "flapping": {
        "label": "Site unstable (flapping)",
        "description": "Sent once when a site keeps dropping and reconnecting.",
        "default": "{{SITE_NAME}} unstable — {{FLAP_COUNT}} state changes in {{WINDOW_MINUTES}} min, may need a physical check.",
        "placeholders": {
            "SITE_NAME": "Site name",
            "FLAP_COUNT": "Number of drops/reconnects",
            "WINDOW_MINUTES": "Time window in minutes",
        },
        "sample": {"SITE_NAME": "Ruiru Site", "FLAP_COUNT": "5", "WINDOW_MINUTES": "15"},
    },
    "mass_down": {
        "label": "Many sites down at once",
        "description": "Replaces the individual alert above whenever 2 or more sites are down at the "
                       "same moment — one message per person either way, no cause is guessed at. "
                       "{{BATTERY_PLAN}} says which sites get batteries first: most people online "
                       "first, then highest revenue as the tiebreak (revenue itself is never shown).",
        "default": "⚠ {{SITE_COUNT}} sites down at once. {{BATTERY_PLAN}}",
        "placeholders": {
            "SITE_COUNT": "Number of sites currently down",
            "SITE_NAMES": "Site names, in priority order",
            "BATTERY_PLAN": "Which sites get which battery, in priority order",
        },
        "sample": {
            "SITE_COUNT": "3",
            "SITE_NAMES": "Kasarani Site, Thika Site, Ruiru Site",
            "BATTERY_PLAN": "Send batteries: 1. Kasarani Site (BAT-014), 2. Thika Site (BAT-007). "
                            "Next if more free up: 3. Ruiru Site.",
        },
    },
}


def render(body, values):
    """Fills every {{TOKEN}}, then tidies the whitespace an empty token
    leaves behind — "...EAT). {{BATTERY_RECOMMENDED}}{{BATTERY_NONE}}" with
    both empty, or "BAT-014 {{PRIORITY}}." with no priority, shouldn't send
    a trailing space or a " ." to someone's phone."""
    text = PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1).upper(), "")), body)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?)])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def validate(kind, body):
    """Returns an error message, or None if the body is fine to save."""
    if not body or not body.strip():
        return "Message can't be empty."
    if len(body) > MAX_LENGTH:
        return f"Message is too long (max {MAX_LENGTH} characters)."
    allowed = TEMPLATES[kind]["placeholders"]
    unknown = sorted({m.group(1) for m in PLACEHOLDER_RE.finditer(body) if m.group(1).upper() not in allowed})
    if unknown:
        names = ", ".join("{{" + u + "}}" for u in unknown)
        return f"Unknown placeholder {names}. This message can use: " + ", ".join("{{" + p + "}}" for p in allowed)
    return None


def load_all():
    """{kind: body} for every kind — the saved override where there is one,
    the code default otherwise. Runs on its own connection, and falls back
    to defaults on a database error, because it's called at the start of
    every alert pass: a missing table (say, migration 0016 not yet applied
    after a deploy) must cost custom wording, never the alert itself."""
    bodies = {kind: t["default"] for kind, t in TEMPLATES.items()}
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT kind, body FROM alert_message_templates")
            for kind, body in cur.fetchall():
                if kind in bodies:
                    bodies[kind] = body
    except psycopg2.Error:
        log.exception("couldn't load custom alert templates — sending with defaults")
    return bodies


def list_for_editor():
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT t.kind, t.body, t.updated_at, u.name
            FROM alert_message_templates t LEFT JOIN users u ON u.id = t.updated_by
            """
        )
        saved = {r[0]: {"body": r[1], "updated_at": utc_iso(r[2]), "updated_by": r[3]} for r in cur.fetchall()}
    result = []
    for kind, t in TEMPLATES.items():
        custom = saved.get(kind)
        result.append({
            "kind": kind,
            "label": t["label"],
            "description": t["description"],
            "body": custom["body"] if custom else t["default"],
            "default": t["default"],
            "is_custom": custom is not None,
            "updated_at": custom["updated_at"] if custom else None,
            "updated_by": custom["updated_by"] if custom else None,
            "placeholders": [{"name": name, "hint": hint} for name, hint in t["placeholders"].items()],
            "sample": t["sample"],
        })
    return result


def save(kind, body, user_id):
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO alert_message_templates (kind, body, updated_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (kind) DO UPDATE
                SET body = EXCLUDED.body, updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            (kind, body, user_id),
        )
        conn.commit()


def reset(kind):
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM alert_message_templates WHERE kind = %s", (kind,))
        conn.commit()
