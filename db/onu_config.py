"""Phase 6D: changing one ONU's configuration in SmartOLT from OPS.

Resync config, speed profiles, Ethernet port mode (LAN / Access on a VLAN /
shut down), location details (zone, splitter and port, seal number, name,
address or comment, contact), attached VLANs, ONU mode (WAN VLAN-ID, Routing
/ Bridging, WAN mode, WAN remote access) and delete. Admin only
(routers/olt.py), one ONU at a time, each confirmed in the page with a
before-and-after preview first. On from day one at the owner's call
(2026-11-11); SMARTOLT_WRITES_ENABLED=false stops them all, and each has its
own switch:

    SMARTOLT_ACTION_RESYNC, _SPEED, _PORTS, _LOCATION, _VLANS, _MODE, _DELETE

Every change goes through change(): checked against the ONU's live state,
written to onu_actions (pending), sent as one or more calls (ONU mode can
take up to four, in SmartOLT's own order), then read back from SmartOLT and
marked succeeded / mismatch / failed. Paths and parameters are from
SmartOLT's openapi.json (looked up 2026-11-11). These calls all run in the
request (no async), so there's no task to follow.

Speed: every ONU was at 1G/1G when 6D was built; the router's PPPoE plan
sets the customer's real speed. So speed is changed by hand per ONU from
SmartOLT's own profile list, never derived from billing packages.

Secrets and personal details: a PPPoE password (typed, or the current one
re-sent when only the username changes) goes to SmartOLT only. It is never
stored, logged or put in the audit row. The owner asked to be able to see
the current password (2026-11-11): pppoe_password() reads it live for an
admin on request and records who looked; it isn't cached. Address and
contact are shown live but never kept: the audit row says they changed, not
what to.
"""
import ipaddress
import logging
import re

from db import onu_links, smartolt
from db.connection import db_cursor
from db.smartolt import SmartOLTError, WritesDisabled, parse_int, parse_text

log = logging.getLogger(__name__)

TIMEOUT_S = 90               # OLT commands can take tens of seconds

FLAGS = {"resync": "resync", "speed": "speed", "port": "ports", "location": "location", "vlans": "vlans",
         "mode": "mode", "delete": "delete"}

ONU_MODES = ("Routing", "Bridging")
WAN_MODES = {"webpage": "Setup via ONU webpage", "dhcp": "DHCP", "static": "Static IP", "pppoe": "PPPoE"}
WAN_PATHS = {"webpage": "set_onu_wan_mode_setup_via_onu_webpage", "dhcp": "set_onu_wan_mode_dhcp",
             "static": "set_onu_wan_mode_static_ip", "pppoe": "set_onu_wan_mode_pppoe"}
PORT_MODES = ("lan", "access", "shutdown")
PORT_PATHS = {"lan": "set_ethernet_port_lan", "access": "set_ethernet_port_access", "shutdown": "shutdown_ethernet_port"}
DHCP = ("No control", "From ISP", "From ONU", "Forbidden")
CONFIG_METHODS = ("OMCI", "TR069")
PPPOE_TEXT = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
PROFILE_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
# SmartOLT's own character rules (update_location_details).
ZONE_TEXT = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
FREE_TEXT = re.compile(r"^[A-Za-z0-9 @#$&()\-`.+,/_]{0,120}$")
SEAL_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{0,64}$")
LOCATION_FIELDS = ("zone", "odb", "odb_port", "seal_number", "name", "address_or_comment", "contact")
PRIVATE_FIELDS = ("address_or_comment", "contact")     # never put in the audit row


class ChangeError(ValueError):
    """The request doesn't fit this ONU (bad port, VLAN, profile...)."""


def enabled(kind):
    return smartolt.action_enabled(FLAGS[kind])


def available():
    return {k: enabled(k) for k in FLAGS}


# ---- Reading what a change needs ----

