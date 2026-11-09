"""Technical Activity Log: validation, the edit window, who sees what, the
reference format and the Overview's bucketing. No database — the data layer is
replaced by fakes. Run from the repo root:
  venv/bin/python -m unittest tests.test_activity -v
"""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from fastapi import HTTPException

from db import activity_logic as logic
from routers import activity as api

TODAY = date(2026, 11, 6)
NOW = datetime(2026, 11, 6, 9, 0, 0)

ADMIN = {"id": 1, "role": "admin", "role_id": None, "name": "Owner"}
TECH = {"id": 2, "role": "technician", "role_id": 5, "name": "Tech"}
OTHER_TECH = {"id": 3, "role": "technician", "role_id": 5, "name": "Other"}
MANAGER = {"id": 4, "role": "Manager", "role_id": 6, "name": "Boss"}


def fields(**overrides):
    base = {"activity_date": TODAY, "type_id": 7, "location_id": 3, "other_location": None,
            "outcome": "completed", "description": None, "time_spent": None, "remarks": None}
    base.update(overrides)
    return base


def has_permissions(*granted):
    """user_has_permission for a custom role holding exactly these activity_log actions."""
    def check(user, section, action):
        if user["role"] == "admin":
            return True
        return section == "activity_log" and action in granted
    return check


