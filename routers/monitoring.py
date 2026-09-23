import hmac
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from db import monitoring as db
from db import alert_templates
from db import monitoring_alerts
from db import monitoring_reconciliation
from db import monitoring_retention
from db.connection import now_eat
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_month(month: Optional[str]) -> Optional[str]:
    """"YYYY-MM" or None. Rejects a future month rather than passing it
    through — _resolve_window's "cap at today" logic assumes a month that
    has at least started, and a future one would just produce a degenerate
    single-day window instead of a useful error."""
    if month is None:
        return None
    if not MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    year, mon = (int(x) for x in month.split("-"))
    today = now_eat()
    if (year, mon) > (today.year, today.month):
        raise HTTPException(status_code=400, detail="month can't be in the future")
    return month


def validate_week(week: Optional[str]) -> Optional[str]:
    """"YYYY-MM-DD" naming a Monday, or None. Weeks run Monday–Sunday, EAT;
    any other day would be a second, overlapping week for the same days.
    A future week is rejected for the same reason a future month is."""
    if week is None:
        return None
    try:
        start = date.fromisoformat(week)
    except ValueError:
        raise HTTPException(status_code=400, detail="week must be YYYY-MM-DD")
    if start.weekday() != 0:
        raise HTTPException(status_code=400, detail="week must start on a Monday")
    if start > now_eat().date():
        raise HTTPException(status_code=400, detail="week can't be in the future")
    return week


def validate_day(day: Optional[str]) -> Optional[str]:
    """"YYYY-MM-DD" (an EAT calendar day), or None. Not a future one."""
    if day is None:
        return None
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    if d > now_eat().date():
        raise HTTPException(status_code=400, detail="day can't be in the future")
    return day


def resolve_period(month: Optional[str], week: Optional[str], day: Optional[str]):
    """One window per request: a day, a week or a month, never two. None of
    them means the current week (see db._resolve_window)."""
    if sum(x is not None for x in (month, week, day)) > 1:
        raise HTTPException(status_code=400, detail="pick one of day, week or month")
    return validate_month(month), validate_week(week), validate_day(day)


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
async def ingest(request: Request, background_tasks: BackgroundTasks):
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
    background_tasks.add_task(monitoring_retention.run_if_due)
    # Runs every heartbeat, not throttled like retention — debounce timers
    # (Phase 4.8) need to be checked on roughly the same cadence transitions
    # arrive on, or a threshold could sit crossed for minutes before anyone
    # is told. Cheap (a handful of indexed queries over ~21 sites) and never
    # raises, so it can't turn a slow SMS provider into a stuck heartbeat.
    background_tasks.add_task(monitoring_alerts.evaluate_and_notify)
    return counts


class AlertSubscriptionUpdate(BaseModel):
    in_app_enabled: bool = True
    sms_enabled: bool = True
    whatsapp_enabled: bool = True


# Own preferences only — no sites:* permission needed to read or set these.
# Holding sites:receive_alerts decides whether this user is IN the recipient
# list at all; this just decides which channels they're on if so. Someone
# without the permission can flip these switches harmlessly — they'll never
# be queried as a recipient (see load_recipients in db/monitoring_alerts.py).
@router.get("/monitoring/alert-subscription")
def get_alert_subscription(current_user: dict = Depends(get_current_user)):
    return monitoring_alerts.get_subscription(current_user["id"])


@router.put("/monitoring/alert-subscription")
def put_alert_subscription(body: AlertSubscriptionUpdate, current_user: dict = Depends(get_current_user)):
    monitoring_alerts.set_subscription(
        current_user["id"], body.in_app_enabled, body.sms_enabled, body.whatsapp_enabled
    )
    return {"ok": True}


class AdminChannelsUpdate(BaseModel):
    sms_enabled: bool
    whatsapp_enabled: bool


