"""SmartOLT (Phase 6, see PHASES.md): ONU status, signal and details, the OLT
page's live reads, and authorizing new ONUs.

POLLING. OPS keeps the latest state of every ONU in `onu_state`: statuses
every 6 minutes, signals every 20, the Configured list's details every 30
(SmartOLT's paginated details call, which uses the general budget rather than
the 15-an-hour full export). About 15 calls an hour against 1,000.

There is no scheduler. poll_if_due() is called on every router heartbeat
(the CCR2116 reports every 15 s, routers/network.py) and whenever someone
loads the OLT page, so polling carries on while the router is down as long as
someone is looking. It never blocks the caller: a due poll runs on its own
thread, at most one per kind at a time. Before calling SmartOLT a poll checks
the table's own "last polled" time, so a restart or a second app process
doesn't poll early.

LIVE READS (the View page, Unconfigured tab, authorize dropdowns, graphs) go
to SmartOLT when asked, through a short cache so a busy page can't burn the
hourly budgets (full status info has its own 300 an hour, the signal graph
500, as SmartOLT's rate-limit headers showed on 2026-11-11).

SECRETS. SmartOLT returns PPPoE and Wi-Fi passwords in plain text (checked
2026-11-11, despite its docs showing them empty). Every reply is passed
through scrub() before anything else sees it: any key named "password" at
any depth is removed, and so are coordinates. The running config is never
fetched (it can hold credentials). Address and contact are shown on the
View page, read live, and never stored.

WRITES. Only authorize, for now (the owner's call, 2026-11-11, ahead of the
original 6D gate). Admin only (routers/olt.py), refused when
SMARTOLT_WRITES_ENABLED is false, one at a time per serial, every attempt
written to `onu_actions` with what was sent, SmartOLT's answer and a
read-back of what SmartOLT actually configured.

Reply formats confirmed against real responses on 2026-11-10/11: lists under
`response` (`onus` for the details call, `onu_details` for one ONU),
signals as "-17.62 dBm" or "-" in the bulk calls but numbers in the details,
times as Nairobi wall-clock with no zone. Configured by SMARTOLT_URL and
SMARTOLT_API_KEY; the key goes only in the X-Token header and is never logged.
"""
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests

from db.connection import EAT, db_cursor

log = logging.getLogger(__name__)

STATUS_EVERY = timedelta(minutes=6)
SIGNAL_EVERY = timedelta(minutes=20)
DETAILS_EVERY = timedelta(minutes=30)
JITTER_S = 45                      # random extra wait per run, so calls don't line up
STATUS_STALE = timedelta(minutes=15)
SIGNAL_STALE = timedelta(minutes=45)   # two missed 20-minute polls
DETAILS_STALE = timedelta(minutes=75)
RETRY_AFTER_FAILURE = timedelta(minutes=2)
MAX_RETRY_WAIT_S = 120             # longest a 429 is waited out before giving up
TIMEOUT_S = 30
AUTHORIZE_TIMEOUT_S = 150          # SmartOLT: "expect this call to take tens of seconds"
PAGE_SIZE = 100

KINDS = {
    "status": {"path": "/api/onu/get_onus_statuses", "every": STATUS_EVERY, "column": "status_polled_at"},
    "signal": {"path": "/api/onu/get_onus_signals", "every": SIGNAL_EVERY, "column": "signal_polled_at"},
    "details": {"path": "/api/onu/get_all_onus_details", "every": DETAILS_EVERY, "column": "details_polled_at"},
}


class SmartOLTError(Exception):
    """A call that didn't give a usable answer. `retry_after` (seconds) is set
    when SmartOLT asked us to slow down."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class WritesDisabled(Exception):
    pass


def _settings():
    return (os.environ.get("SMARTOLT_URL") or "").strip().rstrip("/"), (os.environ.get("SMARTOLT_API_KEY") or "").strip()


def configured():
    url, key = _settings()
    return bool(url and key)


def writes_enabled():
    """The kill switch. On unless SMARTOLT_WRITES_ENABLED says otherwise (the
    owner asked for authorize to be on from day one)."""
    return (os.environ.get("SMARTOLT_WRITES_ENABLED") or "true").strip().lower() not in ("false", "0", "no", "off")


# ---- Calling SmartOLT ----

_send = requests.request
_sleep = time.sleep


def _retry_after(value):
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return 60


def call(method, path, *, params=None, data=None, timeout=TIMEOUT_S, image=False):
    """One SmartOLT call. Returns the parsed JSON object (its `status` checked)
    or, with image=True, the PNG bytes. Retries once on HTTP 429, waiting as
    long as Retry-After says if that's within MAX_RETRY_WAIT_S."""
    url, key = _settings()
    if not (url and key):
        raise SmartOLTError("SmartOLT isn't configured")
    for attempt in (1, 2):
        try:
            r = _send(method, url + path, headers={"X-Token": key, "Accept": "image/png" if image else "application/json"},
                      params=params, data=data, timeout=timeout)
        except requests.RequestException as e:
            raise SmartOLTError(f"couldn't reach SmartOLT ({type(e).__name__})") from None
        if r.status_code != 429:
            break
        wait = _retry_after(r.headers.get("Retry-After"))
        if attempt == 2 or wait > MAX_RETRY_WAIT_S:
            raise SmartOLTError("SmartOLT rate limit reached", retry_after=wait)
        _sleep(wait)
    if image and r.status_code == 200 and "image" in (r.headers.get("Content-Type") or ""):
        return r.content
    try:
        body = r.json()
    except ValueError:
        raise SmartOLTError(f"SmartOLT answered HTTP {r.status_code}" if r.status_code != 200
                            else "SmartOLT's answer wasn't JSON") from None
    if not isinstance(body, dict) or body.get("status") is not True:
        detail = (body.get("error") or body.get("message") or body.get("response")) if isinstance(body, dict) else None
        detail = detail if isinstance(detail, str) else None
        raise SmartOLTError(f"SmartOLT refused the request{f': {detail}' if detail else ''}"
                            if r.status_code in (200, 400) else f"SmartOLT answered HTTP {r.status_code}")
    return body