def snapshot(d):
    """The parts of an ONU a 6D change is judged against. Nothing secret,
    no address or contact (they go in the audit row)."""
    s = smartolt.shape_details(d)
    return {"name": s["name"], "sn": s["sn"], "status": s["status"], "olt_id": s["olt_id"], "pon_type": s["pon_type"],
            "mode": s["mode"], "wan_mode": s["wan_mode"], "vlan": parse_int(s["vlan"]),
            "pppoe_username": s["pppoe_username"], "vlans": s["attached_vlans"], "ethernet_ports": s["ethernet_ports"],
            "zone": s["zone"], "odb": s["odb"], "odb_port": s["odb_port"], "seal_number": s["seal_number"],
            "resync_failed": s["resync_failed"]}


def _private(d):
    """Address and contact as SmartOLT has them now (compared, never kept)."""
    return {"address_or_comment": parse_text(d.get("address")) or "", "contact": parse_text(d.get("contact")) or ""}


def speed_now(onu_id):
    body = smartolt.call("GET", f"/api/onu/get_onu_speed_profiles/{onu_id}")
    return {"upload": parse_text(body.get("upload_speed_profile_name")),
            "download": parse_text(body.get("download_speed_profile_name"))}


def speed_choices():
    """SmartOLT's internet speed profiles by direction (cached 10 minutes)."""
    def load():
        rows = smartolt._list(smartolt.call("GET", "/api/system/get_speed_profiles"))
        out = {"upload": [], "download": []}
        for r in rows:
            name, direction = parse_text(r.get("name")), parse_text(r.get("direction"))
            if name and direction in out and (parse_text(r.get("type")) or "internet") == "internet" and name not in out[direction]:
                out[direction].append(name)
        return out
    return smartolt.cached(("opt", "speed-choices"), 600, load)


def speed_info(onu_id):
    smartolt.check_id(onu_id)
    return {"current": speed_now(onu_id), "choices": speed_choices()}


def _options(before):
    return smartolt.authorize_options(before["olt_id"] or 1, (before["pon_type"] or "gpon").lower())


def _olt_vlans(before):
    return {v["vlan"] for v in _options(before)["vlans"] if v["vlan"] is not None}


def pppoe_password(onu_id, user_id):
    """The ONU's current PPPoE password, read live for an admin who asked to
    see it. Not cached, not stored; the reveal itself is recorded."""
    smartolt.check_id(onu_id)
    raw = smartolt.call("GET", f"/api/onu/get_onu_details/{onu_id}").get("onu_details") or {}
    if not raw:
        raise SmartOLTError("SmartOLT doesn't know this ONU.")
    password = raw.get("password")
    password = password if isinstance(password, str) and password else None
    action_id = smartolt._audit_start("view_password", parse_text(raw.get("sn")), {"onu_id": onu_id}, user_id, onu_id)
    smartolt._audit_finish(action_id, "succeeded", "Viewed the PPPoE password" if password else "No PPPoE password set")
    return password


def _current_password(onu_id):
    raw = smartolt.call("GET", f"/api/onu/get_onu_details/{onu_id}").get("onu_details") or {}
    password = raw.get("password")
    return password if isinstance(password, str) and password else None


# ---- Planning: what to send and what the read-back must show ----

def _port(before, name):
    for p in before["ethernet_ports"]:
        if p["port"] == name:
            return p
    raise ChangeError(f"This ONU has no Ethernet port {name!r}.")


def _ip(value, label):
    try:
        return str(ipaddress.IPv4Address((value or "").strip()))
    except ValueError:
        raise ChangeError(f"{label} must be an IPv4 address.") from None


