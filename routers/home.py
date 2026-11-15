"""Dashboard (landing page): which summaries the viewer may see, and each one.

GET /home            -> {"sections": [...]} the viewer's role allows, in page order
GET /home/{section}  -> that section's summary (db/home.py)

A section is gated by the SAME permission that opens the area's own page, so
the Dashboard never shows a number its viewer couldn't already look up:
revenue needs sites:view_revenue, customers needs what the Customers page
needs, and so on. Each section is its own request so one slow or broken area
never blanks the others (the page loads them two at a time: every query here
opens its own database connection, and the pooler allows 15 in all).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from psycopg2 import errors as pg_errors

from db import activity, activity_logic, batteries, customers, home, inventory_reports, monitoring, network
from db import revenue_report, smartolt
from db.connection import now_eat
from routers.auth import get_current_user
from routers.permissions import user_has_permission

router = APIRouter()
log = logging.getLogger(__name__)

UPLINK_ROUTER = "ccr2116"       # the one router the Network Analysis page charts


def _can(user, *pairs):
    return all(user_has_permission(user, section, action) for section, action in pairs)


def _sites(user):
    return home.sites(monitoring.get_site_statuses(False))


def _people(user):
    return home.people(monitoring.get_fleet(False))


def _revenue(user):
    return home.revenue(revenue_report.trend("daily_14"))


def _fibre(user):
    # Same as opening the OLT page: start any poll that's due (it runs on its
    # own thread and never raises), then read what's stored.
    smartolt.poll_if_due()
    return home.fibre(smartolt.configured_list())


def _uplink(user):
    overview = network.overview(UPLINK_ROUTER)
    history = network.uplink_history(UPLINK_ROUTER, "24h") if overview is not None else None
    return home.uplink(overview, history)


def _customers(user):
    return home.customers(customers.list_customers())


def _batteries(user):
    moving = batteries.get_active_movement_count() if user_has_permission(user, "movements", "view") else None
    return home.batteries(batteries.get_all_batteries(), moving)


def _activity(user):
    start, end = activity_logic.default_overview_range(now_eat().date())
    return home.activity(activity.get_overview(start, end, "day"))


def _stock(user):
    return home.stock(inventory_reports.get_items_summary(None))


def _may_see_stock(user):
    return _can(user, ("inventory_items", "view")) and (
        user_has_permission(user, "inventory_items", "view_stock")
        or user_has_permission(user, "inventory_items", "view_items"))


# name -> (may this user see it?, how to build it). Page order.
SECTIONS = {
    "sites": (lambda u: _can(u, ("sites", "view_status")), _sites),
    "people": (lambda u: _can(u, ("sites", "view_status")), _people),
    "revenue": (lambda u: _can(u, ("sites", "view_revenue")), _revenue),
    "uplink": (lambda u: _can(u, ("sites", "view_network")), _uplink),
    "fibre": (lambda u: _can(u, ("sites", "view_network")), _fibre),
    "customers": (lambda u: _can(u, ("sites", "send_broadcasts")), _customers),
    "batteries": (lambda u: _can(u, ("batteries", "view")), _batteries),
    "activity": (lambda u: _can(u, ("activity_log", "view_overview")), _activity),
    "stock": (_may_see_stock, _stock),
}


@router.get("/home")
def sections(current_user: dict = Depends(get_current_user)):
    return {"sections": [name for name, (allowed, _) in SECTIONS.items() if allowed(current_user)]}


@router.get("/home/{section}")
def section(section: str, current_user: dict = Depends(get_current_user)):
    if section not in SECTIONS:
        raise HTTPException(status_code=404, detail="No such dashboard section")
    allowed, build = SECTIONS[section]
    if not allowed(current_user):
        raise HTTPException(status_code=403, detail="You don't have permission to see this")
    try:
        return build(current_user)
    except (pg_errors.UndefinedTable, pg_errors.UndefinedColumn):
        # An area whose tables aren't set up on this database yet.
        raise HTTPException(status_code=503, detail="This area isn't set up yet.")