def _list(body, key="response"):
    rows = body.get(key)
    if not isinstance(rows, list):
        raise SmartOLTError("SmartOLT's answer had no list")
    return rows


def fetch(kind):
    """Every row of one bulk poll."""
    if kind != "details":
        return _list(call("GET", KINDS[kind]["path"]))
    rows, page = [], 1
    while True:
        body = call("GET", KINDS["details"]["path"], params={"page": page, "page_size": PAGE_SIZE})
        rows += _list(body, "onus")
        if page >= int(body.get("total_pages") or 1) or page >= 200:
            return rows
        page += 1


# ---- Reading the answers (pure) ----

DBM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:dBm)?\s*$", re.IGNORECASE)
NO_VALUE = {"", "-", "none", "null", "n/a"}
SECRET_KEYS = {"password", "latitude", "longitude"}


def scrub(value):
    """A copy with every password (and coordinate) removed, at any depth."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def parse_dbm(value):
    """'-17.62 dBm' or -17.62 -> Decimal('-17.62'); '-' or anything else -> None."""
    if value is None or isinstance(value, bool):
        return None
    m = DBM.match(str(value))
    try:
        return Decimal(m.group(1)) if m else None
    except InvalidOperation:
        return None


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
    try:   # full status info uses "2026-11-07 17:51:22+03:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=EAT)
    except ValueError:
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


def _iso(dt):
    return dt.isoformat() if dt else None


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


def _rows(items, own):
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = _common(item)
        if row["unique_external_id"]:
            row.update(own(item))
            rows.append(row)
    return rows


def status_rows(items):
    """The rows a status poll saves. Address and contact are left behind."""
    return _rows(items, lambda i: {"status": parse_text(i.get("status")),
                                   "last_status_change": parse_time(i.get("last_status_change"))})


def signal_rows(items):
    return _rows(items, lambda i: {"signal_1310_dbm": parse_dbm(i.get("signal_1310")),
                                   "signal_1490_dbm": parse_dbm(i.get("signal_1490")),
                                   "signal_quality": parse_text(i.get("signal"))})


DETAIL_FIELDS = ("olt_name", "pon_type", "gpon_channel", "onu_type_name", "zone_name", "odb_name", "odb_port",
                 "mode", "wan_mode", "vlan", "tr069", "administrative_status", "catv", "voip_service")


def service_vlans(item):
    vlans = []
    for sp in item.get("service_ports") or []:
        v = parse_int(sp.get("vlan")) if isinstance(sp, dict) else None
        if v is not None and v not in vlans:
            vlans.append(v)
    return vlans


def details_row(item):
    """The Configured list's columns, whitelisted: no password, address,
    contact or coordinates."""
    d = {f: parse_text(item.get(f)) for f in DETAIL_FIELDS}
    d["authorization_date"] = _iso(parse_time(item.get("authorization_date")))
    d["vlans"] = service_vlans(item)
    # The PPPoE username (never the password) is how 6B suggests which
    # customer an ONU belongs to. Usernames are already shown on Network Analysis.
    d["pppoe_username"] = parse_text(item.get("username"))
    return {"details": d}


def details_rows(items):
    return _rows(items, details_row)


ROWS = {"status": status_rows, "signal": signal_rows, "details": details_rows}


# ---- Saving ----

COMMON = ("unique_external_id", "sn", "olt_id", "board", "port", "onu", "zone_id", "name")
OWN = {"status": ("status", "last_status_change"),
       "signal": ("signal_1310_dbm", "signal_1490_dbm", "signal_quality"),
       "details": ("details",)}


def save(kind, rows, now=None, full_list=True):
    """Upserts one poll's rows. A status poll is SmartOLT's full ONU list, so
    it also drops ONUs SmartOLT no longer has (never on an empty list: an
    empty answer is far more likely a fault than every ONU being deleted).
    full_list=False (a single ONU's read-back) never deletes anything."""
    from psycopg2.extras import Json, execute_values
    now = now or datetime.now(timezone.utc)
    stamp = KINDS[kind]["column"]
    cols = COMMON + OWN[kind] + (stamp,)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
    values = [tuple(Json(r[c]) if isinstance(r[c], dict) else r[c] for c in COMMON + OWN[kind]) + (now,) for r in rows]
    with db_cursor() as (conn, cur):
        if values:
            execute_values(cur, f"INSERT INTO onu_state ({', '.join(cols)}) VALUES %s "
                                f"ON CONFLICT (unique_external_id) DO UPDATE SET {updates}", values)
            if kind == "status" and full_list:
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
    saved = save(kind, ROWS[kind](fetch(kind)), now)
    log.info("smartolt %s poll saved %s ONUs", kind, saved)
    if kind == "status":
        from db import onu_alerts        # fresh statuses: check for LOS now
        onu_alerts.tick(force=True)
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
    Called on every router heartbeat and every OLT page load. Never raises."""
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


# ---- The Configured list ----

def _num(d):
    return float(d) if d is not None else None


def configured_list(now=None):
    now = now or datetime.now(timezone.utc)
    with db_cursor() as (_, cur):
        cur.execute("""SELECT unique_external_id, sn, olt_id, board, port, onu, name, status, last_status_change,
                              signal_1310_dbm, signal_1490_dbm, signal_quality, details,
                              status_polled_at, signal_polled_at, details_polled_at
                       FROM onu_state""")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return build_list(rows, now)


def _latest(rows, column):
    return max((r[column] for r in rows if r.get(column)), default=None)


def build_list(rows, now):
    """Every ONU for the Configured tab, plus counts and how fresh each kind
    of data is. Statuses are counted with whatever values SmartOLT uses."""
    status_at, signal_at, details_at = (_latest(rows, c) for c in ("status_polled_at", "signal_polled_at", "details_polled_at"))
    counts = {}
    for r in rows:
        if r["status"]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

    def onu(r):
        d = r.get("details") or {}
        since = r["last_status_change"]
        return {
            "id": r["unique_external_id"], "sn": r["sn"], "name": r["name"], "status": r["status"],
            "olt_id": r.get("olt_id"), "board": r["board"], "port": r["port"], "onu": r["onu"],
            "location": "/".join(str(v) for v in (r["board"], r["port"], r["onu"]) if v is not None) or None,
            "last_status_change": _iso(since),
            "for_s": int((now - since).total_seconds()) if since and since <= now else None,
            "signal_1310_dbm": _num(r["signal_1310_dbm"]), "signal_1490_dbm": _num(r["signal_1490_dbm"]),
            "signal_quality": r["signal_quality"],
            "olt_name": d.get("olt_name"), "zone": d.get("zone_name"), "odb": d.get("odb_name"),
            "mode": d.get("mode"), "wan_mode": d.get("wan_mode"), "vlans": d.get("vlans") or [],
            "onu_type": d.get("onu_type_name"), "authorization_date": d.get("authorization_date"),
            "administrative_status": d.get("administrative_status"),
        }

    return {
        "configured": configured(),
        "writes_enabled": writes_enabled(),
        "total": len(rows),
        "counts": counts,
        "weak": sum(1 for r in rows if r["signal_quality"] in ("Warning", "Critical")),
        "status_polled_at": _iso(status_at),
        "signal_polled_at": _iso(signal_at),
        "details_polled_at": _iso(details_at),
        "status_stale": status_at is None or now - status_at > STATUS_STALE,
        "signal_stale": signal_at is None or now - signal_at > SIGNAL_STALE,
        "details_stale": details_at is None or now - details_at > DETAILS_STALE,
        "webhook_at": _iso(webhook_state["last_at"]),
        # Newest authorization first, like SmartOLT's own list.
        "onus": sorted((onu(r) for r in rows), key=lambda o: o["authorization_date"] or "", reverse=True),
    }


# ---- Live reads, through a short cache ----

_cache = {}
_cache_lock = threading.Lock()
CACHE_MAX = 300


def cached(key, ttl_s, load):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl_s:
            return hit[1]
    value = load()
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:CACHE_MAX // 4]:
                _cache.pop(k, None)
        _cache[key] = (now, value)
    return value


def forget(*prefixes):
    with _cache_lock:
        for k in [k for k in _cache if any(k[0] == p for p in prefixes)]:
            _cache.pop(k, None)


ONU_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def check_id(onu_id):
    if not ONU_ID.match(onu_id or ""):
        raise ValueError("not an ONU id")
    return onu_id


def onu_details(onu_id):
    """One ONU, live, scrubbed, shaped for the View page."""
    check_id(onu_id)
    return cached(("details", onu_id), 30, lambda: shape_details(scrub(call("GET", f"/api/onu/get_onu_details/{onu_id}").get("onu_details") or {})))


def shape_details(d):
    def ports(key, fields):
        return [{f: parse_text(p.get(f)) for f in fields} for p in d.get(key) or [] if isinstance(p, dict)]

    return {
        "id": parse_text(d.get("unique_external_id")), "sn": parse_text(d.get("sn")),
        "olt_id": parse_int(d.get("olt_id")), "olt_name": parse_text(d.get("olt_name")),
        "board": parse_int(d.get("board")), "port": parse_int(d.get("port")), "onu": parse_int(d.get("onu")),
        "pon_type": parse_text(d.get("pon_type")), "gpon_channel": parse_text(d.get("gpon_channel")),
        "onu_type": parse_text(d.get("onu_type_name")), "zone": parse_text(d.get("zone_name")),
        "odb": parse_text(d.get("odb_name")), "name": parse_text(d.get("name")),
        "address": parse_text(d.get("address")), "contact": parse_text(d.get("contact")),
        "is_vip": str(d.get("is_vip")) == "1",
        "authorization_date": _iso(parse_time(d.get("authorization_date"))),
        "status": parse_text(d.get("status")), "last_status_change": _iso(parse_time(d.get("last_status_change"))),
        "signal_quality": parse_text(d.get("signal")),
        "signal_1310_dbm": _num(parse_dbm(d.get("signal_1310"))), "signal_1490_dbm": _num(parse_dbm(d.get("signal_1490"))),
        "distance_m": parse_int(d.get("distance")),
        "mode": parse_text(d.get("mode")), "wan_mode": parse_text(d.get("wan_mode")), "vlan": parse_text(d.get("vlan")),
        "ip_address": parse_text(d.get("ip_address")),
        "pppoe_username": parse_text(d.get("username")),
        "tr069": parse_text(d.get("tr069")), "tr069_profile": parse_text(d.get("tr069_profile")),
        "mgmt_ip_mode": parse_text(d.get("mgmt_ip_mode")), "mgmt_ip_address": parse_text(d.get("mgmt_ip_address")),
        "administrative_status": parse_text(d.get("administrative_status")),
        "catv": parse_text(d.get("catv")), "voip_service": parse_text(d.get("voip_service")),
        "resync_failed": str(d.get("is_failed_resync_config") or "0") == "1",
        "attached_vlans": service_vlans(d),
        "service_ports": ports("service_ports", ("service_port", "vlan", "upload_speed", "download_speed", "tag_transform_mode")),
        "ethernet_ports": ports("ethernet_ports", ("port", "admin_state", "mode", "dhcp", "vlan", "allowed_vlans")),
    }


def onu_live_status(onu_id):
    """The OLT's own view of one ONU (SmartOLT's full status info): distance,
    uptime, last down cause, optical levels, history, LAN ports. MAC tables
    are left out. Cached a minute: this call has its own 300-an-hour limit."""
    check_id(onu_id)
    return cached(("status", onu_id), 60, lambda: shape_live_status(
        scrub(call("GET", f"/api/onu/get_onu_full_status_info/{onu_id}", timeout=60).get("full_status_json") or {})))


def shape_live_status(j):
    optical = j.get("Optical status") or {}
    details = j.get("ONU details") or {}
    history = j.get("History") or {}
    lan = j.get("ONU LAN Interfaces status") or {}

    def pick(src, keys):
        return {label: parse_text(src.get(key)) for label, key in keys}

    return {
        "optical": pick(optical, (("onu_rx_dbm", "Rx optical power(dBm)"), ("onu_tx_dbm", "Tx optical power(dBm)"),
                                  ("temperature_c", "Temperature(C)"), ("olt_rx_dbm", "OLT Rx ONT optical power(dBm)"))),
        "onu": pick(details, (("run_state", "Run state"), ("distance_m", "ONT distance(m)"),
                              ("last_down_cause", "Last down cause"), ("last_up_time", "Last up time"),
                              ("last_down_time", "Last down time"), ("online_duration", "ONT online duration"),
                              ("line_profile", "Line profile name"), ("service_profile", "Service profile name"),
                              ("tr069_acs_profile", "TR069 ACS profile"))),
        "history": [{"n": n, "up_at": parse_text(h.get("Auth at")), "down_at": parse_text(h.get("Offline at")),
                     "cause": parse_text(h.get("Cause"))}
                    for n, h in sorted(history.items(), reverse=True) if isinstance(h, dict)],
        "lan_ports": [{"port": n, "type": parse_text(p.get("Type")), "speed": parse_text(p.get("Speed")),
                       "duplex": parse_text(p.get("Duplex")), "link": parse_text(p.get("Link state"))}
                      for n, p in sorted(lan.items(), key=lambda kv: parse_int(kv[0]) or 0) if isinstance(p, dict)],
    }


GRAPH_KINDS = {"traffic": "get_onu_traffic_graph", "signal": "get_onu_signal_graph"}
GRAPH_PERIODS = ("hourly", "daily", "weekly", "monthly", "yearly")


def onu_graph(onu_id, kind, period):
    """SmartOLT's PNG graph, cached 5 minutes (the signal graph has its own
    500-an-hour limit)."""
    check_id(onu_id)
    if kind not in GRAPH_KINDS or period not in GRAPH_PERIODS:
        raise ValueError("unknown graph")
    return cached(("graph", onu_id, kind, period), 300,
                  lambda: call("GET", f"/api/onu/{GRAPH_KINDS[kind]}/{onu_id}/{period}", image=True))


def unconfigured():
    """ONUs the OLT sees but SmartOLT hasn't authorized, live (15 s cache)."""
    def load():
        rows = []
        for i in _list(call("GET", "/api/onu/unconfigured_onus", timeout=60)):
            if not isinstance(i, dict):
                continue
            rows.append({"olt_id": parse_int(i.get("olt_id")), "pon_type": parse_text(i.get("pon_type")),
                         "board": parse_int(i.get("board")), "port": parse_int(i.get("port")),
                         "onu": parse_int(i.get("onu")), "sn": parse_text(i.get("sn")),
                         "onu_type": parse_text(i.get("onu_type_name")), "pon_description": parse_text(i.get("pon_description")),
                         "is_disabled": str(i.get("is_disabled")) == "1",
                         "can_authorize": "authorize" in (i.get("actions") or [])})
        return rows
    return cached(("unconfigured",), 15, load)


