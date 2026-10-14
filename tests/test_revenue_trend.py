"""Trend, band, split and summary rules in db/revenue_logic.py. Run from the
repo root:  venv/bin/python -m unittest tests.test_revenue_trend -v
"""
import unittest
from datetime import datetime
from decimal import Decimal

from db.revenue_logic import (build_summary, compare, comparison_sentence, headline_sentence,
                              mad_band, rolling_mean, slope_pct_of_mean, split_range, threshold_sentence,
                              trend_label)


def at(*a):
    return datetime(*a)


class Split(unittest.TestCase):
    def test_whole_days_only(self):
        self.assertEqual(split_range(at(2026, 10, 1), at(2026, 10, 14)),
                         ((at(2026, 10, 1).date(), at(2026, 10, 14).date()), None))

    def test_partial_day_splits_into_days_and_hours(self):
        days, hours = split_range(at(2026, 10, 1), at(2026, 10, 14, 19))
        self.assertEqual(days, (at(2026, 10, 1).date(), at(2026, 10, 14).date()))
        self.assertEqual(hours, (at(2026, 10, 14), at(2026, 10, 14, 19)))

    def test_partial_first_day_is_all_hourly(self):
        self.assertEqual(split_range(at(2026, 10, 14), at(2026, 10, 14, 19)),
                         (None, (at(2026, 10, 14), at(2026, 10, 14, 19))))

    def test_empty_range(self):
        self.assertEqual(split_range(at(2026, 10, 14), at(2026, 10, 14)), (None, None))


class Rolling(unittest.TestCase):
    def test_none_until_window_full(self):
        out = rolling_mean([1, 2, 3, 4, 5, 6, 7, 8], 7)
        self.assertEqual(out[:6], [None] * 6)
        self.assertEqual(out[6], Decimal(4))
        self.assertEqual(out[7], Decimal(5))


class Band(unittest.TestCase):
    def test_median_plus_minus_k_mad(self):
        history = [100, 110, 90, 105, 95, 100, 120, 80, 100, 100, 110, 90, 100, 100]
        low, high, med = mad_band(history, 3)
        # median 100; deviations -> MAD 5; band 100 ± 15.
        self.assertEqual((low, high, med), (Decimal(85), Decimal(115), Decimal(100)))

    def test_too_little_history_gives_no_band(self):
        self.assertIsNone(mad_band([100] * 13, 3))

    def test_identical_days_give_no_band(self):
        self.assertIsNone(mad_band([100] * 20, 3))


class Slope(unittest.TestCase):
    def test_rising_line(self):
        # 100,110,120 -> slope 10 per period on a mean of 110 = 9.09%.
        self.assertEqual(slope_pct_of_mean([100, 110, 120]).quantize(Decimal("0.01")), Decimal("9.09"))

    def test_flat_and_falling(self):
        self.assertEqual(slope_pct_of_mean([100, 100, 100, 100]), Decimal(0))
        self.assertLess(slope_pct_of_mean([300, 200, 100]), 0)

    def test_needs_three_points_and_nonzero_mean(self):
        self.assertIsNone(slope_pct_of_mean([1, 2]))
        self.assertIsNone(slope_pct_of_mean([0, 0, 0]))

    def test_labels_and_rule(self):
        self.assertEqual(trend_label(Decimal("2.0"))[0], "Increasing")   # at the band edge
        self.assertEqual(trend_label(Decimal("-2.0"))[0], "Decreasing")
        self.assertEqual(trend_label(Decimal("1.9"))[0], "Stable")
        label, rule = trend_label(Decimal("-3.456"))
        self.assertEqual(label, "Decreasing")
        self.assertIn("-3.5%", rule)
        self.assertEqual(trend_label(None)[0], "Not enough data")


def card(period, comparison, current, previous):
    c = compare(current, previous)
    c.update({"period_label": period, "comparison_label": comparison})
    return c


class Summary(unittest.TestCase):
    def test_headline_down(self):
        c = card("Tue 13/10/2026", "Tue 06/10/2026", 10810, 14030)
        self.assertEqual(headline_sentence(c),
                         "Tue 13/10/2026 revenue was KSh 10,810, down KSh 3,220 (23.0%) vs Tue 06/10/2026.")

    def test_headline_up_unchanged_new_none(self):
        self.assertIn("up KSh 500 (5.0%)", headline_sentence(card("A", "B", 10500, 10000)))
        self.assertIn("unchanged vs B", headline_sentence(card("A", "B", 100, 100)))
        self.assertIn("nothing was recorded", headline_sentence(card("A", "B", 100, 0)))
        self.assertIn("no activity", headline_sentence(card("A", "B", 0, 0)))
        none = card("A", "no comparable data", 100, None)
        self.assertIn("no comparable data", headline_sentence(none))

    def test_threshold_needs_both_pct_and_floor(self):
        big = card("A", "B", 8000, 10000)       # -20%, -2,000
        small = card("A", "B", 300, 400)        # -25%, -100
        self.assertEqual(threshold_sentence(big, 20, 500), "This exceeds the 20% alert threshold.")
        self.assertEqual(threshold_sentence(small, 20, 500), "This is within the 20% alert threshold.")
        self.assertIsNone(threshold_sentence(card("A", "B", 11000, 10000), 20, 500))  # rises: no sentence

    def test_comparison_sentence(self):
        self.assertEqual(comparison_sentence(card("This month", "last month", 9000, 10000)),
                         "This month is 10.0% below last month.")
        self.assertEqual(comparison_sentence(card("This month", "last month", 11000, 10000)),
                         "This month is 10.0% above last month.")
        self.assertIsNone(comparison_sentence(card("A", "B", 5, 0)))
        self.assertIsNone(comparison_sentence(card("A", "no comparable data", 5, None)))

    def test_no_causal_language(self):
        lines = build_summary(card("A", "B", 8000, 10000), [card("C", "D", 9000, 10000)], 20, 500)
        text = " ".join(lines).lower()
        for word in ("because", "due to", "likely", "caused"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
