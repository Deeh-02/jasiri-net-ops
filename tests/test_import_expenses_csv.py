"""The one-off expenses CSV import (tools/import_expenses_csv.py): what each
kind of row is filed as, and which rows are held back. Pure: no database."""
import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from tools import import_expenses_csv as imp


def row(title, amount=1000, vendor="", csv_category="Facilities & Utilities", row_id=1):
    return {"id": row_id, "title": title, "vendor": vendor, "amount": Decimal(amount), "csv_category": csv_category}


def filed(title, amount=1000, vendor=""):
    r = imp.categorise(row(title, amount, vendor))
    return r["category"], r["kind"]


class Categories(unittest.TestCase):
    def test_the_old_categories_are_not_followed(self):
        # filed as "Bandwidth" in the file, but it is the billing system's fee
        self.assertEqual(filed("Internet Capacity", 1500, "FREEISPRADIUS"), ("Software & licences", "operating"))
        # filed as "Network Infrastructure", but electricity is electricity
        self.assertEqual(filed("Server Ndambaki Electricity token", 1000, "Kplc"), ("Power & solar", "operating"))
        # routers for customers are a running cost, not equipment kept
        self.assertEqual(filed("10 xpon routers", 12500, "FARHAN HASSAN MUHUMED"), ("Network equipment", "operating"))

    def test_equipment_kept_is_capital(self):
        self.assertEqual(filed("Server HP Proliant 380", 170000), ("Network equipment", "capital"))
        self.assertEqual(filed("ADSS 96 core 1400m", 44800), ("Network equipment", "capital"))
        self.assertEqual(filed("6pcs 48 core mechanical enclosure", 18000), ("Network equipment", "capital"))
        self.assertEqual(filed("625W Monocrystalline Solar Panels 8pcs", 131400), ("Power & solar", "capital"))
        self.assertEqual(filed("New TVS motorbike", 164400), ("Transport & fuel", "capital"))
        self.assertEqual(filed("2 laptops: Specs Dell Latitude", 68000), ("Office & supplies", "capital"))

    def test_words_inside_other_words_do_not_mislead(self):
        self.assertEqual(filed("2 power extensions for the office", 400), ("Office & supplies", "operating"))   # not "tension"
        self.assertEqual(filed("Mechanical enclosure 48 core", 3000), ("Network equipment", "capital"))          # not "mechanic"

    def test_people_and_their_purchases(self):
        self.assertEqual(filed("Phanuel August Salary", 35000, "PHANUEL WAMBANI"), ("Salaries", "operating"))
        self.assertEqual(filed("Dan Khamala Labour", 2500, "Daniel Khamala"), ("Salaries", "operating"))
        self.assertEqual(filed("Binding wire", 500, "Daniel Khamala"), ("Network equipment", "operating"))       # his purchase, not his wages

    def test_running_costs(self):
        self.assertEqual(filed("Motorbike fuel", 500, "Servco Petroleum Limited"), ("Transport & fuel", "operating"))
        self.assertEqual(filed("Motorbike TVS repair", 1000), ("Spares & repairs", "operating"))
        self.assertEqual(filed("Airtime for customer service", 500, "SAFARICOM POSTPAID BUNDLES"), ("Airtime & SMS", "operating"))
        self.assertEqual(filed("1000 sms units - freeispradius billing system (hotspot clients)", 500, "FREEISPRADIUS BILLING"), ("Airtime & SMS", "operating"))
        self.assertEqual(filed("Airtel link 350 mbps", 63000, "Airtel"), ("Upstream bandwidth", "operating"))
        self.assertEqual(filed("Jasiri Office Rent", 7800, "ACUPEX LIMITED"), ("Office rent", "operating"))
        self.assertEqual(filed("Overall Laundry", 200), ("Office & supplies", "operating"))
        self.assertEqual(filed("2 helmets @550 2 anticut gloves @200", 1550), ("Tools & safety gear", "operating"))

    def test_segments_only_when_the_row_says_so(self):
        self.assertEqual(imp.categorise(row("Hotspot Billing System", 1500, "FREEISPRADIUS BILLING"))["segment"], "hotspot")
        self.assertEqual(imp.categorise(row("PHP Radius Billing System", 2000, "PHP Radius"))["segment"], "pppoe")
        self.assertIsNone(imp.categorise(row("Motorbike fuel", 500, "Servco"))["segment"])

    def test_a_guess_is_marked_for_review(self):
        self.assertTrue(imp.categorise(row("Unidentified Business Expense", 3000))["review"])
        self.assertEqual(filed("Unidentified Business Expense", 3000)[0], "Other")