def plan(kind, params, before, onu_id, private=None, current_password=None):
    """(steps, audit_params, summary): steps is a list of (path, form) sent
    in order. Raises ChangeError when the request doesn't fit the ONU as
    SmartOLT has it right now."""
    p = params or {}
    if kind == "resync":
        return [(f"/api/onu/resync_config/{onu_id}", None)], {}, "Resync config"
    if kind == "port":
        return _plan_port(p, before, onu_id)
    if kind == "speed":
        return _plan_speed(p, before, onu_id)
    if kind == "location":
        return _plan_location(p, before, onu_id, private or {"address_or_comment": "", "contact": ""})
    if kind == "vlans":
        return _plan_vlans(p, before, onu_id)
    if kind == "mode":
        return _plan_mode(p, before, onu_id, current_password)
    if kind == "delete":
        typed = (p.get("confirm_sn") or "").strip().upper()
        if not before["sn"] or typed != before["sn"].upper():
            raise ChangeError("Type the ONU's serial exactly to delete it.")
        link = onu_links.onu_customer(onu_id)
        if link and not link.get("not_a_customer"):
            raise ChangeError("This ONU is linked to a customer or hotspot site. Unlink it on OLT > Links first.")
        return [(f"/api/onu/delete/{onu_id}", None)], {"confirm_sn": before["sn"]}, f"Delete ONU {before['sn']}"
    raise ChangeError("Unknown change.")


def _plan_port(p, before, onu_id):
    name, mode = p.get("port"), p.get("mode")
    if mode not in PORT_MODES:
        raise ChangeError("Port mode must be LAN, Access or Shut down.")
    port = _port(before, name)
    form, audit = {"ethernet_port": name}, {"port": name, "mode": mode}
    if mode == "access":
        vlan = parse_int(p.get("vlan"))
        if vlan not in before["vlans"]:
            raise ChangeError(f"VLAN must be one of this ONU's attached VLANs ({', '.join(map(str, before['vlans'])) or 'none'}).")
        form["vlan"] = audit["vlan"] = vlan
    if mode in ("lan", "access") and p.get("dhcp"):
        if p["dhcp"] not in DHCP:
            raise ChangeError("Unknown DHCP setting.")
        form["dhcp"] = audit["dhcp"] = p["dhcp"]
    was = f"{port['mode'] or '?'}{' ' + port['vlan'] if port['vlan'] else ''}{' (off)' if port['admin_state'] == 'Disabled' else ''}"
    to = {"lan": "LAN", "access": f"Access {form.get('vlan')}", "shutdown": "shut down"}[mode]
    return [(f"/api/onu/{PORT_PATHS[mode]}/{onu_id}", form)], audit, f"{name}: {was} → {to}"


def _plan_speed(p, before, onu_id):
    choices = speed_choices()
    form = {}
    for direction in ("upload", "download"):
        name = p.get(direction)
        if not name:
            continue
        if not PROFILE_NAME.match(name) or name not in choices[direction]:
            raise ChangeError(f"{name!r} isn't one of SmartOLT's {direction} speed profiles.")
        form[f"{direction}_speed_profile_name"] = name
    if not form:
        raise ChangeError("Pick an upload or a download speed profile.")
    audit = {"upload": p.get("upload"), "download": p.get("download")}
    was = before.get("speed") or {}
    summary = ", ".join(f"{d.capitalize()} {was.get(d) or '?'} → {p[d]}" for d in ("download", "upload") if p.get(d))
    return [(f"/api/onu/update_onu_speed_profiles/{onu_id}", form)], audit, summary


