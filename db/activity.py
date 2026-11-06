"""Technical Activity Log: queries. Rules that need no database (validation,
the edit window, the reference format, Overview bucketing) are in
db/activity_logic.py. Tables are in migration 0026.

Reads users and locations through their own tables, the same cross-domain
read the other domains do; nothing here writes to either.
"""
from psycopg2 import errors
from psycopg2.extras import execute_values

from db import activity_logic as logic
from db import sites as db_sites
from db.connection import db_cursor, now_eat, utc_iso


class ActivityInvalid(Exception):
    """A report refers to something that doesn't exist or isn't usable. The
    message is shown to the user."""


# One place that says which columns a report row has, so the SELECT and the
# dict built from it can't drift apart.
_REPORT_COLUMNS = [
    ("id", "r.id"),
    ("reference", "r.reference"),
    ("activity_date", "r.activity_date"),
    ("category_id", "c.id"),
    ("category", "c.name"),
    ("type_id", "t.id"),
    ("type", "t.name"),
    ("location_id", "r.location_id"),
    ("location", "l.name"),
    ("other_location", "r.other_location"),
    ("outcome", "r.outcome"),
    ("time_spent", "r.time_spent"),
    ("description", "r.description"),
    ("remarks", "r.remarks"),
    ("details", "r.details"),
    ("submitted_by", "r.submitted_by"),
    ("submitted_by_name", "sb.name"),
    ("submitted_at", "r.submitted_at"),
    ("last_edited_by", "r.last_edited_by"),
    ("last_edited_by_name", "eb.name"),
    ("last_edited_at", "r.last_edited_at"),
    ("technicians", """(SELECT COALESCE(json_agg(json_build_object('id', u.id, 'name', u.name)
                                              ORDER BY u.name), '[]'::json)
                         FROM activity_report_technicians x JOIN users u ON u.id = x.user_id
                         WHERE x.report_id = r.id)"""),
]
_REPORT_SELECT = ", ".join(f"{expr} AS {name}" for name, expr in _REPORT_COLUMNS)
_REPORT_FROM = """
    FROM activity_reports r
    JOIN activity_types t ON t.id = r.type_id
    JOIN activity_categories c ON c.id = t.category_id
    JOIN users sb ON sb.id = r.submitted_by
    LEFT JOIN users eb ON eb.id = r.last_edited_by
    LEFT JOIN locations l ON l.id = r.location_id
"""


def _report(row):
    r = dict(zip((name for name, _ in _REPORT_COLUMNS), row))
    r["activity_date"] = r["activity_date"].isoformat()
    r["submitted_at_dt"] = r["submitted_at"]  # naive UTC, for the edit-window check
    r["submitted_at"] = utc_iso(r["submitted_at"])
    r["last_edited_at"] = utc_iso(r["last_edited_at"])
    r["area"] = r["location"] or r["other_location"]
    r["outcome_label"] = logic.OUTCOME_LABELS.get(r["outcome"], r["outcome"])
    r["time_spent_label"] = logic.TIME_SPENT_LABELS.get(r["time_spent"])
    return r


# ---- Form options ---------------------------------------------------------

