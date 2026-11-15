"""DeepSeek help on the Expenses review (db/finance_ai.py, routers/finance.py's
/suggest). DeepSeek is faked: these check what is sent, what is accepted
back, and that nothing it says can set an amount, code or date unchecked.
Names, numbers and codes are made up."""
import json
import os
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from db import deepseek, finance_ai, finance_logic
from db.finance_messages import read_cash_lines, read_messages
from routers import finance as api

TODAY = date(2026, 11, 15)
CATEGORIES = [
    {"id": 1, "name": "Transport & fuel", "group": "operating", "is_active": True},
    {"id": 2, "name": "Airtime & SMS", "group": "operating", "is_active": True},
    {"id": 3, "name": "M-Pesa & bank charges", "group": "finance_tax", "is_active": True},
    {"id": 4, "name": "Network equipment", "group": "operating", "is_active": True},
    {"id": 9, "name": "Other", "group": "other", "is_active": True},
]
TILL = "QA1BC2DE4G Confirmed. Ksh500.00 paid to Sample Petroleum Limited.. on 14/10/26 at 1:40 PM."
PAYBILL_PHONE_ACCOUNT = ("QA1BC2DE3F Confirmed. Ksh1,946.00 sent to ACME SUPPLIES LIMITED for account 254700000001 "
                         "on 13/10/26 at 7:24 AM")
PAYBILL_CODE_ACCOUNT = "QA1BC2DE5H Confirmed. Ksh500.00 sent to SAMPLE BILLING for account smstopup on 2/11/26 at 6:14 PM"
SEND = "QA1BC2DE6J Confirmed. Ksh5,000.00 sent to JANE  DOE 0700000002 on 17/10/26 at 1:56 PM."
BANK = ("Dear SAMPLE OWNER, Transaction Ref: 20090000001 of KES 30,000.00 from A/c 1*****0001 "
        "to A/c 0*****9001 on 10/10/2026 is Completed Bank Ref: BXXXXXXXXXX01.")
ODD = ("Payment of KES 2,350.00 to SAMPLE HARDWARE LTD ref QZ9XY8WV7U on 03-11-2026 successful. "
       "Call 0700000005. Available balance KES 12,000.00.")


def review(messages, cash=""):
    return finance_logic.build_review(read_messages(messages, TODAY), read_cash_lines(cash), {}, set(), CATEGORIES)


class WhatIsSent(unittest.TestCase):
    def payload(self, messages, cash="", note=""):
        return finance_ai.build_request(review(messages, cash), note, CATEGORIES)

    def test_business_payees_are_sent(self):
        payload, ids = self.payload(f"{TILL}\n{PAYBILL_CODE_ACCOUNT}")
        self.assertEqual([p["payee"] for p in payload["payees"]], ["Sample Petroleum Limited", "SAMPLE BILLING"])
        self.assertEqual(payload["payees"][1]["account"], "smstopup")
        self.assertEqual(ids["p1"], "SAMPLE PETROLEUM LIMITED")

    def test_people_bank_transfers_and_cash_are_never_sent(self):
        payload, _ = self.payload(f"{SEND}\n{BANK}", cash="lunch 300")
        self.assertEqual(payload["payees"], [])
        self.assertFalse(finance_ai.has_work(payload))
        sent = json.dumps(payload)
        for private in ("JANE", "0700000002", "9001", "lunch"):
            self.assertNotIn(private, sent)

    def test_an_account_that_is_a_phone_number_is_left_out(self):
        payload, _ = self.payload(PAYBILL_PHONE_ACCOUNT)
        self.assertIsNone(payload["payees"][0]["account"])
        self.assertNotIn("254700000001", json.dumps(payload))

    def test_unread_messages_go_without_phones_or_balances(self):
        payload, ids = self.payload(ODD)
        text = payload["messages"][0]["text"]
        self.assertNotIn("0700000005", text)
        self.assertNotIn("12,000", text)
        self.assertIn("2,350.00", text)
        self.assertEqual(ids["m1"], "message-0")

    def test_the_note_is_sent_without_phone_numbers(self):
        payload, _ = self.payload(TILL, note="Servco is fuel, call 0712345678 if unsure")
        self.assertNotIn("0712345678", payload["note"])
        self.assertIn("fuel", payload["note"])

    def test_names_of_accounts_are_not_sent(self):
        self.assertIsNone(finance_ai.ai_safe_account("Jane Doe"))
        self.assertEqual(finance_ai.ai_safe_account("550770"), "550770")


