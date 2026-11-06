"""Technical Activity Log: the rules that need no database — what a report
must contain, who may edit it, the reference format, and the Overview's
bucketing and shares. db/activity.py does the queries; tests/test_activity.py
exercises everything here directly. See migration 0026 for the tables.
"""
from datetime import date, timedelta

# Outcome keys are stored as text (no CHECK, like every enum-like column here).
OUTCOMES = [
    ("completed", "Completed"),
    ("partially_completed", "Partially completed"),
    ("inspection_only", "Inspection only"),
    ("unable_to_resolve", "Unable to resolve"),
]
DEFAULT_OUTCOME = "completed"
OUTCOME_LABELS = dict(OUTCOMES)
# These two need a one-line explanation in the description.
EXPLANATION_OUTCOMES = ("partially_completed", "unable_to_resolve")

# Hours each chip counts as in the Overview's effort totals (owner-approved
# 2026-11-06). "Under 30 min" counts as half an hour: a round-up, since the
# real figure isn't recorded.
TIME_SPENT = [
    ("under_30m", "Under 30 min", 0.5),
    ("1h", "1 hour", 1.0),
    ("half_day", "Half day", 4.0),
    ("full_day", "Full day", 8.0),
]
TIME_SPENT_LABELS = {k: label for k, label, _ in TIME_SPENT}
TIME_SPENT_HOURS = {k: hours for k, _, hours in TIME_SPENT}

# A technician can edit their own report for this long after submitting it.
# An admin can edit any report at any time.
EDIT_WINDOW = timedelta(hours=48)

MAX_TECHNICIANS = 20
OTHER_LOCATION_MAX = 120
DESCRIPTION_MAX = 1000
REMARKS_MAX = 2000

BUCKETS = ("day", "week", "month")
MAX_OVERVIEW_DAYS = 366
DEFAULT_OVERVIEW_DAYS = 7


def format_reference(year, number):
    return f"TAL-{year}-{number:05d}"


def can_edit(report, user, now):
    """report: submitted_by (user id) and submitted_at (naive UTC datetime).
    user: the JWT user dict. now: naive UTC. The caller also checks the
    user's Submit permission."""
    if user["role"] == "admin":
        return True
    return report["submitted_by"] == user["id"] and now - report["submitted_at"] <= EDIT_WINDOW


def can_delete(user):
    """Admin only (owner's call, 2026-11-06)."""
    return user["role"] == "admin"


def _clean_text(value):
    value = (value or "").strip()
    return value or None


def check_report(fields, today):
    """Validates and tidies one report's fields.

    fields: activity_date (date), type_id, location_id, other_location,
    outcome, description, time_spent, remarks. today: the Nairobi date.
    Returns (cleaned, errors) — cleaned is None when there are errors.
    Whether the type, location and technicians exist and are active is the
    database layer's check, not this one."""
    errors = []

    activity_date = fields.get("activity_date")
    if not isinstance(activity_date, date):
        errors.append("Pick the date the work was done")
    elif activity_date > today:
        errors.append("The date can't be in the future")

    if not fields.get("type_id"):
        errors.append("Pick a category and an activity type")

    location_id = fields.get("location_id")
    other_location = _clean_text(fields.get("other_location"))
    if location_id and other_location:
        # The picker's "Other" and a real site are one choice, not two.
        other_location = None
    if not location_id and not other_location:
        errors.append("Pick a location, or choose Other and say where")
    if other_location and len(other_location) > OTHER_LOCATION_MAX:
        errors.append(f"Other location is too long (max {OTHER_LOCATION_MAX} characters)")

    outcome = fields.get("outcome") or DEFAULT_OUTCOME
    if outcome not in OUTCOME_LABELS:
        errors.append("Unknown outcome")

    description = _clean_text(fields.get("description"))
    if outcome in EXPLANATION_OUTCOMES and not description:
        errors.append(f"Add a one-line explanation for \"{OUTCOME_LABELS[outcome]}\"")
    if description and len(description) > DESCRIPTION_MAX:
        errors.append(f"Description is too long (max {DESCRIPTION_MAX} characters)")

    time_spent = fields.get("time_spent") or None
    if time_spent is not None and time_spent not in TIME_SPENT_LABELS:
        errors.append("Unknown time spent")

    remarks = _clean_text(fields.get("remarks"))
    if remarks and len(remarks) > REMARKS_MAX:
        errors.append(f"Remarks are too long (max {REMARKS_MAX} characters)")

    if errors:
        return None, errors
    return {
        "activity_date": activity_date,
        "type_id": fields["type_id"],
        "location_id": location_id or None,
        "other_location": other_location,
        "outcome": outcome,
        "description": description,
        "time_spent": time_spent,
        "remarks": remarks,
    }, []


def technicians_with_submitter(submitter_id, technician_ids):
    """The people on a report: the submitter first, then anyone added, once each."""
    seen = [submitter_id]
    for user_id in technician_ids or []:
        if user_id not in seen:
            seen.append(user_id)
    return seen


def like_pattern(text):
    """A contains-match pattern for ILIKE with the user's own % _ \\ taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def csv_cell(value):
    """One CSV cell. Text starting with = + - @ would be run as a formula by
    Excel, and descriptions are typed by people, so it is defused with a
    leading apostrophe."""
    if value is None:
        return ""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


# ---- Overview -------------------------------------------------------------

def bucket_start(day, bucket):
    if bucket == "week":
        return day - timedelta(days=day.weekday())  # Monday
    if bucket == "month":
        return day.replace(day=1)
    return day


def _next_bucket(start, bucket):
    if bucket == "week":
        return start + timedelta(days=7)
    if bucket == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=1)


def fill_buckets(start, end, bucket, counts):
    """One point per day/week/month from start to end, zero where nothing was
    reported — a gap in the trend is a real quiet period, not missing data.
    counts maps a bucket's first day (a date) to its number of reports."""
    points = []
    current = bucket_start(start, bucket)
    while current <= end:
        points.append({"period": current.isoformat(), "count": counts.get(current, 0)})
        current = _next_bucket(current, bucket)
    return points


def outcome_mix(counts):
    """counts: outcome key -> reports. Every outcome appears, in the scope's
    order, with its share of the total (0 when there are no reports)."""
    total = sum(counts.get(key, 0) for key, _ in OUTCOMES)
    rows = [
        {
            "outcome": key,
            "label": label,
            "count": counts.get(key, 0),
            "share": round(counts.get(key, 0) / total, 4) if total else 0,
        }
        for key, label in OUTCOMES
    ]
    return {"total": total, "outcomes": rows,
            "unable_rate": next(r["share"] for r in rows if r["outcome"] == "unable_to_resolve")}


def hours_for(time_spent_counts):
    """time_spent key -> reports, into total hours. Unknown keys count as zero."""
    return sum(TIME_SPENT_HOURS.get(key, 0) * n for key, n in time_spent_counts.items())


def default_overview_range(today):
    return today - timedelta(days=DEFAULT_OVERVIEW_DAYS - 1), today


def check_overview_range(start, end, bucket):
    """Returns an error message, or None when the request is fine."""
    if bucket not in BUCKETS:
        return "bucket must be day, week or month"
    if start > end:
        return "The start date is after the end date"
    if (end - start).days + 1 > MAX_OVERVIEW_DAYS:
        return f"Pick a range of at most {MAX_OVERVIEW_DAYS} days"
    return None
