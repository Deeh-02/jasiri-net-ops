"""Revenue reporting, alerts — runs the §6 rules (db/revenue_logic.evaluate_day)
over every site for one completed day, and keeps the result.

The day evaluated is a COMPLETED EAT day, compared with the same weekday
last week — the report header's default. Reads of a past day come from
revenue_site_status (migration 0021) when it's there, so that day reads back
as it was judged; if the table or the row is missing the day is evaluated
live from the frozen revenue tables, which gives the same answer unless a
threshold has been changed since.

Read-only apart from the one write in store_day(), which only ever touches
revenue_site_status.
"""
import json
import logging
from datetime import timedelta
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values

from db import revenue_config
from db.connection import db_cursor, now_eat
from db.revenue_logic import AMBER, GREEN, RED, evaluate_day, format_kes, to_eat_naive

log = logging.getLogger(__name__)

SERIES_LOOKBACK_DAYS = 120
_ORDER = {RED: 0, AMBER: 1, GREEN: 2, None: 3}


def _plain(value):
    """JSON-safe copy: Decimals -> numbers, dates -> ISO strings."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def last_completed_day(now=None):
    return to_eat_naive(now or now_eat()).date() - timedelta(days=1)


def evaluate_live(cur, day, cfg, hist, series_by_site=None):
    """{site_id: evaluate_day result} for every site that has a row on `day`.
    Pass series_by_site to reuse a series already fetched."""
    from db.revenue_report import _site_series  # same query the site table uses
    if series_by_site is None:
        series_by_site = _site_series(cur, max(hist, day - timedelta(days=SERIES_LOOKBACK_DAYS)), day)
    return {sid: evaluate_day(s, day, hist, cfg) for sid, s in series_by_site.items() if day in s}


def _shape(site_id, name, result):
    return {
        "site_id": site_id, "name": name, "status": result["status"],
        "value": _plain(result["value"]),
        "display": {"value": None if result["value"] is None else format_kes(result["value"])},
        "alerts": [{"rule_id": a["rule_id"], "severity": a["severity"], "text": a["text"],
                    "values": _plain(a["values"])} for a in result["alerts"]],
    }


def _size(alert):
    """The KSh figure an alert is about, signed (losses negative)."""
    v = alert["values"]
    for key, sign in (("abs_change", 1), ("below_baseline", -1), ("avg_14d", -1)):
        if v.get(key) is not None:
            return sign * v[key]
    return 0


def _sorted(sites):
    # Red then amber then green; within a level the biggest KSh movement first.
    def key(s):
        sizes = [_size(a) for a in s["alerts"]] or [0]
        biggest = max(sizes) if s["status"] == GREEN else min(sizes)
        return (_ORDER[s["status"]], -biggest if s["status"] == GREEN else biggest, s["name"])
    return sorted(sites, key=key)


def _counts(sites):
    return {k: sum(1 for s in sites if s["status"] == k) for k in (RED, AMBER, GREEN)}


def report(day=None, now=None):
    """The alerts for one completed day: {day, source, counts, sites} with
    only the sites that have a status, worst first."""
    from db.revenue_report import _site_names, data_as_of, history_start
    now = now or now_eat()
    today = to_eat_naive(now).date()
    day = day or last_completed_day(now)
    if day >= today:
        raise ValueError("Alerts are judged on completed days; pick yesterday or earlier")
    cfg = revenue_config.load()

    with db_cursor() as (conn, cur):
        hist = history_start(cur)
        as_of = data_as_of(cur)
        if hist is None or day < hist:
            return {"day": day.isoformat(), "source": "none", "as_of": as_of, "counts": _counts([]), "sites": [],
                    "basis": "No revenue history for this day"}

        stored = None
        if day <= today - timedelta(days=2):  # frozen: prefer what was recorded
            try:
                cur.execute(
                    "SELECT site_id, status, alerts, value FROM revenue_site_status WHERE date = %s", (day,))
                stored = cur.fetchall() or None
            except psycopg2.Error:
                conn.rollback()
                stored = None

        if stored:
            names = _site_names(cur, [r[0] for r in stored])
            sites = [{"site_id": sid, "name": names.get(sid, f"Site {sid}"), "status": status,
                      "value": float(value) if value is not None else None,
                      "display": {"value": None if value is None else format_kes(value)},
                      "alerts": alerts} for sid, status, alerts, value in stored]
            source = "stored"
        else:
            results = evaluate_live(cur, day, cfg, hist)
            names = _site_names(cur, results.keys())
            sites = [_shape(sid, names.get(sid, f"Site {sid}"), r) for sid, r in results.items()]
            source = "live"

    flagged = _sorted([s for s in sites if s["status"]])
    return {
        "day": day.isoformat(), "day_label": f"{day:%a %d/%m/%Y}", "source": source, "as_of": as_of,
        "counts": _counts(flagged), "sites": flagged,
        "basis": f"{day:%a %d/%m/%Y} against the same weekday last week, and each site's own baseline "
                 f"(median of the last {int(cfg['baseline_weeks'])} {day:%A}s)",
    }


def store_day(day=None, now=None):
    """Evaluates `day` (default: yesterday) for every site and records one
    row each. Replaces that day's rows, so re-judging after a threshold
    change is one call. Returns the number of rows, or 0 if the table isn't
    there yet. Never raises — it runs inside the refresh job."""
    from db.revenue_report import history_start
    try:
        now = now or now_eat()
        day = day or last_completed_day(now)
        cfg = revenue_config.load()
        snapshot = json.dumps({k: float(v) for k, v in cfg.items()})
        with db_cursor() as (conn, cur):
            hist = history_start(cur)
            if hist is None or day < hist:
                return 0
            results = evaluate_live(cur, day, cfg, hist)
            rows = [(sid, day, r["status"], json.dumps(_plain([
                        {"rule_id": a["rule_id"], "severity": a["severity"], "text": a["text"],
                         "values": a["values"]} for a in r["alerts"]])),
                     r["value"], snapshot) for sid, r in results.items()]
            cur.execute("DELETE FROM revenue_site_status WHERE date = %s", (day,))
            if rows:
                execute_values(
                    cur,
                    "INSERT INTO revenue_site_status (site_id, date, status, alerts, value, config) VALUES %s",
                    rows, template="(%s, %s, %s, %s::jsonb, %s, %s::jsonb)")
            conn.commit()
            return len(rows)
    except Exception:
        log.exception("couldn't store revenue alert results")
        return 0


def attention_count(now=None):
    """Sites in red for the last completed day — the "N sites need
    attention" figure. None if it can't be worked out."""
    try:
        return report(now=now)["counts"][RED]
    except Exception:
        log.exception("couldn't count revenue alerts")
        return None