class WhatIsAcceptedBack(unittest.TestCase):
    IDS = {"p1": "SAMPLE PETROLEUM LIMITED", "p2": "SAMPLE BILLING|SMSTOPUP", "m1": "message-0"}

    def parse(self, reply):
        return finance_ai.parse_reply(json.dumps(reply), self.IDS, CATEGORIES)

    def test_a_listed_category_is_used(self):
        result = self.parse({"payees": [{"id": "p1", "category": "transport & fuel", "kind": "operating"}]})
        self.assertEqual(result["payees"]["SAMPLE PETROLEUM LIMITED"]["category_id"], 1)

    def test_an_invented_category_becomes_other(self):
        result = self.parse({"payees": [{"id": "p1", "category": "Vehicle running costs"}]})
        self.assertEqual(result["payees"]["SAMPLE PETROLEUM LIMITED"]["category_id"], 9)

    def test_a_strange_kind_becomes_operating(self):
        result = self.parse({"payees": [{"id": "p2", "category": "Airtime & SMS", "kind": "monthly"}]})
        self.assertEqual(result["payees"]["SAMPLE BILLING|SMSTOPUP"]["kind"], "operating")

    def test_unknown_ids_are_ignored(self):
        self.assertEqual(self.parse({"payees": [{"id": "p7", "category": "Other"}]})["payees"], {})

    def test_a_reading_missing_a_part_is_dropped(self):
        result = self.parse({"messages": [{"id": "m1", "amount_text": "2,350.00", "date_text": None, "code": "QZ9XY8WV7U"}]})
        self.assertEqual(result["readings"], {})

    def test_text_that_is_not_json_is_a_bad_reply(self):
        with self.assertRaises(finance_ai.BadReply):
            finance_ai.parse_reply("Sure! Here are my suggestions", self.IDS, CATEGORIES)

    def test_a_json_fence_is_tolerated(self):
        result = finance_ai.parse_reply('```json\n{"payees": []}\n```', self.IDS, CATEGORIES)
        self.assertEqual(result["payees"], {})


class CheckingAReading(unittest.TestCase):
    GOOD = {"amount_text": "2,350.00", "date_text": "03-11-2026", "code": "QZ9XY8WV7U", "payee": "SAMPLE HARDWARE LTD"}

    def check(self, **changes):
        return finance_ai.verify_reading(ODD, dict(self.GOOD, **changes), TODAY)

    def test_a_reading_that_is_all_in_the_message_is_accepted(self):
        draft, why = self.check()
        self.assertIsNone(why)
        self.assertEqual((draft["amount"], draft["spent_on"], draft["reference"]),
                         (Decimal("2350.00"), date(2026, 11, 3), "QZ9XY8WV7U"))
        self.assertTrue(draft["ai_read"])

    def test_an_amount_not_in_the_message_is_refused(self):
        self.assertIsNone(self.check(amount_text="2,530.00")[0])

    def test_the_balance_is_in_the_message_but_part_of_a_number_is_not(self):
        # "350.00" is inside "2,350.00": not a number on its own.
        self.assertIsNone(self.check(amount_text="350.00")[0])

    def test_a_worked_out_amount_is_refused(self):
        self.assertIsNone(self.check(amount_text="2350")[0])

    def test_a_code_not_in_the_message_is_refused(self):
        self.assertIsNone(self.check(code="QZ9XY8WV7X")[0])

    def test_a_date_not_in_the_message_is_refused(self):
        self.assertIsNone(self.check(date_text="2026-11-03")[0])

    def test_a_future_date_is_refused(self):
        draft, _ = finance_ai.verify_reading(ODD.replace("03-11-2026", "03-12-2026"),
                                             dict(self.GOOD, date_text="03-12-2026"), TODAY)
        self.assertIsNone(draft)

    def test_date_forms_the_code_reads(self):
        for text, expected in (("5/11/26", date(2026, 11, 5)), ("05/11/2026", date(2026, 11, 5)),
                               ("5.11.2026", date(2026, 11, 5)), ("2026-11-05", date(2026, 11, 5)),
                               ("5 Nov 2026", date(2026, 11, 5)), ("5-Nov-26", date(2026, 11, 5))):
            self.assertEqual(finance_ai.read_date_text(text), expected, text)
        self.assertIsNone(finance_ai.read_date_text("31/02/2026"))

    def test_a_phone_number_is_taken_out_of_the_payee(self):
        draft, _ = self.check(payee="JANE DOE 0700000002")
        self.assertEqual(draft["payee"], "JANE DOE")