def authorize_options(olt_id, pon_type):
    """Everything the Authorize form's dropdowns need, cached 10 minutes."""
    pon_type = pon_type if pon_type in ("gpon", "epon") else "gpon"

    def olts():
        return [{"id": parse_int(o.get("id")), "name": parse_text(o.get("name")), "hardware": parse_text(o.get("olt_hardware_version"))}
                for o in _list(call("GET", "/api/system/get_olts"))]

    def zones():
        return sorted(({"id": parse_int(z.get("id")), "name": parse_text(z.get("name"))} for z in _list(call("GET", "/api/system/get_zones"))),
                      key=lambda z: (z["name"] or "").lower())

    def types():
        return sorted(({"name": parse_text(t.get("name")), "capability": parse_text(t.get("capability")),
                        "ethernet_ports": parse_int(t.get("ethernet_ports"))}
                       for t in _list(call("GET", f"/api/system/get_onu_types_by_pon_type/{pon_type}"))),
                      key=lambda t: (t["name"] or "").lower())

    def vlans():
        return [{"vlan": parse_int(v.get("vlan")), "description": parse_text(v.get("description")), "scope": parse_text(v.get("scope")),
                 "default": bool(parse_text(v.get("default_for_pon_ports")))}
                for v in _list(call("GET", f"/api/olt/get_vlans/{int(olt_id)}"))]

    def speeds():
        return [{"name": parse_text(s.get("name")), "direction": parse_text(s.get("direction")), "type": parse_text(s.get("type"))}
                for s in _list(call("GET", "/api/system/get_speed_profiles"))]

    return {
        "olts": cached(("opt", "olts"), 600, olts),
        "zones": cached(("opt", "zones"), 600, zones),
        "onu_types": cached(("opt", "types", pon_type), 600, types),
        "vlans": cached(("opt", "vlans", int(olt_id)), 600, vlans),
        "speed_profiles": cached(("opt", "speeds"), 600, speeds),
    }


