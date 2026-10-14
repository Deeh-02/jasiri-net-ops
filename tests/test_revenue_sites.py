"""Level 2 rules: contribution to change, rankings, per-site series metrics.
Run from the repo root:  venv/bin/python -m unittest tests.test_revenue_sites -v
"""
import unittest
from datetime import date, timedelta
from decimal import Decimal

from db.revenue_logic import (compare, consecutive_vs_baseline, contributions, rankings,
                              trailing_min_max, weekday_baseline)


def row(site_id, name, current, previous):
    c = compare(current, previous)
    return {"site_id": site_id, "name": name, "current": Decimal(current), "previous": Decimal(previous),
            "abs_change": c["abs_change"], "pct_change": c["pct_change"]}


class Contribution(unittest.TestCase):
    ROWS = [
        row(1, "Alpha", 4000, 7000),    # -3,000
        row(2, "Bravo", 5000, 7000),    # -2,000
        row(3, "Charlie", 8000, 9000),  # -1,000
        row(4, "Delta", 6000, 4000),    # +2,000
        row(5, "Echo", 3780, 3000),     # +780
        row(6, "Foxtrot", 5000, 5000),  # unchanged
    ]

    def test_net_is_gross_gain_plus_gross_decline(self):
        c = contributions(self.ROWS)
        self.assertEqual(c["gross_decline"], Decimal(-6000))
        self.assertEqual(c["gross_gain"], Decimal(2780))
        self.assertEqual(c["net_change"], Decimal(-3220))

    def test_shares_sum_to_100_within_each_side(self):
        c = contributions(self.ROWS, cutoff_pct=100)
        self.assertEqual(sum(s["share"] for s in c["decline"]["contributors"]), Decimal("100.0"))
        self.assertEqual(sum(s["share"] for s in c["gain"]["contributors"]), Decimal("100.0"))

    def test_fewest_sites_reaching_the_cutoff(self):
        c = contributions(self.ROWS, cutoff_pct=70)
        # Alpha 50%, +Bravo 83.3% -> two sites cover >= 70%; Charlie is "other".
        self.assertEqual([s["name"] for s in c["decline"]["contributors"]], ["Alpha", "Bravo"])
        self.assertEqual(c["decline"]["other"]["count"], 1)
        self.assertEqual(c["decline"]["other"]["abs_change"], Decimal(-1000))
        shares = [s["share"] for s in c["decline"]["contributors"]] + [c["decline"]["other"]["share"]]
        self.assertEqual(sum(shares), Decimal("100.0"))
        # Delta alone is 71.9% of the gain.
        self.assertEqual([s["name"] for s in c["gain"]["contributors"]], ["Delta"])

    def test_sites_without_a_comparison_are_left_out(self):
        rows = self.ROWS + [{"site_id": 9, "name": "Zulu", "current": Decimal(100), "previous": None,
                             "abs_change": None, "pct_change": None}]
        self.assertEqual(contributions(rows)["net_change"], Decimal(-3220))

    def test_thirds_still_add_up_to_exactly_100(self):
        rows = [row(i, f"S{i}", 0, 1000) for i in range(1, 4)]
        c = contributions(rows, cutoff_pct=100)
        self.assertEqual(sum(s["share"] for s in c["decline"]["contributors"]), Decimal("100.0"))

    def test_no_movement_on_a_side_is_empty_not_an_error(self):
        c = contributions([row(1, "A", 100, 100)])
        self.assertEqual(c["net_change"], Decimal(0))
        self.assertEqual(c["decline"]["contributors"], [])


