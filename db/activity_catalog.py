"""Technical Activity Log: the admin screen's queries — add, rename, reorder
and deactivate categories and types, and edit a type's extra fields. Admins
only (the router enforces it). Nothing is ever deleted: a deactivated type or
category disappears from the Submit form but old reports keep it.
"""
from psycopg2 import errors
from psycopg2.extras import Json

from db import activity_logic as logic
from db.activity import ActivityInvalid
from db.connection import db_cursor


def get_catalog():
    """Everything, deactivated included, with how many reports use each type."""
    with db_cursor() as (conn, cur):
        cur.execute("""SELECT id, name, sort_order, is_active, repeat_visit_tracking
                       FROM activity_categories ORDER BY sort_order, name""")
        categories = [{"id": r[0], "name": r[1], "sort_order": r[2], "is_active": r[3],
                       "repeat_visit_tracking": r[4], "types": []} for r in cur.fetchall()]
        by_id = {c["id"]: c for c in categories}
        cur.execute("""SELECT t.id, t.category_id, t.name, t.sort_order, t.is_active, t.details_schema,
                              (SELECT count(*) FROM activity_reports r WHERE r.type_id = t.id AND NOT r.is_deleted)
                       FROM activity_types t ORDER BY t.sort_order, t.name""")
        for r in cur.fetchall():
            by_id[r[1]]["types"].append({"id": r[0], "name": r[2], "sort_order": r[3], "is_active": r[4],
                                         "fields": r[5] or [], "report_count": r[6]})
    return categories


def _name_or_raise(value, what):
    name, problem = logic.check_name(value, what)
    if problem:
        raise ActivityInvalid(problem)
    return name


def _taken(cur, sql, params):
    """The tables' UNIQUE only catches an exact match; "wireless" next to
    "Wireless" would look like two categories to a technician."""
    cur.execute(sql, params)
    return cur.fetchone() is not None


def add_category(name):
    name = _name_or_raise(name, "category")
    with db_cursor() as (conn, cur):
        if _taken(cur, "SELECT 1 FROM activity_categories WHERE lower(name) = lower(%s)", (name,)):
            raise ActivityInvalid("There is already a category with that name")
        try:
            cur.execute("""INSERT INTO activity_categories (name, sort_order)
                           VALUES (%s, COALESCE((SELECT max(sort_order) FROM activity_categories), 0) + 1)
                           RETURNING id""", (name,))
        except errors.UniqueViolation:
            raise ActivityInvalid("There is already a category with that name")
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id


def update_category(category_id, changes):
    """changes: any of name, is_active, repeat_visit_tracking. Returns False if no such category."""
    sets, params = [], []
    if "name" in changes:
        sets.append("name = %s")
        params.append(_name_or_raise(changes["name"], "category"))
    for flag in ("is_active", "repeat_visit_tracking"):
        if flag in changes:
            sets.append(f"{flag} = %s")
            params.append(bool(changes[flag]))
    if not sets:
        return True
    with db_cursor() as (conn, cur):
        if "name" in changes and _taken(cur, "SELECT 1 FROM activity_categories WHERE lower(name) = lower(%s) AND id <> %s",
                                        (params[0], category_id)):
            raise ActivityInvalid("There is already a category with that name")
        try:
            cur.execute(f"UPDATE activity_categories SET {', '.join(sets)} WHERE id = %s", params + [category_id])
        except errors.UniqueViolation:
            raise ActivityInvalid("There is already a category with that name")
        changed = cur.rowcount > 0
        conn.commit()
    return changed


def add_type(category_id, name):
    name = _name_or_raise(name, "type")
    with db_cursor() as (conn, cur):
        cur.execute("SELECT 1 FROM activity_categories WHERE id = %s", (category_id,))
        if cur.fetchone() is None:
            raise ActivityInvalid("That category doesn't exist")
        if _taken(cur, "SELECT 1 FROM activity_types WHERE category_id = %s AND lower(name) = lower(%s)", (category_id, name)):
            raise ActivityInvalid("That category already has a type with that name")
        try:
            cur.execute("""INSERT INTO activity_types (category_id, name, sort_order)
                           VALUES (%s, %s, COALESCE((SELECT max(sort_order) FROM activity_types WHERE category_id = %s), 0) + 1)
                           RETURNING id""", (category_id, name, category_id))
        except errors.UniqueViolation:
            raise ActivityInvalid("That category already has a type with that name")
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id


def update_type(type_id, changes):
    """changes: any of name, is_active. A type never moves to another
    category: reports derive their category from it. Returns False if no such type."""
    sets, params = [], []
    if "name" in changes:
        sets.append("name = %s")
        params.append(_name_or_raise(changes["name"], "type"))
    if "is_active" in changes:
        sets.append("is_active = %s")
        params.append(bool(changes["is_active"]))
    if not sets:
        return True
    with db_cursor() as (conn, cur):
        if "name" in changes and _taken(cur, """SELECT 1 FROM activity_types
                                                WHERE lower(name) = lower(%s) AND id <> %s
                                                  AND category_id = (SELECT category_id FROM activity_types WHERE id = %s)""",
                                        (params[0], type_id, type_id)):
            raise ActivityInvalid("That category already has a type with that name")
        try:
            cur.execute(f"UPDATE activity_types SET {', '.join(sets)} WHERE id = %s", params + [type_id])
        except errors.UniqueViolation:
            raise ActivityInvalid("That category already has a type with that name")
        changed = cur.rowcount > 0
        conn.commit()
    return changed


def set_type_fields(type_id, fields, apply_to_category=False):
    """Saves a type's extra fields (an empty list means none, stored as [] so
    the seed never refills it). With apply_to_category the same list goes to
    every type in the category. Returns how many types were changed."""
    clean, problems = logic.check_schema(fields)
    if problems:
        raise ActivityInvalid(problems[0])
    with db_cursor() as (conn, cur):
        if apply_to_category:
            cur.execute("""UPDATE activity_types SET details_schema = %s
                           WHERE category_id = (SELECT category_id FROM activity_types WHERE id = %s)""",
                        (Json(clean), type_id))
        else:
            cur.execute("UPDATE activity_types SET details_schema = %s WHERE id = %s", (Json(clean), type_id))
        changed = cur.rowcount
        conn.commit()
    return changed


def reorder_categories(ids):
    return _reorder("activity_categories", "SELECT id FROM activity_categories", (), ids)


def reorder_types(category_id, ids):
    return _reorder("activity_types", "SELECT id FROM activity_types WHERE category_id = %s", (category_id,), ids)


def _reorder(table, existing_sql, existing_params, ids):
    """Sets sort_order to each id's position. The list must be exactly the
    items being ordered: a stale screen must not silently drop or invent one."""
    with db_cursor() as (conn, cur):
        cur.execute(existing_sql, existing_params)
        current = {r[0] for r in cur.fetchall()}
        if len(ids) != len(set(ids)) or set(ids) != current:
            raise ActivityInvalid("The list changed. Reload the page and try again")
        for position, item_id in enumerate(ids, start=1):
            cur.execute(f"UPDATE {table} SET sort_order = %s WHERE id = %s", (position, item_id))
        conn.commit()