def odbs(zone_id):
    return cached(("opt", "odbs", int(zone_id)), 600, lambda: sorted(
        ({"name": parse_text(o.get("name")), "ports": parse_int(o.get("nr_of_ports"))}
         for o in _list(call("GET", f"/api/system/get_odbs/{int(zone_id)}"))), key=lambda o: (o["name"] or "").lower()))


# ---- Authorize ----

_authorizing = set()     # serials being authorized right now (this process)

# What the read-back is compared on: request field -> SmartOLT details field.
CHECKED = {"olt_id": "olt_id", "board": "board", "port": "port", "onu_type": "onu_type_name",
           "zone": "zone_name", "onu_mode": "mode", "vlan": "vlan", "name": "name"}


def compare(sent, got):
    """Fields where SmartOLT's read-back differs from what was sent."""
    out = []
    for field, key in CHECKED.items():
        want = sent.get(field)
        if want in (None, ""):
            continue
        have = got.get(key)
        same = (parse_int(want) == parse_int(have)) if field in ("olt_id", "board", "port", "vlan") \
            else str(want).strip().lower() == str(have or "").strip().lower()
        if not same:
            out.append({"field": field, "sent": want, "smartolt_has": have})
    return out


def _audit_start(action, sn, request, user_id, external_id=None):
    from psycopg2.extras import Json
    with db_cursor() as (conn, cur):
        cur.execute("""INSERT INTO onu_actions (action, sn, unique_external_id, requested_by, request, result)
                       VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING id""", (action, sn, external_id, user_id, Json(request)))
        action_id = cur.fetchone()[0]
        conn.commit()
    return action_id


