"""Old billing history rules (db/billing_history_logic.py). Pure."""
import unittest
from datetime import date, datetime
from decimal import Decimal

from db import billing_history_logic as logic
from db import finance_income_logic
from db.customer_import import ImportFileError

HEAD = "#,Invoice,Username,Plan,Type,Router,Price,Method,Recharged,Expiration\n"


def export(*lines):
    return (HEAD + "\n".join(lines) + "\n").encode()


def row(n, invoice, user, plan, kind, price, method, when, expires=None):
    return f'{n},{invoice},"{user}",{plan},{kind},Lipa10WIFI,{price},{method},{when},{expires or when + ":00"}'


def classified(*lines, new_system=()):
    return logic.classify(logic.parse_export("t.csv", export(*lines))["rows"], new_system)


def reasons(rows):
    return [(r["username"], r["left_out"]) for r in rows]


class Parse(unittest.TestCase):
    def test_reads_a_hotspot_sale(self):
        rows = logic.parse_export("t.csv", export(row(1, "UKG1", "254712345678-A:01", "Quick Surf10", "Hotspot", "10",
                                                     "BankStkPush-UKG1", "16 Nov 2026 16:25", "16 Nov 2026 18:25:10")))["rows"]
        r = rows[0]
        self.assertEqual((r["segment"], r["kind"], r["amount"]), ("hotspot", "hotspot", Decimal("10")))
        self.assertEqual(r["paid_at"], datetime(2026, 11, 16, 13, 25))   # Nairobi time stored as UTC

    def test_oldest_first_and_within_a_minute_by_row_number(self):
        rows = logic.parse_export("t.csv", export(
            row(10, "A", "JS01", "Jasiri 10mbps", "PPPOE", "1500", "Recharge - K", "25 May 2026 10:11"),
            row(11, "B", "JS01", "Jasiri 10mbps", "Balance", "1500.00", "Mpesa Paybill Manual", "25 May 2026 10:11"),
            row(12, "C", "JS02", "Jasiri 10mbps", "Balance", "1500.00", "Mpesa Paybill Manual", "24 May 2026 10:11")))["rows"]
        self.assertEqual([r["invoice"] for r in rows], ["C", "B", "A"])

    def test_unreadable_rows_are_listed(self):
        found = logic.parse_export("t.csv", export(row(1, "X", "a", "p", "Hotspot", "ten", "Vouchers", "01 May 2026 10:00")))
        self.assertEqual((found["rows"], found["unreadable"]), ([], [2]))

    def test_another_file_is_turned_away(self):
        with self.assertRaises(ImportFileError):
            logic.parse_export("t.csv", b"User,Plan,Amount\nJS01,Basic,1000\n")

    def test_pppoe_accounts_lose_the_unknown_prefix(self):
        self.assertEqual(logic.account_of("unknown JS,19", "pppoe"), "JS19")
        self.assertEqual(logic.account_of("unknown JSO5", "pppoe"), "JS05")
        self.assertEqual(logic.account_of("unknown mara", "pppoe"), "mara")
        self.assertEqual(logic.account_of("254712-A:01", "hotspot"), "254712-A:01")


class Hotspot(unittest.TestCase):
    def test_an_mpesa_code_used_twice_counts_once(self):
        rows = classified(row(2, "UE5", "2547-A", "Quick Surf10", "Hotspot", "10", "BankStkPush-UE5", "05 May 2026 21:31"),
                          row(1, "UE5", "2547-A", "Quick Surf10", "Hotspot", "10", "BankStkPush-UE5", "05 May 2026 21:32"))
        self.assertEqual([r["left_out"] for r in rows], [None, "repeat"])

    def test_support_and_bypass_are_free(self):
        rows = classified(row(3, "I1", "Kioko", "hp support users", "Hotspot", "1", "Activation - G", "01 May 2026 10:00"),
                          row(2, "I2", "Derrick", "hp support users", "Hotspot", "0", "Customer - Balance", "01 May 2026 11:00"),
                          row(1, "I3", "x", "10day pass150", "Hotspot", "150", "Admin Bypass", "01 May 2026 12:00"))
        self.assertEqual([r["left_out"] for r in rows], ["free", "free", "free"])

    def test_vouchers_and_staff_sales_count(self):
        rows = classified(row(2, "V1", "v1", "3day pass70", "Hotspot", "70", "Vouchers", "01 May 2026 10:00"),
                          row(1, "R1", "p", "10day pass150", "Hotspot", "150", "Recharge - G", "01 May 2026 11:00"))
        self.assertEqual([r["left_out"] for r in rows], [None, None])