def _plan_location(p, before, onu_id, private):
    now = {"zone": before["zone"] or "", "odb": before["odb"] or "", "odb_port": before["odb_port"],
           "seal_number": before["seal_number"] or "", "name": before["name"] or "", **private}
    form, audit, parts = {}, {}, []
    zone = (p.get("zone") or "").strip()
    if zone and zone != now["zone"]:
        if not ZONE_TEXT.match(zone) or zone not in {z["name"] for z in _options(before)["zones"]}:
            raise ChangeError(f"{zone!r} isn't one of SmartOLT's zones.")
        form["zone"] = audit["zone"] = zone
        parts.append(f"Zone {now['zone'] or 'none'} → {zone}")
    target_zone = zone or now["zone"]
    odb = (p.get("odb") or "").strip()
    if "odb" in p and odb != now["odb"]:
        if not odb:
            raise ChangeError("SmartOLT's API can't take a splitter off an ONU; do that in SmartOLT.")
        zone_id = next((z["id"] for z in _options(before)["zones"] if z["name"] == target_zone), None)
        names = {o["name"] for o in smartolt.odbs(zone_id)} if zone_id is not None else set()
        if not ZONE_TEXT.match(odb) or odb not in names:
            raise ChangeError(f"{odb!r} isn't a splitter in zone {target_zone}.")
        form["odb"] = audit["odb"] = odb
        parts.append(f"Splitter {now['odb'] or 'none'} → {odb}")
    if p.get("odb_port") is not None:
        port = parse_int(p.get("odb_port"))
        if port is None or not 1 <= port <= 128:
            raise ChangeError("Splitter port must be a number from 1.")
        if port != now["odb_port"] or "odb" in form:
            form["odb_port"] = audit["odb_port"] = port
            parts.append(f"Splitter port {now['odb_port'] or 'none'} → {port}")
    for field, label, rule in (("seal_number", "Seal number", SEAL_TEXT), ("name", "Name", FREE_TEXT),
                               ("address_or_comment", "Address or comment", FREE_TEXT), ("contact", "Contact", FREE_TEXT)):
        if field not in p:
            continue
        value = (p.get(field) or "").strip()
        if value == now[field]:
            continue
        if not rule.match(value) or (field == "name" and not value):
            raise ChangeError(f"{label}: letters, numbers, spaces and @#$&()-`.+,/_ only{' (and not empty)' if field == 'name' else ''}.")
        if field == "seal_number" and not value:
            value = "unknown"            # SmartOLT's way of clearing it
        form[field] = value
        if field in PRIVATE_FIELDS:
            audit[field] = "changed"
            parts.append(f"{label} changed")
        else:
            audit[field] = value
            parts.append(f"{label} {now[field] or 'none'} → {value}")
    if not form:
        raise ChangeError("Nothing to change.")
    return [(f"/api/onu/update_location_details/{onu_id}", form)], audit, "; ".join(parts)


def _plan_vlans(p, before, onu_id):
    want = []
    for v in p.get("vlans") or []:
        v = parse_int(v)
        if v is None or not 1 <= v <= 4094:
            raise ChangeError("VLANs are numbers from 1 to 4094.")
        if v not in want:
            want.append(v)
    if not want:
        raise ChangeError("An ONU needs at least one VLAN.")
    have = before["vlans"]
    if before["vlan"] is not None and before["vlan"] not in want:
        raise ChangeError(f"VLAN {before['vlan']} is this ONU's WAN VLAN. Change the WAN VLAN-ID first (ONU mode).")
    unknown = [v for v in want if v not in have and v not in _olt_vlans(before)]
    if unknown:
        raise ChangeError(f"VLAN {', '.join(map(str, unknown))} isn't set up on this OLT.")
    add = [v for v in want if v not in have]
    remove = [v for v in have if v not in want]
    if not add and not remove:
        raise ChangeError("Nothing to change.")
    form = {}
    if add:
        form["add_vlans"] = ",".join(map(str, add))
    if remove:
        form["remove_vlans"] = ",".join(map(str, remove))
    summary = "; ".join(x for x in (f"Add VLAN {', '.join(map(str, add))}" if add else "",
                                    f"Remove VLAN {', '.join(map(str, remove))}" if remove else "") if x)
    return [(f"/api/onu/update_attached_vlans/{onu_id}", form)], {"vlans": want, "add": add, "remove": remove}, summary


