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


# ---- Phase 2: extra fields per activity type -----------------------------
#
# A type's extra fields are DATA (activity_types.details_schema), a list of
#   {"key", "label", "kind": text|number|customer, "required"[, "min", "max"]}
# so a new field is an admin edit, not code. The answers go to
# activity_reports.details as {key: value}; a customer is stored as
# {"id", "name", "username"} (the name is copied so the report still reads
# right if the customer is later removed).

FIELD_KINDS = ("text", "number", "customer")
MAX_FIELDS_PER_TYPE = 12
FIELD_TEXT_MAX = 200
NAME_MAX = 80
_KEY_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def check_name(value, what):
    """A category or type name: trimmed, 1..NAME_MAX characters. Returns (name, error)."""
    name = " ".join((value or "").split())
    if not name:
        return None, f"Give the {what} a name"
    if len(name) > NAME_MAX:
        return None, f"The {what} name is too long (max {NAME_MAX} characters)"
    return name, None


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if number == number and abs(number) != float("inf") else None


def check_schema(fields):
    """Validates a type's field list as saved by an admin. Returns (clean, errors)."""
    if fields is None:
        return [], []
    if not isinstance(fields, list):
        return None, ["The fields must be a list"]
    if len(fields) > MAX_FIELDS_PER_TYPE:
        return None, [f"At most {MAX_FIELDS_PER_TYPE} extra fields per type"]
    clean, seen = [], set()
    for f in fields:
        if not isinstance(f, dict):
            return None, ["Each field must be an object"]
        label = " ".join(str(f.get("label") or "").split())
        key = str(f.get("key") or "")
        kind = f.get("kind")
        if not label or len(label) > 60:
            return None, ["Each field needs a label (up to 60 characters)"]
        if not key or len(key) > 30 or not key[0].isalpha() or not set(key) <= _KEY_OK:
            return None, [f"\"{label}\" has an invalid key"]
        if key in seen:
            return None, [f"Two fields share the key \"{key}\""]
        if kind not in FIELD_KINDS:
            return None, [f"\"{label}\" has an unknown kind"]
        seen.add(key)
        entry = {"key": key, "label": label, "kind": kind, "required": bool(f.get("required"))}
        if kind == "number":
            for bound in ("min", "max"):
                if f.get(bound) not in (None, ""):
                    value = _number(f[bound])
                    if value is None:
                        return None, [f"\"{label}\": {bound} must be a number"]
                    entry[bound] = value
            if "min" in entry and "max" in entry and entry["min"] > entry["max"]:
                return None, [f"\"{label}\": min is above max"]
        clean.append(entry)
    if sum(1 for f in clean if f["kind"] == "customer") > 1:
        return None, ["A type can have only one Customer field"]
    return clean, []


def check_details(schema, details):
    """Validates a report's extra-field answers against its type's schema.
    Only fields in the schema are kept; blank answers are dropped. A customer
    answer comes back as {"id": n} for the database layer to fill in.
    Returns (cleaned_or_None, errors)."""
    details = details if isinstance(details, dict) else {}
    cleaned, errors = {}, []
    for f in schema or []:
        value = details.get(f["key"])
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "", {}):
            if f.get("required"):
                errors.append(f"{f['label']} is required")
            continue
        if f["kind"] == "text":
            if not isinstance(value, str):
                errors.append(f"{f['label']} must be text")
            elif len(value) > FIELD_TEXT_MAX:
                errors.append(f"{f['label']} is too long (max {FIELD_TEXT_MAX} characters)")
            else:
                cleaned[f["key"]] = value
        elif f["kind"] == "number":
            number = _number(value)
            low, high = f.get("min"), f.get("max")
            if number is None:
                errors.append(f"{f['label']} must be a number")
            elif (low is not None and number < low) or (high is not None and number > high):
                errors.append(f"{f['label']} must be between {low:g} and {high:g}" if low is not None and high is not None
                              else f"{f['label']} is out of range")
            else:
                cleaned[f["key"]] = number
        else:  # customer
            customer_id = value.get("id") if isinstance(value, dict) else None
            if isinstance(customer_id, bool) or not isinstance(customer_id, int):
                errors.append(f"{f['label']} is not valid")
            else:
                cleaned[f["key"]] = {"id": customer_id}
    if errors:
        return None, errors
    return cleaned or None, []


def customer_of(details):
    """The customer a report names, if any: the one answer shaped like a
    customer. A type has at most one Customer field (check_schema)."""
    for value in (details or {}).values():
        if isinstance(value, dict) and isinstance(value.get("id"), int):
            return value
    return None


def describe_details(schema, details):
    """[(label, text)] for showing a report's extra answers, in the schema's
    order. An answer whose field has since been removed still shows, under its key."""
    details = details or {}
    known = {f["key"] for f in schema or []}
    rows = []
    for f in schema or []:
        if f["key"] in details:
            rows.append((f["label"], _show(details[f["key"]])))
    rows += [(key, _show(value)) for key, value in details.items() if key not in known]
    return rows


def _show(value):
    if isinstance(value, dict):
        name, username = value.get("name"), value.get("username")
        return f"{name} ({username})" if name and username else str(name or username or "")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---- Phase 2: repeat visits -----------------------------------------------

REPEAT_WINDOW_DAYS = 30
REPEAT_LIST_MAX = 20


def repeat_groups(rows):
    """Customers and areas visited on more than one day.

    rows: the reports to look at (the caller has already limited them to the
    tracked categories and the 30-day window), each
    {reference, activity_date, location_id, area, details}. A visit is a
    day, so two technicians reporting the same job on the same day are one
    visit, not a repeat. An area is a site, or the typed "Other" text matched
    case-insensitively. A customer is only known where the report carries
    one. A report counts toward its customer AND its area, each in its own list."""
    customers, areas = {}, {}

    def add(groups, key, name, row):
        group = groups.setdefault(key, {"name": name, "days": set(), "references": []})
        group["days"].add(row["activity_date"])
        group["references"].append(row["reference"])

    for row in rows:
        customer = customer_of(row.get("details"))
        if customer:
            add(customers, customer["id"], customer.get("name") or customer.get("username") or "Customer", row)
        if row.get("location_id"):
            add(areas, ("site", row["location_id"]), row["area"], row)
        elif row.get("area"):
            add(areas, ("other", row["area"].strip().lower()), row["area"], row)

    def top(groups):
        found = [{"name": g["name"], "count": len(g["days"]), "last": max(g["days"]), "references": g["references"]}
                 for g in groups.values() if len(g["days"]) > 1]
        found.sort(key=lambda g: (-g["count"], g["name"].lower()))
        return found[:REPEAT_LIST_MAX]

    return {"customers": top(customers), "areas": top(areas)}