class Pppoe(unittest.TestCase):
    def test_a_paybill_payment_counts_once_not_with_the_plan_it_bought(self):
        # The old system writes the cash (Balance) and the plan paid from it (PPPOE).
        rows = classified(row(2, "UE3", "JS12", "Jasiri 10mbps", "Balance", "1500.00", "Mpesa Paybill Manual", "03 May 2026 01:00"),
                          row(1, "INV-1", "JS12", "Jasiri 10mbps", "PPPOE", "1500", "Mpesa Paybill Manual - Mpesa", "03 May 2026 01:00",
                              "03 Jun 2026 23:59:00"))
        self.assertEqual([(r["kind"], r["left_out"]) for r in rows], [("balance", None), ("pppoe", "balance")])

    def test_a_plan_paid_later_from_balance_is_left_out(self):
        rows = classified(row(2, "UE4", "JS15", "Jasiri 5mbps", "Balance", "1000.00", "Mpesa Paybill Manual", "14 May 2026 21:45"),
                          row(1, "INV-2", "JS15", "Jasiri 5mbps", "PPPOE", "1000", "Customer - Balance", "16 May 2026 00:02",
                              "16 Jun 2026 23:59:00"))
        self.assertEqual([r["left_out"] for r in rows], [None, "balance"])

    def test_a_staff_recharge_with_no_payment_counts(self):
        rows = classified(row(1, "INV-3", "JS20", "Jasiri 10mbps", "PPPOE", "1500", "Recharge - Kennedy Munyua", "12 May 2026 18:12",
                              "12 Jun 2026 23:59:00"))
        self.assertIsNone(rows[0]["left_out"])

    def test_a_staff_recharge_applying_an_unlinked_paybill_payment_is_left_out(self):
        rows = classified(row(2, "UFE", "unknown JS,19", "unknown", "Balance", "1500.00", "Mpesa Paybill Manual", "14 Jun 2026 17:13"),
                          row(1, "INV-4", "JS19", "Jasiri 10mbps", "PPPOE", "1500", "Recharge - Kennedy Munyua", "14 Jun 2026 17:35",
                              "14 Jul 2026 23:59:00"))
        self.assertEqual([r["left_out"] for r in rows], [None, "balance"])

    def test_the_same_period_twice_is_a_repeat(self):
        rows = classified(row(2, "INV-5", "Prisca", "50mbps home dedicated", "PPPOE", "7000", "Recharge - Kennedy Munyua",
                              "01 Oct 2026 12:44", "01 Nov 2026 23:59:00"),
                          row(1, "INV-6", "Prisca", "50mbps home dedicated", "PPPOE", "7000", "Recharge - Geoffrey Gitau",
                              "01 Oct 2026 13:49", "01 Nov 2026 23:59:00"))
        self.assertEqual([r["left_out"] for r in rows], [None, "repeat"])

    def test_the_balance_correction_is_left_out(self):
        rows = classified(row(2, "UEC", "Ken", "hp support users", "Balance", "16000.00", "Mpesa Paybill Manual", "12 May 2026 07:28"),
                          row(1, "Admin1", "Kenn", "Manual Balance Edit", "Balance", "-16040", "Balance Edit - K", "22 May 2026 14:51"))
        self.assertEqual([r["left_out"] for r in rows], ["correction", "correction"])

    def test_after_the_move_the_new_system_has_it(self):
        new = [("JS06", datetime(2026, 10, 11, 12, 35))]    # recharged in the new system 7 minutes later
        rows = classified(row(2, "UJB", "unknown JS06", "unknown", "Balance", "1500.00", "Mpesa Paybill Manual", "11 Oct 2026 15:28"),
                          row(1, "UHL", "unknown mara", "unknown", "Balance", "5500.00", "Mpesa Paybill Manual", "27 Jul 2026 08:27"),
                          new_system=new)
        self.assertEqual(reasons(rows), [("unknown mara", None), ("unknown JS06", "moved")])

    def test_before_the_move_the_new_system_is_not_asked(self):
        rows = classified(row(1, "UF3", "JS12", "Jasiri 10mbps", "Balance", "1500.00", "Mpesa Paybill Manual", "03 Jun 2026 14:26"),
                          new_system=[("JS12", datetime(2026, 6, 4, 10, 0))])
        self.assertIsNone(rows[0]["left_out"])


