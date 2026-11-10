"""SmartOLT, Phase 6A: read-only ONU status and signal (see PHASES.md).

OPS asks SmartOLT for every ONU's status every 6 minutes and every ONU's
signal every 20 minutes, two bulk calls (about 13 an hour against SmartOLT's
1,000), and keeps the latest answer in `onu_state`. The browser never talks to
SmartOLT. Nothing here writes to SmartOLT: 6A is read only.

There is no scheduler. poll_if_due() is called on every router heartbeat
(the CCR2116 reports every 15 s, routers/network.py) and whenever someone
loads the ONU panel, so polling carries on while the router is down as long
as someone is looking. It never blocks the caller: a due poll runs on its own
thread, at most one per kind at a time. Before calling SmartOLT a poll checks
the table's own "last polled" time, so a restart or a second app process
doesn't poll early.

The reply format below was confirmed against real responses on 2026-11-10:
{"status": true, "response_code": "success", "response": [ {...}, ... ]},
signals as "-17.62 dBm" or "-", times as Nairobi wall-clock
"2026-11-10 21:03:11.123456" with no zone, statuses Online / Offline / LOS /
Power fail, and SmartOLT's own signal grade Very good / Warning / Critical.

Configured by SMARTOLT_URL and SMARTOLT_API_KEY. The key goes only in the
X-Token header and is never logged.
"""
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests

from db.connection import EAT, db_cursor

log = logging.getLogger(__name__)

STATUS_EVERY = timedelta(minutes=6)
SIGNAL_EVERY = timedelta(minutes=20)
JITTER_S = 45                      # random extra wait per run, so calls don't line up
STATUS_STALE = timedelta(minutes=15)
SIGNAL_STALE = timedelta(minutes=45)   # two missed 20-minute polls
RETRY_AFTER_FAILURE = timedelta(minutes=2)
MAX_RETRY_WAIT_S = 120             # longest a 429 is waited out before giving up this run
TIMEOUT_S = 30

KINDS = {
    "status": {"path": "/api/onu/get_onus_statuses", "every": STATUS_EVERY, "column": "status_polled_at"},
    "signal": {"path": "/api/onu/get_onus_signals", "every": SIGNAL_EVERY, "column": "signal_polled_at"},
}