class SavingAReading(unittest.TestCase):
    def test_a_checked_reading_is_saved_with_its_own_amount_and_the_sparkle(self):
        items = review(ODD)
        key = items[0]["key"]
        reading = {"amount_text": "2,350.00", "date_text": "03-11-2026", "code": "QZ9XY8WV7U", "payee": None}
        choices = {key: {"category_id": 4, "ai_reading": reading}}
        updated, problems = finance_ai.apply_readings(items, choices, TODAY)
        self.assertEqual(problems, {})
        rows, rules = finance_logic.build_rows_to_save(updated, choices, CATEGORIES, TODAY)
        self.assertEqual((rows[0]["amount"], rows[0]["reference"], rows[0]["ai_suggested"]), (Decimal("2350.00"), "QZ9XY8WV7U", True))
        self.assertEqual(rules, {})

    def test_a_forged_reading_is_refused_on_save(self):
        items = review(ODD)
        choices = {items[0]["key"]: {"category_id": 4, "ai_reading": {
            "amount_text": "9,999.00", "date_text": "03-11-2026", "code": "QZ9XY8WV7U"}}}
        _, problems = finance_ai.apply_readings(items, choices, TODAY)
        self.assertIn(items[0]["key"], problems)

    def test_a_kept_ai_category_is_marked(self):
        items = review(TILL)
        rows, _ = finance_logic.build_rows_to_save(items, {"QA1BC2DE4G": {"category_id": 1, "ai_suggested": True}}, CATEGORIES, TODAY)
        self.assertTrue(rows[0]["ai_suggested"])


class TheCall(unittest.TestCase):
    def test_one_call_and_only_checked_readings_come_back(self):
        items = review(f"{TILL}\n{ODD}")
        reply = {"payees": [{"id": "p1", "category": "Transport & fuel", "kind": "operating", "description": "Generator fuel"}],
                 "messages": [{"id": "m1", "amount_text": "2,350.00", "date_text": "03-11-2026", "code": "QZ9XY8WV7U"}]}
        with mock.patch.object(finance_ai.deepseek, "chat", return_value={"content": json.dumps(reply)}) as chat:
            result = finance_ai.suggest(items, "fuel is for the generator", CATEGORIES, TODAY)
        self.assertEqual(chat.call_count, 1)
        self.assertTrue(chat.call_args.kwargs["json_output"])
        self.assertEqual(result["payees"]["SAMPLE PETROLEUM LIMITED"]["description"], "Generator fuel")
        self.assertEqual(result["readings"]["message-1"]["amount"], Decimal("2350.00"))

    def test_a_reading_that_fails_the_check_is_left_out(self):
        items = review(ODD)
        reply = {"messages": [{"id": "m1", "amount_text": "2,400.00", "date_text": "03-11-2026", "code": "QZ9XY8WV7U"}]}
        with mock.patch.object(finance_ai.deepseek, "chat", return_value={"content": json.dumps(reply)}):
            self.assertEqual(finance_ai.suggest(items, "", CATEGORIES, TODAY)["readings"], {})

    def test_no_call_when_there_is_nothing_to_ask(self):
        with mock.patch.object(finance_ai.deepseek, "chat") as chat:
            finance_ai.suggest(review(f"{SEND}\n{BANK}"), "", CATEGORIES, TODAY)
        chat.assert_not_called()


class Switch(unittest.TestCase):
    def test_off_unless_switched_on_and_a_key_is_set(self):
        with mock.patch.dict(os.environ, {"FINANCE_AI_ENABLED": "", "DEEPSEEK_API_KEY": "x"}):
            self.assertFalse(finance_ai.is_enabled())
        with mock.patch.dict(os.environ, {"FINANCE_AI_ENABLED": "true", "DEEPSEEK_API_KEY": ""}):
            self.assertFalse(finance_ai.is_enabled())
        with mock.patch.dict(os.environ, {"FINANCE_AI_ENABLED": "true", "DEEPSEEK_API_KEY": "x"}):
            self.assertTrue(finance_ai.is_enabled())


class SuggestEndpoint(unittest.TestCase):
    ADMIN = {"id": 1, "role": "admin"}

    def call(self):
        body = api.SuggestExpenses(messages=TILL, note="")
        with mock.patch.object(api, "_review", return_value=(review(TILL), CATEGORIES, TODAY)):
            return api.suggest_expenses(body, current_user=self.ADMIN)

    def test_switched_off_says_so_and_calls_nothing(self):
        with mock.patch.object(api.finance_ai, "is_enabled", return_value=False), \
             mock.patch.object(api.finance_ai, "suggest") as suggest:
            self.assertEqual(self.call(), {"enabled": False})
        suggest.assert_not_called()

    def test_a_deepseek_failure_is_a_plain_message_not_an_error(self):
        with mock.patch.object(api.finance_ai, "is_enabled", return_value=True), \
             mock.patch.object(api.finance_ai, "suggest", side_effect=deepseek.DeepSeekError("timeout")):
            result = self.call()
        self.assertTrue(result["enabled"])
        self.assertIn("too long", result["error"])
        self.assertNotIn("deepseek_api_key", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