class Vendors(unittest.TestCase):
    def test_vendor_cleanup(self):
        self.assertEqual(imp.clean_vendor("Servco Petroleum Limited.."), "Servco Petroleum Limited")
        self.assertEqual(imp.clean_vendor("NICODEMUS  MATINYANI 0743072338"), "NICODEMUS MATINYANI")      # a person's phone number is dropped
        self.assertIsNone(imp.clean_vendor("Bank Ref: BAAATCUCOXCN69."))
        self.assertIsNone(imp.clean_vendor("500"))
        self.assertIsNone(imp.clean_vendor(""))


class Reading(unittest.TestCase):
    def test_a_mistyped_year_takes_its_neighbours_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "e.csv"
            with open(path, "w", newline="") as handle:
                w = csv.writer(handle)
                w.writerow(["ID", "Date", "Title", "Category", "Amount", "Tax", "Vendor", "Payment Method", "Status"])
                w.writerow([274, "2026-08-24", "a", "x", "10.00", "0.00", "v", "mpesa", "approved"])
                w.writerow([275, "3600-08-24", "b", "x", "20.00", "0.00", "v", "bank_transfer", "approved"])
            rows = imp.load(path)
        fixed = next(r for r in rows if r["id"] == 275)
        self.assertEqual(fixed["date"], date(2026, 8, 24))
        self.assertTrue(fixed["notes"])
        self.assertEqual(fixed["method"], "bank")


class HeldBack(unittest.TestCase):
    def test_a_row_already_in_the_app_is_matched_one_to_one(self):
        rows = [{"id": 1, "date": date(2026, 10, 13), "amount": Decimal(3000), "method": "bank"},
                {"id": 2, "date": date(2026, 10, 14), "amount": Decimal(3000), "method": "bank"}]
        existing = [{"id": 10, "spent_on": date(2026, 10, 13), "amount": Decimal(3000), "method": "bank"}]
        held = imp.already_in_app(rows, existing)
        self.assertEqual(held, {1: 10})                         # the closer date; the other is a different payment

    def test_other_way_of_paying_or_far_apart_is_not_a_match(self):
        rows = [{"id": 1, "date": date(2026, 10, 1), "amount": Decimal(3000), "method": "mpesa"}]
        self.assertEqual(imp.already_in_app(rows, [{"id": 10, "spent_on": date(2026, 10, 1), "amount": Decimal(3000), "method": "bank"}]), {})
        self.assertEqual(imp.already_in_app(rows, [{"id": 10, "spent_on": date(2026, 10, 9), "amount": Decimal(3000), "method": "mpesa"}]), {})

    def test_the_same_payment_twice_is_held_but_two_people_are_not(self):
        base = {"date": date(2026, 8, 3), "amount": Decimal(500), "title": "Motorbike fuel", "method": "mpesa"}
        rows = [{**base, "id": 1, "vendor": "Derrick Weru"}, {**base, "id": 2, "vendor": "Derrick Weru"}, {**base, "id": 3, "vendor": "PHANUEL WAMBANI"}]
        self.assertEqual(list(imp.spreadsheet_duplicates(rows)), [2])


if __name__ == "__main__":
    unittest.main()
