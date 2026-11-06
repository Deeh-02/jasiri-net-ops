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
            "remarks": None, "submitted_by_name": "Tech", "submitted_at_dt": NOW}]
        response = api.export_reports(current_user=MANAGER)
        text = response.body.decode()
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("2026-11-06 12:00", text)  # 09:00 UTC is 12:00 in Nairobi
        self.assertIn("text/csv", response.media_type)


if __name__ == "__main__":
    unittest.main()