def _audit_finish(action_id, result, response, read_back=None, external_id=None):
    from psycopg2.extras import Json
    with db_cursor() as (conn, cur):
        cur.execute("""UPDATE onu_actions SET result = %s, response = %s, read_back = %s,
                              unique_external_id = COALESCE(%s, unique_external_id), finished_at = now()
                       WHERE id = %s""", (result, (response or "")[:2000], Json(read_back) if read_back is not None else None,
                                         external_id, action_id))
        conn.commit()


def read_back(sn):
    """What SmartOLT has for this serial right after a write (paginated
    details call, so it doesn't touch the 15-an-hour full export)."""
    body = call("GET", KINDS["details"]["path"], params={"sn": sn, "page": 1, "page_size": 10})
    matches = [o for o in _list(body, "onus") if str(o.get("sn") or "").upper() == sn.upper()]
    return scrub(matches[0]) if matches else None


def authorize(request, user_id):
    """Authorizes one ONU through SmartOLT. `request` is already validated by
    the router. Returns {result, message, mismatches, onu}. Every attempt is
    in onu_actions, whatever happens."""
    if not writes_enabled():
        raise WritesDisabled("SmartOLT writes are switched off (SMARTOLT_WRITES_ENABLED).")
    sn = request["sn"]
    with _lock:
        if sn in _authorizing:
            raise SmartOLTError("This ONU is already being authorized.")
        _authorizing.add(sn)
    try:
        action_id = _audit_start("authorize", sn, request, user_id)
        form = {k: v for k, v in {
            "olt_id": request["olt_id"], "pon_type": request["pon_type"], "sn": sn,
            "board": request["board"], "port": request["port"], "onu_type": request["onu_type"],
            "onu_mode": request["onu_mode"], "vlan": request.get("vlan"), "zone": request["zone"],
            "odb": request.get("odb"), "name": request["name"], "address_or_comment": request.get("address_or_comment"),
            "onu_external_id": request.get("onu_external_id"),
            "upload_speed_profile_name": request.get("upload_speed_profile_name"),
            "download_speed_profile_name": request.get("download_speed_profile_name"),
        }.items() if v not in (None, "")}
        try:
            body = call("POST", "/api/onu/authorize_onu", data=form, timeout=AUTHORIZE_TIMEOUT_S)
        except SmartOLTError as e:
            _audit_finish(action_id, "failed", str(e))
            raise
        answer = body.get("response") if isinstance(body.get("response"), str) else "ONU configuration saved"
        forget("unconfigured")
        try:
            got = read_back(sn)
        except SmartOLTError as e:
            _audit_finish(action_id, "mismatch", f"{answer}. Read-back failed: {e}")
            return {"result": "mismatch", "message": f"SmartOLT said: {answer}. But OPS couldn't read it back ({e}); check it in the Configured tab.",
                    "mismatches": [], "onu": None}
        if got is None:
            _audit_finish(action_id, "mismatch", f"{answer}. Read-back found no ONU with this serial yet.")
            return {"result": "mismatch", "message": f"SmartOLT said: {answer}. But it doesn't list this serial yet; check again in a minute.",
                    "mismatches": [], "onu": None}
        diffs = compare(request, got)
        kept = details_row(got)["details"]
        external_id = parse_text(got.get("unique_external_id"))
        result = "mismatch" if diffs else "succeeded"
        _audit_finish(action_id, result, answer, {"unique_external_id": external_id, "status": got.get("status"), **kept,
                                                  "name": got.get("name"), "mismatches": diffs}, external_id)
        try:   # show it in Configured straight away, without waiting for the next poll
            for kind in ("status", "signal", "details"):
                save(kind, ROWS[kind]([got]), full_list=False)
        except Exception:
            log.exception("smartolt: couldn't save the authorized ONU locally")
        return {"result": result, "message": answer, "mismatches": diffs, "onu": external_id}
    finally:
        with _lock:
            _authorizing.discard(sn)


