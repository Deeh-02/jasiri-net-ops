"""Fixed inputs -> exact outputs for db/revenue_logic.py. Run from the repo
root:  venv/bin/python -m unittest tests.test_revenue_logic -v
"""
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from db.revenue_logic import (EAT, aggregate_comparison, compare, eat_day_of_utc, format_kes,
                              period_bounds, resolve)


def at(*args):
    return datetime(*args)  # naive EAT wall-clock, as resolve() accepts


class PartialPeriods(unittest.TestCase):
    def test_partial_day_at_1900_compares_with_yesterday_to_1900(self):
        r = resolve("day", date(2026, 10, 14), at(2026, 10, 14, 19, 25), mode="previous_day")
        self.assertTrue(r.is_partial)
        self.assertEqual(r.current_range, (at(2026, 10, 14, 0), at(2026, 10, 14, 19)))
        self.assertEqual(r.comparison_ranges, [(at(2026, 10, 13, 0), at(2026, 10, 13, 19))])
        self.assertTrue(r.equal_length)

    def test_default_day_mode_is_same_weekday_last_week(self):
        r = resolve("day", date(2026, 10, 14), at(2026, 10, 14, 19, 0))
        self.assertEqual(r.mode, "same_weekday_last_week")
        self.assertEqual(r.comparison_ranges, [(at(2026, 10, 7, 0), at(2026, 10, 7, 19))])

    def test_mtd_oct_1_to_13_compares_with_sep_1_to_13(self):
        # 00:30 on the 14th: Oct 1-13 are the completed days.
        r = resolve("month", date(2026, 10, 14), at(2026, 10, 14, 0, 30))
        self.assertEqual(r.current_range, (at(2026, 10, 1), at(2026, 10, 14)))
        self.assertEqual(r.comparison_ranges, [(at(2026, 9, 1), at(2026, 9, 14))])
        self.assertTrue(r.equal_length)

    def test_mtd_includes_todays_hours_on_both_sides(self):
        r = resolve("month", date(2026, 10, 14), at(2026, 10, 14, 19, 5))
        self.assertEqual(r.current_range[1], at(2026, 10, 14, 19))
        self.assertEqual(r.comparison_ranges[0][1], at(2026, 9, 14, 19))

    def test_week_mon_to_wed_compares_with_previous_mon_to_wed(self):
        # Wed 14/10/2026 at 00:00 -> Mon & Tue complete.
        r = resolve("week", date(2026, 10, 14), at(2026, 10, 14, 0, 0))
        self.assertEqual(r.current_range, (at(2026, 10, 12), at(2026, 10, 14)))
        self.assertEqual(r.comparison_ranges, [(at(2026, 10, 5), at(2026, 10, 7))])

    def test_ytd_compares_with_previous_year_same_point(self):
        r = resolve("year", date(2026, 10, 14), at(2026, 10, 14, 0, 0))
        self.assertEqual(r.current_range, (at(2026, 1, 1), at(2026, 10, 14)))
        self.assertEqual(r.comparison_ranges, [(at(2025, 1, 1), at(2025, 10, 14))])

    def test_partial_month_against_shorter_month_is_cut_and_labelled(self):
        # Mar 31 10:00 vs Feb: Feb has no 29th-31st.
        r = resolve("month", date(2026, 3, 31), at(2026, 3, 31, 10, 0), mode="previous_month")
        self.assertEqual(r.comparison_ranges, [(at(2026, 2, 1), at(2026, 3, 1))])
        self.assertFalse(r.equal_length)
        self.assertTrue(any("shorter" in n for n in r.notes))


