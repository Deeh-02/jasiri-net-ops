"""Finance (Phase 7): who may see Finance and who may change expenses
(routers/finance.py). The database is faked."""
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from fastapi import HTTPException

from db import finance_expenses, finance_logic
from db.finance_messages import read_cash_lines, read_messages
from routers import finance as api

ADMIN = {"id": 1, "role": "admin"}
STAFF = {"id": 2, "role": "technician", "role_id": 1}


def perms(*granted):
    return mock.patch.object(api, "user_has_permission",
                             side_effect=lambda user, section, action: user["role"] == "admin" or (section, action) in granted)


class WhoMaySeeFinance(unittest.TestCase):
    def test_admin_always_may(self):
        with perms():
            self.assertIs(api.require_finance_view(ADMIN), ADMIN)
            self.assertIs(api.require_expense_manager(ADMIN), ADMIN)

    def test_staff_without_the_switch_are_refused(self):
        with perms(), self.assertRaises(HTTPException) as refused:
            api.require_finance_view(STAFF)
        self.assertEqual(refused.exception.status_code, 403)

    def test_seeing_finance_does_not_allow_changing_expenses(self):
        with perms(("finance", "view")):
            self.assertIs(api.require_finance_view(STAFF), STAFF)
            with self.assertRaises(HTTPException) as refused:
                api.require_expense_manager(STAFF)
        self.assertEqual(refused.exception.status_code, 403)

    def test_both_switches_allow_changing_expenses(self):
        with perms(("finance", "view"), ("finance", "manage_expenses")):
            self.assertIs(api.require_expense_manager(STAFF), STAFF)


class NotSetUpYet(unittest.TestCase):
    def test_missing_tables_say_which_migration_to_run(self):
        with mock.patch.object(api.finance_expenses, "list_categories",
                               side_effect=finance_expenses.FinanceNotSetUp()):
            with self.assertRaises(HTTPException) as refused:
                api.get_categories(current_user=ADMIN)
        self.assertEqual(refused.exception.status_code, 503)
        self.assertIn("0037", refused.exception.detail)


CATEGORIES = [
    {"id": 1, "name": "Transport & fuel", "group": "operating", "is_active": True},
    {"id": 2, "name": "Airtime & SMS", "group": "operating", "is_active": True},
    {"id": 3, "name": "M-Pesa & bank charges", "group": "finance_tax", "is_active": True},
    {"id": 4, "name": "Network equipment", "group": "operating", "is_active": True},
    {"id": 5, "name": "Old category", "group": "other", "is_active": False},
]
TODAY = date(2026, 11, 15)
FUEL = "QA1BC2DE4G Confirmed. Ksh500.00 paid to Sample Petroleum Limited.. on 14/10/26 at 1:40 PM."
SMS = "QA1BC2DE3F Confirmed. Ksh500.00 sent to SAMPLE BILLING for account smstopup on 2/11/26 at 6:14 PM"
SUBSCRIPTION = "QA1BC2DE5H Confirmed. Ksh1,500.00 sent to SAMPLE BILLING for account myisp on 2/11/26 at 6:07 PM"
WITH_FEE = ("QA1BC2DE8L Confirmed. Ksh1,000.00 sent to NEW SUPPLIER LTD for account 1 on 5/11/26 at 10:15 AM "
            "New M-PESA balance is Ksh2,345.67. Transaction cost, Ksh23.00.")


def review(messages="", cash="", rules=None, saved=()):
    return finance_logic.build_review(read_messages(messages, TODAY), read_cash_lines(cash),
                                      rules or {}, set(saved), CATEGORIES)