STALE_PENDING = timedelta(minutes=10)    # a "pending" older than this lost its tracker (restart)


def _action_row(r, now):
    result = r[7]
    if result == "pending" and r[5] and now - r[5] > STALE_PENDING:
        result = "unknown"
    return {"id": r[0], "action": r[1], "sn": r[2], "onu": r[3], "by": r[4], "at": _iso(r[5]),
            "finished_at": _iso(r[6]), "result": result, "response": r[8], "name": r[9], "zone": r[10],
            "summary": r[11]}


ACTION_SELECT = """SELECT a.id, a.action, a.sn, a.unique_external_id, u.name, a.requested_at, a.finished_at,
                          a.result, a.response, COALESCE(a.request->>'name', a.request->'before'->>'name'), a.request->>'zone',
                          a.request->>'summary'
                   FROM onu_actions a LEFT JOIN users u ON u.id = a.requested_by"""


def recent_actions(limit=20, action=None, onu_id=None):
    where, params = [], []
    if action:
        where.append("a.action = %s"); params.append(action)
    if onu_id:
        where.append("a.unique_external_id = %s"); params.append(onu_id)
    with db_cursor() as (_, cur):
        cur.execute(f"{ACTION_SELECT} {'WHERE ' + ' AND '.join(where) if where else ''} "
                    "ORDER BY a.requested_at DESC LIMIT %s", (*params, limit))
        now = datetime.now(timezone.utc)
        return [_action_row(r, now) for r in cur.fetchall()]


def get_action(action_id):
    with db_cursor() as (_, cur):
        cur.execute(f"{ACTION_SELECT} WHERE a.id = %s", (action_id,))
        r = cur.fetchone()
    return _action_row(r, datetime.now(timezone.utc)) if r else None


# ---- ONU actions (6C): reboot, disable, enable ----
# One ONU at a time, admin only (routers/olt.py), each confirmed in the page
# first. Refused when SMARTOLT_WRITES_ENABLED is false, or when that action's
# own switch (SMARTOLT_ACTION_REBOOT / _DISABLE / _ENABLE) is false: rule 2 of
# the scope, one flag per action. Paths from openapi.json (looked up
# 2026-11-10): enable/disable take async=1 and are only done when
# check_bulk_task_status says "succeeded"; reboot has no async option and is
# done when SmartOLT accepts it. Every action is in onu_actions with the
# before and after state; enable/disable are read back from SmartOLT.

ACTIONS = {
    "reboot": {"path": "/api/onu/reboot/{id}", "queued": False},
    "disable": {"path": "/api/onu/disable/{id}", "queued": True, "admin_status": "Disabled"},
    "enable": {"path": "/api/onu/enable/{id}", "queued": True, "admin_status": "Enabled"},
}
TASK_CHECK_EVERY_S = 5
TASK_CHECKS = 36                     # about 3 minutes
TASK_FAILED = {"failed", "onu_not_found"}
_acting = set()                      # ONUs with an action in flight (this process)


def action_enabled(action):
    flag = (os.environ.get(f"SMARTOLT_ACTION_{action.upper()}") or "true").strip().lower()
    return writes_enabled() and flag not in ("false", "0", "no", "off")


def actions_available():
    return {a: action_enabled(a) for a in ACTIONS}


def _snapshot(d):
    """What an action is judged against: kept small, nothing secret."""
    return {"name": parse_text(d.get("name")), "status": parse_text(d.get("status")),
            "administrative_status": parse_text(d.get("administrative_status"))}


def _live_details(onu_id):
    return scrub(call("GET", f"/api/onu/get_onu_details/{onu_id}").get("onu_details") or {})