class CompletedPeriods(unittest.TestCase):
    NOW = at(2026, 10, 14, 12, 0)

    def test_completed_march_vs_february_is_whole_february_and_labelled(self):
        r = resolve("month", date(2026, 3, 15), self.NOW, mode="previous_month")
        self.assertFalse(r.is_partial)
        self.assertEqual(r.current_range, (at(2026, 3, 1), at(2026, 4, 1)))
        self.assertEqual(r.comparison_ranges, [(at(2026, 2, 1), at(2026, 3, 1))])
        self.assertFalse(r.equal_length)
        self.assertTrue(any("Unequal lengths: 31 days compared with 28 days" in n for n in r.notes))

    def test_completed_day_is_full_day_against_full_day(self):
        r = resolve("day", date(2026, 10, 13), self.NOW)
        self.assertFalse(r.is_partial)
        self.assertEqual(r.current_range, (at(2026, 10, 13), at(2026, 10, 14)))
        self.assertEqual(r.comparison_ranges, [(at(2026, 10, 6), at(2026, 10, 7))])
        self.assertEqual(r.current_label, "Tue 13/10/2026")

    def test_baseline_median_uses_last_n_same_weekdays(self):
        r = resolve("day", date(2026, 10, 13), self.NOW, mode="baseline_median", baseline_weeks=3)
        self.assertEqual(r.aggregate, "median")
        self.assertEqual([c[0] for c in r.comparison_ranges],
                         [at(2026, 10, 6), at(2026, 9, 29), at(2026, 9, 22)])

    def test_same_iso_week_last_year(self):
        r = resolve("week", date(2026, 10, 14), self.NOW, mode="same_week_last_year")
        # ISO week 42 of 2026 -> week 42 of 2025 starts Mon 13/10/2025.
        self.assertEqual(r.comparison_ranges[0][0], at(2025, 10, 13))

    def test_iso_week_53_falls_back_to_52_with_note(self):
        # 2026 has ISO week 53 (Mon 28/12/2026); 2025 does not.
        r = resolve("week", date(2026, 12, 29), at(2027, 1, 10, 0, 0), mode="same_week_last_year")
        self.assertEqual(r.comparison_ranges[0][0], at(2025, 12, 22))
        self.assertTrue(any("week 52" in n for n in r.notes))

    def test_custom_comparison_and_self_comparison_rejected(self):
        r = resolve("month", date(2026, 9, 10), self.NOW, mode="custom", custom_anchor=date(2026, 7, 4))
        self.assertEqual(r.comparison_ranges, [(at(2026, 7, 1), at(2026, 8, 1))])
        with self.assertRaises(ValueError):
            resolve("month", date(2026, 9, 10), self.NOW, mode="custom", custom_anchor=date(2026, 9, 30))


class Guards(unittest.TestCase):
    def test_future_period_rejected(self):
        with self.assertRaises(ValueError):
            resolve("day", date(2026, 10, 15), at(2026, 10, 14, 12))

    def test_mode_must_fit_granularity(self):
        with self.assertRaises(ValueError):
            resolve("year", date(2026, 1, 1), at(2026, 10, 14), mode="previous_week")

    def test_aware_now_is_converted_to_eat(self):
        # 21:30 UTC on the 13th is 00:30 EAT on the 14th.
        now = datetime(2026, 10, 13, 21, 30, tzinfo=timezone.utc)
        r = resolve("day", date(2026, 10, 14), now, mode="previous_day")
        self.assertTrue(r.is_partial)
        self.assertEqual(r.current_range, (at(2026, 10, 14), at(2026, 10, 14, 0)))

    def test_same_inputs_same_output(self):
        a = resolve("week", date(2026, 10, 6), at(2026, 10, 14, 9), mode="previous_week", history_start=date(2026, 9, 22))
        b = resolve("week", date(2026, 10, 6), at(2026, 10, 14, 9), mode="previous_week", history_start=date(2026, 9, 22))
        self.assertEqual(a, b)


