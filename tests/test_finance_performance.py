"""Finance > Performance rules (db/finance_performance_logic.py). Pure."""
import unittest
from datetime import date
from decimal import Decimal

from db import finance_performance_logic as logic

CATEGORIES = [
    {"id": 1, "name": "Upstream bandwidth", "group": "direct_cost", "is_active": True},
    {"id": 2, "name": "Last-mile / fibre rental", "group": "direct_cost", "is_active": True},
    {"id": 3, "name": "Salaries", "group": "operating", "is_active": True},
    {"id": 4, "name": "Transport & fuel", "group": "operating", "is_active": True},
    {"id": 5, "name": "M-Pesa & bank charges", "group": "finance_tax", "is_active": True},
    {"id": 6, "name": "Other", "group": "other", "is_active": True},
]
GROUP = {c["name"]: c["group"] for c in CATEGORIES}


def expense(category, amount, kind="operating", segment=None, day="2026-11-05"):
    return {"category": category, "group": GROUP[category], "amount": str(amount), "kind": kind,
            "segment": segment, "spent_on": day}


INCOME = {"pppoe": None, "hotspot": Decimal(100000), "other": None}
MONTH = [expense("Upstream bandwidth", 20000, segment="shared"), expense("Last-mile / fibre rental", 5000, segment="pppoe"),
         expense("Salaries", 30000), expense("Transport & fuel", 4000), expense("M-Pesa & bank charges", 1000),
         expense("Other", 500), expense("Salaries", 80000, kind="capital")]


class Statement(unittest.TestCase):
    def test_the_whole_business_down_to_net_profit(self):
        s = logic.month_statement(INCOME, MONTH, CATEGORIES)
        self.assertEqual(s["direct_total"], Decimal(25000))
        self.assertEqual((s["gross_profit"], s["gross_margin"]), (Decimal(75000), Decimal("75.0")))
        self.assertEqual(s["operating_total"], Decimal(34500))          # Other counts as operating
        self.assertEqual(s["operating_profit"], Decimal(40500))
        self.assertEqual((s["net_profit"], s["net_margin"]), (Decimal(39500), Decimal("39.5")))

    def test_capital_purchases_stay_out_of_profit(self):
        s = logic.month_statement(INCOME, MONTH, CATEGORIES)
        self.assertEqual(s["expenses_total"], Decimal(60500))           # not the 80,000 bought to keep
        self.assertEqual(logic.notes(MONTH, CATEGORIES)["capital"]["total"], Decimal(80000))

    def test_equipment_comes_off_after_profit(self):
        s = logic.month_statement(INCOME, MONTH, CATEGORIES)
        self.assertEqual((s["net_profit"], s["equipment_total"], s["cash_left"]), (Decimal(39500), Decimal(80000), Decimal(-40500)))
        none_bought = logic.month_statement(INCOME, MONTH[:-1], CATEGORIES)
        self.assertEqual((none_bought["equipment_total"], none_bought["cash_left"]), (Decimal(0), Decimal(39500)))
        nothing = logic.month_statement(INCOME, [], CATEGORIES)
        self.assertIsNone(nothing["cash_left"])                         # no expenses recorded: no figure, not 0

    def test_missing_income_is_marked_incomplete_not_zero(self):
        s = logic.month_statement(INCOME, MONTH, CATEGORIES)
        self.assertIsNone(s["lines"]["income.pppoe"])
        self.assertTrue(s["income_incomplete"])
        self.assertEqual(s["income_missing"], ["pppoe"])

    def test_other_income_without_a_source_does_not_flag_the_totals(self):
        s = logic.month_statement({"pppoe": Decimal(30000), "hotspot": Decimal(100000), "other": None}, MONTH, CATEGORIES)
        self.assertEqual(s["income_total"], Decimal(130000))
        self.assertFalse(s["income_incomplete"])
        self.assertIsNone(s["lines"]["income.other"])                  # still no data, not 0

    def test_a_segment_view_with_its_income_recorded(self):
        s = logic.month_statement({"pppoe": Decimal(30000), "hotspot": None, "other": None}, MONTH, CATEGORIES, segment="pppoe")
        self.assertEqual(s["before_shared_costs"], Decimal(25000))     # 30,000 less the 5,000 tagged PPPoE
        self.assertFalse(s["income_incomplete"])

    def test_a_month_with_no_expenses_has_no_profit_figure(self):
        s = logic.month_statement(INCOME, [], CATEGORIES)
        self.assertEqual(s["expenses_recorded"], 0)
        self.assertIsNone(s["net_profit"])
        self.assertIsNone(s["lines"]["cost.3"])

    def test_a_segment_ends_before_shared_costs(self):
        s = logic.month_statement(INCOME, MONTH, CATEGORIES, segment="pppoe")
        self.assertEqual(s["direct_total"], Decimal(5000))              # only the cost tagged PPPoE
        self.assertNotIn("net_profit", s)
        self.assertIsNone(s["before_shared_costs"])                     # PPPoE income isn't recorded
        hotspot = logic.month_statement(INCOME, MONTH, CATEGORIES, segment="hotspot")
        self.assertEqual(hotspot["before_shared_costs"], Decimal(100000))


