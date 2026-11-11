"""Phase 6D: configuration changes on one ONU. SmartOLT and the database are
mocked; serials and names are made up. Run:
  venv/bin/python -m unittest tests.test_onu_config -v
"""
import os
import unittest
from unittest import mock

from fastapi import HTTPException
from pydantic import ValidationError

from db import onu_config, smartolt
from routers import olt as api

ONU = "HWTCTEST0001"


def raw(**over):
    d = {"unique_external_id": ONU, "sn": "HWTCTEST0001", "name": "Test_ONU", "status": "Online",
         "mode": "Routing", "wan_mode": "PPPoE", "username": "JS99", "password": "secret-in-smartolt",
         "service_ports": [{"vlan": "50"}, {"vlan": "10"}],
         "ethernet_ports": [{"port": "eth_0/1", "admin_state": "Enabled", "mode": "LAN", "dhcp": "No control", "vlan": ""},
                            {"port": "eth_0/2", "admin_state": "Enabled", "mode": "Access", "dhcp": "No control", "vlan": "10"}],
         "is_failed_resync_config": "0"}
    d.update(over)
    return smartolt.scrub(d)


def before(**over):
    return onu_config.snapshot(raw(**over))


class Plan(unittest.TestCase):
    def test_port_access_needs_an_attached_vlan(self):
        path, form, audit, summary = onu_config.plan("port", {"port": "eth_0/1", "mode": "access", "vlan": 50}, before(), ONU)
        self.assertEqual((path, form), (f"/api/onu/set_ethernet_port_access/{ONU}", {"ethernet_port": "eth_0/1", "vlan": 50}))
        self.assertEqual(summary, "eth_0/1: LAN → Access 50")
        with self.assertRaises(onu_config.ChangeError):
            onu_config.plan("port", {"port": "eth_0/1", "mode": "access", "vlan": 99}, before(), ONU)

    def test_unknown_port_and_mode_refused(self):
        for p in ({"port": "eth_0/9", "mode": "lan"}, {"port": "eth_0/1", "mode": "trunk"}):
            with self.assertRaises(onu_config.ChangeError, msg=str(p)):
                onu_config.plan("port", p, before(), ONU)

    def test_shutdown_sends_only_the_port(self):
        _, form, _, _ = onu_config.plan("port", {"port": "eth_0/2", "mode": "shutdown", "dhcp": "From ISP"}, before(), ONU)
        self.assertEqual(form, {"ethernet_port": "eth_0/2"})

    def test_onu_mode(self):
        path, form, _, summary = onu_config.plan("onu_mode", {"onu_mode": "Bridging"}, before(), ONU)
        self.assertEqual((form, summary), ({"onu_mode": "Bridging"}, "ONU mode Routing → Bridging"))
        with self.assertRaises(onu_config.ChangeError):
            onu_config.plan("onu_mode", {"onu_mode": "Routing"}, before(), ONU)   # already

    def test_pppoe_password_goes_to_smartolt_but_never_into_the_audit(self):
        _, form, audit, summary = onu_config.plan("wan_mode", {"wan_mode": "pppoe", "username": "JS99", "password": "abc123"}, before(), ONU)
        self.assertEqual(form["password"], "abc123")
        self.assertNotIn("password", audit)
        self.assertNotIn("abc123", repr(audit) + summary)

    def test_pppoe_needs_both_and_no_spaces(self):
        for p in ({"wan_mode": "pppoe", "username": "JS99"}, {"wan_mode": "pppoe", "username": "JS 99", "password": "x"},
                  {"wan_mode": "pppoe", "username": "JS99", "password": "a b"}):
            with self.assertRaises(onu_config.ChangeError) as e:
                onu_config.plan("wan_mode", p, before(), ONU)
            self.assertNotIn("a b", str(e.exception))

    def test_dhcp_wan_has_no_form(self):
        path, form, _, _ = onu_config.plan("wan_mode", {"wan_mode": "dhcp"}, before(), ONU)
        self.assertEqual((path, form), (f"/api/onu/set_onu_wan_mode_dhcp/{ONU}", None))

    def test_speed_only_from_smartolts_list(self):
        choices = {"upload": ["100M", "1G"], "download": ["100M", "1G"]}
        with mock.patch.object(onu_config, "speed_choices", return_value=choices):
            _, form, _, summary = onu_config.plan("speed", {"download": "100M"}, {**before(), "speed": {"download": "1G"}}, ONU)
            self.assertEqual(form, {"download_speed_profile_name": "100M"})
            self.assertEqual(summary, "Download 1G → 100M")
            for p in ({"download": "7M"}, {}):
                with self.assertRaises(onu_config.ChangeError):
                    onu_config.plan("speed", p, before(), ONU)

    def test_delete_needs_the_typed_serial_and_no_link(self):
        with mock.patch.object(onu_config.onu_links, "onu_customer", return_value=None):
            with self.assertRaises(onu_config.ChangeError):
                onu_config.plan("delete", {"confirm_sn": "HWTCTEST0002"}, before(), ONU)
            path, *_ = onu_config.plan("delete", {"confirm_sn": "hwtctest0001"}, before(), ONU)
            self.assertEqual(path, f"/api/onu/delete/{ONU}")
        for link in ({"id": 3, "username": "JS99"}, {"site": {"id": 1, "name": "Redsoil"}}):
            with mock.patch.object(onu_config.onu_links, "onu_customer", return_value=link):
                with self.assertRaises(onu_config.ChangeError):
                    onu_config.plan("delete", {"confirm_sn": "HWTCTEST0001"}, before(), ONU)
        with mock.patch.object(onu_config.onu_links, "onu_customer", return_value={"not_a_customer": True}):
            onu_config.plan("delete", {"confirm_sn": "HWTCTEST0001"}, before(), ONU)