# Admin-side counterpart to the two endpoints above: an admin picking
# SMS/WhatsApp on someone else's behalf, instead of that person doing it
# themselves in Settings > Notifications. Gated on roles:edit, the same
# permission that already controls sites:receive_alerts itself (Roles
# page) — this list IS "who's on sites:receive_alerts", just with their
# channels alongside, so it lives behind the same gate rather than a new
# permission just for this view.
@router.get("/monitoring/alert-recipients")
def get_alert_recipients(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to manage alert recipients")
    return monitoring_alerts.list_recipients()


@router.put("/monitoring/alert-recipients/{user_id}")
def put_alert_recipient(user_id: int, body: AdminChannelsUpdate, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "roles", "edit"):
        raise HTTPException(status_code=403, detail="You don't have permission to manage alert recipients")
    monitoring_alerts.admin_set_channels(user_id, body.sms_enabled, body.whatsapp_enabled)
    return {"ok": True}


class AlertTemplateUpdate(BaseModel):
    body: str


# Alerts > SMS Templates. Its own permission (Roles > Sites > Site Status >
# "Edit Alert Messages"), separate from receive_alerts: being paged about an
# outage and deciding what every page says are different jobs.
def require_template_access(current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "sites", "edit_alert_messages"):
        raise HTTPException(status_code=403, detail="You don't have permission to edit alert messages")
    return current_user


@router.get("/monitoring/alert-templates")
def get_alert_templates(current_user: dict = Depends(require_template_access)):
    return alert_templates.list_for_editor()


@router.put("/monitoring/alert-templates/{kind}")
def put_alert_template(kind: str, body: AlertTemplateUpdate, current_user: dict = Depends(require_template_access)):
    if kind not in alert_templates.TEMPLATES:
        raise HTTPException(status_code=404, detail="Unknown message type")
    error = alert_templates.validate(kind, body.body)
    if error:
        raise HTTPException(status_code=400, detail=error)
    alert_templates.save(kind, body.body.strip(), current_user["id"])
    return {"ok": True}


@router.delete("/monitoring/alert-templates/{kind}")
def reset_alert_template(kind: str, current_user: dict = Depends(require_template_access)):
    if kind not in alert_templates.TEMPLATES:
        raise HTTPException(status_code=404, detail="Unknown message type")
    alert_templates.reset(kind)
    return {"ok": True}


class ConfirmationCheck(BaseModel):
    is_online: bool


# Phase 4.9. Gated on movements:manage, not a sites:* permission — this is
# triggered by the exact same tap as POST /movements/{id}/confirm-online
# (routers/batteries.py, unchanged), so it needs the same permission that
# action already requires, not a new one. The frontend calls this
# alongside that endpoint, never instead of it — confirm-online stays the
# movement's own source of truth; this is purely the reconciliation record.
@router.post("/monitoring/sites/{site_id}/confirmation-check")
def confirmation_check(site_id: int, body: ConfirmationCheck, current_user: dict = Depends(get_current_user)):
    if not user_has_permission(current_user, "movements", "manage"):
        raise HTTPException(status_code=403, detail="You don't have permission to move batteries")
    result = monitoring_reconciliation.record_confirmation(site_id, body.is_online, current_user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Monitored site not found")
    return result


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
    result = {"last_ingest_at": db.get_last_ingest_at(), "counts": counts, "sites": sites,
              **db.get_fleet_activity()}
    if can_revenue:
        # Everything Ops saw today, placed or not — the rows below stay strictly
        # per-site, so the difference is carried on the card rather than dropped.
        unplaced = db.get_unplaced_revenue_today()
        result["revenue_today_kes"] = sum(s["revenue_today_kes"] for s in sites) + unplaced["kes"]
        result["revenue_unplaced_kes"] = unplaced["kes"]
        result["revenue_unplaced_sales"] = unplaced["sales"]
        # Only alongside revenue: the customer list feeds nothing else, and to
        # someone who cannot see money an aged feed is a worry with no
        # corresponding number to explain it.
        result["revenue_feed"] = db.get_revenue_feed()
    return result


@router.get("/monitoring/sites/{site_id}")
def site_detail(
    site_id: int, month: Optional[str] = None, week: Optional[str] = None, day: Optional[str] = None,
    current_user: dict = Depends(require_status_access),
):
    # Same gate as the fleet Status page (view_status) — drilling into one
    # site isn't a heavier claim than seeing it in the list. Revenue stays
    # gated separately, same as the list.
    can_revenue = user_has_permission(current_user, "sites", "view_revenue")
    month, week, day = resolve_period(month, week, day)
    detail = db.get_site_detail(site_id, can_revenue, month=month, week=week, day=day)
    if detail is None:
        raise HTTPException(status_code=404, detail="Monitored site not found")
    return detail


@router.get("/monitoring/fleet")
def fleet(
    month: Optional[str] = None, week: Optional[str] = None, day: Optional[str] = None,
    current_user: dict = Depends(require_status_access),
):
    """Every site over one window, for the Trends tab. Same gates as Status:
    view_status to see it at all, view_revenue for any money — the revenue
    and sales keys, and the unattributed line, are absent without it."""
    can_revenue = user_has_permission(current_user, "sites", "view_revenue")
    month, week, day = resolve_period(month, week, day)
    return db.get_fleet(can_revenue, month=month, week=week, day=day)


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


class AckRequest(BaseModel):
    note: Optional[str] = None


# Acknowledging takes a site out of the Status headline, so it sits behind
# manage_monitoring rather than view_status: seeing an outage is not the same
# claim as deciding it no longer needs anyone's attention.
@router.post("/monitoring/sites/{site_id}/acknowledge")
def acknowledge(site_id: int, body: AckRequest, current_user: dict = Depends(require_manage_access)):
    note = (body.note or "").strip()[:200] or None
    try:
        db.acknowledge_site(site_id, current_user["id"], note)
    except db.AckConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@router.delete("/monitoring/sites/{site_id}/acknowledge")
def unacknowledge(site_id: int, current_user: dict = Depends(require_manage_access)):
    if not db.clear_acknowledgement(site_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="That site is not acknowledged")
    return {"ok": True}


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


@router.get("/monitoring/inbox/dismissed")
def dismissed_inbox(current_user: dict = Depends(require_manage_access)):
    return db.get_dismissed()


@router.post("/monitoring/inbox/{item_id}/restore")
def restore_inbox(item_id: int, current_user: dict = Depends(require_manage_access)):
    if not db.restore_inbox_item(item_id):
        raise HTTPException(status_code=404, detail="That item can't be restored")
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


# ---- Packages (the price list) ----
# Gated on manage_monitoring, not view_revenue: this screen EDITS what every
# future sale is worth, which is a heavier thing than reading a total.


class PackageCreate(BaseModel):
    profile_name: str
    price_kes: float = 0
    is_comped: bool = False
    duration_minutes: Optional[int] = None
    notes: Optional[str] = None


class PackageUpdate(BaseModel):
    price_kes: Optional[float] = None
    is_comped: Optional[bool] = None
    is_active: Optional[bool] = None
    duration_minutes: Optional[int] = None
    notes: Optional[str] = None


@router.get("/monitoring/packages")
def packages(current_user: dict = Depends(require_manage_access)):
    return db.list_packages()


@router.post("/monitoring/packages")
def create_package(body: PackageCreate, current_user: dict = Depends(require_manage_access)):
    fields = {k: _clean(v) for k, v in body.model_dump().items()}
    if not fields.get("profile_name"):
        raise HTTPException(status_code=400, detail="A package needs its profile name")
    if fields.get("price_kes") is not None and fields["price_kes"] < 0:
        raise HTTPException(status_code=400, detail="A price cannot be negative")
    try:
        package_id = db.create_package(fields)
    except db.SiteConflict as err:
        raise HTTPException(status_code=409, detail=str(err))
    return {"id": package_id}


@router.patch("/monitoring/packages/{package_id}")
def update_package(package_id: int, body: PackageUpdate, current_user: dict = Depends(require_manage_access)):
    fields = {k: _clean(v) for k, v in body.model_dump(exclude_unset=True).items()}
    if fields.get("price_kes") is not None and fields["price_kes"] < 0:
        raise HTTPException(status_code=400, detail="A price cannot be negative")
    if not db.update_package(package_id, fields):
        raise HTTPException(status_code=404, detail="Package not found")
    return {"ok": True}


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
