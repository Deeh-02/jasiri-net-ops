"""OLT page (Phase 6, SmartOLT): the Configured list, one ONU's details,
graphs, the Unconfigured list and Authorize.

READS need Sites > Network on the role (sites:view_network; admins always),
the same switch as Network Analysis. AUTHORIZE is admin only, and refused when
SMARTOLT_WRITES_ENABLED is false. Every reply has been through
smartolt.scrub(): no password ever reaches the browser.
"""
import logging
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg2 import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from db import onu_alerts, onu_config, onu_links, smartolt
from db.activity import search_customers
from routers.network import require_network_view
from routers.permissions import require_admin

router = APIRouter()
log = logging.getLogger(__name__)


def _smartolt_failed(e):
    raise HTTPException(status_code=502, detail=f"{e}")


@router.get("/olt/onus")
def configured_onus(current_user: dict = Depends(require_network_view)):
    # Loading the page also starts any poll that's due, so the list stays
    # fresh while the router isn't reporting.
    smartolt.poll_if_due()
    onu_alerts.tick()
    try:
        data = smartolt.configured_list()
    except pg_errors.UndefinedColumn:
        raise HTTPException(status_code=503, detail="The OLT tables aren't set up yet (migration 0032).")
    except pg_errors.UndefinedTable:
        raise HTTPException(status_code=503, detail="The OLT tables aren't set up yet (migrations 0031 and 0032).")
    return {**data, "can_authorize": current_user["role"] == "admin"}


@router.get("/olt/onus/{onu_id}")
def onu_details(onu_id: str, current_user: dict = Depends(require_network_view)):
    try:
        return {**smartolt.onu_details(onu_id), "customer": onu_links.onu_customer(onu_id),
                "can_act": current_user["role"] == "admin",
                "actions": {**smartolt.actions_available(), **onu_config.available()}}
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/onus/{onu_id}/status")
def onu_status(onu_id: str, current_user: dict = Depends(require_network_view)):
    try:
        return smartolt.onu_live_status(onu_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/onus/{onu_id}/graph/{kind}/{period}")
def onu_graph(onu_id: str, kind: str, period: str, current_user: dict = Depends(require_network_view)):
    try:
        png = smartolt.onu_graph(onu_id, kind, period)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown graph")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/olt/unconfigured")
def unconfigured_onus(current_user: dict = Depends(require_network_view)):
    try:
        rows = smartolt.unconfigured()
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)
    return {"onus": rows, "can_authorize": current_user["role"] == "admin", "writes_enabled": smartolt.writes_enabled()}


@router.get("/olt/authorize-options")
def authorize_options(olt_id: int = 1, pon_type: str = "gpon", current_user: dict = Depends(require_admin)):
    try:
        return smartolt.authorize_options(olt_id, pon_type)
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/zones/{zone_id}/odbs")
def zone_odbs(zone_id: int, current_user: dict = Depends(require_admin)):
    try:
        return {"odbs": smartolt.odbs(zone_id)}
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


# SmartOLT's own character rules for these fields (openapi.json).
NAME_CHARS = re.compile(r"^[A-Za-z0-9 @#$&()\-`.+,/_]*$")
ZONE_CHARS = re.compile(r"^[A-Za-z0-9 _\-]*$")
PROFILE_CHARS = re.compile(r"^[A-Za-z0-9 _.\-/]*$")


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    olt_id: int = Field(ge=1)
    pon_type: Literal["gpon", "epon"]
    sn: str = Field(min_length=4, max_length=40, pattern=r"^[A-Za-z0-9:]+$")
    board: int = Field(ge=0, le=64)
    port: int = Field(ge=0, le=256)
    onu_type: str = Field(min_length=1, max_length=64)
    onu_mode: Literal["Routing", "Bridging"]
    vlan: Optional[int] = Field(default=None, ge=1, le=4094)
    zone: str = Field(min_length=1, max_length=64)
    odb: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    address_or_comment: Optional[str] = Field(default=None, max_length=120)
    onu_external_id: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9]*$")
    upload_speed_profile_name: Optional[str] = Field(default=None, max_length=40)
    download_speed_profile_name: Optional[str] = Field(default=None, max_length=40)

    @field_validator("name", "address_or_comment")
    @classmethod
    def _name_chars(cls, v):
        if v and not NAME_CHARS.match(v):
            raise ValueError("only letters, numbers, spaces and @#$&()-`.+,/_ are allowed")
        return v

    @field_validator("zone", "odb")
    @classmethod
    def _zone_chars(cls, v):
        if v and not ZONE_CHARS.match(v):
            raise ValueError("only letters, numbers, spaces, - and _ are allowed")
        return v

    @field_validator("onu_type", "upload_speed_profile_name", "download_speed_profile_name")
    @classmethod
    def _profile_chars(cls, v):
        if v and not PROFILE_CHARS.match(v):
            raise ValueError("unexpected characters")
        return v