def parse_task(body, onu_id):
    """(status, error) for this ONU from check_bulk_task_status, tolerant of
    a list of rows, a dict keyed by ONU, or a single row."""
    resp = body.get("response") if isinstance(body, dict) else None
    if isinstance(resp, dict) and onu_id in resp and isinstance(resp[onu_id], dict):
        rows = [resp[onu_id]]
    elif isinstance(resp, dict):
        rows = [resp]
    elif isinstance(resp, list):
        rows = [r for r in resp if isinstance(r, dict)]
    else:
        rows = []
    for r in rows:
        ids = {str(r.get(k)) for k in ("onu_external_id", "unique_external_id", "external_id", "onu", "sn") if r.get(k)}
        if onu_id in ids or len(rows) == 1:
            return parse_text(r.get("status")), parse_text(r.get("error_message"))
    return None, None


def start_action(onu_id, action, user_id):
    """Sends one action. Returns the audit row (result "pending" while a
    queued action is tracked in the background)."""
    check_id(onu_id)
    if action not in ACTIONS:
        raise ValueError("unknown action")
    if not writes_enabled():
        raise WritesDisabled("SmartOLT writes are switched off (SMARTOLT_WRITES_ENABLED).")
    if not action_enabled(action):
        raise WritesDisabled(f"{action.capitalize()} is switched off (SMARTOLT_ACTION_{action.upper()}).")
    with _lock:
        if onu_id in _acting:
            raise SmartOLTError("Another action on this ONU is still running.")
        _acting.add(onu_id)
    handed_off = False
    try:
        before = _live_details(onu_id)
        if not before:
            raise SmartOLTError("SmartOLT doesn't know this ONU.")
        spec = ACTIONS[action]
        sn = parse_text(before.get("sn"))
        action_id = _audit_start(action, sn, {"onu_id": onu_id, "before": _snapshot(before)}, user_id, onu_id)
        try:
            body = call("POST", spec["path"].format(id=onu_id), data={"async": "1"} if spec["queued"] else None, timeout=60)
        except SmartOLTError as e:
            _audit_finish(action_id, "failed", str(e))
            raise
        answer = (body.get("response") if isinstance(body.get("response"), str) else "accepted").rstrip(". ")
        forget("details", "status")
        if not spec["queued"]:
            _audit_finish(action_id, "succeeded", answer)
            return get_action(action_id)
        threading.Thread(target=_track, args=(action_id, onu_id, action, answer), name=f"smartolt-{action}", daemon=True).start()
        handed_off = True
        return get_action(action_id)
    finally:
        if not handed_off:
            with _lock:
                _acting.discard(onu_id)


def _track(action_id, onu_id, action, answer):
    """Follows a queued enable/disable to the end, then reads the ONU back."""
    want = ACTIONS[action]["admin_status"]
    status, error = None, None
    try:
        for _ in range(TASK_CHECKS):
            _sleep(TASK_CHECK_EVERY_S)
            try:
                status, error = parse_task(call("POST", "/api/onu/check_bulk_task_status", data={"onus_external_ids": onu_id}), onu_id)
            except SmartOLTError as e:
                status, error = None, str(e)
                continue
            if status == "succeeded" or status in TASK_FAILED:
                break
        if status in TASK_FAILED:
            _audit_finish(action_id, "failed", f"SmartOLT: {status}{f' ({error})' if error else ''}")
            return
        after = _live_details(onu_id)
        have = parse_text(after.get("administrative_status"))
        try:
            save("details", details_rows([after]), full_list=False)
        except Exception:
            log.exception("smartolt: couldn't save the ONU after %s", action)
        forget("details")
        if have == want:
            note = "SmartOLT: succeeded" if status == "succeeded" else f"task said {status or 'nothing'}, but the ONU is {want}"
            _audit_finish(action_id, "succeeded", f"{answer}. {note}.", {"after": _snapshot(after)})
        else:
            _audit_finish(action_id, "mismatch" if status == "succeeded" else "failed",
                          f"{answer}. Task: {status or 'no answer'}; the ONU is still {have or 'unknown'}, not {want}.",
                          {"after": _snapshot(after)})
    except Exception as e:
        log.exception("smartolt %s tracking failed", action)
        _audit_finish(action_id, "failed", f"OPS lost track of it: {e}")
    finally:
        with _lock:
            _acting.discard(onu_id)


# ---- Webhooks (6A-W) ----
# SmartOLT pushes events to POST /smartolt/webhook, signed the Standard
# Webhooks way (openapi.json x-webhooks, looked up 2026-11-10): headers
# webhook-id, webhook-timestamp and webhook-signature "v1,<base64>", where the
# signature is HMAC-SHA256 over "{id}.{timestamp}.{raw body}" with the
# Signing secret minus its "whsec_" prefix, base64-decoded. The secret lives
# only in SMARTOLT_WEBHOOK_SECRET; without it every delivery is refused.
#
# Not documented: whether a retry keeps its original timestamp. So old
# timestamps are not refused (that could refuse every retry until SmartOLT
# disables the webhook); replays are harmless anyway, because each update
# only applies when it's newer than what OPS has, and a repeated webhook-id
# is answered "already done" without touching the table.
#
# Polling keeps running: webhooks make status and signal arrive at once, the
# polls remain the safety net for anything missed.

import base64
import hashlib
import hmac
from collections import OrderedDict

WEBHOOK_MAX_BYTES = 4 * 1024 * 1024
_seen_ids = OrderedDict()          # recent webhook-ids, so a resend is a no-op
_SEEN_MAX = 2000
webhook_state = {"last_at": None, "last_type": None}


def webhook_secret():
    raw = (os.environ.get("SMARTOLT_WEBHOOK_SECRET") or "").strip()
    if not raw:
        return None
    try:
        return base64.b64decode(raw.removeprefix("whsec_"), validate=True)
    except (ValueError, TypeError):
        log.error("SMARTOLT_WEBHOOK_SECRET isn't a valid whsec_ secret")
        return None