class Review(unittest.TestCase):
    def test_a_rule_makes_a_row_ready(self):
        rules = {"SAMPLE PETROLEUM LIMITED": {"category_id": 1, "kind": "operating"}}
        [item] = review(FUEL, rules=rules)
        self.assertEqual((item["status"], item["suggestion"]["source"], item["key"]), ("ready", "rule", "QA1BC2DE4G"))

    def test_an_unknown_payee_needs_a_category(self):
        [item] = review(FUEL)
        self.assertEqual(item["status"], "needs_category")

    def test_payee_and_account_rule_beats_the_payee_rule(self):
        rules = {"SAMPLE BILLING|SMSTOPUP": {"category_id": 2, "kind": "operating"},
                 "SAMPLE BILLING": {"category_id": 4, "kind": "operating"}}
        sms, subscription = review(f"{SMS}\n{SUBSCRIPTION}", rules=rules)
        self.assertEqual(sms["suggestion"]["category_id"], 2)
        self.assertEqual(subscription["suggestion"]["category_id"], 4)

    def test_an_already_saved_code_is_a_duplicate(self):
        [item] = review(FUEL, saved={"QA1BC2DE4G"})
        self.assertEqual(item["status"], "duplicate")

    def test_cash_lines_get_their_own_keys_and_keyword_category(self):
        fuel, lunch = review(cash="fuel 2000\nlunch 300")
        self.assertEqual((fuel["key"], fuel["status"], fuel["suggestion"]["category_id"]), ("cash-0", "ready", 1))
        self.assertEqual((lunch["key"], lunch["status"]), ("cash-1", "needs_category"))

    def test_a_rule_pointing_at_a_retired_category_is_ignored(self):
        rules = {"SAMPLE PETROLEUM LIMITED": {"category_id": 5, "kind": "operating"}}
        [item] = review(FUEL, rules=rules)
        self.assertEqual(item["status"], "needs_category")
        with self.assertRaises(finance_logic.ChoiceError):
            finance_logic.build_rows_to_save([item], {item["key"]: {"category_id": 5}}, CATEGORIES, TODAY)


class Saving(unittest.TestCase):
    def test_amounts_come_from_the_message_not_the_choice(self):
        items = review(FUEL)
        rows, _ = finance_logic.build_rows_to_save(items, {"QA1BC2DE4G": {"category_id": 1, "amount": "9999"}}, CATEGORIES, TODAY)
        self.assertEqual(rows[0]["amount"], Decimal("500.00"))
        self.assertEqual(rows[0]["spent_on"], date(2026, 10, 14))

    def test_the_fee_is_its_own_row(self):
        items = review(WITH_FEE)
        rows, _ = finance_logic.build_rows_to_save(items, {"QA1BC2DE8L": {"category_id": 4}}, CATEGORIES, TODAY)
        self.assertEqual([(r["reference"], r["amount"], r["category_id"]) for r in rows],
                         [("QA1BC2DE8L", Decimal("1000.00"), 4), ("QA1BC2DE8L-FEE", Decimal("23.00"), 3)])

    def test_skipped_and_unchosen_rows_are_not_saved(self):
        items = review(f"{FUEL}\n{SMS}")
        rows, _ = finance_logic.build_rows_to_save(items, {"QA1BC2DE4G": {"category_id": 1, "skip": True}}, CATEGORIES, TODAY)
        self.assertEqual(rows, [])

    def test_a_rule_is_only_written_when_asked_and_under_the_most_specific_key(self):
        items = review(SMS)
        _, rules = finance_logic.build_rows_to_save(items, {"QA1BC2DE3F": {"category_id": 2}}, CATEGORIES, TODAY)
        self.assertEqual(rules, {})
        _, rules = finance_logic.build_rows_to_save(items, {"QA1BC2DE3F": {"category_id": 2, "remember": True}}, CATEGORIES, TODAY)
        self.assertEqual(rules, {"SAMPLE BILLING|SMSTOPUP": {"category_id": 2, "kind": "operating"}})

    def test_cash_rows_use_the_cash_date(self):
        items = review(cash="fuel 2000")
        rows, rules = finance_logic.build_rows_to_save(items, {"cash-0": {"category_id": 1, "remember": True}}, CATEGORIES, date(2026, 11, 3))
        self.assertEqual((rows[0]["spent_on"], rows[0]["method"], rows[0]["reference"]), (date(2026, 11, 3), "cash", None))
        self.assertEqual(rules, {})

    def test_duplicates_are_never_saved_even_if_chosen(self):
        items = review(FUEL, saved={"QA1BC2DE4G"})
        rows, _ = finance_logic.build_rows_to_save(items, {"QA1BC2DE4G": {"category_id": 1}}, CATEGORIES, TODAY)
        self.assertEqual(rows, [])


