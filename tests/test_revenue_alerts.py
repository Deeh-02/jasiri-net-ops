"""Alert rules (§6) over fixed daily series. Run from the repo root:
  venv/bin/python -m unittest tests.test_revenue_alerts -v
"""
import unittest
from datetime import date, timedelta
from decimal import Decimal

from db.revenue_config import DEFAULTS
from db.revenue_logic import GREEN, RED, AMBER, active_site, evaluate_day

HIST = date(2026, 8, 3)  # a Monday
CFG = dict(DEFAULTS)


def series_from(values, start=HIST):
    return {start + timedelta(days=i): Decimal(v) for i, v in enumerate(values)}


def steady(base, days=40, **overrides):
    """`days` days at `base`, with {day_index: value} overrides."""
    values = [base] * days
    for i, v in overrides.items():
        values[int(i.lstrip("d"))] = v
    return series_from(values)


def ids(result):
    return [a["rule_id"] for a in result["alerts"]]


class RedPct(unittest.TestCase):
    def test_400_to_300_does_not_fire_red(self):
        # -25% but only -KSh 100, under red_min_abs (500).
        s = steady(400, d35=300)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertNotEqual(r["status"], RED)
        self.assertNotIn("red_pct_drop", ids(r))

    def test_10000_to_8000_fires_red(self):
        s = steady(10000, d35=8000)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertEqual(r["status"], RED)
        a = next(a for a in r["alerts"] if a["rule_id"] == "red_pct_drop")
        self.assertEqual(a["values"]["pct_change"], Decimal("-20.0"))
        self.assertEqual(a["values"]["abs_change"], Decimal(-2000))
        self.assertIn("-20.0% (-KSh 2,000) vs Mon 31/08", a["text"])

    def test_19_pct_with_big_amount_is_not_red(self):
        s = steady(10000, d35=8100)  # -19.0%, -1,900
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertNotIn("red_pct_drop", ids(r))
        self.assertEqual(r["status"], RED)  # but below the 7-day low and baseline by >= 500


class OtherRed(unittest.TestCase):
    def test_silent_active_site(self):
        s = steady(1000, d35=0)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertEqual(r["status"], RED)
        self.assertIn("red_silent", ids(r))

    def test_zero_day_on_an_inactive_site_is_not_silent(self):
        s = steady(100, d35=0)  # averages 100/day, under active_site_min_daily (300)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertFalse(r["active"])
        self.assertNotIn("red_silent", ids(r))

    def test_three_days_below_own_baseline(self):
        s = steady(1000, 38, d35=700, d36=700, d37=700)
        r = evaluate_day(s, HIST + timedelta(days=37), HIST, CFG)
        self.assertEqual(r["status"], RED)
        self.assertIn("red_declining", ids(r))
        a = next(a for a in r["alerts"] if a["rule_id"] == "red_declining")
        self.assertEqual(a["values"]["consecutive_days"], 3)

    def test_below_7d_low_needs_baseline_gap(self):
        # 150 under baseline: below the 7-day low but less than red_min_abs under.
        s = steady(1000, d35=850)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertNotIn("red_below_7d_low", ids(r))
        s = steady(1000, d35=450)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertIn("red_below_7d_low", ids(r))


class Amber(unittest.TestCase):
    def test_pct_between_amber_and_red_with_floor(self):
        s = steady(2000, d35=1700)  # -15.0%, -300: amber, not red
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertEqual(r["status"], AMBER)
        self.assertEqual(ids(r), ["amber_pct_drop"])

    def test_amber_pct_needs_its_floor(self):
        s = steady(1000, d35=880)  # -12.0% but only -KSh 120 (< 250)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertIsNone(r["status"])

    def test_two_days_declining_is_amber(self):
        s = steady(1000, 37, d35=700, d36=700)
        r = evaluate_day(s, HIST + timedelta(days=36), HIST, CFG)
        self.assertEqual(r["status"], AMBER)
        self.assertIn("amber_declining", ids(r))

    def test_seven_day_total_down(self):
        # Last 7 days each ~12% under the 7 before, but no single day is a big drop vs last week...
        values = [1000] * 28 + [900] * 7
        s = series_from(values)
        r = evaluate_day(s, HIST + timedelta(days=34), HIST, CFG)
        self.assertEqual(r["status"], AMBER)
        self.assertIn("amber_7d_vs_7d", ids(r))