def get_options(user_id):
    """Everything the Submit form shows, so the browser hardcodes none of it."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, name FROM activity_categories WHERE is_active ORDER BY sort_order, name")
        categories = [{"id": r[0], "name": r[1], "types": []} for r in cur.fetchall()]
        by_id = {c["id"]: c for c in categories}
        cur.execute("""SELECT id, category_id, name FROM activity_types
                       WHERE is_active ORDER BY sort_order, name""")
        for type_id, category_id, name in cur.fetchall():
            if category_id in by_id:
                by_id[category_id]["types"].append({"id": type_id, "name": name})

        cur.execute("SELECT id, name FROM users WHERE status = 'active' ORDER BY name")
        technicians = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

        cur.execute("""SELECT location_id, other_location FROM activity_reports
                       WHERE submitted_by = %s AND NOT is_deleted
                       ORDER BY submitted_at DESC, id DESC LIMIT 1""", (user_id,))
        last = cur.fetchone()

    return {
        "today": now_eat().date().isoformat(),
        "categories": [c for c in categories if c["types"]],
        "locations": [{"id": loc["id"], "name": loc["name"]} for loc in db_sites.get_all_locations()],
        "technicians": technicians,
        "outcomes": [{"key": k, "label": label} for k, label in logic.OUTCOMES],
        "default_outcome": logic.DEFAULT_OUTCOME,
        "explanation_outcomes": list(logic.EXPLANATION_OUTCOMES),
        "time_spent": [{"key": k, "label": label} for k, label, _ in logic.TIME_SPENT],
        "last_location": {"location_id": last[0], "other_location": last[1]} if last else None,
    }


def get_filter_options():
    """For the Reports filters: every category and type, deactivated ones too,
    because an old report still carries them."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, name FROM activity_categories ORDER BY sort_order, name")
        categories = [{"id": r[0], "name": r[1], "types": []} for r in cur.fetchall()]
        by_id = {c["id"]: c for c in categories}
        cur.execute("SELECT id, category_id, name FROM activity_types ORDER BY sort_order, name")
        for type_id, category_id, name in cur.fetchall():
            by_id[category_id]["types"].append({"id": type_id, "name": name})
        cur.execute("SELECT DISTINCT u.id, u.name FROM users u JOIN activity_report_technicians x ON x.user_id = u.id ORDER BY u.name")
        technicians = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT l.id, l.name FROM locations l
                       JOIN activity_reports r ON r.location_id = l.id ORDER BY l.name""")
        locations = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
    return {
        "categories": categories,
        "technicians": technicians,
        "locations": locations,
        "outcomes": [{"key": k, "label": label} for k, label in logic.OUTCOMES],
    }


# ---- Writing --------------------------------------------------------------

def _check_references(cur, cleaned, technician_ids, existing=None):
    """The type, location and people must exist and be active — unless the
    report being edited already had them (a type deactivated later must not
    block fixing a typo in an old report)."""
    existing = existing or {}

    cur.execute("SELECT is_active FROM activity_types WHERE id = %s", (cleaned["type_id"],))
    row = cur.fetchone()
    if row is None or (not row[0] and cleaned["type_id"] != existing.get("type_id")):
        raise ActivityInvalid("That activity type isn't available. Pick another")

    if cleaned["location_id"]:
        cur.execute("SELECT is_active FROM locations WHERE id = %s", (cleaned["location_id"],))
        row = cur.fetchone()
        if row is None or (not row[0] and cleaned["location_id"] != existing.get("location_id")):
            raise ActivityInvalid("That location isn't available. Pick another")

    cur.execute("SELECT id, status FROM users WHERE id = ANY(%s)", (list(technician_ids),))
    found = {r[0]: r[1] for r in cur.fetchall()}
    for user_id in technician_ids:
        if user_id not in found:
            raise ActivityInvalid("One of the technicians doesn't exist")
        if found[user_id] != "active" and user_id not in existing.get("technician_ids", ()):
            raise ActivityInvalid("One of the technicians is no longer active")


def _by_token(cur, token, user_id):
    cur.execute("SELECT id, reference FROM activity_reports WHERE client_token = %s AND submitted_by = %s",
                (token, user_id))
    row = cur.fetchone()
    return {"id": row[0], "reference": row[1], "duplicate": True} if row else None


def create_report(user_id, cleaned, technician_ids, client_token=None):
    """Saves a report and returns its id and reference. The reference comes
    from a per-year counter inside the same transaction, so two people
    submitting at once can't share one and a failed save doesn't burn a
    number. Re-sending the same client_token returns the report already
    saved (duplicate=True) instead of making a second."""
    people = logic.technicians_with_submitter(user_id, technician_ids)
    with db_cursor() as (conn, cur):
        if client_token:
            existing = _by_token(cur, client_token, user_id)
            if existing:
                return existing
        _check_references(cur, cleaned, people)

        year = now_eat().year
        cur.execute("""INSERT INTO activity_reference_counters (year, last_number) VALUES (%s, 1)
                       ON CONFLICT (year) DO UPDATE SET last_number = activity_reference_counters.last_number + 1
                       RETURNING last_number""", (year,))
        reference = logic.format_reference(year, cur.fetchone()[0])

        try:
            cur.execute("""
                INSERT INTO activity_reports
                    (reference, client_token, activity_date, type_id, location_id, other_location,
                     outcome, description, time_spent, remarks, submitted_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (reference, client_token, cleaned["activity_date"], cleaned["type_id"],
                 cleaned["location_id"], cleaned["other_location"], cleaned["outcome"],
                 cleaned["description"], cleaned["time_spent"], cleaned["remarks"], user_id))
        except errors.UniqueViolation:
            # Two sends of the same form arrived together; the other one won.
            conn.rollback()
            existing = _by_token(cur, client_token, user_id) if client_token else None
            if existing:
                return existing
            raise ActivityInvalid("Couldn't save the report. Please try again")
        report_id = cur.fetchone()[0]
        execute_values(cur, "INSERT INTO activity_report_technicians (report_id, user_id) VALUES %s",
                       [(report_id, u) for u in people])
        conn.commit()
    return {"id": report_id, "reference": reference, "duplicate": False}


