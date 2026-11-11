"""Phase 6D: changing one ONU's configuration in SmartOLT from OPS.

Resync config, speed profiles, Ethernet port mode (LAN / Access on a VLAN /
shut down), ONU mode (Routing / Bridging), WAN mode (PPPoE / DHCP / Setup via
ONU webpage) and delete. Admin only (routers/olt.py), one ONU at a time,
each confirmed in the page with a before-and-after preview first. On from
day one at the owner's call (2026-11-11); SMARTOLT_WRITES_ENABLED=false stops
them all, and each has its own switch:

    SMARTOLT_ACTION_RESYNC, _SPEED, _PORTS, _MODE (ONU and WAN mode), _DELETE

Every change goes through change(): checked against the ONU's live state,
written to onu_actions (pending), sent, then read back from SmartOLT and
marked succeeded / mismatch / failed. Paths and parameters are from
SmartOLT's openapi.json (looked up 2026-11-11). These calls all run in the
request (no async), so there's no task to follow.

Speed: every ONU was at 1G/1G when 6D was built; the router's PPPoE plan
sets the customer's real speed. So speed is changed by hand per ONU from
SmartOLT's own profile list, never derived from billing packages.

A PPPoE password typed for a WAN change goes to SmartOLT only. It is never
stored, logged or put in the audit row.
"""
import logging
import re

from db import onu_links, smartolt
from db.connection import db_cursor
from db.smartolt import SmartOLTError, WritesDisabled, parse_text

log = logging.getLogger(__name__)

TIMEOUT_S = 90               # OLT commands can take tens of seconds

FLAGS = {"resync": "resync", "speed": "speed", "port": "ports", "onu_mode": "mode", "wan_mode": "mode", "delete": "delete"}

ONU_MODES = ("Routing", "Bridging")
WAN_MODES = {"pppoe": "PPPoE", "dhcp": "DHCP", "webpage": "Setup via ONU webpage"}
WAN_PATHS = {"pppoe": "set_onu_wan_mode_pppoe", "dhcp": "set_onu_wan_mode_dhcp", "webpage": "set_onu_wan_mode_setup_via_onu_webpage"}
PORT_MODES = ("lan", "access", "shutdown")
PORT_PATHS = {"lan": "set_ethernet_port_lan", "access": "set_ethernet_port_access", "shutdown": "shutdown_ethernet_port"}
DHCP = ("No control", "From ISP", "From ONU", "Forbidden")
CONFIG_METHODS = ("OMCI", "TR069")
PPPOE_TEXT = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
PROFILE_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")


class ChangeError(ValueError):
    """The request doesn't fit this ONU (bad port, VLAN, profile...)."""


def enabled(kind):
    return smartolt.action_enabled(FLAGS[kind])


def available():
    return {k: enabled(k) for k in FLAGS}


# ---- Reading what a change needs ----

def snapshot(d):
    """The parts of an ONU a 6D change is judged against. Nothing secret."""
    s = smartolt.shape_details(d)
    return {"name": s["name"], "sn": s["sn"], "status": s["status"], "mode": s["mode"], "wan_mode": s["wan_mode"],
            "pppoe_username": s["pppoe_username"], "vlans": s["attached_vlans"], "ethernet_ports": s["ethernet_ports"],
            "resync_failed": str(d.get("is_failed_resync_config") or "0") == "1"}


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


# ---- Planning: what to send and what the read-back must show ----

def _port(before, name):
    for p in before["ethernet_ports"]:
        if p["port"] == name:
            return p
    raise ChangeError(f"This ONU has no Ethernet port {name!r}.")