def verify_signature(secret, msg_id, timestamp, body, signature_header):
    """True if any "v1,<sig>" in the header matches. Constant-time."""
    if not (secret and msg_id and timestamp and signature_header):
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest())
    for part in signature_header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig.encode(), expected):
            return True
    return False


def already_seen(msg_id):
    with _lock:
        if msg_id in _seen_ids:
            return True
        _seen_ids[msg_id] = True
        while len(_seen_ids) > _SEEN_MAX:
            _seen_ids.popitem(last=False)
    return False


def forget_seen(msg_id):
    with _lock:
        _seen_ids.pop(msg_id, None)


GRADES = {"very good": "Very good", "very_good": "Very good", "good": "Very good",
          "warning": "Warning", "critical": "Critical"}


def parse_grade(value):
    text = parse_text(value)
    return GRADES.get(text.lower(), text) if text else None


def event_rows(event_type, rows):
    """(kind, row dicts) to apply for one event type; kind None = nothing to save."""
    rows = [r for r in rows if isinstance(r, dict) and parse_text(r.get("unique_external_id"))]
    if event_type == "onu.status_changed":
        return "status", status_rows(rows)
    if event_type in ("onu.signal_changed", "onu.signal_reading"):
        return "signal_1310", [{**_common(r), "signal_1310_dbm": parse_dbm(r.get("rx1310")),
                                "signal_quality": parse_grade(r.get("band"))} for r in rows]
    if event_type == "onu.rx1490_reading":
        return "signal_1490", [{**_common(r), "signal_1490_dbm": parse_dbm(r.get("rx1490"))} for r in rows]
    if event_type in ("onu.updated", "onu.authorized"):
        return "full", rows
    if event_type == "onu.deleted":
        return "deleted", [_common(r) for r in rows]
    return None, []


def apply_event(event_type, data):
    """Applies one webhook batch to onu_state. Returns rows touched."""
    rows = (data or {}).get("onus") if isinstance(data, dict) else None
    kind, parsed = event_rows(event_type, rows if isinstance(rows, list) else [])
    if not kind or not parsed:
        if event_type not in ("webhook.test",):
            log.info("smartolt webhook %s: nothing to store", event_type)
        return 0
    if kind == "full":
        for k in ("status", "signal", "details"):
            save(k, ROWS[k](parsed), full_list=False)
        return len(parsed)
    with db_cursor() as (conn, cur):
        if kind == "deleted":
            cur.execute("DELETE FROM onu_state WHERE unique_external_id = ANY(%s)",
                        ([r["unique_external_id"] for r in parsed],))
        elif kind == "status":
            # Only a newer change wins: an old retry arriving late can't undo a newer status.
            for r in parsed:
                cur.execute("""INSERT INTO onu_state (unique_external_id, sn, olt_id, board, port, onu, zone_id, name,
                                                      status, last_status_change)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (unique_external_id) DO UPDATE SET
                                   status = EXCLUDED.status, last_status_change = EXCLUDED.last_status_change
                               WHERE onu_state.last_status_change IS NULL OR EXCLUDED.last_status_change IS NULL
                                  OR EXCLUDED.last_status_change >= onu_state.last_status_change""",
                            tuple(r[c] for c in COMMON) + (r["status"], r["last_status_change"]))
        elif kind == "signal_1310":
            for r in parsed:
                cur.execute("""UPDATE onu_state SET signal_1310_dbm = COALESCE(%s, signal_1310_dbm),
                                                    signal_quality = COALESCE(%s, signal_quality)
                               WHERE unique_external_id = %s""",
                            (r["signal_1310_dbm"], r["signal_quality"], r["unique_external_id"]))
        elif kind == "signal_1490":
            for r in parsed:
                cur.execute("UPDATE onu_state SET signal_1490_dbm = COALESCE(%s, signal_1490_dbm) WHERE unique_external_id = %s",
                            (r["signal_1490_dbm"], r["unique_external_id"]))
        conn.commit()
    return len(parsed)


def receive_webhook(headers, body):
    """Verifies and applies one delivery. Returns (http_status, message)."""
    secret = webhook_secret()
    if secret is None:
        return 503, "webhooks aren't set up on this server"
    if len(body) > WEBHOOK_MAX_BYTES:
        return 413, "payload too large"
    msg_id = headers.get("webhook-id")
    if not verify_signature(secret, msg_id, headers.get("webhook-timestamp"), body, headers.get("webhook-signature")):
        log.warning("smartolt webhook rejected: bad or missing signature")
        return 401, "invalid signature"
    if already_seen(msg_id):
        return 200, "already done"
    import json
    try:
        payload = json.loads(body)
        event_type = payload.get("type")
    except (ValueError, AttributeError):
        return 400, "body is not a JSON object"
    try:
        touched = apply_event(event_type, payload.get("data"))
    except Exception:
        forget_seen(msg_id)        # let SmartOLT's retry try again
        log.exception("smartolt webhook %s failed", event_type)
        return 500, "couldn't store the event"
    webhook_state.update(last_at=datetime.now(timezone.utc), last_type=event_type)
    if event_type in ("onu.status_changed", "onu.deleted", "onu.updated"):
        from db import onu_alerts
        onu_alerts.tick(force=True)
    log.info("smartolt webhook %s: %s rows", event_type, touched)
    return 200, "ok"