@router.post("/olt/authorize")
def authorize(body: AuthorizeRequest, current_user: dict = Depends(require_admin)):
    try:
        return smartolt.authorize(body.model_dump(), current_user["id"])
    except smartolt.WritesDisabled as e:
        raise HTTPException(status_code=403, detail=str(e))
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/actions")
def recent_actions(action: Optional[str] = None, onu: Optional[str] = None,
                   current_user: dict = Depends(require_network_view)):
    if onu is not None and not smartolt.ONU_ID.match(onu):
        raise HTTPException(status_code=404, detail="Unknown ONU")
    return {"actions": smartolt.recent_actions(action=action, onu_id=onu)}


@router.get("/olt/actions/{action_id}")
def one_action(action_id: int, current_user: dict = Depends(require_network_view)):
    row = smartolt.get_action(action_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown action")
    return row


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reboot", "disable", "enable"]


# 6C: one ONU at a time, admin only; the page has already shown a
# confirmation with the customer, the serial and the action.
@router.post("/olt/onus/{onu_id}/actions")
def onu_action(onu_id: str, body: ActionRequest, current_user: dict = Depends(require_admin)):
    try:
        return smartolt.start_action(onu_id, body.action, current_user["id"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.WritesDisabled as e:
        raise HTTPException(status_code=403, detail=str(e))
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


# 6D: configuration changes on one ONU. Admin only; the page shows the
# before-and-after and asks for a confirm click (delete: the typed serial,
# checked again in onu_config). The PPPoE password only passes through to
# SmartOLT: it isn't stored or logged, and a bad one is refused by
# onu_config without echoing it back.
class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change: Literal["resync", "speed", "port", "location", "vlans", "mode", "delete"]
    # port
    port: Optional[str] = Field(None, max_length=20)
    mode: Optional[str] = Field(None, max_length=20)
    dhcp: Optional[str] = Field(None, max_length=20)
    # port (access VLAN) and mode (WAN VLAN-ID)
    vlan: Optional[int] = Field(None, ge=1, le=4094)
    # speed
    upload: Optional[str] = Field(None, max_length=64)
    download: Optional[str] = Field(None, max_length=64)
    # location
    zone: Optional[str] = Field(None, max_length=64)
    odb: Optional[str] = Field(None, max_length=64)
    odb_port: Optional[int] = Field(None, ge=1, le=128)
    seal_number: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = Field(None, max_length=120)
    address_or_comment: Optional[str] = Field(None, max_length=120)
    contact: Optional[str] = Field(None, max_length=120)
    # vlans
    vlans: Optional[list[int]] = Field(None, max_length=64)
    # mode
    onu_mode: Optional[str] = Field(None, max_length=20)
    wan_mode: Optional[str] = Field(None, max_length=20)
    configuration_method: Optional[str] = Field(None, max_length=10)
    username: Optional[str] = Field(None, max_length=200)
    password: Optional[str] = Field(None, max_length=200)
    ipv4_address: Optional[str] = Field(None, max_length=40)
    subnet_mask: Optional[str] = Field(None, max_length=40)
    gateway: Optional[str] = Field(None, max_length=40)
    dns1: Optional[str] = Field(None, max_length=40)
    dns2: Optional[str] = Field(None, max_length=40)
    remote_access: Optional[str] = Field(None, max_length=10)
    # delete
    confirm_sn: Optional[str] = Field(None, max_length=64)


# The owner asked to see PPPoE passwords (2026-11-11). Admin only, read live
# from SmartOLT on each click, never cached or stored; every reveal is in
# the ONU's action history.
@router.get("/olt/onus/{onu_id}/pppoe-password")
def onu_pppoe_password(onu_id: str, response: Response, current_user: dict = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"password": onu_config.pppoe_password(onu_id, current_user["id"])}
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.get("/olt/onus/{onu_id}/speed")
def onu_speed(onu_id: str, current_user: dict = Depends(require_admin)):
    try:
        return onu_config.speed_info(onu_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


@router.post("/olt/onus/{onu_id}/config")
def onu_change(onu_id: str, body: ConfigRequest, current_user: dict = Depends(require_admin)):
    params = body.model_dump(exclude={"change"}, exclude_none=True)
    try:
        return onu_config.change(onu_id, body.change, params, current_user["id"])
    except onu_config.ChangeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown ONU")
    except smartolt.WritesDisabled as e:
        raise HTTPException(status_code=403, detail=str(e))
    except smartolt.SmartOLTError as e:
        _smartolt_failed(e)


# SmartOLT's webhook (6A-W). Public on purpose (SmartOLT calls it), so no
# login: every delivery must carry a valid Standard Webhooks signature made
# with SMARTOLT_WEBHOOK_SECRET, or it's refused. It only ever updates
# onu_state; it never writes to SmartOLT. SmartOLT wants a 2xx within 15 s.
@router.post("/smartolt/webhook")
async def smartolt_webhook(request: Request):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > smartolt.WEBHOOK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    body = await request.body()
    status, message = await run_in_threadpool(smartolt.receive_webhook, request.headers, body)
    if status != 200:
        raise HTTPException(status_code=status, detail=message)
    return {"ok": True, "message": message}


# ---- 6B: linking ONUs to customers. Reading needs the Network permission;
# every change is admin only, and each one is a person confirming a link. ----

def _link_failed(e):
    raise HTTPException(status_code=409, detail=str(e))


@router.get("/olt/links")
def links_overview(current_user: dict = Depends(require_network_view)):
    try:
        data = onu_links.overview()
    except pg_errors.UndefinedTable:
        raise HTTPException(status_code=503, detail="Customer links aren't set up yet (migration 0033).")
    return {**data, "can_edit": current_user["role"] == "admin"}


@router.get("/olt/customer-search")
def customer_search(q: str = "", current_user: dict = Depends(require_admin)):
    return {"customers": search_customers(q)}


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    onu_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    customer_id: int = Field(ge=1)
    matched_by: Literal["pppoe_username", "name", "manual"] = "manual"


class OnuOnly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    onu_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/olt/links")
def create_link(body: LinkRequest, current_user: dict = Depends(require_admin)):
    try:
        onu_links.link(body.onu_id, body.customer_id, body.matched_by, current_user["id"])
    except onu_links.LinkError as e:
        _link_failed(e)
    return {"ok": True}


@router.post("/olt/links/not-a-customer")
def not_a_customer(body: OnuOnly, current_user: dict = Depends(require_admin)):
    try:
        onu_links.mark_not_customer(body.onu_id, current_user["id"])
    except onu_links.LinkError as e:
        _link_failed(e)
    return {"ok": True}


@router.post("/olt/links/accept-username-matches")
def accept_username_matches(current_user: dict = Depends(require_admin)):
    return {"linked": onu_links.accept_username_matches(current_user["id"])}


@router.delete("/olt/links/{onu_id}")
def remove_link(onu_id: str, current_user: dict = Depends(require_admin)):
    try:
        onu_links.unlink(onu_id)
    except onu_links.LinkError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


class SiteLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    onu_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    site_id: int = Field(ge=1)
    matched_by: Literal["pppoe_username", "name", "manual"] = "manual"


@router.post("/olt/links/site")
def create_site_link(body: SiteLinkRequest, current_user: dict = Depends(require_admin)):
    try:
        onu_links.link_site(body.onu_id, body.site_id, body.matched_by, current_user["id"])
    except onu_links.LinkError as e:
        _link_failed(e)
    return {"ok": True}


@router.post("/olt/links/accept-site-matches")
def accept_site_matches(current_user: dict = Depends(require_admin)):
    return {"linked": onu_links.accept_site_matches(current_user["id"])}


# ---- LOS alerts: what's open now, and acknowledging one. Anyone who can see
# the Network pages may acknowledge (it only slows the reminders to twice a
# day; it never silences an outage). ----

@router.get("/olt/alerts")
def los_alerts(current_user: dict = Depends(require_network_view)):
    try:
        return {"alerts": onu_alerts.open_alerts(), "enabled": onu_alerts.enabled()}
    except pg_errors.UndefinedTable:
        return {"alerts": [], "enabled": False}


class AckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Optional[str] = Field(default=None, max_length=200)


@router.post("/olt/alerts/{alert_id}/ack")
def ack_los_alert(alert_id: int, body: AckRequest, current_user: dict = Depends(require_network_view)):
    if not onu_alerts.acknowledge(alert_id, current_user["id"], (body.note or "").strip() or None):
        raise HTTPException(status_code=409, detail="That alert has ended or is already acknowledged.")
    return {"ok": True}
