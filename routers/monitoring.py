import hmac
import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from db import monitoring as db
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()


def require_ingest_token(x_ingest_token: str = Header(default="")):
    """Shared-secret auth for the router's heartbeat. DELIBERATELY not the
    user-JWT dependency every other endpoint uses: a RouterOS script cannot
    hold or refresh a JWT. See ARCHITECTURE.md's Monitoring ingest auth.
    Fails closed — with no token configured, nothing is accepted."""
    expected = os.environ.get("MONITORING_INGEST_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Ingest is not configured")
    if not hmac.compare_digest(x_ingest_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid ingest token")


def parse_gmt_offset_minutes(value):
    """RouterOS reports gmt-offset as seconds (14400) or "+04:00" depending on
    how the script formats it; accept both. The offset comes from the router
    so correcting its clock later needs no change here."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) // 60
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text) // 60
        sign = -1 if text.startswith("-") else 1
        hours, _, minutes = text.lstrip("+-").partition(":")
        return sign * (int(hours) * 60 + int(minutes or 0))
    raise ValueError("gmt_offset must be seconds or +HH:MM")


def validate_snapshot(snapshot):
    """Returns (router_ts, router_ts_utc, offset_minutes) or raises ValueError."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    sites = snapshot.get("sites")
    pppoe = snapshot.get("pppoe")
    if not isinstance(sites, list) or not isinstance(pppoe, list):
        raise ValueError("'sites' and 'pppoe' must be lists")
    for s in sites:
        if not isinstance(s, dict) or not isinstance(s.get("vlan_id"), int) or not isinstance(s.get("sessions"), int):
            raise ValueError("each site needs integer vlan_id and sessions")
    if not all(isinstance(u, str) for u in pppoe):
        raise ValueError("'pppoe' must be a list of usernames")
    if snapshot.get("seq") is not None and not isinstance(snapshot["seq"], int):
        raise ValueError("seq must be an integer")
    router_ts = datetime.strptime(str(snapshot.get("router_ts")), "%Y-%m-%d %H:%M:%S")
    offset_minutes = parse_gmt_offset_minutes(snapshot.get("gmt_offset"))
    return router_ts, router_ts - timedelta(minutes=offset_minutes), offset_minutes


@router.post("/monitoring/ingest", dependencies=[Depends(require_ingest_token)])
async def ingest(request: Request):
    """Accepts one snapshot, or {"snapshots": [...]} for a batch. Bad DATA is
    never a 4xx: the router has no retry and nobody reads its logs, so a
    rejection is silent permanent loss. It is quarantined and answered 200."""
    try:
        body = await request.json()
    except ValueError:
        db.quarantine("parse_error", {"error": "body is not valid JSON"})
        return {"stored": 0, "duplicates": 0, "quarantined": 1}

    snapshots = body.get("snapshots") if isinstance(body, dict) and "snapshots" in body else [body]
    if not isinstance(snapshots, list):
        db.quarantine("parse_error", body)
        return {"stored": 0, "duplicates": 0, "quarantined": 1}

    counts = {"stored": 0, "duplicates": 0, "quarantined": 0}
    for snapshot in snapshots:
        try:
            router_ts, router_ts_utc, offset_minutes = validate_snapshot(snapshot)
        except (ValueError, TypeError) as e:
            db.quarantine("parse_error", {"error": str(e), "snapshot": snapshot})
            counts["quarantined"] += 1
            continue
        result = db.ingest_snapshot(snapshot, snapshot, router_ts, router_ts_utc, offset_minutes)
        counts["duplicates" if result == "duplicate" else "stored"] += 1
    return counts


def require_status_access(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "view_status"):
        raise HTTPException(status_code=403, detail="You don't have permission to view site status")
    return current_user


@router.get("/monitoring/status")
def status(current_user: dict = Depends(require_status_access)):
    """Revenue keys are left out entirely, not nulled, without
    sites:view_revenue — the client renders its locked placeholder from the
    permission flag alone."""
    can_revenue = user_has_permission(current_user, "sites", "view_revenue")
    sites = db.get_site_statuses(can_revenue)
    counts = {}
    for s in sites:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    result = {"last_ingest_at": db.get_last_ingest_at(), "counts": counts, "sites": sites}
    if can_revenue:
        result["revenue_today_kes"] = sum(s["revenue_today_kes"] for s in sites)
    return result


@router.get("/monitoring/sites/{site_id}")
def site_detail(site_id: int, current_user: dict = Depends(require_status_access)):
    can_revenue = user_has_permission(current_user, "sites", "view_revenue")
    detail = db.get_site_detail(site_id, can_revenue)
    if detail is None:
        raise HTTPException(status_code=404, detail="Monitored site not found")
    return detail


# ---- Site management ----
# Admins pass user_has_permission unconditionally; other roles need
# sites:manage_monitoring, a permission row to be granted from Roles.

# 'ping' is deliberately absent: it needs an AP address, and there is no
# field for one yet, so a ping site made here would never report.
MANAGE_LIVENESS = ("pppoe", "activity")


def require_manage_access(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "manage_monitoring"):
        raise HTTPException(status_code=403, detail="You don't have permission to manage monitored sites")
    return current_user


def _clean(value):
    """Blank strings from a form mean 'not set', and '' would otherwise
    collide on the UNIQUE columns the moment two sites left one empty."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class SiteCreate(BaseModel):
    name: Optional[str] = None
    location_id: Optional[int] = None
    vlan_id: Optional[int] = None
    pppoe_username: Optional[str] = None
    liveness_source: str = "pppoe"
    notes: Optional[str] = None
    inbox_item_id: Optional[int] = None


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    location_id: Optional[int] = None
    vlan_id: Optional[int] = None
    pppoe_username: Optional[str] = None
    liveness_source: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


def _check_site_fields(fields, creating):
    if "liveness_source" in fields and fields["liveness_source"] not in MANAGE_LIVENESS:
        raise HTTPException(status_code=400, detail="Liveness must be 'pppoe' or 'activity'")
    if creating:
        if fields.get("liveness_source") == "pppoe" and not fields.get("pppoe_username"):
            raise HTTPException(status_code=400, detail="A PPPoE site needs its PPPoE username")
        # Activity is judged purely on hotspot sessions, which are counted
        # per VLAN — without one, the site would sit at unknown forever and
        # look configured.
        if fields.get("liveness_source") == "activity" and fields.get("vlan_id") is None:
            raise HTTPException(status_code=400, detail="An activity site needs its VLAN number")
        if not (fields.get("name") or fields.get("location_id")):
            raise HTTPException(status_code=400, detail="Give the site a name, or link it to a site from the Sites list")
    # On update the PPPoE-needs-a-username rule is checked in the database
    # layer against the real row: a PATCH that only flips liveness_source
    # does not resend the username, and can't be judged from the body alone.


@router.get("/monitoring/inbox")
def inbox(current_user: dict = Depends(require_manage_access)):
    """What the router has reported that has no site yet."""
    return db.get_inbox()


@router.post("/monitoring/inbox/{item_id}/dismiss")
def dismiss_inbox(item_id: int, current_user: dict = Depends(require_manage_access)):
    if not db.dismiss_inbox_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found or already handled")
    return {"ok": True}


@router.get("/monitoring/manage/sites")
def manage_sites(current_user: dict = Depends(require_manage_access)):
    return {"sites": db.list_managed_sites(), "locations": db.list_linkable_locations()}


@router.post("/monitoring/sites")
def create_site(body: SiteCreate, current_user: dict = Depends(require_manage_access)):
    fields = {k: _clean(v) for k, v in body.model_dump().items() if k != "inbox_item_id"}
    _check_site_fields(fields, creating=True)
    try:
        site_id = db.create_site(fields, inbox_item_id=body.inbox_item_id)
    except db.SiteConflict as err:
        raise HTTPException(status_code=409, detail=str(err))
    return {"id": site_id}


@router.patch("/monitoring/sites/{site_id}")
def update_site(site_id: int, body: SiteUpdate, current_user: dict = Depends(require_manage_access)):
    # exclude_unset: only what the caller actually sent, so a PATCH that
    # omits vlan_id leaves it alone rather than clearing it.
    fields = {k: _clean(v) for k, v in body.model_dump(exclude_unset=True).items()}
    _check_site_fields(fields, creating=False)
    try:
        found = db.update_site(site_id, fields)
    except db.SiteConflict as err:
        raise HTTPException(status_code=409, detail=str(err))
    if not found:
        raise HTTPException(status_code=404, detail="Monitored site not found")
    return {"ok": True}
