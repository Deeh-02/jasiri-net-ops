"""Phase 6B: suggesting which customer each ONU belongs to. Pure matching
rules plus the link endpoints' validation; no database. Names are made up.
Run: venv/bin/python -m unittest tests.test_onu_links -v
"""
import unittest
from unittest import mock

from fastapi import HTTPException
from pydantic import ValidationError

from db import onu_links
from routers import olt as api


def onu(id_, name=None, user=None):
    return {"id": id_, "sn": id_, "name": name, "pppoe_username": user}


def customer(id_, username, name=None):
    return {"id": id_, "username": username, "name": name or username, "account_status": "Active"}


class Suggest(unittest.TestCase):
    def test_pppoe_username_match_is_bulk_safe(self):
        s = onu_links.suggest([onu("A", "Mark_JS45", "JS45")], [customer(1, "js45")], [])
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["matched_by"], s[0]["ambiguous"]), ("pppoe_username", False))
        self.assertTrue(onu_links.bulk_ok(s[0]))

    def test_name_match_is_never_bulk(self):
        s = onu_links.suggest([onu("A", "Gabriel_JS41")], [customer(1, "JS41")], [])
        self.assertEqual(s[0]["matched_by"], "name")
        self.assertFalse(onu_links.bulk_ok(s[0]))

    def test_username_wins_over_name(self):
        s = onu_links.suggest([onu("A", "Bella_JS43", "JS99")], [customer(1, "JS43"), customer(2, "JS99")], [])
        self.assertEqual([(x["customer"]["id"], x["matched_by"]) for x in s], [(2, "pppoe_username")])

    def test_one_customer_for_two_onus_is_ambiguous(self):
        s = onu_links.suggest([onu("A", None, "JS10"), onu("B", None, "JS10")], [customer(1, "JS10")], [])
        self.assertEqual(len(s), 2)
        self.assertTrue(all(x["ambiguous"] and not onu_links.bulk_ok(x) for x in s))

    def test_two_customers_for_one_onu_is_ambiguous(self):
        s = onu_links.suggest([onu("A", "JS10 JS11")], [customer(1, "JS10"), customer(2, "JS11")], [])
        self.assertEqual(len(s), 2)
        self.assertTrue(all(x["ambiguous"] for x in s))

    def test_already_linked_onus_and_customers_are_left_out(self):
        links = [{"onu_id": "A", "customer_id": 1}, {"onu_id": "C", "customer_id": None, "not_a_customer": True}]
        s = onu_links.suggest([onu("A", None, "JS1"), onu("B", None, "JS1"), onu("C", "JS2")],
                              [customer(1, "JS1"), customer(2, "JS2")], links)
        self.assertEqual(s, [])

    def test_no_match(self):
        self.assertEqual(onu_links.suggest([onu("A", "Redsoil_Hotspot")], [customer(1, "JS1")], []), [])


class Endpoints(unittest.TestCase):
    def test_link_request_validation(self):
        api.LinkRequest(onu_id="HWTC19D4158E", customer_id=3, matched_by="name")
        for bad in ({"onu_id": "../x", "customer_id": 1}, {"onu_id": "A", "customer_id": 0},
                    {"onu_id": "A", "customer_id": 1, "matched_by": "guess"}, {"onu_id": "A", "customer_id": 1, "extra": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                api.LinkRequest(**bad)

    def test_a_clash_is_a_409(self):
        with mock.patch.object(onu_links, "link", side_effect=onu_links.LinkError("already linked")):
            with self.assertRaises(HTTPException) as e:
                api.create_link(api.LinkRequest(onu_id="A", customer_id=1), current_user={"id": 1, "role": "admin"})
        self.assertEqual(e.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