class Green(unittest.TestCase):
    def test_pct_rise_with_floor(self):
        s = steady(2000, d35=2400)  # +20%, +400
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertEqual(r["status"], GREEN)
        self.assertIn("green_pct_rise", ids(r))

    def test_small_site_rise_under_floor_is_normal(self):
        s = steady(400, d35=500)  # +25% but +KSh 100 (< 300)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertIsNone(r["status"])

    def test_above_baseline_three_days(self):
        s = steady(1000, 38, d35=1400, d36=1400, d37=1400)
        r = evaluate_day(s, HIST + timedelta(days=37), HIST, CFG)
        self.assertEqual(r["status"], GREEN)
        self.assertIn("green_above_baseline", ids(r))

    def test_recovered_after_red(self):
        # Day 35 is a red (KSh 450 vs a usual 1,000); day 36 is back to normal.
        # (A deeper drop, e.g. zero, leaves the 7-day-vs-7-day amber rule
        # firing for a week, and amber outranks "recovered".)
        s = steady(1000, 37, d35=450)
        r = evaluate_day(s, HIST + timedelta(days=36), HIST, CFG)
        self.assertEqual(r["status"], GREEN)
        self.assertEqual(ids(r), ["green_recovered"])
        self.assertEqual(r["alerts"][0]["values"]["previous_status"], RED)

    def test_not_recovered_if_still_well_below_baseline(self):
        s = steady(1000, 37, d35=450, d36=300)
        r = evaluate_day(s, HIST + timedelta(days=36), HIST, CFG)
        self.assertNotEqual(r["status"], GREEN)

    def test_after_a_zero_day_the_week_rule_outranks_recovered(self):
        s = steady(1000, 37, d35=0)
        r = evaluate_day(s, HIST + timedelta(days=36), HIST, CFG)
        self.assertEqual(r["status"], AMBER)
        self.assertEqual(ids(r), ["amber_7d_vs_7d"])


class Precedence(unittest.TestCase):
    def test_red_hides_amber_and_green_rules(self):
        s = steady(10000, d35=7000)
        r = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        self.assertEqual(r["status"], RED)
        self.assertTrue(all(a["severity"] == RED for a in r["alerts"]))

    def test_normal_day_has_no_status(self):
        r = evaluate_day(steady(1000), HIST + timedelta(days=30), HIST, CFG)
        self.assertIsNone(r["status"])
        self.assertEqual(r["alerts"], [])

    def test_no_data_for_the_day(self):
        r = evaluate_day(steady(1000), HIST + timedelta(days=60), HIST, CFG)
        self.assertIsNone(r["status"])
        self.assertIsNone(r["value"])

    def test_same_series_same_result(self):
        s = steady(1000, d35=300)
        a = evaluate_day(s, HIST + timedelta(days=35), HIST, CFG)
        b = evaluate_day(dict(s), HIST + timedelta(days=35), HIST, CFG)
        self.assertEqual(a, b)

    def test_short_history_makes_no_judgement(self):
        r = evaluate_day(steady(1000, 5, d4=0), HIST + timedelta(days=4), HIST, CFG)
        # Silent can fire (it needs no baseline) but nothing built on a baseline does.
        self.assertNotIn("red_declining", ids(r))
        self.assertNotIn("red_below_7d_low", ids(r))


class Active(unittest.TestCase):
    def test_only_counts_days_the_site_has_rows_for(self):
        s = series_from([1000] * 5, start=HIST + timedelta(days=20))
        active, avg = active_site(s, HIST + timedelta(days=25), HIST, 300)
        self.assertTrue(active)
        self.assertEqual(avg, Decimal(1000))

    def test_no_rows_is_inactive(self):
        self.assertEqual(active_site({}, HIST + timedelta(days=5), HIST, 300), (False, Decimal(0)))


if __name__ == "__main__":
    unittest.main()