class CheckReport(unittest.TestCase):
    def test_minimal_report_is_valid_and_defaults_to_completed(self):
        cleaned, errors = logic.check_report(fields(outcome=None), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(cleaned["outcome"], "completed")

    def test_future_date_refused_but_an_earlier_day_is_fine(self):
        self.assertTrue(logic.check_report(fields(activity_date=TODAY + timedelta(days=1)), TODAY)[1])
        self.assertEqual(logic.check_report(fields(activity_date=TODAY - timedelta(days=30)), TODAY)[1], [])

    def test_location_needs_a_site_or_other_text(self):
        self.assertTrue(logic.check_report(fields(location_id=None, other_location=None), TODAY)[1])
        self.assertTrue(logic.check_report(fields(location_id=None, other_location="   "), TODAY)[1])
        cleaned, errors = logic.check_report(fields(location_id=None, other_location=" Mwiki gate "), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(cleaned["other_location"], "Mwiki gate")

    def test_a_site_wins_over_leftover_other_text(self):
        cleaned, _ = logic.check_report(fields(location_id=3, other_location="old text"), TODAY)
        self.assertIsNone(cleaned["other_location"])

    def test_explanation_required_only_for_partial_and_unable(self):
        for outcome in ("partially_completed", "unable_to_resolve"):
            self.assertTrue(logic.check_report(fields(outcome=outcome), TODAY)[1], outcome)
            self.assertTrue(logic.check_report(fields(outcome=outcome, description="  "), TODAY)[1], outcome)
            self.assertEqual(logic.check_report(fields(outcome=outcome, description="No power at site"), TODAY)[1], [])
        for outcome in ("completed", "inspection_only"):
            self.assertEqual(logic.check_report(fields(outcome=outcome), TODAY)[1], [])

    def test_unknown_outcome_and_time_spent_refused(self):
        self.assertTrue(logic.check_report(fields(outcome="done-ish"), TODAY)[1])
        self.assertTrue(logic.check_report(fields(time_spent="2h"), TODAY)[1])

    def test_blank_optional_text_becomes_none(self):
        cleaned, _ = logic.check_report(fields(description="  ", remarks=""), TODAY)
        self.assertIsNone(cleaned["description"])
        self.assertIsNone(cleaned["remarks"])


class Rules(unittest.TestCase):
    def test_reference_format(self):
        self.assertEqual(logic.format_reference(2026, 1), "TAL-2026-00001")
        self.assertEqual(logic.format_reference(2027, 12345), "TAL-2027-12345")

    def test_submitter_is_always_on_the_report_once(self):
        self.assertEqual(logic.technicians_with_submitter(2, []), [2])
        self.assertEqual(logic.technicians_with_submitter(2, [5, 2, 5, 6]), [2, 5, 6])

    def test_edit_window(self):
        report = {"submitted_by": TECH["id"], "submitted_at": NOW - timedelta(hours=47)}
        self.assertTrue(logic.can_edit(report, TECH, NOW))
        report["submitted_at"] = NOW - timedelta(hours=49)
        self.assertFalse(logic.can_edit(report, TECH, NOW))

    def test_only_the_submitter_edits_and_admin_edits_anything(self):
        report = {"submitted_by": TECH["id"], "submitted_at": NOW - timedelta(hours=1)}
        self.assertFalse(logic.can_edit(report, OTHER_TECH, NOW))
        old = {"submitted_by": TECH["id"], "submitted_at": NOW - timedelta(days=300)}
        self.assertTrue(logic.can_edit(old, ADMIN, NOW))

    def test_only_admin_deletes(self):
        self.assertTrue(logic.can_delete(ADMIN))
        self.assertFalse(logic.can_delete(TECH))
        self.assertFalse(logic.can_delete(MANAGER))

    def test_search_text_is_taken_literally(self):
        self.assertEqual(logic.like_pattern("50%_off"), "%50\\%\\_off%")

    def test_csv_cells_cannot_run_as_formulas(self):
        self.assertEqual(logic.csv_cell("=1+1"), "'=1+1")
        self.assertEqual(logic.csv_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(logic.csv_cell("Fibre repair"), "Fibre repair")
        self.assertEqual(logic.csv_cell(None), "")

    def test_time_chips_count_as_agreed(self):
        self.assertEqual(logic.hours_for({"under_30m": 2, "1h": 1, "half_day": 1, "full_day": 1}), 14.0)


class OverviewShapes(unittest.TestCase):
    def test_days_without_reports_show_as_zero(self):
        points = logic.fill_buckets(date(2026, 11, 1), date(2026, 11, 4), "day", {date(2026, 11, 2): 3})
        self.assertEqual([p["count"] for p in points], [0, 3, 0, 0])

    def test_weeks_start_on_monday_and_include_the_first_partial_week(self):
        # 2026-11-04 is a Wednesday; its week starts Monday 2026-11-02.
        points = logic.fill_buckets(date(2026, 11, 4), date(2026, 11, 17), "week", {date(2026, 11, 2): 5})
        self.assertEqual([p["period"] for p in points], ["2026-11-02", "2026-11-09", "2026-11-16"])
        self.assertEqual(points[0]["count"], 5)

    def test_months_roll_over_the_year(self):
        points = logic.fill_buckets(date(2026, 11, 20), date(2027, 1, 5), "month", {})
        self.assertEqual([p["period"] for p in points], ["2026-11-01", "2026-12-01", "2027-01-01"])

    def test_outcome_shares_and_unable_rate(self):
        mix = logic.outcome_mix({"completed": 6, "unable_to_resolve": 2, "inspection_only": 2})
        self.assertEqual(mix["total"], 10)
        self.assertEqual(mix["unable_rate"], 0.2)
        self.assertEqual([r["outcome"] for r in mix["outcomes"]],
                         ["completed", "partially_completed", "inspection_only", "unable_to_resolve"])

    def test_outcome_mix_with_no_reports_is_zero_not_an_error(self):
        mix = logic.outcome_mix({})
        self.assertEqual(mix["total"], 0)
        self.assertEqual(mix["unable_rate"], 0)

    def test_range_checks(self):
        self.assertIsNone(logic.check_overview_range(TODAY - timedelta(days=6), TODAY, "day"))
        self.assertTrue(logic.check_overview_range(TODAY, TODAY - timedelta(days=1), "day"))
        self.assertTrue(logic.check_overview_range(TODAY - timedelta(days=400), TODAY, "day"))
        self.assertTrue(logic.check_overview_range(TODAY, TODAY, "year"))


class Endpoints(unittest.TestCase):
    """The router with the data layer faked: who may do what."""

    def setUp(self):
        self.db = mock.patch.object(api, "db").start()
        self.db.ActivityInvalid = Exception
        self.db.is_involved.side_effect = lambda r, uid: r["submitted_by"] == uid or any(
            t["id"] == uid for t in r["technicians"])
        self.addCleanup(mock.patch.stopall)

    def permissions(self, *granted):
        mock.patch.object(api, "user_has_permission", has_permissions(*granted)).start()

    def report(self, submitted_by=2, hours_ago=1, technicians=(2,)):
        return {"id": 9, "reference": "TAL-2026-00009", "submitted_by": submitted_by,
                "submitted_at_dt": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago),
                "technicians": [{"id": t, "name": "x"} for t in technicians]}

    def body(self, **overrides):
        data = {"activity_date": date.today(), "type_id": 7, "location_id": 3}
        data.update(overrides)
        return api.ReportFields(**data)

    def test_technician_without_see_all_is_limited_to_their_own(self):
        self.permissions("view", "submit")
        self.db.list_reports.return_value = {"total": 0, "reports": []}
        api.list_reports(current_user=TECH)
        self.assertEqual(self.db.list_reports.call_args.args[1], TECH["id"])

    def test_manager_with_see_all_is_not_limited(self):
        self.permissions("view", "view_all")
        self.db.list_reports.return_value = {"total": 0, "reports": []}
        api.list_reports(current_user=MANAGER)
        self.assertIsNone(self.db.list_reports.call_args.args[1])

    def test_a_report_you_are_not_on_looks_missing(self):
        self.permissions("view", "submit")
        self.db.get_report.return_value = self.report(submitted_by=3, technicians=(3,))
        with self.assertRaises(HTTPException) as caught:
            api.get_report(9, current_user=TECH)
        self.assertEqual(caught.exception.status_code, 404)

    def test_a_listed_technician_can_see_but_not_edit(self):
        self.permissions("view", "submit")
        self.db.get_report.return_value = self.report(submitted_by=3, technicians=(3, 2))
        detail = api.get_report(9, current_user=TECH)
        self.assertFalse(detail["can_edit"])
        self.assertFalse(detail["can_delete"])

    def test_submitter_edits_inside_the_window_and_not_after(self):
        self.permissions("view", "submit")
        self.db.get_report.return_value = self.report(hours_ago=47)
        api.edit_report(9, self.body(), current_user=TECH)
        self.db.update_report.assert_called_once()

        self.db.update_report.reset_mock()
        self.db.get_report.return_value = self.report(hours_ago=49)
        with self.assertRaises(HTTPException) as caught:
            api.edit_report(9, self.body(), current_user=TECH)
        self.assertEqual(caught.exception.status_code, 403)
        self.db.update_report.assert_not_called()

    def test_admin_edits_an_old_report(self):
        self.db.get_report.return_value = self.report(submitted_by=2, hours_ago=24 * 90)
        api.edit_report(9, self.body(), current_user=ADMIN)
        self.db.update_report.assert_called_once()

    def test_edit_needs_the_submit_permission(self):
        self.permissions("view")
        self.db.get_report.return_value = self.report()
        with self.assertRaises(HTTPException):
            api.edit_report(9, self.body(), current_user=TECH)

    def test_only_admin_deletes(self):
        with self.assertRaises(HTTPException) as caught:
            api.require_admin(TECH)
        self.assertEqual(caught.exception.status_code, 403)
        self.db.soft_delete_report.return_value = True
        self.assertEqual(api.delete_report(9, current_user=ADMIN)["deleted"], True)

    def test_a_failed_check_comes_back_as_400_with_the_reason(self):
        with self.assertRaises(HTTPException) as caught:
            api.submit_report(api.ReportCreate(activity_date=date.today(), type_id=7, location_id=3,
                                               outcome="unable_to_resolve"), current_user=TECH)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("explanation", caught.exception.detail)
        self.db.create_report.assert_not_called()

    def test_submit_passes_the_token_through(self):
        self.db.create_report.return_value = {"id": 1, "reference": "TAL-2026-00001", "duplicate": False}
        api.submit_report(api.ReportCreate(activity_date=date.today(), type_id=7, location_id=3,
                                           client_token="abc"), current_user=TECH)
        self.assertEqual(self.db.create_report.call_args.args[3], "abc")

    def test_overview_rejects_half_a_range_and_bad_buckets(self):
        for kwargs in ({"start": TODAY}, {"start": TODAY, "end": TODAY, "bucket": "year"}):
            with self.assertRaises(HTTPException):
                api.get_overview(current_user=MANAGER, **kwargs)

    def test_csv_export_defuses_formulas(self):
        self.permissions("view", "view_all")
        self.db.export_reports.return_value = [{
            "reference": "TAL-2026-00001", "activity_date": "2026-11-06", "category": "Customer Support",
            "type": "No internet", "area": "Mwiki", "technicians": [{"id": 2, "name": "Tech"}],
            "outcome_label": "Completed", "time_spent_label": None, "description": "=HYPERLINK(\"x\")",
            "remarks": None, "details_view": [{"label": "Package", "text": "10 Mbps"}],
            "follows_up": None, "followed_up_by": {"reference": "TAL-2026-00009"},
            "submitted_by_name": "Tech", "submitted_at_dt": NOW}]
        response = api.export_reports(current_user=MANAGER)
        text = response.body.decode()
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("2026-11-06 12:00", text)  # 09:00 UTC is 12:00 in Nairobi
        self.assertIn("Package: 10 Mbps", text)
        self.assertIn("TAL-2026-00009", text)
        self.assertIn("text/csv", response.media_type)


# ---- Phase 2 --------------------------------------------------------------

INSTALL_FIELDS = [
    {"key": "customer", "label": "Customer", "kind": "customer", "required": False},
    {"key": "package", "label": "Package", "kind": "text", "required": False},
    {"key": "signal_dbm", "label": "Optical signal reading (dBm)", "kind": "number", "required": False, "min": -60, "max": 10},
]


class ExtraFields(unittest.TestCase):
    def test_valid_answers_are_kept_and_blanks_dropped(self):
        cleaned, errors = logic.check_details(INSTALL_FIELDS, {"package": " 10 Mbps ", "signal_dbm": "-18.5", "customer": None})
        self.assertEqual(errors, [])
        self.assertEqual(cleaned, {"package": "10 Mbps", "signal_dbm": -18.5})

    def test_nothing_answered_stores_nothing(self):
        self.assertEqual(logic.check_details(INSTALL_FIELDS, {}), (None, []))
        self.assertEqual(logic.check_details(INSTALL_FIELDS, None), (None, []))

    def test_answers_for_fields_the_type_does_not_have_are_dropped(self):
        cleaned, _ = logic.check_details(INSTALL_FIELDS, {"package": "x", "cable_length": 300})
        self.assertEqual(cleaned, {"package": "x"})

    def test_signal_reading_must_be_a_sane_number(self):
        for bad in ("abc", "-200", 99, float("nan"), True):
            self.assertTrue(logic.check_details(INSTALL_FIELDS, {"signal_dbm": bad})[1], bad)

    def test_required_field_is_enforced(self):
        schema = [{"key": "fault", "label": "Fault encountered", "kind": "text", "required": True}]
        self.assertEqual(logic.check_details(schema, {"fault": "  "})[1], ["Fault encountered is required"])
        self.assertEqual(logic.check_details(schema, {"fault": "Cut cable"})[1], [])

    def test_customer_answer_needs_an_id_and_is_filled_in_by_the_database(self):
        self.assertEqual(logic.check_details(INSTALL_FIELDS, {"customer": {"id": 5, "name": "forged"}})[0],
                         {"customer": {"id": 5}})
        self.assertTrue(logic.check_details(INSTALL_FIELDS, {"customer": {"id": "5"}})[1])
        self.assertTrue(logic.check_details(INSTALL_FIELDS, {"customer": "Jane"})[1])

    def test_long_text_refused(self):
        self.assertTrue(logic.check_details(INSTALL_FIELDS, {"package": "x" * 201})[1])

    def test_a_materials_list_is_a_few_lines_of_text_and_nothing_is_deducted(self):
        schema = [{"key": "materials", "label": "Materials used", "kind": "longtext", "required": False}]
        listing = "67 m drop cable\n1 xpon router\n1 patchcord"
        self.assertEqual(logic.check_details(schema, {"materials": f"  {listing} "}), ({"materials": listing}, []))
        self.assertTrue(logic.check_details(schema, {"materials": "x" * 1001})[1])
        self.assertEqual(logic.check_schema(schema)[1], [])

    def test_details_read_back_in_the_schemas_order_and_keep_orphans(self):
        rows = logic.describe_details(INSTALL_FIELDS, {"signal_dbm": -18.0, "customer": {"id": 1, "name": "Jane", "username": "jane01"},
                                                       "old_field": "kept"})
        self.assertEqual(rows, [("Customer", "Jane (jane01)"), ("Optical signal reading (dBm)", "-18"), ("old_field", "kept")])


class FieldDefinitions(unittest.TestCase):
    def test_a_good_list_is_accepted(self):
        clean, errors = logic.check_schema(INSTALL_FIELDS)
        self.assertEqual(errors, [])
        self.assertEqual([f["key"] for f in clean], ["customer", "package", "signal_dbm"])

    def test_no_fields_is_fine(self):
        self.assertEqual(logic.check_schema([]), ([], []))
        self.assertEqual(logic.check_schema(None), ([], []))

    def test_bad_definitions_refused(self):
        ok = {"key": "a", "label": "A", "kind": "text"}
        for bad in (
            [{**ok, "kind": "photo"}],                     # unknown kind
            [{**ok, "key": "Bad Key"}],                    # not a slug
            [{**ok, "key": "1a"}],                         # starts with a digit
            [{**ok, "label": "  "}],                       # no label
            [ok, ok],                                      # duplicate key
            [{**ok, "kind": "number", "min": 5, "max": 1}],
            [{**ok, "kind": "number", "min": "x"}],
            [{"key": "c1", "label": "C1", "kind": "customer"}, {"key": "c2", "label": "C2", "kind": "customer"}],
            [ok] * (logic.MAX_FIELDS_PER_TYPE + 1),
            "not a list",
        ):
            self.assertTrue(logic.check_schema(bad)[1], bad)

    def test_names(self):
        self.assertEqual(logic.check_name("  Fibre   repair ", "type"), ("Fibre repair", None))
        self.assertIsNotNone(logic.check_name("   ", "type")[1])
        self.assertIsNotNone(logic.check_name("x" * 81, "type")[1])


class RepeatVisits(unittest.TestCase):
    def row(self, ref, day, location_id=1, area="Mwiki", details=None):
        return {"reference": ref, "activity_date": day, "location_id": location_id, "area": area, "details": details}

    def test_area_with_two_reports_is_a_repeat(self):
        out = logic.repeat_groups([self.row("A", "2026-11-01"), self.row("B", "2026-11-05"), self.row("C", "2026-11-05", 2, "Sunton")])
        self.assertEqual([(g["name"], g["count"], g["last"]) for g in out["areas"]], [("Mwiki", 2, "2026-11-05")])
        self.assertEqual(out["customers"], [])

    def test_a_single_visit_is_not_a_repeat(self):
        self.assertEqual(logic.repeat_groups([self.row("A", "2026-11-01")]), {"customers": [], "areas": []})

    def test_customers_counted_where_reports_name_one(self):
        jane = {"customer": {"id": 7, "name": "Jane", "username": "jane01"}}
        out = logic.repeat_groups([self.row("A", "2026-11-01", 1, "Mwiki", jane), self.row("B", "2026-11-02", 2, "Sunton", jane),
                                   self.row("C", "2026-11-03", 3, "Stima")])
        self.assertEqual([(g["name"], g["count"]) for g in out["customers"]], [("Jane", 2)])
        self.assertEqual(out["areas"], [])      # different sites: no repeated area

    def test_other_locations_match_ignoring_case_and_spaces(self):
        out = logic.repeat_groups([self.row("A", "2026-11-01", None, "Behind Stima"), self.row("B", "2026-11-02", None, " behind stima ")])
        self.assertEqual([g["count"] for g in out["areas"]], [2])

    def test_two_reports_of_the_same_job_on_one_day_are_one_visit(self):
        out = logic.repeat_groups([self.row("A", "2026-11-05"), self.row("B", "2026-11-05")])
        self.assertEqual(out, {"customers": [], "areas": []})

    def test_an_account_typed_in_identifies_a_customer_who_is_not_in_the_list_yet(self):
        esther = lambda: {"client_name": "Esther", "account": "JS16"}
        out = logic.repeat_groups([self.row("A", "2026-10-13", 1, "ACK Road", esther()),
                                   self.row("B", "2026-10-17", 1, "ACK Road", {"account": " js16 "}),
                                   self.row("C", "2026-10-19", 2, "Catholic Rd", {"account": "JS99"})])
        self.assertEqual([(g["name"], g["count"]) for g in out["customers"]], [("Esther", 2)])

    def test_a_picked_customer_and_a_typed_account_are_the_same_customer(self):
        picked = {"customer": {"id": 7, "name": "Esther W", "username": "JS16"}}
        out = logic.repeat_groups([self.row("A", "2026-10-13", 1, "ACK", picked), self.row("B", "2026-10-17", 2, "Sunton", {"account": "js16"})])
        self.assertEqual([g["count"] for g in out["customers"]], [2])

    def test_a_report_with_no_account_or_customer_names_nobody(self):
        self.assertIsNone(logic.customer_identity({"package": "10 Mbps"}))
        self.assertIsNone(logic.customer_identity({"account": "  "}))

    def test_most_repeated_first(self):
        rows = [self.row(f"A{i}", f"2026-11-0{i + 1}", 1, "Mwiki") for i in range(3)] + [self.row(f"B{i}", f"2026-11-0{i + 1}", 2, "Sunton") for i in range(2)]
        self.assertEqual([g["name"] for g in logic.repeat_groups(rows)["areas"]], ["Mwiki", "Sunton"])


class Phase2Endpoints(unittest.TestCase):
    def setUp(self):
        self.db = mock.patch.object(api, "db").start()
        self.db.ActivityInvalid = type("ActivityInvalid", (Exception,), {})
        self.catalog = mock.patch.object(api, "catalog").start()
        self.addCleanup(mock.patch.stopall)

    def create(self, **kw):
        return api.ReportCreate(activity_date=date.today(), type_id=7, location_id=3, **kw)

    def test_a_possible_duplicate_comes_back_as_409_with_the_existing_reference(self):
        self.db.prepare_details.return_value = None
        self.db.create_report.return_value = {"possible_duplicates": [{"id": 4, "reference": "TAL-2026-00004", "submitted_by_name": "Other"}]}
        with self.assertRaises(HTTPException) as caught:
            api.submit_report(self.create(), current_user=TECH)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["possible_duplicates"][0]["reference"], "TAL-2026-00004")

    def test_submit_anyway_is_passed_through_and_saves(self):
        self.db.prepare_details.return_value = None
        self.db.create_report.return_value = {"id": 9, "reference": "TAL-2026-00009", "duplicate": False}
        result = api.submit_report(self.create(confirm_duplicate=True), current_user=TECH)
        self.assertEqual(result["reference"], "TAL-2026-00009")
        self.assertTrue(self.db.create_report.call_args.args[5])

    def test_extra_field_problems_are_a_400_with_the_reason(self):
        self.db.prepare_details.side_effect = self.db.ActivityInvalid("Fault encountered is required")
        with self.assertRaises(HTTPException) as caught:
            api.submit_report(self.create(), current_user=TECH)
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (400, "Fault encountered is required"))
        self.db.create_report.assert_not_called()

    def test_manage_endpoints_are_admin_only(self):
        with self.assertRaises(HTTPException) as caught:
            api.require_admin(MANAGER)
        self.assertEqual(caught.exception.status_code, 403)
        routes = {r.path: r for r in api.router.routes}
        for path in ("/activity/admin/catalog", "/activity/admin/categories", "/activity/admin/types/{type_id}/fields",
                     "/activity/admin/categories/order"):
            deps = [d.call for d in routes[path].dependant.dependencies]
            self.assertIn(api.require_admin, deps, path)

    def test_manage_change_returns_the_fresh_catalog_and_maps_errors(self):
        self.catalog.get_catalog.return_value = ["fresh"]
        self.catalog.add_category.return_value = 8
        self.assertEqual(api.add_category(api.NameBody(name="Wireless"), current_user=ADMIN), {"catalog": ["fresh"]})
        self.catalog.add_type.side_effect = self.db.ActivityInvalid("already has a type with that name")
        with self.assertRaises(HTTPException) as caught:
            api.add_type(api.NewType(category_id=1, name="Splicing"), current_user=ADMIN)
        self.assertEqual(caught.exception.status_code, 400)
        self.catalog.update_type.return_value = False
        with self.assertRaises(HTTPException) as caught:
            api.change_type(99, api.TypeChanges(name="x"), current_user=ADMIN)
        self.assertEqual(caught.exception.status_code, 404)

    def test_customer_search_needs_the_submit_permission(self):
        routes = {r.path: r for r in api.router.routes}
        self.assertEqual(len(routes["/activity/customers"].dependant.dependencies), 1)
        self.db.search_customers.return_value = [{"id": 1, "name": "Jane", "username": "j", "plan": "10M"}]
        self.assertEqual(api.search_customers(q="jan", current_user=TECH)[0]["name"], "Jane")


