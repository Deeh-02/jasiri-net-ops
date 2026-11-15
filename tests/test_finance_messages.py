"""Reading pasted M-Pesa / bank messages and cash lines (db/finance_messages.py).
Every message below copies the exact wording of a real one (2026-11-15) with
the names, numbers and codes made up."""
import unittest
from datetime import date
from decimal import Decimal

from db.finance_messages import read_cash_line, read_message, read_messages, split_messages, payee_key

TODAY = date(2026, 11, 15)

PAYBILL = "QA1BC2DE3F Confirmed. Ksh1,946.00 sent to ACME SUPPLIES LIMITED for account 254700000001 on 13/10/26 at 7:24 AM"
TILL = "QA1BC2DE4G Confirmed. Ksh500.00 paid to Sample Petroleum Limited.. on 14/10/26 at 1:40 PM."
SEND = "QA1BC2DE5H Confirmed. Ksh5,000.00 sent to JANE  DOE 0700000002 on 17/10/26 at 1:56 PM."
SEND_SPACED = "QA1BC2DE6J Confirmed. Ksh 2,000.00 sent to JOHN  ROE 0700000003 on 30/10/26 at 12:13 PM."
RECEIPT = ("KES 16,640.00 paid to EXAMPLE PLASTICS LIMITED (Acc 400001) on 19/10/26 at 01:33 PM "
           "Ref: QA1BC2DE7K. Enquiries, call 0700000004.")
BANK_ACCOUNT = ("Dear SAMPLE OWNER, Transaction Ref: 20090000001 of KES 30,000.00 from A/c 1*****0001 "
                "to A/c 0*****9001 on 10/10/2026 is Completed Bank Ref: BXXXXXXXXXX01.")
BANK_PHONE = ("Dear SAMPLE OWNER, Transaction Ref: 20090000002 of KES 1,500.00 from A/c 1*****0001 "
              "to A/c +254*****0002 on 07/10/2026 is Completed Bank Ref: BXXXXXXXXXX02.")
FULL_WITH_FEE = ("QA1BC2DE8L Confirmed. Ksh1,000.00 sent to SAMPLE POWER PREPAID for account 37000000001 on 5/11/26 "
                 "at 10:15 AM New M-PESA balance is Ksh2,345.67. Transaction cost, Ksh23.00.Amount you can transact "
                 "within the day is 498,000.00.")


class ReadingOneMessage(unittest.TestCase):
    def test_paybill(self):
        r = read_message(PAYBILL, TODAY)
        self.assertEqual((r["status"], r["message_type"], r["reference"]), ("expense", "paybill", "QA1BC2DE3F"))
        self.assertEqual(r["amount"], Decimal("1946.00"))
        self.assertEqual(r["spent_on"], date(2026, 10, 13))
        self.assertEqual((r["payee"], r["account"]), ("ACME SUPPLIES LIMITED", "254700000001"))
        self.assertEqual(r["rule_keys"], ["ACME SUPPLIES LIMITED|254700000001", "ACME SUPPLIES LIMITED"])
        self.assertTrue(r["ai_may_see_payee"])

    def test_till_drops_the_trailing_dots(self):
        r = read_message(TILL, TODAY)
        self.assertEqual((r["message_type"], r["payee"], r["amount"]), ("till", "Sample Petroleum Limited", Decimal("500.00")))

    def test_send_money_is_a_person_and_keeps_the_phone_out_of_the_payee(self):
        r = read_message(SEND, TODAY)
        self.assertEqual((r["message_type"], r["payee"]), ("send_money", "JANE DOE"))
        self.assertTrue(r["is_person"])
        self.assertFalse(r["ai_may_see_payee"])
        self.assertNotIn("0700000002", r["payee"])

    def test_a_space_after_ksh_is_fine(self):
        self.assertEqual(read_message(SEND_SPACED, TODAY)["amount"], Decimal("2000.00"))

    def test_payee_side_receipt_has_its_code_at_the_end(self):
        r = read_message(RECEIPT, TODAY)
        self.assertEqual((r["message_type"], r["reference"], r["account"]), ("merchant_receipt", "QA1BC2DE7K", "400001"))
        self.assertEqual(r["amount"], Decimal("16640.00"))

    def test_bank_transfer_to_an_account(self):
        r = read_message(BANK_ACCOUNT, TODAY)
        self.assertEqual((r["method"], r["reference"], r["payee"]), ("bank", "BXXXXXXXXXX01", "Bank A/c 0*****9001"))
        self.assertEqual(r["spent_on"], date(2026, 10, 10))
        self.assertFalse(r["ai_may_see_payee"])

    def test_bank_transfer_to_a_phone_is_a_person(self):
        r = read_message(BANK_PHONE, TODAY)
        self.assertEqual(r["payee"], "Bank to M-Pesa +254*****0002")
        self.assertTrue(r["is_person"])

    def test_transaction_cost_becomes_the_fee(self):
        r = read_message(FULL_WITH_FEE, TODAY)
        self.assertEqual((r["amount"], r["fee"]), (Decimal("1000.00"), Decimal("23.00")))

    def test_a_reversal_is_never_an_expense(self):
        text = "QA1BC2DE9M Confirmed. Transaction QA1BC2DE3F has been reversed. Your account balance is now Ksh3,000.00."
        self.assertEqual(read_message(text, TODAY)["status"], "reversal")

    def test_money_received_is_not_an_expense(self):
        text = "QA1BC2DEAN Confirmed.You have received Ksh1,500.00 from JANE DOE 0700000002 on 5/11/26 at 9:00 AM"
        self.assertEqual(read_message(text, TODAY)["status"], "money_in")

    def test_unknown_wording_is_shown_not_guessed(self):
        r = read_message("Fuliza M-Pesa amount is Ksh 50.00. Interest charged Ksh 0.50.", TODAY)
        self.assertEqual(r["status"], "unrecognised")
        self.assertIn("reason", r)

    def test_a_future_date_is_flagged(self):
        r = read_message(TILL.replace("14/10/26", "14/12/26"), TODAY)
        self.assertEqual(r["status"], "expense")
        self.assertTrue(r["warnings"])

    def test_an_impossible_date_is_unrecognised(self):
        self.assertEqual(read_message(TILL.replace("14/10/26", "31/02/26"), TODAY)["status"], "unrecognised")


