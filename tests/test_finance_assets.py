"""Finance > Assets rules (db/finance_assets_logic.py). Pure: Inventory items
and Finance rows in, figures out."""
import unittest
from datetime import date
from decimal import Decimal

from db import finance_assets_logic as logic

TODAY = date(2026, 11, 15)


def item(cost=36000, status="Active", location="Store", category_id=1, item_id=1):
    return {"id": item_id, "name": "OLT", "serial_number": f"SN{item_id}", "make_model": None, "category_id": category_id,
            "category_name": "Network", "asset_status": status, "location_name": location, "supplier": None,
            "unit_cost": cost}


LIFE = {"life_months": 36, "salvage_pct": Decimal("0")}


def asset(value=None, life=LIFE, **kwargs):
    return logic.describe(item(**kwargs), value, life, TODAY)


class Depreciation(unittest.TestCase):
    def test_straight_line_from_the_purchase_month(self):
        a = asset({"purchased_on": date(2026, 1, 20)})
        self.assertEqual(a["monthly"], Decimal("1000.00"))
        # Jan to Nov 2026 = 11 months used
        self.assertEqual(a["book_value"], Decimal("25000.00"))
        self.assertEqual(a["depreciated"], Decimal("11000.00"))

    def test_category_salvage_is_kept_back(self):
        a = asset({"purchased_on": date(2026, 11, 1)}, life={"life_months": 60, "salvage_pct": Decimal("20")}, cost=165000)
        self.assertEqual(a["terms"]["salvage"], Decimal("33000.00"))
        self.assertEqual(a["monthly"], Decimal("2200.00"))

    def test_the_item_overrides_its_category(self):
        a = asset({"purchased_on": date(2026, 11, 1), "life_months": 12, "salvage": Decimal("0")})
        self.assertEqual(a["monthly"], Decimal("3000.00"))
        self.assertTrue(a["life_override"])

    def test_fully_used_stays_at_salvage(self):
        a = asset({"purchased_on": date(2020, 1, 1)}, life={"life_months": 36, "salvage_pct": Decimal("10")})
        self.assertEqual(a["book_value"], Decimal("3600.00"))
        self.assertEqual(logic.depreciation_in_month(a["terms"], date(2026, 11, 1)), Decimal("0"))

    def test_the_last_month_takes_the_rounding(self):
        t = logic.terms(item(cost=1000), {"purchased_on": date(2026, 1, 1)}, {"life_months": 3, "salvage_pct": 0})
        months = [logic.depreciation_in_month(t, date(2026, m, 1)) for m in (1, 2, 3)]
        self.assertEqual(months, [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")])
        self.assertEqual(sum(months), Decimal("1000.00"))

    def test_nothing_before_the_purchase_month(self):
        t = logic.terms(item(), {"purchased_on": date(2026, 11, 3)}, LIFE)
        self.assertEqual(logic.depreciation_in_month(t, date(2026, 10, 1)), Decimal("0"))


class NothingInvented(unittest.TestCase):
    def test_no_purchase_date_means_no_book_value(self):
        a = asset(None)
        self.assertIsNone(a["book_value"])
        self.assertEqual(a["terms"]["missing"], ["purchase date"])

    def test_no_life_says_so(self):
        a = asset({"purchased_on": date(2026, 1, 1)}, life=None)
        self.assertIsNone(a["monthly"])
        self.assertIn("useful life", a["terms"]["missing"])

    def test_month_total_counts_only_complete_items(self):
        assets = [asset({"purchased_on": date(2026, 1, 1)}, item_id=1), asset(None, item_id=2),
                  asset({"purchased_on": date(2026, 1, 1)}, status="Decommissioned", item_id=3)]
        result = logic.month_depreciation(assets, date(2026, 11, 1))
        self.assertEqual(result, {"total": Decimal("1000.00"), "counted": 1, "left_out": 1})


class SummaryAndAlerts(unittest.TestCase):
    def test_summary(self):
        assets = [asset({"purchased_on": date(2026, 1, 1)}, item_id=1),
                  asset(None, status="Faulty", item_id=2),
                  asset(None, status="Spare — In Storage", item_id=3),
                  asset(None, status="Decommissioned", item_id=4)]
        s = logic.summary(assets, TODAY)
        self.assertEqual((s["count"], s["retired"], s["deployed"], s["in_stock"], s["needs_repair"]), (3, 1, 1, 1, 1))
        self.assertEqual((s["total_cost"], s["book_value"], s["valued"]), (Decimal(108000), Decimal("25000.00"), 1))

    def test_warranty_ending_within_60_days(self):
        soon = asset({"warranty_until": date(2026, 12, 20)})
        later = asset({"warranty_until": date(2027, 6, 1)})
        self.assertTrue(soon["warranty_ending"])
        self.assertFalse(later["warranty_ending"])
        kinds = [a["kind"] for a in logic.alerts([soon])]
        self.assertIn("warranty", kinds)

    def test_alerts_name_what_is_missing(self):
        kinds = [a["kind"] for a in logic.alerts([asset(None, location=None), asset(None, status="Faulty", item_id=2)])]
        self.assertEqual(kinds, ["no_date", "repair", "no_location"])


if __name__ == "__main__":
    unittest.main()