class FollowUps(unittest.TestCase):
    def target(self, **kw):
        base = {"id": 5, "outcome": "partially_completed", "activity_date": date(2026, 10, 23), "is_deleted": False,
                "follows_up_report_id": None, "followed_up_by": None}
        base.update(kw)
        return base

    def test_a_later_visit_can_finish_an_unfinished_job(self):
        self.assertIsNone(logic.check_follow_up(self.target(), None, date(2026, 10, 26)))
        self.assertIsNone(logic.check_follow_up(self.target(outcome="unable_to_resolve"), None, date(2026, 10, 26)))
        self.assertIsNone(logic.check_follow_up(self.target(), None, date(2026, 10, 23)))   # same day is fine

    def test_what_cannot_be_followed_up(self):
        cases = (
            (None, None, "wasn't found"),
            (self.target(is_deleted=True), None, "wasn't found"),
            (self.target(outcome="completed"), None, "Only a partially completed"),
            (self.target(activity_date=date(2026, 10, 30)), None, "same day or before"),
            (self.target(followed_up_by=8), None, "already been followed up"),
            (self.target(), 5, "can't follow up itself"),
            (self.target(follows_up_report_id=9), 9, "already follows up this one"),
        )
        for target, report_id, expected in cases:
            self.assertIn(expected, logic.check_follow_up(target, report_id, date(2026, 10, 26)) or "", expected)

    def test_editing_the_report_that_already_follows_it_up_is_allowed(self):
        self.assertIsNone(logic.check_follow_up(self.target(followed_up_by=9), 9, date(2026, 10, 26)))

    def test_overview_splits_unfinished_into_finished_later_and_still_open(self):
        mix = logic.outcome_mix({"completed": 47, "partially_completed": 12, "inspection_only": 1},
                                {"partially_completed": 3})
        partial = next(r for r in mix["outcomes"] if r["outcome"] == "partially_completed")
        self.assertEqual((partial["followed_up"], partial["still_open"]), (3, 9))
        self.assertEqual(mix["still_open"], 9)
        self.assertNotIn("still_open", next(r for r in mix["outcomes"] if r["outcome"] == "completed"))

    def test_still_open_is_a_filter_not_an_outcome(self):
        with mock.patch.object(api, "db") as fake, mock.patch.object(api, "user_has_permission", has_permissions("view", "view_all")):
            fake.list_reports.return_value = {"total": 0, "reports": []}
            api.list_reports(outcome="open", current_user=MANAGER)
            self.assertEqual(fake.list_reports.call_args.args[0]["outcome"], "open")
            with self.assertRaises(HTTPException):
                api.list_reports(outcome="nearly", current_user=MANAGER)

    def test_the_follow_up_link_is_passed_through_on_submit(self):
        with mock.patch.object(api, "db") as fake:
            fake.ActivityInvalid = type("ActivityInvalid", (Exception,), {})
            fake.prepare_details.return_value = None
            fake.create_report.return_value = {"id": 9, "reference": "TAL-2026-00061", "duplicate": False}
            api.submit_report(api.ReportCreate(activity_date=date.today(), type_id=7, location_id=3, follows_up_report_id=52), current_user=TECH)
            self.assertEqual(fake.create_report.call_args.kwargs["follows_up_report_id"], 52)


if __name__ == "__main__":
    unittest.main()
