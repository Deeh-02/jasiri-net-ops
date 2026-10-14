"""Revenue reporting thresholds (migration 0020). Defaults live here in
code; revenue_report_config holds only the keys someone has changed, so
deleting a row ("reset") puts the default back. Every report and the alert
rules read their numbers through load() — nothing hard-codes a threshold.
"""
import logging
from decimal import Decimal

import psycopg2

from db.connection import db_cursor

log = logging.getLogger(__name__)

# key -> (default, kind, description). kind: 'pct' (0-100), 'kes' / 'num'
# (>= 0), 'count' (whole number >= 1).
SCHEMA = {
    "flat_band_pct":           (Decimal("2"),    "pct",   "Change within ± this % counts as flat (→)"),
    "baseline_weeks":          (Decimal("6"),    "count", "Weeks of same-weekday history in the baseline median"),
    "active_site_min_daily":   (Decimal("300"),  "kes",   "Average daily KSh over the last 14 completed days for a site to count as active"),
    "pct_rank_min_revenue":    (Decimal("1000"), "kes",   "Previous-period KSh a site needs to appear in % rankings"),
    "contribution_cutoff_pct": (Decimal("70"),   "pct",   "Share of gross decline/gain the listed contributors must cover"),
    "anomaly_k":               (Decimal("3"),    "num",   "Trend band width: median ± this × MAD over 28 days"),
    "red_pct":                 (Decimal("20"),   "pct",   "Investigate: drop of at least this %"),
    "red_min_abs":             (Decimal("500"),  "kes",   "Investigate: and a drop of at least this many KSh"),
    "amber_pct":               (Decimal("10"),   "pct",   "Attention: drop of at least this %"),
    "amber_min_abs":           (Decimal("250"),  "kes",   "Attention: and a drop of at least this many KSh"),
    "green_pct":               (Decimal("15"),   "pct",   "Positive: rise of at least this %"),
    "green_min_abs":           (Decimal("300"),  "kes",   "Positive: and a rise of at least this many KSh"),
    "red_consecutive":         (Decimal("3"),    "count", "Investigate: declining this many periods in a row"),
    "amber_consecutive":       (Decimal("2"),    "count", "Attention: declining this many periods in a row"),
    "green_consecutive":       (Decimal("3"),    "count", "Positive: above baseline this many periods in a row"),
}

DEFAULTS = {key: spec[0] for key, spec in SCHEMA.items()}


def validate(key, value):
    """(Decimal, None) if fine, (None, error message) otherwise."""
    if key not in SCHEMA:
        return None, f"Unknown setting {key!r}"
    try:
        number = Decimal(str(value))
    except Exception:
        return None, "Enter a number"
    if not number.is_finite():
        return None, "Enter a number"
    kind = SCHEMA[key][1]
    if kind == "pct" and not (0 <= number <= 100):
        return None, "Must be between 0 and 100"
    if kind in ("kes", "num") and number < 0:
        return None, "Can't be negative"
    if kind == "count" and (number < 1 or number != number.to_integral_value()):
        return None, "Must be a whole number, 1 or more"
    return number, None


def load():
    """{key: Decimal} — saved value where there is one, default otherwise.
    Own connection; falls back to defaults on a database error, so a missing
    table costs custom thresholds, never the report."""
    values = dict(DEFAULTS)
    try:
        with db_cursor() as (conn, cur):
            cur.execute("SELECT key, value FROM revenue_report_config")
            for key, value in cur.fetchall():
                if key in values:
                    values[key] = Decimal(value)
    except psycopg2.Error:
        log.exception("couldn't load revenue report config — using defaults")
    return values


def save(key, value, user_id):
    number, error = validate(key, value)
    if error:
        raise ValueError(error)
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO revenue_report_config (key, value, updated_by) VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            (key, number, user_id),
        )
        conn.commit()


def reset(key):
    if key not in SCHEMA:
        raise ValueError(f"Unknown setting {key!r}")
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM revenue_report_config WHERE key = %s", (key,))
        conn.commit()