def plan(kind, params, before, onu_id):
    """(path, form, audit_params, summary). Raises ChangeError when the
    request doesn't fit the ONU as SmartOLT has it right now."""
    p = params or {}
    if kind == "resync":
        return f"/api/onu/resync_config/{onu_id}", None, {}, "Resync config"
    if kind == "onu_mode":
        mode = p.get("onu_mode")
        if mode not in ONU_MODES:
            raise ChangeError("ONU mode must be Routing or Bridging.")
        if mode == before["mode"]:
            raise ChangeError(f"The ONU is already in {mode} mode.")
        return f"/api/onu/update_onu_mode/{onu_id}", {"onu_mode": mode}, {"onu_mode": mode}, f"ONU mode {before['mode'] or '?'} → {mode}"
    if kind == "wan_mode":
        mode = p.get("wan_mode")
        if mode not in WAN_MODES:
            raise ChangeError("Unknown WAN mode.")
        form, audit = {}, {"wan_mode": WAN_MODES[mode]}
        if p.get("configuration_method"):
            if p["configuration_method"] not in CONFIG_METHODS:
                raise ChangeError("Configuration method must be OMCI or TR069.")
            form["configuration_method"] = audit["configuration_method"] = p["configuration_method"]
        if mode == "pppoe":
            user, password = (p.get("username") or "").strip(), p.get("password") or ""
            if not PPPOE_TEXT.match(user):
                raise ChangeError("PPPoE username: letters and numbers, up to 64.")
            if not PPPOE_TEXT.match(password):
                raise ChangeError("PPPoE password: letters and numbers, up to 64, no spaces.")
            form.update(username=user, password=password)
            audit["username"] = user                       # never the password
        return (f"/api/onu/{WAN_PATHS[mode]}/{onu_id}", form or None, audit,
                f"WAN mode {before['wan_mode'] or '?'} → {WAN_MODES[mode]}")
    if kind == "port":
        name, mode = p.get("port"), p.get("mode")
        if mode not in PORT_MODES:
            raise ChangeError("Port mode must be LAN, Access or Shut down.")
        port = _port(before, name)
        form, audit = {"ethernet_port": name}, {"port": name, "mode": mode}
        if mode == "access":
            vlan = smartolt.parse_int(p.get("vlan"))
            if vlan not in before["vlans"]:
                raise ChangeError(f"VLAN must be one of this ONU's attached VLANs ({', '.join(map(str, before['vlans'])) or 'none'}).")
            form["vlan"] = audit["vlan"] = vlan
        if mode in ("lan", "access") and p.get("dhcp"):
            if p["dhcp"] not in DHCP:
                raise ChangeError("Unknown DHCP setting.")
            form["dhcp"] = audit["dhcp"] = p["dhcp"]
        was = f"{port['mode'] or '?'}{' ' + port['vlan'] if port['vlan'] else ''}{' (off)' if port['admin_state'] == 'Disabled' else ''}"
        to = {"lan": "LAN", "access": f"Access {form.get('vlan')}", "shutdown": "shut down"}[mode]
        return f"/api/onu/{PORT_PATHS[mode]}/{onu_id}", form, audit, f"{name}: {was} → {to}"
    if kind == "speed":
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
        return f"/api/onu/update_onu_speed_profiles/{onu_id}", form, audit, \
            ", ".join(f"{d.capitalize()} {was.get(d) or '?'} → {p[d]}" for d in ("download", "upload") if p.get(d))
    if kind == "delete":
        typed = (p.get("confirm_sn") or "").strip().upper()
        if not before["sn"] or typed != before["sn"].upper():
            raise ChangeError("Type the ONU's serial exactly to delete it.")
        link = onu_links.onu_customer(onu_id)
        if link and not link.get("not_a_customer"):
            raise ChangeError("This ONU is linked to a customer or hotspot site. Unlink it on OLT > Links first.")
        return f"/api/onu/delete/{onu_id}", None, {"confirm_sn": before["sn"]}, f"Delete ONU {before['sn']}"
    raise ChangeError("Unknown change.")


def check(kind, params, after, onu_id):
    """Mismatches between what was asked and SmartOLT's read-back."""
    p = params or {}
    out = []

    def differs(field, want, have):
        if str(want or "").strip().lower() != str(have or "").strip().lower():
            out.append({"field": field, "sent": want, "smartolt_has": have})

    if kind == "onu_mode":
        differs("onu_mode", p["onu_mode"], after["mode"])
    elif kind == "wan_mode":
        differs("wan_mode", WAN_MODES[p["wan_mode"]], after["wan_mode"])
        if p["wan_mode"] == "pppoe":
            differs("username", (p.get("username") or "").strip(), after["pppoe_username"])
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
                differs("vlan", str(smartolt.parse_int(p.get("vlan"))), port["vlan"])
    elif kind == "resync":
        if after["resync_failed"]:
            out.append({"field": "resync", "sent": "resynced", "smartolt_has": "last resync failed"})
    return out


# ---- Doing it ----

def _forget(onu_id):
    smartolt.forget("details", "status", "speed")


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
        path, form, audit_params, summary = plan(kind, params, before, onu_id)
        action_id = smartolt._audit_start(kind, before["sn"], {"onu_id": onu_id, "change": audit_params,
                                                               "summary": summary, "before": before}, user_id, onu_id)
        try:
            body = smartolt.call("POST", path, data=form, timeout=TIMEOUT_S)
        except SmartOLTError as e:
            smartolt._audit_finish(action_id, "failed", str(e))
            raise
        answer = (body.get("response") if isinstance(body.get("response"), str) else "accepted").rstrip(". ")
        _forget(onu_id)
        result, note, read_back = _read_back(kind, params, onu_id, before)
        smartolt._audit_finish(action_id, result, f"{answer}. {note}" if note else answer, read_back)
        return smartolt.get_action(action_id)
    finally:
        with smartolt._lock:
            smartolt._acting.discard(onu_id)


def _read_back(kind, params, onu_id, before):
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
        diffs = [{"field": d, "sent": params[d], "smartolt_has": now[d]}
                 for d in ("upload", "download") if params.get(d) and params[d] != now[d]]
        return ("mismatch" if diffs else "succeeded"), _diff_note(diffs), {"after": {"speed": now}, "mismatches": diffs}
    try:
        raw = smartolt._live_details(onu_id)
    except SmartOLTError as e:
        return "mismatch", f"Couldn't read it back ({e}).", None
    after = snapshot(raw)
    _save_after(raw)
    diffs = check(kind, params, after, onu_id)
    return ("mismatch" if diffs else "succeeded"), _diff_note(diffs), {"after": after, "mismatches": diffs}


def _diff_note(diffs):
    if not diffs:
        return "Read back from SmartOLT: matches."
    return "Read back differs: " + "; ".join(f"{d['field']} is {d['smartolt_has'] or 'empty'}, not {d['sent']}" for d in diffs) + "."
