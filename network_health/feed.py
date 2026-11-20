"""Reads one record of the Frankfurt shipper's batch into a clean dict.

The contract is docs/network-health-ingest-contract.md. Pure code: no
database, no clock, so tests can feed it any line.

A record that can't be read is SKIPPED, not refused with an error: the
shipper resends a whole batch whenever it doesn't get a 2xx, so one bad
record answered with a 4xx would block the feed forever (every later batch
starts with it again). Skips are counted and logged by the caller.
"""
from datetime import datetime, timezone

KNOWN_KINDS = ("probe", "inner", "link", "linknew", "speed", "speednew", "route")
MAX_KEY_LENGTH = 200
MAX_RAW_LENGTH = 4000
MAX_NAME_LENGTH = 40
MAX_PROBES = 50
MAX_HOPS = 64
MAX_HOST_LENGTH = 253

LOAD_FIELDS = ("down_mbps", "up_mbps", "new_down_mbps", "ppp", "cpu_pct", "conns")
LINK_FIELDS = ("rx_dbm", "tx_dbm", "temp_c", "link_downs", "rx_fcs", "rx_err_events", "rx_overflow", "tx_drop")
SPEED_FIELDS = ("duration_s", "mbps", "load_before_mbps")


class SkipRecord(ValueError):
    """This record can't be stored; the message says why (for the log)."""


def number_or_none(value):
    """A reading's number, or None. True/False are not numbers here, and a
    string is not guessed at: the shipper sends JSON numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_timestamp(value):
    """ISO 8601 WITH an offset (the contract's `ts`) -> an aware UTC datetime.
    A time without an offset is refused: guessing its zone would shift a
    reading by three hours without anyone noticing."""
    if not isinstance(value, str):
        raise SkipRecord("ts is missing")
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SkipRecord(f"ts is not ISO 8601: {value[:40]!r}")
    if moment.tzinfo is None:
        raise SkipRecord("ts has no UTC offset")
    return moment.astimezone(timezone.utc)


def _pick_numbers(source, fields):
    """The known numeric fields of a dict; unknown fields are ignored (the
    contract lets the routers add readings without breaking the feed)."""
    if not isinstance(source, dict):
        return None
    return {field: number_or_none(source.get(field)) for field in fields}


def _probe(entry):
    """One probe result. rtt_ms null with 100 % loss is an outage and stays
    null, NEVER 0: an average that counted it as 0 ms would make an outage
    look like the best minute of the day. A 0 ms rtt with 100 % loss is
    treated the same way."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    loss = number_or_none(entry.get("loss_pct"))
    rtt = number_or_none(entry.get("rtt_ms"))
    if loss is not None and loss >= 100:
        rtt = None
    return {
        "name": name.strip()[:MAX_NAME_LENGTH],
        "rtt_ms": rtt,
        "jitter_ms": None if rtt is None else number_or_none(entry.get("jitter_ms")),
        "loss_pct": loss,
    }


def _probes(value):
    if not isinstance(value, list):
        return []
    probes = (_probe(entry) for entry in value[:MAX_PROBES])
    return [probe for probe in probes if probe is not None]


def _speed(value):
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    speed = _pick_numbers(value, SPEED_FIELDS)
    speed["status"] = status if status in ("finished", "failed") else "unknown"
    return speed


def _hop(entry):
    """One traceroute hop. A null address/ms is a hop that didn't answer
    (normal for traceroute, not loss) and stays null."""
    if not isinstance(entry, dict):
        return None
    address = entry.get("address")
    number = number_or_none(entry.get("hop"))
    return {"hop": None if number is None else int(number),
            "address": address.strip()[:64] if isinstance(address, str) and address.strip() else None,
            "ms": number_or_none(entry.get("ms"))}


def _route(value):
    """A traceroute to one site. status "failed" comes with ip null and no
    hops. A route without a host can't be placed on the page: skipped."""
    if not isinstance(value, dict):
        return None
    host = value.get("host")
    if not isinstance(host, str) or not host.strip():
        raise SkipRecord("route record has no host")
    ip = value.get("ip")
    status = value.get("status")
    hops = value.get("hops") if isinstance(value.get("hops"), list) else []
    return {
        "host": host.strip().lower()[:MAX_HOST_LENGTH],
        "ip": ip.strip()[:64] if isinstance(ip, str) and ip.strip() else None,
        "status": status if status in ("ok", "failed") else "unknown",
        "last_ms": number_or_none(value.get("last_ms")),
        "hops": [hop for hop in (_hop(entry) for entry in hops[:MAX_HOPS]) if hop is not None],
    }


def parse_record(record):
    """One record -> {key, ts, kind, from_ip, raw, load, probes, link, speed, route},
    or None for a kind this version doesn't know (ignored, as the contract
    asks). Raises SkipRecord for a record that can't be stored."""
    if not isinstance(record, dict):
        raise SkipRecord("record is not an object")
    key = record.get("key")
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_LENGTH:
        raise SkipRecord("key is missing or too long")
    kind = record.get("kind")
    if kind not in KNOWN_KINDS:
        return None
    from_ip = record.get("from_ip")
    raw = record.get("raw")
    parsed = {
        "key": key.strip(),
        "ts": parse_timestamp(record.get("ts")),
        "kind": kind,
        "from_ip": from_ip[:64] if isinstance(from_ip, str) else None,
        "raw": raw[:MAX_RAW_LENGTH] if isinstance(raw, str) else None,
        "load": None, "probes": None, "link": None, "speed": None, "route": None,
    }
    if kind in ("probe", "inner"):
        parsed["load"] = _pick_numbers(record.get("load"), LOAD_FIELDS)
        parsed["probes"] = _probes(record.get("probes"))
    elif kind in ("link", "linknew"):
        parsed["link"] = _pick_numbers(record.get("link"), LINK_FIELDS)
        if parsed["link"] is None:
            raise SkipRecord(f"{kind} record has no link object")
    elif kind == "route":
        parsed["route"] = _route(record.get("route"))
        if parsed["route"] is None:
            raise SkipRecord("route record has no route object")
    else:
        parsed["speed"] = _speed(record.get("speed"))
        if parsed["speed"] is None:
            raise SkipRecord(f"{kind} record has no speed object")
    return parsed


def parse_batch(records):
    """The whole batch -> (parsed records, ignored count, skip reasons).
    Duplicate keys inside one batch keep the first."""
    parsed, ignored, skipped, seen = [], 0, [], set()
    for record in records:
        try:
            result = parse_record(record)
        except SkipRecord as reason:
            skipped.append(str(reason))
            continue
        if result is None:
            ignored += 1
        elif result["key"] not in seen:
            seen.add(result["key"])
            parsed.append(result)
    return parsed, ignored, skipped
