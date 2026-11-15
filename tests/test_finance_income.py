"""Finance > Income rules (db/finance_income_logic.py). Pure: fixed days in,
fixed answers out. Records "begin" on 21 Sep 2026, like the real data."""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from db import finance_income_logic as logic

START = date(2026, 9, 21)


def daily_from(first, last, amount=100):
    day, out = first, {}
    while day <= last:
        out[day] = (Decimal(amount), 10)
        day += timedelta(days=1)
    return out


class MonthSummary(unittest.TestCase):
    def test_current_month_counts_today_in_the_total_but_not_the_average(self):
        daily = daily_from(date(2026, 11, 1), date(2026, 11, 14), 100)
        daily[date(2026, 11, 15)] = (Decimal(30), 3)          # today, half way through
        s = logic.month_summary(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual(s["total"], Decimal(1430))
        self.assertEqual(s["average_per_day"], Decimal(100))
        self.assertEqual((s["completed_days"], s["today"], s["is_current_month"]), (14, Decimal(30), True))

    def test_best_day_is_a_completed_day(self):
        daily = daily_from(date(2026, 11, 1), date(2026, 11, 14), 100)
        daily[date(2026, 11, 9)] = (Decimal(500), 40)
        daily[date(2026, 11, 15)] = (Decimal(900), 90)        # today: not "best" yet
        s = logic.month_summary(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual(s["best_day"], {"day": date(2026, 11, 9), "total": Decimal(500)})

    def test_a_day_with_no_sales_after_records_began_is_a_real_zero(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 10, 31), 100)
        del daily[date(2026, 10, 5)]
        s = logic.month_summary(date(2026, 10, 1), daily, date(2026, 11, 15), START)
        self.assertEqual((s["total"], s["days_with_data"]), (Decimal(3000), 31))

    def test_a_month_before_records_is_no_data_not_zero(self):
        s = logic.month_summary(date(2026, 8, 1), {}, date(2026, 11, 15), START)
        self.assertEqual(s, {"has_data": False})

    def test_the_month_records_began_in_says_so(self):
        daily = daily_from(START, date(2026, 9, 30))
        s = logic.month_summary(date(2026, 9, 1), daily, date(2026, 11, 15), START)
        self.assertEqual((s["records_start"], s["days_with_data"]), (START, 10))


class VsLastMonth(unittest.TestCase):
    def test_current_month_compares_completed_days_with_the_same_days(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 10, 31), 100)
        daily.update(daily_from(date(2026, 11, 1), date(2026, 11, 14), 90))
        daily[date(2026, 11, 15)] = (Decimal(5), 1)
        v = logic.vs_last_month(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual((v["this"], v["previous"], v["days"]), (Decimal(1260), Decimal(1400), 14))
        self.assertEqual(v["change_pct"], Decimal("-10.0"))

    def test_finished_months_compare_whole(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 30), 100)
        v = logic.vs_last_month(date(2026, 11, 1), daily, date(2026, 12, 5), START)
        self.assertEqual((v["this"], v["previous"], v["days"]), (Decimal(3000), Decimal(3100), None))

    def test_no_comparison_against_a_month_records_only_partly_cover(self):
        daily = daily_from(START, date(2026, 10, 31))
        self.assertIsNone(logic.vs_last_month(date(2026, 10, 1), daily, date(2026, 11, 15), START))

    def test_no_comparison_on_the_first_of_the_month(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 1))
        self.assertIsNone(logic.vs_last_month(date(2026, 11, 1), daily, date(2026, 11, 1), START))