class HistoryCoverage(unittest.TestCase):
    NOW = at(2026, 10, 14, 12, 0)

    def test_mtd_against_a_month_that_ends_before_history_is_none(self):
        # The live situation: history starts 21 Sep, so Sep 1-14 has no data.
        r = resolve("month", date(2026, 10, 14), self.NOW, history_start=date(2026, 9, 21))
        self.assertEqual(r.coverage, "none")
        self.assertIsNone(r.comparison_ranges)

    def test_comparison_before_history_is_none(self):
        r = resolve("year", date(2026, 10, 14), self.NOW, history_start=date(2026, 9, 21))
        self.assertEqual(r.coverage, "none")
        self.assertIsNone(r.comparison_ranges)
        self.assertEqual(r.comparison_label, "no comparable data")

    def test_comparison_straddling_history_start_is_partial_not_zero(self):
        # MTD at Oct 14 12:00 compares with Sep 1-14 12:00; history starts Sep 10.
        r = resolve("month", date(2026, 10, 14), self.NOW, history_start=date(2026, 9, 10))
        self.assertEqual(r.coverage, "partial")
        self.assertIsNotNone(r.comparison_ranges)
        self.assertTrue(any("History only starts" in n for n in r.notes))

    def test_fully_covered(self):
        r = resolve("day", date(2026, 10, 14), self.NOW, history_start=date(2026, 9, 21))
        self.assertEqual(r.coverage, "full")


class Compare(unittest.TestCase):
    def test_zero_previous_positive_current_is_new(self):
        c = compare(500, 0)
        self.assertIsNone(c["pct_change"])
        self.assertEqual(c["label"], "New")
        self.assertEqual(c["abs_change"], Decimal(500))
        self.assertEqual(c["direction"], "↑")

    def test_both_zero_is_no_activity(self):
        c = compare(0, 0)
        self.assertIsNone(c["pct_change"])
        self.assertEqual(c["label"], "No activity")
        self.assertEqual(c["direction"], "→")

    def test_current_zero_previous_positive_is_a_full_decline_not_new(self):
        c = compare(0, 500)
        self.assertEqual(c["pct_change"], Decimal("-100.0"))
        self.assertIsNone(c["label"])
        self.assertEqual(c["direction"], "↓")

    def test_percentage_rounds_to_one_decimal(self):
        c = compare(10810, 14030)
        self.assertEqual(c["abs_change"], Decimal(-3220))
        self.assertEqual(c["pct_change"], Decimal("-23.0"))

    def test_flat_band_is_inclusive(self):
        self.assertEqual(compare(102, 100)["direction"], "→")
        self.assertEqual(compare(103, 100)["direction"], "↑")
        self.assertEqual(compare(97, 100)["direction"], "↓")
        self.assertEqual(compare(98, 100)["direction"], "→")

    def test_no_comparable_data_is_not_zero(self):
        c = compare(1000, None)
        self.assertEqual(c["label"], "No comparable data")
        self.assertIsNone(c["abs_change"])
        self.assertIsNone(c["pct_change"])
        self.assertIsNone(c["direction"])

    def test_aggregate_median_and_sum(self):
        self.assertEqual(aggregate_comparison([100, 300, 200], "median"), Decimal(200))
        self.assertEqual(aggregate_comparison([100, 300, 200], "sum"), Decimal(600))
        self.assertIsNone(aggregate_comparison([], "sum"))


class DayBoundaryAndMoney(unittest.TestCase):
    def test_2330_utc_lands_on_next_eat_day(self):
        self.assertEqual(eat_day_of_utc(datetime(2026, 10, 12, 23, 30)), date(2026, 10, 13))
        self.assertEqual(eat_day_of_utc(datetime(2026, 10, 12, 20, 59)), date(2026, 10, 12))
        self.assertEqual(eat_day_of_utc(datetime(2026, 10, 12, 21, 0)), date(2026, 10, 13))

    def test_format_kes(self):
        self.assertEqual(format_kes(10810), "KSh 10,810")
        self.assertEqual(format_kes(-3220), "-KSh 3,220")
        self.assertEqual(format_kes(0), "KSh 0")
        self.assertEqual(format_kes(Decimal("1234.5")), "KSh 1,234.50")

    def test_week_runs_monday_to_sunday(self):
        self.assertEqual(period_bounds("week", date(2026, 10, 18)), (at(2026, 10, 12), at(2026, 10, 19)))


if __name__ == "__main__":
    unittest.main()