class SplittingAPaste(unittest.TestCase):
    def test_whatsapp_forwards_lose_their_prefix(self):
        paste = f"[10/13, 7:31 AM] Bro: {PAYBILL}\n[10/15, 9:55 AM] Bro: {TILL}"
        self.assertEqual(split_messages(paste), [PAYBILL, TILL])

    def test_whatsapp_export_lines_too(self):
        self.assertEqual(split_messages(f"13/10/2026, 07:31 - Bro: {PAYBILL}"), [PAYBILL])

    def test_messages_run_together_are_split(self):
        self.assertEqual(split_messages(f"{PAYBILL} {TILL}\n{BANK_ACCOUNT}"), [PAYBILL, TILL, BANK_ACCOUNT])

    def test_a_message_wrapped_over_lines_stays_whole(self):
        wrapped = FULL_WITH_FEE.replace(" New M-PESA", "\nNew M-PESA")
        self.assertEqual(len(split_messages(wrapped)), 1)

    def test_the_same_code_twice_in_one_paste_is_read_once(self):
        results = read_messages(f"{TILL}\n\n{TILL}", TODAY)
        self.assertEqual([r["status"] for r in results], ["expense", "repeated"])


class CashLines(unittest.TestCase):
    def test_words_then_amount(self):
        r = read_cash_line("fuel 2000")
        self.assertEqual((r["amount"], r["description"], r["keyword_category"]), (Decimal("2000"), "fuel", "Transport & fuel"))

    def test_amount_first_with_commas(self):
        r = read_cash_line("1,500 boda to Kinamba site")
        self.assertEqual((r["amount"], r["keyword_category"]), (Decimal("1500"), "Transport & fuel"))

    def test_two_numbers_need_the_amount_marked(self):
        self.assertEqual(read_cash_line("2 litres oil 500")["status"], "unrecognised")
        self.assertEqual(read_cash_line("2 litres oil ksh 500")["amount"], Decimal("500"))
        self.assertEqual(read_cash_line("2 connectors 300/=")["amount"], Decimal("300"))

    def test_no_amount_or_no_words(self):
        self.assertEqual(read_cash_line("fuel")["status"], "unrecognised")
        self.assertEqual(read_cash_line("2000")["status"], "unrecognised")

    def test_unknown_words_have_no_category(self):
        self.assertIsNone(read_cash_line("lunch 300")["keyword_category"])


class PayeeKey(unittest.TestCase):
    def test_case_spaces_and_dots_do_not_matter(self):
        self.assertEqual(payee_key("Sample  Petroleum Limited.."), payee_key("SAMPLE PETROLEUM LIMITED"))


if __name__ == "__main__":
    unittest.main()