class Preview(unittest.TestCase):
    def test_counts_by_month_and_segment_and_skips_saved_rows(self):
        rows = classified(row(3, "A", "2547-A", "Quick Surf10", "Hotspot", "10", "BankStkPush-A", "31 May 2026 23:30"),
                          row(2, "B", "2547-B", "Quick Surf10", "Hotspot", "10", "BankStkPush-B", "01 Jun 2026 09:00"),
                          row(1, "C", "JS01", "Jasiri 10mbps", "Balance", "1500.00", "Mpesa Paybill Manual", "01 Jun 2026 10:00"))
        p = logic.preview(rows, {logic.row_key(rows[0])})
        self.assertEqual((p["new"], p["counted"], p["counted_total"]), (2, 2, Decimal("1510.00")))
        self.assertEqual([(m["month"], m["segment"]) for m in p["months"]], [("2026-06", "hotspot"), ("2026-06", "pppoe")])

    def test_raw_daily_totals_include_left_out_rows(self):
        rows = classified(row(2, "A", "x", "hp support users", "Hotspot", "1", "Activation - G", "01 May 2026 10:00"),
                          row(1, "B", "2547-B", "Quick Surf10", "Hotspot", "10", "BankStkPush-B", "01 May 2026 11:00"))
        self.assertEqual(logic.daily_raw_totals(rows), {(date(2026, 5, 1), "hotspot"): (2, Decimal("11"))})


class Sources(unittest.TestCase):
    def test_the_router_takes_over_the_day_after_its_partial_first_day(self):
        self.assertEqual(logic.router_from(date(2026, 9, 21), date(2026, 11, 16)), date(2026, 9, 22))
        self.assertEqual(logic.router_from(date(2026, 9, 21), None), date(2026, 9, 21))
        self.assertIsNone(logic.router_from(None, date(2026, 11, 16)))

    def test_how_paid(self):
        self.assertEqual(logic.how_paid("BankStkPush-UKG1"), "M-Pesa (STK push)")
        self.assertEqual(logic.how_paid("Activation - Derrick Mucheru"), "Activated by Derrick Mucheru")
        self.assertEqual(logic.how_paid("Mpesa Paybill Manual - Mpesa"), "M-Pesa paybill")

    def test_source_notes(self):
        move = date(2026, 6, 20)
        sources = {"router_from": date(2026, 9, 22), "history_from": date(2026, 5, 1)}
        notes = finance_income_logic.source_notes(date(2026, 9, 1), date(2026, 9, 30), sources, None, move)
        self.assertEqual(notes, ["Hotspot: 1 to 21 Sep from the billing system's transactions export, from 22 Sep the router's sales."])
        self.assertEqual(finance_income_logic.source_notes(date(2026, 10, 1), date(2026, 10, 31), sources, date(2026, 5, 1), move), [])
        june = finance_income_logic.source_notes(date(2026, 6, 1), date(2026, 6, 30), None, date(2026, 5, 1), move)
        self.assertIn("until the move on 20 Jun", june[0])


if __name__ == "__main__":
    unittest.main()