class Rankings(unittest.TestCase):
    ROWS = [
        row(1, "Big", 9000, 12000),     # -3,000  -25.0%
        row(2, "Mid", 4000, 5000),      # -1,000  -20.0%
        row(3, "Tiny", 300, 900),       # -600    -66.7%  previous < 1,000
        row(4, "Grower", 3000, 2000),   # +1,000  +50.0%
        row(5, "Small", 700, 400),      # +300    +75.0%  previous < 1,000
        row(6, "Flat", 2000, 2000),
    ]

    def test_site_below_the_floor_is_excluded_from_pct_rankings_only(self):
        r = rankings(self.ROWS, pct_rank_min_revenue=1000)
        self.assertNotIn("Tiny", [x["name"] for x in r["largest_pct_declines"]])
        self.assertNotIn("Small", [x["name"] for x in r["fastest_pct_growth"]])
        self.assertIn("Tiny", [x["name"] for x in r["largest_losses"]])
        self.assertIn("Small", [x["name"] for x in r["largest_gains"]])

    def test_orders(self):
        r = rankings(self.ROWS)
        self.assertEqual([x["name"] for x in r["top_revenue"]][:2], ["Big", "Mid"])
        self.assertEqual([x["name"] for x in r["largest_losses"]], ["Big", "Mid", "Tiny"])
        self.assertEqual([x["name"] for x in r["largest_pct_declines"]], ["Big", "Mid"])
        self.assertEqual([x["name"] for x in r["largest_gains"]], ["Grower", "Small"])
        self.assertEqual([x["name"] for x in r["fastest_pct_growth"]], ["Grower"])

    def test_losses_and_pct_declines_are_different_lists(self):
        r = rankings(self.ROWS, pct_rank_min_revenue=0)
        self.assertEqual(r["largest_losses"][0]["name"], "Big")          # most KSh
        self.assertEqual(r["largest_pct_declines"][0]["name"], "Tiny")  # most %

    def test_top_n_and_deterministic_ties(self):
        rows = [row(i, f"S{i}", 100, 100) for i in range(10)]
        top = rankings(rows, top_n=5)["top_revenue"]
        self.assertEqual([x["name"] for x in top], ["S0", "S1", "S2", "S3", "S4"])


def series_of(start, values):
    return {start + timedelta(days=i): Decimal(v) for i, v in enumerate(values)}


class SeriesRules(unittest.TestCase):
    HIST = date(2026, 9, 1)

    def weekly_pattern(self, weeks, base=1000):
        return [base] * (7 * weeks)

    def test_weekday_baseline_is_median_of_same_weekday(self):
        s = series_of(self.HIST, [100, 0, 0, 0, 0, 0, 0,
                                  300, 0, 0, 0, 0, 0, 0,
                                  200, 0, 0, 0, 0, 0, 0,
                                  999])
        day = self.HIST + timedelta(days=21)  # a Monday, like HIST+0
        self.assertEqual(weekday_baseline(s, day, 6, self.HIST), Decimal(200))

    def test_baseline_needs_three_prior_weeks(self):
        s = series_of(self.HIST, [100] * 15)
        self.assertIsNone(weekday_baseline(s, self.HIST + timedelta(days=14), 6, self.HIST))

    def test_baseline_ignores_weeks_before_history(self):
        s = series_of(self.HIST, [100] * 28)
        day = self.HIST + timedelta(days=27)
        # Only 3 prior same weekdays exist on/after HIST.
        self.assertEqual(weekday_baseline(s, day, 6, self.HIST), Decimal(100))

    def test_consecutive_declines_counts_back_from_last_day(self):
        values = [1000] * 28 + [900, 800, 700]          # three days under their baselines
        s = series_of(self.HIST, values)
        last = self.HIST + timedelta(days=len(values) - 1)
        self.assertEqual(consecutive_vs_baseline(s, last, 6, self.HIST, below=True), 3)
        self.assertEqual(consecutive_vs_baseline(s, last, 6, self.HIST, below=False), 0)

    def test_streak_breaks_on_a_normal_day(self):
        values = [1000] * 28 + [900, 1000, 800]
        s = series_of(self.HIST, values)
        last = self.HIST + timedelta(days=len(values) - 1)
        self.assertEqual(consecutive_vs_baseline(s, last, 6, self.HIST), 1)

    def test_too_little_history_is_not_a_decline(self):
        s = series_of(self.HIST, [1000, 500, 100])
        self.assertEqual(consecutive_vs_baseline(s, self.HIST + timedelta(days=2), 6, self.HIST), 0)

    def test_trailing_min_max_with_dates(self):
        s = series_of(self.HIST, [500, 700, 300, 900, 600, 800, 400, 1000])
        end = self.HIST + timedelta(days=7)
        r = trailing_min_max(s, end, 7, self.HIST)
        self.assertEqual((r["min"], r["max"]), (Decimal(300), Decimal(1000)))
        self.assertEqual(r["min_date"], self.HIST + timedelta(days=2))
        self.assertIsNone(trailing_min_max({}, end, 7, self.HIST))


if __name__ == "__main__":
    unittest.main()
