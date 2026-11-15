"""Revenue page → Income overview: the figures come from the server
(db/revenue_report.income_overview); the page only draws them."""
import unittest

from db import revenue_report
from db.revenue_logic import format_kes


def day(start, value, partial=False):
    return {"start": start, "label": start, "value": value, "display": format_kes(value), "partial": partial}


class IncomeOverview(unittest.TestCase):
    def test_total_average_highest_lowest(self):
        pts = [day("2026-11-02", 8960), day("2026-11-03", 8531), day("2026-11-04", 9980),
               day("2026-11-08", 7970), day("2026-11-10", 10590)]
        o = revenue_report.income_overview(pts, "day")
        self.assertEqual(o["total"], "KSh 46,031")
        self.assertEqual(o["average"], "KSh 9,206")          # 9,206.2 to the shilling
        self.assertEqual(o["highest"], {"name": "Tue 10 Nov", "display": "KSh 10,590"})
        self.assertEqual(o["lowest"], {"name": "Sun 08 Nov", "display": "KSh 7,970"})
        self.assertEqual(o["periods"], 5)
        self.assertFalse(o["partial_left_out"])
        self.assertEqual([p["vs_average_pct"] for p in pts], [-3, -7, 8, -13, 15])

    def test_today_so_far_is_shown_but_not_counted(self):
        pts = [day("2026-11-09", 9000), day("2026-11-10", 11000), day("2026-11-11", 1200, partial=True)]
        o = revenue_report.income_overview(pts, "day")
        self.assertEqual(o["total"], "KSh 20,000")
        self.assertEqual(o["lowest"]["name"], "Mon 09 Nov")
        self.assertTrue(o["partial_left_out"])
        self.assertIsNone(pts[2]["vs_average_pct"])
        self.assertEqual(pts[2]["name"], "Wed 11 Nov")

    def test_ties_name_the_most_recent(self):
        pts = [day("2026-11-01", 500), day("2026-11-02", 500)]
        o = revenue_report.income_overview(pts, "day")
        self.assertEqual(o["highest"]["name"], "Mon 02 Nov")
        self.assertEqual(o["lowest"]["name"], "Mon 02 Nov")

    def test_names_for_weeks_and_months(self):
        w = [day("2026-11-02", 1)]
        revenue_report.income_overview(w, "week")
        self.assertEqual(w[0]["name"], "Week of 02 Nov")
        m = [day("2026-10-01", 1)]
        revenue_report.income_overview(m, "month")
        self.assertEqual(m[0]["name"], "October 2026")

    def test_nothing_completed(self):
        pts = [day("2026-11-11", 300, partial=True)]
        self.assertIsNone(revenue_report.income_overview(pts, "day"))
        self.assertEqual(revenue_report.income_overview([], "day"), None)

    def test_all_zero_has_no_percentages(self):
        pts = [day("2026-11-01", 0), day("2026-11-02", 0)]
        o = revenue_report.income_overview(pts, "day")
        self.assertEqual(o["average"], "KSh 0")
        self.assertEqual([p["vs_average_pct"] for p in pts], [None, None])

    def test_fourteen_day_series_exists(self):
        self.assertEqual(revenue_report.TREND_SERIES["daily_14"], ("day", 14))


if __name__ == "__main__":
    unittest.main()
