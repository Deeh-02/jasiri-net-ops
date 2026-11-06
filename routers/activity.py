"""Technical Activity Log (Submit / Reports / Overview). Permissions are the
activity_log section, set per role on the Roles page:
  view           see Reports — your own, and ones you're listed on
  submit         file a report; edit your own for 48 hours
  view_all       see every report, not just your own
  view_overview  see the Activity Overview
The built-in admin passes every check, may edit any report at any time, and
alone may delete one. Rules are in db/activity_logic.py.

Phase 2: a type's extra fields (customer, package, ...) come from the type's own
configuration, never from code here; duplicate warning (409, never a block);
the admin Manage screen's endpoints are all admin-only (/activity/admin/...).
"""
import csv
import io
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from db import activity as db
from db import activity_catalog as catalog
from db import activity_logic as logic
from db.connection import now_eat, to_eat
from routers.auth import get_current_user
from routers.permissions import require_admin, user_has_permission

router = APIRouter()

SECTION = "activity_log"
MAX_PAGE_SIZE = 100


def require_activity(action):
    def check(current_user: dict = Depends(get_current_user)):
        if not user_has_permission(current_user, SECTION, action):
            raise HTTPException(status_code=403, detail="You don't have permission to do this")
        return current_user
    return check


def _only_user_id(user):
    """None means "every report"; otherwise the one person whose reports (own
    and ones they're listed on) the caller may see."""
    return None if user_has_permission(user, SECTION, "view_all") else user["id"]


class ReportFields(BaseModel):
    activity_date: date
    type_id: int
    location_id: int | None = None
    other_location: str | None = None
    outcome: str = logic.DEFAULT_OUTCOME
    description: str | None = None
    time_spent: str | None = None
    remarks: str | None = None
    technician_ids: list[int] = Field(default_factory=list, max_length=logic.MAX_TECHNICIANS)
    # Answers to the type's extra fields, {key: value}; checked against the type.
    details: dict | None = None


class ReportCreate(ReportFields):
    # One per form, re-sent on a retry so a lost answer can't make a duplicate.
    client_token: str | None = Field(default=None, max_length=64)
    # True once the person has seen the duplicate warning and chosen to go ahead.
    confirm_duplicate: bool = False


def _clean_or_400(body):
    cleaned, problems = logic.check_report(body.model_dump(), now_eat().date())
    if problems:
        raise HTTPException(status_code=400, detail=problems[0])
    return cleaned


@router.get("/activity/options")
def get_options(current_user: dict = Depends(require_activity("submit"))):
    return db.get_options(current_user["id"])


@router.get("/activity/filter-options")
def get_filter_options(current_user: dict = Depends(require_activity("view"))):
    return db.get_filter_options()


@router.get("/activity/customers")
def search_customers(q: str = "", current_user: dict = Depends(require_activity("submit"))):
    return db.search_customers(q)


@router.post("/activity/reports")
def submit_report(body: ReportCreate, current_user: dict = Depends(require_activity("submit"))):
    cleaned = _clean_or_400(body)
    try:
        details = db.prepare_details(cleaned["type_id"], body.details)
        result = db.create_report(current_user["id"], cleaned, body.technician_ids, body.client_token,
                                  details, body.confirm_duplicate)
    except db.ActivityInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    if "possible_duplicates" in result:
        # A warning, not a refusal: the same request with confirm_duplicate goes through.
        raise HTTPException(status_code=409, detail={
            "message": "A report for this type, location and date has already been filed",
            "possible_duplicates": result["possible_duplicates"]})
    return result


def _filters(date_from, date_to, category_id, type_id, location_id, technician_id, outcome, q):
    if outcome and outcome not in logic.OUTCOME_LABELS:
        raise HTTPException(status_code=400, detail="Unknown outcome")
    return {
        "date_from": date_from, "date_to": date_to, "category_id": category_id, "type_id": type_id,
        "location_id": location_id, "technician_id": technician_id, "outcome": outcome,
        "q": (q or "").strip() or None,
    }


def _public(report):
    return {k: v for k, v in report.items() if k != "submitted_at_dt"}


@router.get("/activity/reports")
def list_reports(
    date_from: date | None = None, date_to: date | None = None,
    category_id: int | None = None, type_id: int | None = None,
    location_id: int | None = None, technician_id: int | None = None,
    outcome: str | None = None, q: str | None = None,
    limit: int = Query(25, ge=1, le=MAX_PAGE_SIZE), offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_activity("view")),
):
    filters = _filters(date_from, date_to, category_id, type_id, location_id, technician_id, outcome, q)
    result = db.list_reports(filters, _only_user_id(current_user), limit, offset)
    return {"total": result["total"], "reports": [_public(r) for r in result["reports"]]}


_CSV_HEADER = ["Reference", "Activity date", "Category", "Activity type", "Area", "Technicians",
               "Outcome", "Time spent", "Description", "Remarks", "Extra details", "Submitted by", "Submitted at (EAT)"]