BANK = ("Dear SAMPLE OWNER, Transaction Ref: 20090000001 of KES 30,000.00 from A/c 1*****0001 "
        "to A/c 0*****9001 on 10/10/2026 is Completed Bank Ref: BXXXXXXXXXX01.")


class WhoWasPaid(unittest.TestCase):
    def saved_payee(self, text, choice):
        items = review(text)
        key = items[0]["key"]
        rows, _ = finance_logic.build_rows_to_save(items, {key: dict({"category_id": 1}, **choice)}, CATEGORIES, TODAY)
        return rows[0]["payee"]

    def test_the_name_in_the_message_is_used(self):
        self.assertEqual(self.saved_payee(FUEL, {}), "Sample Petroleum Limited")

    def test_no_name_in_the_message_leaves_it_blank(self):
        self.assertIsNone(self.saved_payee(BANK, {}))

    def test_the_user_can_write_one(self):
        self.assertEqual(self.saved_payee(BANK, {"payee": "  Sample Fibre Co  "}), "Sample Fibre Co")

    def test_the_user_can_correct_the_name_from_the_message(self):
        self.assertEqual(self.saved_payee(FUEL, {"payee": "Sample Petroleum, Ngong Rd"}), "Sample Petroleum, Ngong Rd")

    def test_an_emptied_box_means_blank_not_the_message_name(self):
        self.assertIsNone(self.saved_payee(FUEL, {"payee": ""}))

    def test_a_bank_account_is_never_saved_as_the_payee(self):
        self.assertIsNone(self.saved_payee(BANK, {"payee": None}))

    def test_a_typed_name_is_not_remembered_as_part_of_the_rule(self):
        items = review(BANK)
        _, rules = finance_logic.build_rows_to_save(
            items, {"BXXXXXXXXXX01": {"category_id": 1, "payee": "Sample Fibre Co", "remember": True}}, CATEGORIES, TODAY)
        self.assertEqual(rules, {"BANK A/C 0*****9001": {"category_id": 1, "kind": "operating"}})


class Summary(unittest.TestCase):
    def test_voided_rows_are_left_out_of_totals(self):
        rows = [
            {"amount": "500.00", "kind": "operating", "category": "Fuel", "group": "operating", "voided_at": None},
            {"amount": "1000.00", "kind": "capital", "category": "Equipment", "group": "operating", "voided_at": None},
            {"amount": "9999.00", "kind": "operating", "category": "Fuel", "group": "operating", "voided_at": "2026-11-01T00:00:00Z"},
        ]
        s = finance_logic.summarise_expenses(rows)
        self.assertEqual((s["total"], s["operating"], s["capital"], s["count"], s["voided_count"]),
                         (Decimal("1500.00"), Decimal("500.00"), Decimal("1000.00"), 2, 1))
        self.assertEqual([c["category"] for c in s["by_category"]], ["Equipment", "Fuel"])

    def test_month_bounds(self):
        self.assertEqual(finance_logic.month_bounds("2026-12"), (date(2026, 12, 1), date(2026, 12, 31)))
        self.assertEqual(finance_logic.month_bounds("2028-02"), (date(2028, 2, 1), date(2028, 2, 29)))
        with self.assertRaises(ValueError):
            finance_logic.month_bounds("November")


if __name__ == "__main__":
    unittest.main()