class Rows(unittest.TestCase):
    def test_income_totals_are_not_compared_across_different_coverage(self):
        # Last month had PPPoE only (hotspot records began part-way): no change, not +1,700%.
        this = logic.month_statement({"pppoe": Decimal(35000), "hotspot": Decimal(353400), "other": None}, MONTH, CATEGORIES)
        last = logic.month_statement({"pppoe": Decimal(21500), "hotspot": None, "other": None}, MONTH, CATEGORIES)
        self.assertFalse(logic.income_comparable(this, last))
        rows = logic.statement_rows(this, last, CATEGORIES, {})
        by_label = {r["label"]: r for r in rows if "label" in r}
        self.assertIsNone(by_label["Total income"]["change"])
        self.assertIsNone(by_label["Net profit"]["change"])
        self.assertIsNotNone(by_label["Total operating expenses"]["change"])   # costs don't rest on income
        pppoe = next(r for r in rows if r.get("key") == "income.pppoe")
        self.assertEqual(pppoe["change"]["amount"], Decimal(13500))            # PPPoE itself is known both months

    def rows(self, is_draft=False):
        this = logic.month_statement(INCOME, MONTH, CATEGORIES)
        last = logic.month_statement({"pppoe": None, "hotspot": Decimal(80000), "other": None}, MONTH[:3], CATEGORIES)
        return logic.statement_rows(this, last, CATEGORIES, {"Salaries": [MONTH[2]]}, is_draft=is_draft)

    def test_change_against_last_month(self):
        hotspot = next(r for r in self.rows() if r.get("key") == "income.hotspot")
        self.assertEqual(hotspot["change"], {"amount": Decimal(20000), "pct": Decimal("25.0")})

    def test_no_change_column_while_the_month_is_running(self):
        self.assertTrue(all(r.get("change") is None for r in self.rows(is_draft=True)))

    def test_expense_lines_carry_their_expenses(self):
        salaries = next(r for r in self.rows() if r.get("label") == "Salaries")
        self.assertEqual(len(salaries["items"]), 1)

    def test_the_statement_ends_at_cash_left_after_equipment(self):
        rows = self.rows()
        self.assertEqual([r["label"] for r in rows[-4:]], ["Net profit", "After equipment", "Equipment bought", "Cash left"])
        self.assertEqual(rows[-1]["this"], Decimal(39500) - Decimal(80000))
        self.assertFalse(any(r.get("key") == "depreciation" for r in rows))


class BreakEven(unittest.TestCase):
    def test_the_day_income_passes_costs(self):
        daily = {date(2026, 11, d): Decimal(1000) for d in range(1, 16)}
        self.assertEqual(logic.break_even(date(2026, 11, 1), daily, Decimal(4500), date(2026, 11, 15)), {"day": date(2026, 11, 5)})

    def test_not_yet_says_how_much_is_left(self):
        daily = {date(2026, 11, d): Decimal(1000) for d in range(1, 4)}
        self.assertEqual(logic.break_even(date(2026, 11, 1), daily, Decimal(4500), date(2026, 11, 3)), {"to_go": Decimal(1500)})

    def test_no_costs_recorded_means_no_answer(self):
        self.assertIsNone(logic.break_even(date(2026, 11, 1), {}, None, date(2026, 11, 3)))


class TrendAndNotes(unittest.TestCase):
    def test_profit_only_where_both_sides_are_known(self):
        points = logic.monthly_trend(
            date(2026, 11, 1), 3,
            {date(2026, 9, 1): Decimal(50), date(2026, 10, 1): Decimal(300), date(2026, 11, 1): Decimal(100)},
            {date(2026, 10, 1): {"operating": Decimal(40), "capital": Decimal(0)}},
            date(2026, 9, 21), date(2026, 11, 15))
        sept, octo, nov = points
        self.assertIsNone(sept["income"])                       # records began part-way through
        self.assertEqual(octo["profit"], Decimal(260))
        self.assertIsNone(nov["expenses"])                      # nothing recorded yet
        self.assertTrue(nov["partial"])

    def test_where_the_money_went_names_the_biggest_changes(self):
        last = [expense("Salaries", 30000), expense("Transport & fuel", 1000)]
        where = logic.where_money_went(MONTH, last)
        self.assertEqual(where["categories"][0]["category"], "Salaries")
        self.assertEqual(where["biggest_changes"][0]["category"], "Upstream bandwidth")

    def test_missing_direct_costs_are_pointed_out(self):
        notes = logic.notes([expense("Salaries", 100)], CATEGORIES)
        self.assertEqual(notes["no_direct_costs"], ["Upstream bandwidth", "Last-mile / fibre rental"])
        self.assertEqual(logic.notes([], CATEGORIES)["no_direct_costs"], [])
        self.assertEqual(logic.notes(MONTH, CATEGORIES)["uncategorised"], {"count": 1, "total": Decimal(500)})


if __name__ == "__main__":
    unittest.main()