def update_report(report_id, editor_id, cleaned, technician_ids):
    """Replaces a report's editable fields and stamps who edited it and when.
    The submitter stays on the report whoever edits it."""
    with db_cursor() as (conn, cur):
        cur.execute("""SELECT submitted_by, type_id, location_id FROM activity_reports
                       WHERE id = %s AND NOT is_deleted FOR UPDATE""", (report_id,))
        row = cur.fetchone()
        if row is None:
            return False
        submitter, old_type, old_location = row
        cur.execute("SELECT user_id FROM activity_report_technicians WHERE report_id = %s", (report_id,))
        old_people = [r[0] for r in cur.fetchall()]

        people = logic.technicians_with_submitter(submitter, technician_ids)
        _check_references(cur, cleaned, people,
                          existing={"type_id": old_type, "location_id": old_location,
                                    "technician_ids": old_people})
        cur.execute("""
            UPDATE activity_reports
            SET activity_date = %s, type_id = %s, location_id = %s, other_location = %s,
                outcome = %s, description = %s, time_spent = %s, remarks = %s,
                last_edited_by = %s, last_edited_at = now()
            WHERE id = %s""",
            (cleaned["activity_date"], cleaned["type_id"], cleaned["location_id"],
             cleaned["other_location"], cleaned["outcome"], cleaned["description"],
             cleaned["time_spent"], cleaned["remarks"], editor_id, report_id))
        cur.execute("DELETE FROM activity_report_technicians WHERE report_id = %s", (report_id,))
        execute_values(cur, "INSERT INTO activity_report_technicians (report_id, user_id) VALUES %s",
                       [(report_id, u) for u in people])
        conn.commit()
    return True


def soft_delete_report(report_id, user_id):
    """Never a hard delete: the row stays, hidden from lists and totals."""
    with db_cursor() as (conn, cur):
        cur.execute("""UPDATE activity_reports SET is_deleted = true, deleted_by = %s, deleted_at = now()
                       WHERE id = %s AND NOT is_deleted""", (user_id, report_id))
        changed = cur.rowcount > 0
        conn.commit()
    return changed


# ---- Reading --------------------------------------------------------------

def get_report(report_id):
    with db_cursor() as (conn, cur):
        cur.execute(f"SELECT {_REPORT_SELECT} {_REPORT_FROM} WHERE r.id = %s AND NOT r.is_deleted", (report_id,))
        row = cur.fetchone()
    return _report(row) if row else None


def is_involved(report, user_id):
    return report["submitted_by"] == user_id or any(t["id"] == user_id for t in report["technicians"])


def _where(filters, only_user_id):
    """WHERE clause shared by the list, the CSV and the counts. only_user_id
    limits it to reports that person submitted or is listed on (a technician
    without the see-all permission)."""
    clauses, params = ["NOT r.is_deleted"], []
    if only_user_id is not None:
        clauses.append("""(r.submitted_by = %s OR EXISTS (SELECT 1 FROM activity_report_technicians me
                           WHERE me.report_id = r.id AND me.user_id = %s))""")
        params += [only_user_id, only_user_id]
    if filters.get("date_from"):
        clauses.append("r.activity_date >= %s")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("r.activity_date <= %s")
        params.append(filters["date_to"])
    if filters.get("category_id"):
        clauses.append("c.id = %s")
        params.append(filters["category_id"])
    if filters.get("type_id"):
        clauses.append("t.id = %s")
        params.append(filters["type_id"])
    if filters.get("location_id"):
        clauses.append("r.location_id = %s")
        params.append(filters["location_id"])
    if filters.get("technician_id"):
        clauses.append("""EXISTS (SELECT 1 FROM activity_report_technicians ft
                           WHERE ft.report_id = r.id AND ft.user_id = %s)""")
        params.append(filters["technician_id"])
    if filters.get("outcome"):
        clauses.append("r.outcome = %s")
        params.append(filters["outcome"])
    if filters.get("q"):
        clauses.append("(r.reference ILIKE %s OR r.description ILIKE %s)")
        pattern = logic.like_pattern(filters["q"])
        params += [pattern, pattern]
    return " AND ".join(clauses), params


_NEWEST_FIRST = "ORDER BY r.activity_date DESC, r.submitted_at DESC, r.id DESC"


def list_reports(filters, only_user_id, limit, offset):
    where, params = _where(filters, only_user_id)
    with db_cursor() as (conn, cur):
        cur.execute(f"SELECT count(*) {_REPORT_FROM} WHERE {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"SELECT {_REPORT_SELECT} {_REPORT_FROM} WHERE {where} {_NEWEST_FIRST} LIMIT %s OFFSET %s",
                    params + [limit, offset])
        rows = [_report(r) for r in cur.fetchall()]
    return {"total": total, "reports": rows}


