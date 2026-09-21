import hmac
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