def _plan_mode(p, before, onu_id, current_password):
    """SmartOLT's "Update ONU mode" dialog as up to four calls: WAN VLAN-ID,
    ONU mode, WAN mode, WAN remote access."""
    steps, audit, parts = [], {}, []
    vlan = parse_int(p.get("vlan"))
    if vlan is not None and vlan != before["vlan"]:
        if vlan not in before["vlans"] and vlan not in _olt_vlans(before):
            raise ChangeError(f"VLAN {vlan} isn't set up on this OLT.")
        steps.append((f"/api/onu/update_main_vlan/{onu_id}", {"vlan": vlan}))
        audit["vlan"] = vlan
        parts.append(f"WAN VLAN {before['vlan'] or '?'} → {vlan}")
    mode = p.get("onu_mode") or before["mode"]
    if mode not in ONU_MODES:
        raise ChangeError("ONU mode must be Routing or Bridging.")
    if mode != before["mode"]:
        steps.append((f"/api/onu/update_onu_mode/{onu_id}", {"onu_mode": mode}))
        audit["onu_mode"] = mode
        parts.append(f"ONU mode {before['mode'] or '?'} → {mode}")
    wan = p.get("wan_mode")
    if wan and mode == "Routing":
        if wan not in WAN_MODES:
            raise ChangeError("Unknown WAN mode.")
        form = {}
        if p.get("configuration_method"):
            if p["configuration_method"] not in CONFIG_METHODS:
                raise ChangeError("Configuration method must be OMCI or TR069.")
            form["configuration_method"] = p["configuration_method"]
        detail = ""
        if wan == "pppoe":
            user = (p.get("username") or "").strip()
            if not PPPOE_TEXT.match(user):
                raise ChangeError("PPPoE username: letters and numbers, up to 64.")
            password = p.get("password") or ""
            same_user = before["wan_mode"] == "PPPoE" and user == (before["pppoe_username"] or "")
            if not password and same_user and not form:
                wan = None                                   # nothing about the WAN changes
            else:
                if not password:
                    password = current_password() if (current_password and before["wan_mode"] == "PPPoE") else ""
                    if not password:
                        raise ChangeError("Type the PPPoE password (SmartOLT has none to keep).")
                if not PPPOE_TEXT.match(password):
                    raise ChangeError("PPPoE password: letters and numbers, up to 64, no spaces.")
                form.update(username=user, password=password)
                detail = f", username {user}" + ("" if p.get("password") else ", same password")
        elif wan == "static":
            for key, label in (("ipv4_address", "IP address"), ("subnet_mask", "Subnet mask"), ("gateway", "Gateway"), ("dns1", "DNS 1")):
                form[key] = _ip(p.get(key), label)
            if p.get("dns2"):
                form["dns2"] = _ip(p.get("dns2"), "DNS 2")
            detail = f", {form['ipv4_address']}/{form['subnet_mask']} via {form['gateway']}"
        elif WAN_MODES[wan] == before["wan_mode"] and not form:
            wan = None
        if wan:
            steps.append((f"/api/onu/{WAN_PATHS[wan]}/{onu_id}", form or None))
            audit["wan_mode"] = WAN_MODES[wan]
            audit.update({k: v for k, v in form.items() if k != "password"})
            parts.append(f"WAN mode {before['wan_mode'] or '?'} → {WAN_MODES[wan]}{detail}")
    remote = p.get("remote_access")
    if remote in ("enable", "disable"):
        steps.append((f"/api/onu/{remote}_allow_remote_access_to_wan_ip/{onu_id}",
                      {"allow_access_from": "everywhere"} if remote == "enable" else None))
        audit["remote_access"] = remote
        parts.append(f"WAN remote access {'on' if remote == 'enable' else 'off'}")
    elif remote:
        raise ChangeError("Unknown WAN remote access setting.")
    if not steps:
        raise ChangeError("Nothing to change.")
    return steps, audit, "; ".join(parts)