def export_reports(filters, only_user_id, limit=10000):
    where, params = _where(filters, only_user_id)
    with db_cursor() as (conn, cur):
        cur.execute(f"SELECT {_REPORT_SELECT} {_REPORT_FROM} WHERE {where} {_NEWEST_FIRST} LIMIT %s",
                    params + [limit])
        return [_report(r) for r in cur.fetchall()]


# ---- Overview -------------------------------------------------------------

def get_overview(start, end, bucket):
    """The six core metrics for reports dated start..end (inclusive).
    Deleted reports are excluded from every figure."""
    base = "FROM activity_reports r WHERE NOT r.is_deleted AND r.activity_date BETWEEN %s AND %s"
    period = (start, end)
    with db_cursor() as (conn, cur):
        # bucket is one of logic.BUCKETS (checked by the caller), never user text.
        cur.execute(f"SELECT date_trunc('{bucket}', r.activity_date)::date, count(*) {base} GROUP BY 1", period)
        over_time = logic.fill_buckets(start, end, bucket, {r[0]: r[1] for r in cur.fetchall()})

        cur.execute(f"""SELECT c.id, c.name, t.id, t.name, count(*)
                        FROM activity_reports r
                        JOIN activity_types t ON t.id = r.type_id
                        JOIN activity_categories c ON c.id = t.category_id
                        WHERE NOT r.is_deleted AND r.activity_date BETWEEN %s AND %s
                        GROUP BY c.id, c.name, t.id, t.name""", period)
        categories = {}
        for cat_id, cat_name, type_id, type_name, n in cur.fetchall():
            cat = categories.setdefault(cat_id, {"category_id": cat_id, "category": cat_name, "count": 0, "types": []})
            cat["count"] += n
            cat["types"].append({"type_id": type_id, "type": type_name, "count": n})
        by_category = sorted(categories.values(), key=lambda c: (-c["count"], c["category"]))
        for cat in by_category:
            cat["types"].sort(key=lambda t: (-t["count"], t["type"]))

        cur.execute(f"""SELECT r.location_id, COALESCE(l.name, 'Other'), count(*)
                        FROM activity_reports r LEFT JOIN locations l ON l.id = r.location_id
                        WHERE NOT r.is_deleted AND r.activity_date BETWEEN %s AND %s
                        GROUP BY r.location_id, l.name
                        ORDER BY count(*) DESC, 2""", period)
        by_area = [{"location_id": r[0], "area": r[1], "count": r[2]} for r in cur.fetchall()]

        # Everyone listed on a report counts it, the submitter included.
        cur.execute(f"""SELECT u.id, u.name, count(*)
                        FROM activity_reports r
                        JOIN activity_report_technicians x ON x.report_id = r.id
                        JOIN users u ON u.id = x.user_id
                        WHERE NOT r.is_deleted AND r.activity_date BETWEEN %s AND %s
                        GROUP BY u.id, u.name ORDER BY count(*) DESC, u.name""", period)
        by_technician = [{"user_id": r[0], "name": r[1], "count": r[2]} for r in cur.fetchall()]

        cur.execute(f"SELECT r.outcome, count(*) {base} GROUP BY r.outcome", period)
        outcomes = logic.outcome_mix({r[0]: r[1] for r in cur.fetchall()})

        cur.execute(f"""SELECT c.id, r.time_spent, count(*)
                        FROM activity_reports r
                        JOIN activity_types t ON t.id = r.type_id
                        JOIN activity_categories c ON c.id = t.category_id
                        WHERE NOT r.is_deleted AND r.activity_date BETWEEN %s AND %s
                              AND r.time_spent IS NOT NULL
                        GROUP BY c.id, r.time_spent""", period)
        timed = {}
        for cat_id, key, n in cur.fetchall():
            timed.setdefault(cat_id, {})[key] = n

    # Effort only counts reports that gave a time, so each row also says how
    # many of the category's reports that was.
    time_spent = []
    for cat in by_category:
        counts = timed.get(cat["category_id"], {})
        time_spent.append({
            "category_id": cat["category_id"],
            "category": cat["category"],
            "hours": logic.hours_for(counts),
            "reports_with_time": sum(counts.values()),
            "reports": cat["count"],
        })
    time_spent.sort(key=lambda t: (-t["hours"], t["category"]))

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bucket": bucket,
        "total": outcomes["total"],
        "over_time": over_time,
        "by_category": by_category,
        "by_area": by_area,
        "by_technician": by_technician,
        "outcomes": outcomes,
        "time_spent": time_spent,
    }