class SmartOLTError(Exception):
    """A call that didn't give a usable answer. `retry_after` (seconds) is set
    when SmartOLT asked us to slow down."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _settings():
    return (os.environ.get("SMARTOLT_URL") or "").strip().rstrip("/"), (os.environ.get("SMARTOLT_API_KEY") or "").strip()


def configured():
    url, key = _settings()
    return bool(url and key)


# ---- Calling SmartOLT ----

def _retry_after(value):
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return 60


def fetch(kind, get=requests.get, sleep=time.sleep):
    """The `response` list of one bulk endpoint. Retries once on HTTP 429,
    waiting as long as Retry-After says (if that's within MAX_RETRY_WAIT_S)."""
    url, key = _settings()
    if not (url and key):
        raise SmartOLTError("SmartOLT isn't configured")
    for attempt in (1, 2):
        try:
            r = get(url + KINDS[kind]["path"], headers={"X-Token": key, "Accept": "application/json"}, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise SmartOLTError(f"couldn't reach SmartOLT ({type(e).__name__})") from None
        if r.status_code != 429:
            break
        wait = _retry_after(r.headers.get("Retry-After"))
        if attempt == 2 or wait > MAX_RETRY_WAIT_S:
            raise SmartOLTError("SmartOLT rate limit reached", retry_after=wait)
        sleep(wait)
    if r.status_code != 200:
        raise SmartOLTError(f"SmartOLT answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise SmartOLTError("SmartOLT's answer wasn't JSON") from None
    if not isinstance(data, dict) or data.get("status") is not True:
        detail = data.get("error") or data.get("message") if isinstance(data, dict) else None
        raise SmartOLTError(f"SmartOLT refused the request{f': {detail}' if detail else ''}")
    rows = data.get("response")
    if not isinstance(rows, list):
        raise SmartOLTError("SmartOLT's answer had no ONU list")
    return rows


# ---- Reading the answer (pure) ----

DBM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*dBm\s*$", re.IGNORECASE)
NO_VALUE = {"", "-", "none", "null", "n/a"}


def parse_dbm(value):
    """'-17.62 dBm' -> Decimal('-17.62'); '-' or anything else -> None."""
    m = DBM.match(str(value)) if value is not None else None
    return Decimal(m.group(1)) if m else None


def parse_time(value):
    """SmartOLT's Nairobi wall-clock time -> an aware datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=EAT)
        except ValueError:
            pass
    return None


def parse_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in NO_VALUE else text


def _common(item):
    return {
        "unique_external_id": parse_text(item.get("unique_external_id")),
        "sn": parse_text(item.get("sn")),
        "olt_id": parse_int(item.get("olt_id")),
        "board": parse_int(item.get("board")),
        "port": parse_int(item.get("port")),
        "onu": parse_int(item.get("onu")),
        "zone_id": parse_int(item.get("zone_id")),
        "name": parse_text(item.get("name")),
    }


def status_rows(items):
    """The rows a status poll saves. Address and contact are left behind."""
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = _common(item)
        if not row["unique_external_id"]:
            continue
        row["status"] = parse_text(item.get("status"))
        row["last_status_change"] = parse_time(item.get("last_status_change"))
        rows.append(row)
    return rows


def signal_rows(items):
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = _common(item)
        if not row["unique_external_id"]:
            continue
        row["signal_1310_dbm"] = parse_dbm(item.get("signal_1310"))
        row["signal_1490_dbm"] = parse_dbm(item.get("signal_1490"))
        row["signal_quality"] = parse_text(item.get("signal"))
        rows.append(row)
    return rows


# ---- Saving ----

COMMON = ("unique_external_id", "sn", "olt_id", "board", "port", "onu", "zone_id", "name")
OWN = {"status": ("status", "last_status_change"),
       "signal": ("signal_1310_dbm", "signal_1490_dbm", "signal_quality")}


def save(kind, rows, now=None):
    """Upserts one poll's rows. A status poll is SmartOLT's full ONU list, so
    it also drops ONUs SmartOLT no longer has (never on an empty list: an
    empty answer is far more likely a fault than every ONU being deleted)."""
    from psycopg2.extras import execute_values
    now = now or datetime.now(timezone.utc)
    stamp = KINDS[kind]["column"]
    cols = COMMON + OWN[kind] + (stamp,)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
    values = [tuple(r[c] for c in COMMON + OWN[kind]) + (now,) for r in rows]
    with db_cursor() as (conn, cur):
        if values:
            execute_values(cur, f"INSERT INTO onu_state ({', '.join(cols)}) VALUES %s "
                                f"ON CONFLICT (unique_external_id) DO UPDATE SET {updates}", values)
            if kind == "status":
                cur.execute("DELETE FROM onu_state WHERE NOT (unique_external_id = ANY(%s))",
                            ([r["unique_external_id"] for r in rows],))
        conn.commit()
    return len(values)


def last_polled(kind):
    with db_cursor() as (_, cur):
        cur.execute(f"SELECT max({KINDS[kind]['column']}) FROM onu_state")
        return cur.fetchone()[0]


# ---- Polling ----

_lock = threading.Lock()
_running = set()
_next_at = {kind: 0.0 for kind in KINDS}   # time.monotonic() when each kind may next run


def _jitter():
    return random.uniform(0, JITTER_S)


def poll_once(kind, now=None):
    """One poll if the table says it's due. Returns seconds until the next one."""
    now = now or datetime.now(timezone.utc)
    every = KINDS[kind]["every"]
    last = last_polled(kind)
    if last is not None and now - last < every:
        return (every - (now - last)).total_seconds() + _jitter()
    saved = save(kind, (status_rows if kind == "status" else signal_rows)(fetch(kind)), now)
    log.info("smartolt %s poll saved %s ONUs", kind, saved)
    return every.total_seconds() + _jitter()


def _run(kind):
    try:
        wait = poll_once(kind)
    except SmartOLTError as e:
        log.warning("smartolt %s poll failed: %s", kind, e)
        wait = max(RETRY_AFTER_FAILURE.total_seconds(), e.retry_after or 0)
    except Exception:
        log.exception("smartolt %s poll failed", kind)
        wait = RETRY_AFTER_FAILURE.total_seconds()
    with _lock:
        _next_at[kind] = time.monotonic() + wait
        _running.discard(kind)


def poll_if_due():
    """Starts any poll that's due on its own thread and returns at once.
    Called on every router heartbeat and every ONU panel load. Never raises."""
    try:
        if not configured():
            return
        now = time.monotonic()
        with _lock:
            due = [k for k in KINDS if k not in _running and now >= _next_at[k]]
            _running.update(due)
        for kind in due:
            threading.Thread(target=_run, args=(kind,), name=f"smartolt-{kind}", daemon=True).start()
    except Exception:
        log.exception("smartolt poll_if_due failed")


# ---- The Network page panel ----

def _iso(dt):
    return dt.isoformat() if dt else None


def _num(d):
    return float(d) if d is not None else None


def panel(now=None):
    """Everything the ONU panel shows. Statuses are counted with whatever
    values SmartOLT uses; "not online" is any status other than Online."""
    now = now or datetime.now(timezone.utc)
    with db_cursor() as (_, cur):
        cur.execute("""SELECT unique_external_id, sn, board, port, onu, name, status, last_status_change,
                              signal_1310_dbm, signal_1490_dbm, signal_quality, status_polled_at, signal_polled_at
                       FROM onu_state""")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return build_panel(rows, now)


QUALITY_ORDER = {"Critical": 0, "Warning": 1}


def build_panel(rows, now):
    status_at = max((r["status_polled_at"] for r in rows if r["status_polled_at"]), default=None)
    signal_at = max((r["signal_polled_at"] for r in rows if r["signal_polled_at"]), default=None)
    counts = {}
    for r in rows:
        if r["status"]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

    def onu(r):
        since = r["last_status_change"]
        return {
            "id": r["unique_external_id"], "sn": r["sn"], "name": r["name"], "status": r["status"],
            "location": "/".join(str(v) for v in (r["board"], r["port"], r["onu"]) if v is not None) or None,
            "last_status_change": _iso(since),
            "for_s": int((now - since).total_seconds()) if since and since <= now else None,
            "signal_1310_dbm": _num(r["signal_1310_dbm"]), "signal_1490_dbm": _num(r["signal_1490_dbm"]),
            "signal_quality": r["signal_quality"],
        }

    # Most recently changed first: a fresh outage is the one to act on.
    down = sorted((r for r in rows if r["status"] and r["status"] != "Online"),
                  key=lambda r: r["last_status_change"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    # SmartOLT's own grade (owner's call, 2026-11-10): Critical before Warning,
    # then the weakest reading at the OLT first.
    weak = sorted((r for r in rows if r["signal_quality"] in QUALITY_ORDER),
                  key=lambda r: (QUALITY_ORDER[r["signal_quality"]],
                                 r["signal_1310_dbm"] if r["signal_1310_dbm"] is not None else Decimal(0)))
    return {
        "configured": configured(),
        "total": len(rows),
        "counts": counts,
        "status_polled_at": _iso(status_at),
        "signal_polled_at": _iso(signal_at),
        "status_stale": status_at is None or now - status_at > STATUS_STALE,
        "signal_stale": signal_at is None or now - signal_at > SIGNAL_STALE,
        "down": [onu(r) for r in down],
        "weak": [onu(r) for r in weak],
    }