def check(kind, params, after, onu_id, private=None):
    """Mismatches between what was asked and SmartOLT's read-back. For
    address and contact only the field name is reported, never the text."""
    p = params or {}
    out = []

    def differs(field, want, have, quiet=False):
        if str(want if want is not None else "").strip().lower() != str(have if have is not None else "").strip().lower():
            out.append({"field": field, "sent": None if quiet else want, "smartolt_has": None if quiet else have})

    if kind == "mode":
        if "vlan" in p:
            differs("vlan", p["vlan"], after["vlan"])
        if "onu_mode" in p:
            differs("onu_mode", p["onu_mode"], after["mode"])
        if "wan_mode" in p:
            differs("wan_mode", p["wan_mode"], after["wan_mode"])
        if "username" in p:
            differs("username", p["username"], after["pppoe_username"])
    elif kind == "port":
        port = next((x for x in after["ethernet_ports"] if x["port"] == p["port"]), None)
        if port is None:
            out.append({"field": "port", "sent": p["port"], "smartolt_has": None})
        elif p["mode"] == "shutdown":
            differs("admin_state", "Disabled", port["admin_state"])
        else:
            differs("mode", "LAN" if p["mode"] == "lan" else "Access", port["mode"])
            differs("admin_state", "Enabled", port["admin_state"])
            if p["mode"] == "access":
                differs("vlan", str(parse_int(p.get("vlan"))), port["vlan"])
    elif kind == "location":
        for field in ("zone", "odb", "odb_port", "name"):
            if field in p:
                differs(field, p[field], after[field])
        if "seal_number" in p:
            differs("seal_number", "" if p["seal_number"] == "unknown" else p["seal_number"], after["seal_number"])
        for field in PRIVATE_FIELDS:
            if field in p and private is not None:
                differs(field, p[field], private[field], quiet=True)
    elif kind == "vlans":
        if sorted(p["vlans"]) != sorted(after["vlans"]):
            out.append({"field": "vlans", "sent": sorted(p["vlans"]), "smartolt_has": sorted(after["vlans"])})
    elif kind == "resync":
        if after["resync_failed"]:
            out.append({"field": "resync", "sent": "resynced", "smartolt_has": "last resync failed"})
    return out


# ---- Doing it ----

def _save_after(raw):
    try:
        smartolt.save("details", smartolt.details_rows([raw]), full_list=False)
    except Exception:
        log.exception("onu_config: couldn't save the ONU after a change")


def _drop_local(onu_id):
    """After a delete: SmartOLT no longer has it, so neither does OPS. A
    "not a customer" mark goes with it; an open LOS alert closes on its next
    check (the ONU is no longer LOS)."""
    with db_cursor() as (conn, cur):
        cur.execute("DELETE FROM onu_customer_links WHERE unique_external_id = %s", (onu_id,))
        cur.execute("DELETE FROM onu_state WHERE unique_external_id = %s", (onu_id,))
        conn.commit()


def _sent(kind, form):
    """What a step's form means for the read-back (no password)."""
    form = form or {}
    if kind == "location":
        return {k: form[k] for k in LOCATION_FIELDS if k in form}
    return {}