class ReadBack(unittest.TestCase):
    def test_port_and_modes(self):
        after = before(ethernet_ports=[{"port": "eth_0/1", "admin_state": "Enabled", "mode": "Access", "vlan": "50"}])
        self.assertEqual(onu_config.check("port", {"port": "eth_0/1", "mode": "access", "vlan": 50}, after, ONU), [])
        self.assertEqual([d["field"] for d in onu_config.check("port", {"port": "eth_0/1", "mode": "lan"}, after, ONU)], ["mode"])
        self.assertEqual(onu_config.check("wan_mode", {"wan_mode": "pppoe", "username": "js99"}, before(), ONU), [])
        self.assertEqual([d["field"] for d in onu_config.check("onu_mode", {"onu_mode": "Bridging"}, before(), ONU)], ["onu_mode"])

    def test_resync_flag(self):
        self.assertEqual(onu_config.check("resync", {}, before(), ONU), [])
        self.assertEqual(len(onu_config.check("resync", {}, before(is_failed_resync_config="1"), ONU)), 1)


class Change(unittest.TestCase):
    """The whole flow with SmartOLT and the audit table mocked."""
    def setUp(self):
        self.audits = []
        patches = [
            mock.patch.object(smartolt, "_audit_start", side_effect=lambda *a, **k: self.audits.append(("start", a)) or 7),
            mock.patch.object(smartolt, "_audit_finish", side_effect=lambda *a, **k: self.audits.append(("finish", a))),
            mock.patch.object(smartolt, "get_action", side_effect=lambda i: {"id": i, "result": self.audits[-1][1][1]}),
            mock.patch.object(smartolt, "save"),
            mock.patch.dict(os.environ, {"SMARTOLT_WRITES_ENABLED": "true"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_change(self, kind, params, details_seq, post=None):
        sent = []

        def fake_call(method, path, **kw):
            sent.append((method, path, kw.get("data")))
            if method == "POST":
                if isinstance(post, Exception):
                    raise post
                return post or {"status": True, "response": "done"}
            nxt = details_seq.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return {"status": True, "onu_details": nxt}

        with mock.patch.object(smartolt, "call", side_effect=fake_call):
            row = onu_config.change(ONU, kind, params, user_id=1)
        return row, sent

    def test_mode_change_read_back_matches(self):
        row, sent = self.run_change("onu_mode", {"onu_mode": "Bridging"}, [raw(), raw(mode="Bridging")])
        self.assertEqual(row["result"], "succeeded")
        self.assertEqual(sent[1], ("POST", f"/api/onu/update_onu_mode/{ONU}", {"onu_mode": "Bridging"}))

    def test_read_back_mismatch_is_flagged(self):
        row, _ = self.run_change("onu_mode", {"onu_mode": "Bridging"}, [raw(), raw()])
        self.assertEqual(row["result"], "mismatch")

    def test_password_never_reaches_the_audit_row(self):
        self.run_change("wan_mode", {"wan_mode": "pppoe", "username": "JS99", "password": "Sup3rSecret"},
                        [raw(wan_mode="DHCP"), raw()])
        self.assertNotIn("Sup3rSecret", repr(self.audits))
        self.assertNotIn("secret-in-smartolt", repr(self.audits))

    def test_smartolt_refusal_is_audited_as_failed(self):
        with self.assertRaises(smartolt.SmartOLTError):
            self.run_change("resync", {}, [raw()], post=smartolt.SmartOLTError("OLT wasn't able to process the command"))
        self.assertEqual(self.audits[-1][1][1], "failed")

    def test_delete_succeeds_when_smartolt_no_longer_has_it(self):
        gone = smartolt.SmartOLTError("SmartOLT refused the request: Invalid parameters: No ONU was found for this ONU external ID")
        with mock.patch.object(onu_config.onu_links, "onu_customer", return_value=None), \
             mock.patch.object(onu_config, "_drop_local") as drop:
            row, _ = self.run_change("delete", {"confirm_sn": "HWTCTEST0001"}, [raw(), gone])
        self.assertEqual(row["result"], "succeeded")
        drop.assert_called_once_with(ONU)

    def test_switches(self):
        with mock.patch.dict(os.environ, {"SMARTOLT_ACTION_DELETE": "false"}):
            with self.assertRaises(smartolt.WritesDisabled):
                onu_config.change(ONU, "delete", {}, 1)
            self.assertTrue(onu_config.enabled("port"))
        with mock.patch.dict(os.environ, {"SMARTOLT_WRITES_ENABLED": "false"}):
            with self.assertRaises(smartolt.WritesDisabled):
                onu_config.change(ONU, "resync", {}, 1)
        self.assertEqual(self.audits, [])


class Endpoint(unittest.TestCase):
    def test_request_shape(self):
        api.ConfigRequest(change="port", port="eth_0/1", mode="access", vlan=10)
        for bad in ({"change": "restore"}, {"change": "port", "vlan": 5000}, {"change": "resync", "extra": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                api.ConfigRequest(**bad)

    def test_a_bad_fit_is_a_400(self):
        with mock.patch.object(onu_config, "change", side_effect=onu_config.ChangeError("no such port")):
            with self.assertRaises(HTTPException) as e:
                api.onu_change(ONU, api.ConfigRequest(change="resync"), current_user={"id": 1, "role": "admin"})
        self.assertEqual(e.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