@router.get("/activity/reports/export")
def export_reports(
    date_from: date | None = None, date_to: date | None = None,
    category_id: int | None = None, type_id: int | None = None,
    location_id: int | None = None, technician_id: int | None = None,
    outcome: str | None = None, q: str | None = None,
    current_user: dict = Depends(require_activity("view")),
):
    filters = _filters(date_from, date_to, category_id, type_id, location_id, technician_id, outcome, q)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_CSV_HEADER)
    for r in db.export_reports(filters, _only_user_id(current_user)):
        submitted = to_eat(r["submitted_at_dt"]).strftime("%Y-%m-%d %H:%M")
        writer.writerow([logic.csv_cell(v) for v in (
            r["reference"], r["activity_date"], r["category"], r["type"], r["area"],
            ", ".join(t["name"] for t in r["technicians"]), r["outcome_label"],
            r["time_spent_label"], r["description"], r["remarks"],
            "; ".join(f"{d['label']}: {d['text']}" for d in r["details_view"]),
            r["submitted_by_name"], submitted)])
    filename = f"activity-reports-{now_eat().date().isoformat()}.csv"
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _report_or_404(report_id, user):
    report = db.get_report(report_id)
    # A report you may not see is reported as missing, not as forbidden.
    if report is None or (_only_user_id(user) is not None and not db.is_involved(report, user["id"])):
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _can_edit(report, user):
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, like the stored times
    return (user_has_permission(user, SECTION, "submit")
            and logic.can_edit({"submitted_by": report["submitted_by"], "submitted_at": report["submitted_at_dt"]},
                               user, now))


@router.get("/activity/reports/{report_id}")
def get_report(report_id: int, current_user: dict = Depends(require_activity("view"))):
    report = _report_or_404(report_id, current_user)
    return {**_public(report), "can_edit": _can_edit(report, current_user),
            "can_delete": logic.can_delete(current_user)}


@router.patch("/activity/reports/{report_id}")
def edit_report(report_id: int, body: ReportFields, current_user: dict = Depends(require_activity("submit"))):
    report = _report_or_404(report_id, current_user)
    if not _can_edit(report, current_user):
        raise HTTPException(status_code=403, detail="You can only edit your own report, for 48 hours after submitting it")
    cleaned = _clean_or_400(body)
    try:
        details = db.prepare_details(cleaned["type_id"], body.details)
        db.update_report(report_id, current_user["id"], cleaned, body.technician_ids, details)
    except db.ActivityInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": report_id, "reference": report["reference"]}


@router.delete("/activity/reports/{report_id}")
def delete_report(report_id: int, current_user: dict = Depends(require_admin)):
    if not db.soft_delete_report(report_id, current_user["id"]):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"id": report_id, "deleted": True}


@router.get("/activity/overview")
def get_overview(
    start: date | None = None, end: date | None = None, bucket: str = "day",
    current_user: dict = Depends(require_activity("view_overview")),
):
    today = now_eat().date()
    if start is None and end is None:
        start, end = logic.default_overview_range(today)
    elif start is None or end is None:
        raise HTTPException(status_code=400, detail="Give both a start and an end date")
    problem = logic.check_overview_range(start, end, bucket)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    return db.get_overview(start, end, bucket)


# ---- Manage screen (admin only) ------------------------------------------

class NameBody(BaseModel):
    name: str


class CategoryChanges(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    repeat_visit_tracking: bool | None = None


class NewType(BaseModel):
    category_id: int
    name: str


class TypeChanges(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class FieldsBody(BaseModel):
    fields: list[dict] = Field(max_length=logic.MAX_FIELDS_PER_TYPE)
    apply_to_category: bool = False


class OrderBody(BaseModel):
    ids: list[int]


def _manage(action, *args, missing="Not found"):
    """Runs one catalog change and answers with the whole catalog, so the
    screen redraws from the truth. A falsy result means nothing was found."""
    try:
        result = action(*args)
    except db.ActivityInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is False or result == 0:
        raise HTTPException(status_code=404, detail=missing)
    return {"catalog": catalog.get_catalog()}


@router.get("/activity/admin/catalog")
def get_catalog(current_user: dict = Depends(require_admin)):
    return {"catalog": catalog.get_catalog()}


@router.post("/activity/admin/categories")
def add_category(body: NameBody, current_user: dict = Depends(require_admin)):
    return _manage(catalog.add_category, body.name)


@router.put("/activity/admin/categories/order")
def order_categories(body: OrderBody, current_user: dict = Depends(require_admin)):
    return _manage(catalog.reorder_categories, body.ids)


@router.patch("/activity/admin/categories/{category_id}")
def change_category(category_id: int, body: CategoryChanges, current_user: dict = Depends(require_admin)):
    return _manage(catalog.update_category, category_id, body.model_dump(exclude_unset=True), missing="Category not found")


@router.put("/activity/admin/categories/{category_id}/types/order")
def order_types(category_id: int, body: OrderBody, current_user: dict = Depends(require_admin)):
    return _manage(catalog.reorder_types, category_id, body.ids)


@router.post("/activity/admin/types")
def add_type(body: NewType, current_user: dict = Depends(require_admin)):
    return _manage(catalog.add_type, body.category_id, body.name)


@router.patch("/activity/admin/types/{type_id}")
def change_type(type_id: int, body: TypeChanges, current_user: dict = Depends(require_admin)):
    return _manage(catalog.update_type, type_id, body.model_dump(exclude_unset=True), missing="Type not found")


@router.put("/activity/admin/types/{type_id}/fields")
def set_fields(type_id: int, body: FieldsBody, current_user: dict = Depends(require_admin)):
    return _manage(catalog.set_type_fields, type_id, body.fields, body.apply_to_category, missing="Type not found")
