"""Finance > Assets rules (db/finance_assets_logic.py). Pure: Inventory items
and Finance rows in, figures out. No book value or depreciation (owner's
call, 2026-11-16)."""
import unittest
from datetime import date
from decimal import Decimal

from db import finance_assets_logic as logic

TODAY = date(2026, 11, 15)


def item(cost=36000, status="Active", location="Store", category_id=1, item_id=1):
    return {"id": item_id, "name": "OLT", "serial_number": f"SN{item_id}", "make_model": None, "category_id": category_id,
            "category_name": "Network", "asset_status": status, "location_name": location, "supplier": None,
            "unit_cost": cost}


def asset(value=None, **kwargs):
    return logic.describe(item(**kwargs), value, TODAY)


class Age(unittest.TestCase):
    def test_whole_months_since_it_was_bought(self):
        self.assertEqual(logic.age_months(date(2024, 11, 15), TODAY), 24)
        self.assertEqual(logic.age_months(date(2024, 11, 16), TODAY), 23)     # a day short of two years
        self.assertEqual(logic.age_months(date(2026, 11, 1), TODAY), 0)

    def test_no_purchase_date_means_no_age_and_no_warning(self):
        a = asset(None)
        self.assertIsNone(a["age_months"])
        self.assertEqual(logic.alerts([a]), [])
        self.assertEqual(a["cost"], Decimal(36000))


class SummaryAndAlerts(unittest.TestCase):
    def test_summary(self):
        assets = [asset({"purchased_on": date(2026, 1, 1)}, item_id=1),
                  asset(None, status="Faulty", item_id=2),
                  asset(None, status="Spare — In Storage", item_id=3),
                  asset(None, status="Decommissioned", item_id=4)]
        s = logic.summary(assets)
        self.assertEqual((s["count"], s["retired"], s["deployed"], s["in_stock"], s["needs_repair"]), (3, 1, 1, 1, 1))
        self.assertEqual(s["total_cost"], Decimal(108000))              # the decommissioned one isn't counted

    def test_warranty_ending_within_60_days(self):
        soon = asset({"warranty_until": date(2026, 12, 20)})
        later = asset({"warranty_until": date(2027, 6, 1)})
        self.assertTrue(soon["warranty_ending"])
        self.assertFalse(later["warranty_ending"])
        self.assertIn("warranty", [a["kind"] for a in logic.alerts([soon])])

    def test_alerts_worst_first_where_it_matters(self):
        kinds = [a["kind"] for a in logic.alerts([asset(None, location=None), asset(None, status="Faulty", item_id=2)])]
        self.assertEqual(kinds, ["repair", "no_location"])


if __name__ == "__main__":
    unittest.main()
