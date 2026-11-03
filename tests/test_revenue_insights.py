"""The AI analysis panel's rules: figures are placeholders, never typed by the
model; the model is called rarely; only admins get in. No database, no network
(the model and the report are replaced by fakes). Run from the repo root:
  venv/bin/python -m unittest tests.test_revenue_insights -v
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi import HTTPException

from db import deepseek, revenue_insights
from db.revenue_insights_logic import (InsightsInvalid, build_figures, fill_in, figures_hash, parse_model_output,
                                       placeholder_values, sites_from_ids)
from routers.permissions import require_admin


def display(current, previous, change, pct):
    return {"current": current, "previous": previous, "abs_change": change, "pct_change": pct}


def card(key, title, current, previous, change, pct, partial=False):
    return {"key": key, "title": title, "period_label": f"{title} period", "comparison_label": "last time",
            "is_partial": partial, "direction": "↑", "display": display(current, previous, change, pct)}


def site(site_id, name, abs_change, current="KSh 900", label=None):
    return {"site_id": site_id, "name": name, "abs_change": abs_change, "label": label,
            "display": display(current, "KSh 1,000", f"KSh {abs_change}", "-10.0%")}


def report(today_value="KSh 4,200"):
    overview = {"attention_count": 2, "cards": [
        card("today", "Today (so far)", today_value, "KSh 3,900", "KSh 300", "+7.7%", partial=True),
        card("yesterday", "Yesterday", "KSh 10,810", "KSh 14,030", "-KSh 3,220", "-23.0%"),
        card("wtd", "This week", "KSh 50,000", "KSh 48,000", "KSh 2,000", "+4.2%", partial=True),
        card("mtd", "This month", "KSh 120,000", "KSh 110,000", "KSh 10,000", "+9.1%", partial=True),
        card("ytd", "This year", "KSh 900,000", "KSh 800,000", "KSh 100,000", "+12.5%", partial=True),
    ]}
    sites = {
        "period": {"granularity": "day", "period_label": "Mon 02/11/2026", "comparison_label": "Mon 26/10/2026",
                   "is_partial": False},
        "network": {"direction": "↓", "display": display("KSh 10,810", "KSh 14,030", "-KSh 3,220", "-23.0%")},
        "sites": [site(1, "Garage", -2000), site(2, "Stage", 500), site(3, "Maji", -800), site(4, "Ndambaki", 50)],
    }
    alerts = {"day_label": "Mon 02/11/2026", "sites": [{"site_id": 3, "status": "red"}]}
    return build_figures(overview, sites, alerts), sites["sites"]


class Placeholders(unittest.TestCase):
    def test_every_figure_equals_the_matching_card(self):
        figures, _ = report()
        values = placeholder_values(figures)
        template = {"headline": "{yesterday.value} vs {yesterday.previous}, {yesterday.delta} ({yesterday.delta_pct})",
                    "highlights": ["{today.value}", "{month.delta_pct}", "{year.value}", "{week.delta}"],
                    "watch": ["{period.value}", "{attention.count}"]}
        out = fill_in(template, values)
        self.assertEqual(out["headline"], "KSh 10,810 vs KSh 14,030, -KSh 3,220 (-23.0%)")
        self.assertEqual(out["highlights"], ["KSh 4,200", "+9.1%", "KSh 900,000", "KSh 2,000"])
        self.assertEqual(out["watch"], ["KSh 10,810", "2"])

    def test_site_names_come_from_the_report_not_the_model(self):
        figures, _ = report()
        values = placeholder_values(figures)
        names = {values[k] for k in values if k.endswith(".name")}
        self.assertEqual(names, {"Garage", "Stage", "Maji", "Ndambaki"})

    def test_flagged_sites_are_listed_first(self):
        figures, _ = report()
        self.assertEqual(figures["sites"][0]["name"], "Maji")
        self.assertEqual(figures["sites"][0]["status_last_day"], "red")

    def test_a_missing_comparison_offers_no_placeholder(self):
        figures, _ = report()
        figures["cards"]["today"].pop("previous")
        self.assertNotIn("today.previous", placeholder_values(figures))

    def test_letters_keep_pointing_at_the_same_site(self):
        figures, rows = report()
        ids = [s["site_id"] for s in figures["sites"]]
        rows.reverse()  # the order of "biggest movers" changes
        again = sites_from_ids(rows, ids)
        self.assertEqual([s["site_id"] for s in again], ids)
        self.assertEqual(again[0]["letter"], "site_a")


class Validation(unittest.TestCase):
    ALLOWED = {"today.value", "month.delta_pct", "site_a.name"}

    def parse(self, **data):
        base = {"headline": "Sales are steady.", "highlights": [], "watch": []}
        return parse_model_output(json.dumps({**base, **data}), self.ALLOWED)

    def test_good_output_is_accepted(self):
        out = self.parse(headline="Today is {today.value}.", highlights=["{site_a.name} is up {month.delta_pct}."])
        self.assertEqual(out["highlights"], ["{site_a.name} is up {month.delta_pct}."])

    def test_a_digit_is_rejected(self):
        for text in ("Sales hit 5000 today.", "Up 12% on last week.", "KSh 500 lost", "Week 2 looks slow."):
            with self.assertRaises(InsightsInvalid, msg=text):
                self.parse(headline=text)

    def test_digits_hidden_in_any_field_are_rejected(self):
        with self.assertRaises(InsightsInvalid):
            self.parse(highlights=["Fine.", "Down by 3 sites."])
        with self.assertRaises(InsightsInvalid):
            self.parse(watch=["Watch the 7 day trend."])

    def test_unknown_placeholder_is_rejected(self):
        for text in ("{total.value} earned", "{today.profit} earned", "{site_1.name} fell", "{today.value"):
            with self.assertRaises(InsightsInvalid, msg=text):
                self.parse(headline=text)

    def test_too_many_items_and_bad_shapes_are_rejected(self):
        with self.assertRaises(InsightsInvalid):
            self.parse(highlights=["a", "b", "c", "d"])
        with self.assertRaises(InsightsInvalid):
            self.parse(watch=["a", "b", "c"])
        with self.assertRaises(InsightsInvalid):
            self.parse(headline="")
        for raw in ("not json", "[]", '{"headline": 5}'):
            with self.assertRaises(InsightsInvalid, msg=raw):
                parse_model_output(raw, self.ALLOWED)


class Generation(unittest.TestCase):
    def setUp(self):
        self.figures, _ = report()

    def test_one_retry_then_gives_up(self):
        bad = json.dumps({"headline": "Sales hit 5000.", "highlights": [], "watch": []})
        with mock.patch.object(deepseek, "chat_json", return_value=bad) as call:
            self.assertIsNone(revenue_insights.generate_template(self.figures))
        self.assertEqual(call.call_count, 2)

    def test_second_attempt_can_succeed(self):
        bad = json.dumps({"headline": "Sales hit 5000.", "highlights": [], "watch": []})
        good = json.dumps({"headline": "Yesterday was {yesterday.value}.", "highlights": [], "watch": []})
        with mock.patch.object(deepseek, "chat_json", side_effect=[bad, good]):
            template = revenue_insights.generate_template(self.figures)
        self.assertEqual(template["headline"], "Yesterday was {yesterday.value}.")

    def test_an_unreachable_service_is_not_retried(self):
        with mock.patch.object(deepseek, "chat_json", side_effect=deepseek.DeepSeekError("timeout")) as call:
            self.assertIsNone(revenue_insights.generate_template(self.figures))
        self.assertEqual(call.call_count, 1)

    def test_the_model_never_sees_the_key_or_customer_data(self):
        text = json.dumps(deepseek_messages(self.figures))
        self.assertNotIn("sk-", text)
        self.assertNotIn("phone", text.lower())


def deepseek_messages(figures):
    from db.revenue_insights_logic import build_messages
    return build_messages(figures)


T0 = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
GOOD_TEMPLATE = {"headline": "Today is {today.value}.", "highlights": ["{site_a.name} moved."], "watch": []}


class Caching(unittest.TestCase):
    def setUp(self):
        revenue_insights._cache.clear()
        revenue_insights._generating.clear()
        self.env = mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
        self.env.start()
        self.calls = 0

    def tearDown(self):
        self.env.stop()

    def generate(self, figures):
        self.calls += 1
        return dict(GOOD_TEMPLATE)

    def ask(self, seconds, today_value="KSh 4,200"):
        figures, rows = report(today_value)
        return revenue_insights.get_insights(
            "day", gather_figures=lambda *a: (figures, rows), generate=self.generate, now=T0 + timedelta(seconds=seconds))

    def test_ten_admins_for_ten_minutes_cause_one_model_call(self):
        for step, seconds in enumerate(range(0, 600, 30)):   # polling every 30 s
            for _admin in range(10):
                result = self.ask(seconds, today_value=f"KSh {4200 + step * 100}")  # figures change every poll
                self.assertEqual(result["status"], "ok")
        self.assertEqual(self.calls, 1)

    def test_unchanged_figures_never_trigger_a_second_call(self):
        self.ask(0)
        self.ask(3 * 3600)
        self.assertEqual(self.calls, 1)

    def test_changed_figures_regenerate_after_the_interval(self):
        self.ask(0)
        self.ask(600, today_value="KSh 9,999")
        self.assertEqual(self.calls, 2)

    def test_numbers_follow_the_cards_even_when_the_wording_is_kept(self):
        self.ask(0)
        result = self.ask(60, today_value="KSh 4,700")
        self.assertEqual(self.calls, 1)
        self.assertEqual(result["insights"]["headline"], "Today is KSh 4,700.")

    def test_different_views_have_separate_entries(self):
        figures, rows = report()
        for period in ("day", "week"):
            revenue_insights.get_insights(period, gather_figures=lambda *a: (figures, rows),
                                          generate=self.generate, now=T0)
        self.assertEqual(self.calls, 2)

    def test_failure_shows_unavailable_and_backs_off(self):
        failing = mock.Mock(return_value=None)
        figures, rows = report()
        ask = lambda s: revenue_insights.get_insights(
            "day", gather_figures=lambda *a: (figures, rows), generate=failing, now=T0 + timedelta(seconds=s))
        self.assertEqual(ask(0)["status"], "unavailable")
        ask(30)
        self.assertEqual(failing.call_count, 1)
        ask(130)
        self.assertEqual(failing.call_count, 2)

    def test_old_wording_survives_a_failed_refresh(self):
        self.ask(0)
        figures, rows = report("KSh 9,999")
        result = revenue_insights.get_insights(
            "day", gather_figures=lambda *a: (figures, rows), generate=lambda f: None,
            now=T0 + timedelta(seconds=700))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["insights"]["headline"], "Today is KSh 9,999.")

    def test_no_key_means_unavailable_and_no_call(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            self.assertEqual(self.ask(0)["status"], "unavailable")
        self.assertEqual(self.calls, 0)

    def test_a_second_request_waits_for_the_one_already_generating(self):
        figures, rows = report()
        key = revenue_insights._view_key("day", None, None, {})
        revenue_insights._generating.add(key)
        result = revenue_insights.get_insights("day", gather_figures=lambda *a: (figures, rows),
                                               generate=self.generate, now=T0)
        self.assertEqual(result["status"], "preparing")
        self.assertEqual(self.calls, 0)

    def test_hash_ignores_nothing_that_changes_a_number(self):
        a, _ = report("KSh 4,200")
        b, _ = report("KSh 4,300")
        self.assertNotEqual(figures_hash(a), figures_hash(b))
        self.assertEqual(figures_hash(a), figures_hash(report("KSh 4,200")[0]))


class AdminOnly(unittest.TestCase):
    def test_admin_passes(self):
        user = {"id": 1, "role": "admin", "role_id": None}
        self.assertIs(require_admin(user), user)

    def test_technician_and_custom_roles_get_403(self):
        for user in ({"id": 2, "role": "technician", "role_id": None},
                     {"id": 3, "role": "Accountant", "role_id": 7},
                     {"id": 4, "role": "", "role_id": None}):
            with self.assertRaises(HTTPException) as caught:
                require_admin(user)
            self.assertEqual(caught.exception.status_code, 403)

    def test_the_insights_route_is_guarded_by_it(self):
        from routers import monitoring
        route = next(r for r in monitoring.router.routes if r.path == "/monitoring/revenue-report/insights")
        guards = [d.call for d in route.dependant.dependencies]
        self.assertIn(require_admin, guards)


if __name__ == "__main__":
    unittest.main()