class Trends(unittest.TestCase):
    def test_day_trend_has_every_day_with_last_month_beside_it(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 15), 100)
        points = logic.day_trend(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual(len(points), 30)
        self.assertEqual((points[0]["value"], points[0]["last_month"]), (Decimal(100), Decimal(100)))
        self.assertTrue(points[14]["partial"])
        self.assertIsNone(points[15]["value"])                # tomorrow: not yet

    def test_last_month_shorter_leaves_the_extra_days_empty(self):
        daily = daily_from(START, date(2026, 10, 31))
        points = logic.day_trend(date(2026, 10, 1), daily, date(2026, 11, 15), START)
        self.assertIsNone(points[30]["last_month"])           # no 31 September
        self.assertIsNone(points[0]["last_month"])            # 1 Sep: before records

    def test_weeks_before_records_are_left_out(self):
        daily = daily_from(START, date(2026, 11, 15))
        weeks = logic.week_trend(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual(len(weeks), 12)
        self.assertIsNone(weeks[0]["value"])
        known = [w for w in weeks if w["value"] is not None]
        self.assertEqual(known[0]["week_start"], START)        # 21 Sep 2026 was a Monday
        self.assertTrue(weeks[-1]["partial"])

    def test_months_before_records_are_left_out(self):
        daily = daily_from(START, date(2026, 11, 15))
        months = logic.month_trend(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual(months[-1], {"month": date(2026, 11, 1), "value": Decimal(1500), "partial": True})
        self.assertIsNone(next(m for m in months if m["month"] == date(2026, 9, 1))["value"])
        self.assertEqual(next(m for m in months if m["month"] == date(2026, 10, 1))["value"], Decimal(3100))


class Patterns(unittest.TestCase):
    def test_weekday_averages_say_how_many_days_they_rest_on(self):
        daily = daily_from(date(2026, 11, 1), date(2026, 11, 14), 100)
        daily[date(2026, 11, 2)] = (Decimal(300), 30)          # a Monday
        pattern = logic.weekday_pattern(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        monday = pattern[0]
        self.assertEqual((monday["weekday"], monday["average"], monday["days"]), ("Mon", Decimal(200), 2))

    def test_every_hour_is_present(self):
        hours = logic.hour_pattern([(9, Decimal(50), 5)])
        self.assertEqual(len(hours), 24)
        self.assertEqual((hours[9]["total"], hours[10]["total"]), (Decimal(50), Decimal(0)))

    def test_package_share(self):
        shares = logic.package_shares([("Quick Surf10", Decimal(600), 60)], Decimal(1000))
        self.assertEqual(shares[0]["share_pct"], Decimal("60.0"))


class SplitAndPrivacy(unittest.TestCase):
    def test_pppoe_and_other_are_no_data_not_zero(self):
        self.assertEqual(logic.segment_split(Decimal(500)), {"pppoe": None, "hotspot": Decimal(500), "other": None})

    def test_the_middle_of_the_phone_number_is_hidden(self):
        self.assertEqual(logic.mask_account("254700000001-F:D2"), "2547****0001-F:D2")
        self.assertEqual(logic.mask_account("guest-01"), "guest-01")

    def test_add_months_crosses_years(self):
        self.assertEqual(logic.add_months(date(2026, 1, 1), -1), date(2025, 12, 1))
        self.assertEqual(logic.add_months(date(2026, 11, 1), -12), date(2025, 11, 1))


class Target(unittest.TestCase):
    def test_projection_uses_the_same_weekdays(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 14), 100)
        for day in list(daily):
            if day.weekday() == 5:                              # Saturdays sell 300
                daily[day] = (Decimal(300), 30)
        p = logic.projection(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        # 1-14 Nov done, 2 Saturdays among them: 12x100 + 2x300 = 1,800. Today (a Sunday)
        # at its usual 100. 16-30 Nov left, 2 Saturdays: 13x100 + 2x300 = 1,900.
        self.assertEqual(p["projected"], Decimal(1800 + 100 + 1900))

    def test_a_strong_today_counts_as_it_is(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 14), 100)
        daily[date(2026, 11, 15)] = (Decimal(900), 90)
        p = logic.projection(date(2026, 11, 1), daily, date(2026, 11, 15), START)
        self.assertEqual(p["projected"], Decimal(1400 + 900 + 1500))

    def test_no_projection_without_enough_history_or_after_the_month(self):
        daily = daily_from(date(2026, 11, 1), date(2026, 11, 14))
        self.assertIsNone(logic.projection(date(2026, 11, 1), daily, date(2026, 11, 15), date(2026, 11, 1)))
        self.assertIsNone(logic.projection(date(2026, 10, 1), daily, date(2026, 11, 15), START))

    def test_break_even_averages_only_months_with_expenses(self):
        line = logic.break_even_line([(date(2026, 9, 1), Decimal(30000)), (date(2026, 10, 1), Decimal(50000))])
        self.assertEqual(line, {"amount": Decimal(40000), "months": 2})
        self.assertIsNone(logic.break_even_line([]))

    def test_target_is_the_owners_figure_then_break_even(self):
        be = {"amount": Decimal(40000), "months": 2}
        self.assertEqual(logic.month_target(Decimal(20000), be, None, None)["progress_pct"], Decimal(50))
        manual = logic.month_target(Decimal(20000), be, None, None, manual=Decimal(80000))
        self.assertEqual((manual["target_source"], manual["progress_pct"]), ("manual", Decimal(25)))
        self.assertIsNone(logic.month_target(Decimal(20000), None, None, None)["target"])


class Attention(unittest.TestCase):
    NOW = datetime(2026, 11, 15, 14, 0)

    def items(self, daily, last_sale=datetime(2026, 11, 15, 13, 50), unpriced=(), unlinked=(0, 0), now=None):
        return logic.attention(date(2026, 11, 1), daily, date(2026, 11, 15), START, now or self.NOW,
                               last_sale, list(unpriced), unlinked)

    def test_a_quiet_month_needs_nothing(self):
        self.assertEqual(self.items(daily_from(date(2026, 10, 1), date(2026, 11, 15))), [])

    def test_an_unpriced_package_comes_first(self):
        items = self.items(daily_from(date(2026, 10, 1), date(2026, 11, 15)), unpriced=[("Weekend pass", 12)])
        self.assertEqual(items[0]["level"], "bad")
        self.assertIn("Weekend pass", items[0]["title"])

    def test_hours_without_a_sale_in_the_day(self):
        items = self.items(daily_from(date(2026, 10, 1), date(2026, 11, 15)), last_sale=datetime(2026, 11, 15, 9, 30))
        self.assertIn("No hotspot sale for 4 hours", items[0]["title"])
        night = self.items(daily_from(date(2026, 10, 1), date(2026, 11, 15)), last_sale=datetime(2026, 11, 14, 23, 0),
                           now=datetime(2026, 11, 15, 3, 0))
        self.assertEqual(night, [])                             # nobody buys at 3am

    def test_a_day_without_sales(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 15))
        del daily[date(2026, 11, 10)]
        items = self.items(daily)
        self.assertEqual((items[0]["day"], items[0]["level"]), (date(2026, 11, 10), "bad"))

    def test_a_day_well_below_its_usual_weekday(self):
        daily = daily_from(date(2026, 10, 1), date(2026, 11, 15), 100)
        daily[date(2026, 11, 11)] = (Decimal(40), 4)            # a Wednesday at 40% of usual
        items = self.items(daily)
        self.assertEqual(items[0]["day"], date(2026, 11, 11))
        self.assertIn("60% below a usual Wednesday", items[0]["title"])

    def test_unlinked_sales_are_mentioned(self):
        items = self.items(daily_from(date(2026, 10, 1), date(2026, 11, 15)), unlinked=(3, Decimal(60)))
        self.assertEqual(items[-1]["level"], "info")


if __name__ == "__main__":
    unittest.main()