def change(onu_id, kind, params, user_id):
    """Applies one change to one ONU. Returns the audit row."""
    smartolt.check_id(onu_id)
    if kind not in FLAGS:
        raise ChangeError("Unknown change.")
    if not smartolt.writes_enabled():
        raise WritesDisabled("SmartOLT writes are switched off (SMARTOLT_WRITES_ENABLED).")
    if not enabled(kind):
        raise WritesDisabled(f"This change is switched off (SMARTOLT_ACTION_{FLAGS[kind].upper()}).")
    with smartolt._lock:
        if onu_id in smartolt._acting:
            raise SmartOLTError("Another action on this ONU is still running.")
        smartolt._acting.add(onu_id)
    try:
        raw = smartolt._live_details(onu_id)
        if not raw:
            raise SmartOLTError("SmartOLT doesn't know this ONU.")
        before = snapshot(raw)
        if kind == "speed":
            before["speed"] = speed_now(onu_id)
        steps, audit_params, summary = plan(kind, params, before, onu_id, private=_private(raw),
                                            current_password=lambda: _current_password(onu_id))
        action_id = smartolt._audit_start(kind, before["sn"], {"onu_id": onu_id, "change": audit_params,
                                                               "summary": summary, "before": before}, user_id, onu_id)
        answers = []
        for i, (path, form) in enumerate(steps):
            try:
                body = smartolt.call("POST", path, data=form, timeout=TIMEOUT_S)
            except SmartOLTError as e:
                done = f" Already done: {'; '.join(answers)}." if answers else ""
                smartolt._audit_finish(action_id, "failed", f"{e}.{done}")
                smartolt.forget("details", "status")
                raise SmartOLTError(f"{e}.{done}") from None
            answers.append((body.get("response") if isinstance(body.get("response"), str) else "accepted").rstrip(". "))
        smartolt.forget("details", "status")
        answer = "; ".join(answers)
        expect = _expected(kind, params, steps)
        result, note, read_back = _read_back(kind, expect, onu_id)
        smartolt._audit_finish(action_id, result, f"{answer}. {note}" if note else answer, read_back)
        return smartolt.get_action(action_id)
    finally:
        with smartolt._lock:
            smartolt._acting.discard(onu_id)


def _expected(kind, params, steps):
    """What the read-back must show, built from what was actually sent."""
    p = params or {}
    if kind == "mode":
        out = {}
        for path, form in steps:
            form = form or {}
            if "/update_main_vlan/" in path:
                out["vlan"] = form["vlan"]
            elif "/update_onu_mode/" in path:
                out["onu_mode"] = form["onu_mode"]
            elif "/set_onu_wan_mode_" in path:
                key = next(k for k, v in WAN_PATHS.items() if f"/{v}/" in path)
                out["wan_mode"] = WAN_MODES[key]
                if key == "pppoe":
                    out["username"] = form["username"]
        return out
    if kind == "location":
        return _sent(kind, steps[0][1])
    if kind == "vlans":
        return {"vlans": [parse_int(v) for v in p.get("vlans") or []]}
    return p


def _read_back(kind, expect, onu_id):
    """(result, note, read_back) after SmartOLT accepted the change."""
    if kind == "delete":
        try:
            still = smartolt._live_details(onu_id)
        except SmartOLTError as e:
            if "no onu was found" not in str(e).lower():
                return "mismatch", f"Couldn't check it's gone ({e}).", None
            still = None
        if still:
            return "mismatch", "SmartOLT still lists this ONU.", {"after": snapshot(still)}
        try:
            _drop_local(onu_id)
        except Exception:
            log.exception("onu_config: couldn't drop the deleted ONU locally")
        return "succeeded", "Gone from SmartOLT; it shows under Unconfigured while it's still plugged in.", {"after": None}
    if kind == "speed":
        try:
            now = speed_now(onu_id)
        except SmartOLTError as e:
            return "mismatch", f"Couldn't read it back ({e}).", None
        diffs = [{"field": d, "sent": expect[d], "smartolt_has": now[d]}
                 for d in ("upload", "download") if expect.get(d) and expect[d] != now[d]]
        return ("mismatch" if diffs else "succeeded"), _diff_note(diffs), {"after": {"speed": now}, "mismatches": diffs}
    try:
        raw = smartolt._live_details(onu_id)
    except SmartOLTError as e:
        return "mismatch", f"Couldn't read it back ({e}).", None
    after = snapshot(raw)
    _save_after(raw)
    diffs = check(kind, expect, after, onu_id, private=_private(raw))
    return ("mismatch" if diffs else "succeeded"), _diff_note(diffs), {"after": after, "mismatches": diffs}


def _diff_note(diffs):
    if not diffs:
        return "Read back from SmartOLT: matches."
    return "Read back differs: " + "; ".join(
        f"{d['field']} is {d['smartolt_has'] or 'empty'}, not {d['sent']}" if d["sent"] is not None
        else f"{d['field']} isn't what was sent" for d in diffs) + "."
