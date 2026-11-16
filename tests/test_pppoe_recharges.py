"""PPPoE recharge export rules (db/pppoe_recharge_logic.py). Pure."""
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from db import pppoe_recharge_logic as logic
from db.customer_import import ImportFileError

HEAD = "JASIRI NET LIMITED,,,,,,,,,,,\nUser,Name,Plan,Base Plan,Quantity,Amount,Status,Recharge Date,Type,Zone,Created By,Action\n"


def export(*lines):
    return (HEAD + "\n".join(lines) + "\n").encode()


def line(user, when, amount="KSh1500.00", plan="Standard", by="NCBAServer", status="Active", qty="0/1"):
    return f'{user},Name {user},{plan},"10 Mbps @Kshs 1,500",{qty},{amount},{status},{when},Plan,JASIRINET,{by},'


def classified(*lines):
    return logic.classify(logic.parse_recharge_file("r.csv", export(*lines))["rows"])


class Parse(unittest.TestCase):
    def test_reads_the_export_below_its_title_row(self):
        rows = logic.parse_recharge_file("r.csv", export(line("JS22", "15/11/2026 21:37:50")))["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["username"], r["plan"], r["amount"], r["created_by"]), ("JS22", "Standard", Decimal("1500.00"), "NCBAServer"))
        self.assertEqual(r["recharged_at"], datetime(2026, 11, 15, 18, 37, 50))   # Nairobi time stored as UTC

    def test_rows_come_back_oldest_first(self):
        rows = logic.parse_recharge_file("r.csv", export(line("A", "02/11/2026 10:00:00"), line("B", "01/11/2026 10:00:00")))["rows"]
        self.assertEqual([r["username"] for r in rows], ["B", "A"])

    def test_an_unreadable_row_is_listed_not_guessed(self):
        found = logic.parse_recharge_file("r.csv", export(line("A", "not a date"), line("B", "01/11/2026 10:00:00", amount="KSh?")),)
        self.assertEqual(found["rows"], [])
        self.assertEqual(found["unreadable"], [3, 4])

    def test_the_customer_export_is_turned_away_with_a_pointer(self):
        customers = b"JASIRI NET LIMITED\nUsername,Id,Name,Phone,Plan,Expiry,Account,Status\nJS01,1,A,0712345678,Basic,,Active,Enable\n"
        with self.assertRaisesRegex(ImportFileError, "Customers page"):
            logic.parse_recharge_file("c.csv", customers)

    def test_amounts_with_commas_and_spaces(self):
        self.assertEqual(logic._amount("KSh 1,500"), Decimal("1500"))
        self.assertIsNone(logic._amount("-5"))


class Rules(unittest.TestCase):
    def test_test_accounts_and_free_recharges_are_left_out(self):
        rows = classified(line("Test", "01/11/2026 10:00:00"), line("Jasiri", "01/11/2026 11:00:00", amount="KSh1.00"),
                          line("JS01", "01/11/2026 12:00:00", amount="KSh0.00"), line("JS02", "01/11/2026 13:00:00"))
        self.assertEqual([r["left_out"] for r in rows], ["test", "test", "free", None])

    def test_a_burst_of_staff_recharges_counts(self):
        # 20 Jun 2026: 18 accounts recharged in 3 seconds; the owner confirmed they paid.
        rows = classified(*[line(f"JS{n:02}", f"20/06/2026 17:46:{21 + n // 3:02}", by="JASIRINET") for n in range(6)])
        self.assertTrue(all(r["left_out"] is None for r in rows))

    def test_the_same_recharge_again_within_minutes_is_a_repeat(self):
        rows = classified(line("JS24", "20/08/2026 16:32:39", by="JASIRINET"), line("JS24", "20/08/2026 16:33:58", by="JASIRINET"))
        self.assertEqual([r["left_out"] for r in rows], [None, "repeat"])

    def test_two_recharges_hours_apart_both_count(self):
        rows = classified(line("JS32", "29/08/2026 19:23:39", by="JASIRINET"), line("JS32", "29/08/2026 21:20:41"))
        self.assertEqual([r["left_out"] for r in rows], [None, None])

    def test_a_different_plan_is_not_a_repeat(self):
        rows = classified(line("JS16", "17/10/2026 11:20:51", plan="Basic", amount="KSh1000.00"), line("JS16", "17/10/2026 11:25:00"))
        self.assertEqual([r["left_out"] for r in rows], [None, None])


class Preview(unittest.TestCase):
    def test_counts_only_new_rows_and_totals_them_by_nairobi_month(self):
        rows = classified(line("JS01", "31/10/2026 23:30:00"), line("JS02", "01/11/2026 09:00:00", by="Geoffrey"),
                          line("Test", "02/11/2026 09:00:00"))
        saved = {logic.recharge_key(rows[0])}
        p = logic.preview(rows, saved)
        self.assertEqual((p["in_file"], p["already_saved"], p["new"], p["counted"]), (3, 1, 2, 1))
        self.assertEqual(p["counted_total"], Decimal("1500.00"))
        self.assertEqual(p["by_bank"], 0)
        self.assertEqual([m["month"] for m in p["months"]], ["2026-11"])
        self.assertEqual([(l["reason"], l["recharges"]) for l in p["left_out"]], [("test", 1)])

    def test_a_late_night_recharge_lands_in_its_nairobi_month(self):
        p = logic.preview(classified(line("JS01", "31/10/2026 23:30:00")), set())
        self.assertEqual(p["months"][0]["month"], "2026-10")


class Labels(unittest.TestCase):
    def test_how_paid(self):
        self.assertEqual(logic.how_paid("NCBAServer"), "NCBA paybill (automatic)")
        self.assertEqual(logic.how_paid("Geoffrey"), "Recharged by Geoffrey")

    def test_not_used_yet(self):
        self.assertTrue(logic.not_used_yet({"billing_status": "Inactive", "quantity": "1/1"}))
        self.assertFalse(logic.not_used_yet({"billing_status": "Active", "quantity": "0/1"}))


class Attention(unittest.TestCase):
    NOW = datetime(2026, 11, 20, 12, 0)

    def test_an_old_upload_is_flagged(self):
        items = logic.attention((0, 0), (0, 0), self.NOW - timedelta(days=4), datetime(2026, 11, 15, 21, 37), self.NOW)
        self.assertEqual(len(items), 1)
        self.assertIn("4 days ago", items[0]["title"])

    def test_a_fresh_upload_with_nothing_held_is_quiet(self):
        self.assertEqual(logic.attention((0, 0), (0, 0), self.NOW - timedelta(hours=5), self.NOW, self.NOW), [])

    def test_held_repeats_and_unused_recharges_are_mentioned(self):
        items = logic.attention((1, Decimal(1500)), (2, Decimal(3000)), self.NOW, self.NOW, self.NOW)
        self.assertEqual([i["level"] for i in items], ["info", "info"])
        self.assertIn("repeats", items[0]["title"])
        self.assertIn("not used yet", items[1]["title"])


if __name__ == "__main__":
    unittest.main()
