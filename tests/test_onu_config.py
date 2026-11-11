"""Phase 6D: configuration changes on one ONU. SmartOLT and the database are
mocked; serials, names and addresses are made up. Run:
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
OPTIONS = {"zones": [{"id": 1, "name": "Ndabaki-Deliverance Rd"}, {"id": 2, "name": "Garage"}],
           "vlans": [{"vlan": v} for v in (10, 50, 51, 60, 70)]}


def raw(**over):
    d = {"unique_external_id": ONU, "sn": "HWTCTEST0001", "name": "Test_ONU", "status": "Online", "olt_id": "1",
         "pon_type": "gpon", "mode": "Routing", "wan_mode": "PPPoE", "vlan": "51", "username": "JS99",
         "password": "secret-in-smartolt", "zone_name": "Ndabaki-Deliverance Rd", "odb_name": None, "odb_port": "",
         "seal_number": None, "address": "Plot 7 Test Lane", "contact": "0700000001",
         "service_ports": [{"vlan": "51"}, {"vlan": "10"}],
         "ethernet_ports": [{"port": "eth_0/1", "admin_state": "Enabled", "mode": "LAN", "dhcp": "No control", "vlan": ""},
                            {"port": "eth_0/2", "admin_state": "Enabled", "mode": "Access", "dhcp": "No control", "vlan": "10"}],
         "is_failed_resync_config": "0"}
    d.update(over)
    return d


def before(**over):
    return onu_config.snapshot(smartolt.scrub(raw(**over)))


def plan(kind, params, b=None, **kw):
    with mock.patch.object(smartolt, "authorize_options", return_value=OPTIONS), \
         mock.patch.object(smartolt, "odbs", return_value=[{"name": "DRD", "ports": 8}]):
        return onu_config.plan(kind, params, b or before(), ONU, private=onu_config._private(raw()), **kw)


class Plan(unittest.TestCase):
    def test_port_access_needs_an_attached_vlan(self):
        steps, audit, summary = plan("port", {"port": "eth_0/1", "mode": "access", "vlan": 10})
        self.assertEqual(steps, [(f"/api/onu/set_ethernet_port_access/{ONU}", {"ethernet_port": "eth_0/1", "vlan": 10})])
        self.assertEqual(summary, "eth_0/1: LAN → Access 10")
        with self.assertRaises(onu_config.ChangeError):
            plan("port", {"port": "eth_0/1", "mode": "access", "vlan": 99})

    def test_unknown_port_and_mode_refused(self):
        for p in ({"port": "eth_0/9", "mode": "lan"}, {"port": "eth_0/1", "mode": "trunk"}):
            with self.assertRaises(onu_config.ChangeError, msg=str(p)):
                plan("port", p)

    def test_shutdown_sends_only_the_port(self):
        steps, _, _ = plan("port", {"port": "eth_0/2", "mode": "shutdown", "dhcp": "From ISP"})
        self.assertEqual(steps[0][1], {"ethernet_port": "eth_0/2"})

    def test_speed_only_from_smartolts_list(self):
        choices = {"upload": ["100M", "1G"], "download": ["100M", "1G"]}
        with mock.patch.object(onu_config, "speed_choices", return_value=choices):
            steps, _, summary = plan("speed", {"download": "100M"}, {**before(), "speed": {"download": "1G"}})
            self.assertEqual(steps[0][1], {"download_speed_profile_name": "100M"})
            self.assertEqual(summary, "Download 1G → 100M")
            for p in ({"download": "7M"}, {}):
                with self.assertRaises(onu_config.ChangeError):
                    plan("speed", p)

    def test_delete_needs_the_typed_serial_and_no_link(self):
        with mock.patch.object(onu_config.onu_links, "onu_customer", return_value=None):
            with self.assertRaises(onu_config.ChangeError):
                plan("delete", {"confirm_sn": "HWTCTEST0002"})
            steps, *_ = plan("delete", {"confirm_sn": "hwtctest0001"})
            self.assertEqual(steps[0][0], f"/api/onu/delete/{ONU}")
        for link in ({"id": 3, "username": "JS99"}, {"site": {"id": 1, "name": "Redsoil"}}):
            with mock.patch.object(onu_config.onu_links, "onu_customer", return_value=link):
                with self.assertRaises(onu_config.ChangeError):
                    plan("delete", {"confirm_sn": "HWTCTEST0001"})


class Location(unittest.TestCase):
    def test_only_changed_fields_are_sent(self):
        steps, audit, summary = plan("location", {"zone": "Garage", "name": "Test_ONU", "contact": "0700000001",
                                                  "address_or_comment": "Plot 8 Test Lane", "seal_number": ""})
        self.assertEqual(steps[0], (f"/api/onu/update_location_details/{ONU}",
                                    {"zone": "Garage", "address_or_comment": "Plot 8 Test Lane"}))
        self.assertEqual(summary, "Zone Ndabaki-Deliverance Rd → Garage; Address or comment changed")

    def test_address_and_contact_never_reach_the_audit(self):
        _, audit, summary = plan("location", {"address_or_comment": "Plot 8 Test Lane", "contact": "0711111111"})
        self.assertEqual(audit, {"address_or_comment": "changed", "contact": "changed"})
        self.assertNotIn("0711111111", summary + repr(audit))

    def test_splitter_must_be_in_the_zone(self):
        steps, _, _ = plan("location", {"odb": "DRD", "odb_port": 3})
        self.assertEqual(steps[0][1], {"odb": "DRD", "odb_port": 3})
        with self.assertRaises(onu_config.ChangeError):
            plan("location", {"odb": "XYZ"})
        with self.assertRaises(onu_config.ChangeError):
            plan("location", {"zone": "Nowhere"})

    def test_smartolts_character_rules(self):
        for p in ({"name": "Bad<name>"}, {"name": ""}, {"contact": "a;b"}):
            with self.assertRaises(onu_config.ChangeError, msg=str(p)):
                plan("location", p)

    def test_nothing_changed(self):
        with self.assertRaises(onu_config.ChangeError):
            plan("location", {"name": "Test_ONU", "zone": "Ndabaki-Deliverance Rd"})


class Vlans(unittest.TestCase):
    def test_add_and_remove(self):
        steps, audit, summary = plan("vlans", {"vlans": [51, 50]})
        self.assertEqual(steps[0][1], {"add_vlans": "50", "remove_vlans": "10"})
        self.assertEqual(summary, "Add VLAN 50; Remove VLAN 10")

    def test_wan_vlan_stays_and_vlan_must_exist(self):
        for p in ({"vlans": [10]}, {"vlans": [51, 999]}, {"vlans": []}, {"vlans": [51, 10]}):
            with self.assertRaises(onu_config.ChangeError, msg=str(p)):
                plan("vlans", p)


class Mode(unittest.TestCase):
    def test_smartolt_order_vlan_mode_wan_remote(self):
        steps, audit, summary = plan("mode", {"vlan": 50, "onu_mode": "Routing", "wan_mode": "dhcp", "remote_access": "disable"})
        self.assertEqual([s[0].split("/")[3] for s in steps],
                         ["update_main_vlan", "set_onu_wan_mode_dhcp", "disable_allow_remote_access_to_wan_ip"])
        self.assertEqual(summary, "WAN VLAN 51 → 50; WAN mode PPPoE → DHCP; WAN remote access off")

    def test_bridging_skips_the_wan(self):
        steps, _, summary = plan("mode", {"onu_mode": "Bridging", "wan_mode": "dhcp"})
        self.assertEqual([s[0].split("/")[3] for s in steps], ["update_onu_mode"])

    def test_pppoe_password_goes_to_smartolt_but_never_into_the_audit(self):
        steps, audit, summary = plan("mode", {"wan_mode": "pppoe", "username": "JS98", "password": "abc123"})
        self.assertEqual(steps[0][1]["password"], "abc123")
        self.assertNotIn("abc123", repr(audit) + summary)

    def test_new_username_reuses_the_current_password(self):
        steps, audit, summary = plan("mode", {"wan_mode": "pppoe", "username": "JS98"}, current_password=lambda: "kept1")
        self.assertEqual((steps[0][1]["username"], steps[0][1]["password"]), ("JS98", "kept1"))
        self.assertIn("same password", summary)
        self.assertNotIn("kept1", repr(audit) + summary)

    def test_same_pppoe_is_no_change(self):
        with self.assertRaises(onu_config.ChangeError):
            plan("mode", {"onu_mode": "Routing", "wan_mode": "pppoe", "username": "JS99"})

    def test_static_ip_needs_valid_addresses(self):
        good = {"wan_mode": "static", "ipv4_address": "10.10.8.195", "subnet_mask": "255.255.255.0", "gateway": "10.10.8.1", "dns1": "8.8.8.8"}
        steps, audit, _ = plan("mode", good)
        self.assertEqual(steps[0][1]["ipv4_address"], "10.10.8.195")
        with self.assertRaises(onu_config.ChangeError):
            plan("mode", {**good, "gateway": "10.10.8"})

    def test_bad_pppoe_text(self):
        for p in ({"wan_mode": "pppoe", "username": "JS 99", "password": "x"}, {"wan_mode": "pppoe", "username": "JS98", "password": "a b"}):
            with self.assertRaises(onu_config.ChangeError) as e:
                plan("mode", p)
            self.assertNotIn("a b", str(e.exception))


class ReadBack(unittest.TestCase):
    def test_port_mode_and_location(self):
        after = before(ethernet_ports=[{"port": "eth_0/1", "admin_state": "Enabled", "mode": "Access", "vlan": "50"}])
        self.assertEqual(onu_config.check("port", {"port": "eth_0/1", "mode": "access", "vlan": 50}, after, ONU), [])
        self.assertEqual([d["field"] for d in onu_config.check("port", {"port": "eth_0/1", "mode": "lan"}, after, ONU)], ["mode"])
        self.assertEqual(onu_config.check("mode", {"wan_mode": "PPPoE", "username": "js99", "vlan": 51}, before(), ONU), [])
        diffs = onu_config.check("location", {"contact": "0711111111"}, before(), ONU, private=onu_config._private(raw()))
        self.assertEqual(diffs, [{"field": "contact", "sent": None, "smartolt_has": None}])

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
            mock.patch.object(smartolt, "get_action", side_effect=lambda i: {"id": i, "result": self.audits[-1][1][1],
                                                                            "response": self.audits[-1][1][2]}),
            mock.patch.object(smartolt, "save"),
            mock.patch.object(smartolt, "authorize_options", return_value=OPTIONS),
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
                answer = post(path) if callable(post) else post
                if isinstance(answer, Exception):
                    raise answer
                return answer or {"status": True, "response": "done"}
            nxt = details_seq.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return {"status": True, "onu_details": nxt}

        with mock.patch.object(smartolt, "call", side_effect=fake_call):
            row = onu_config.change(ONU, kind, params, user_id=1)
        return row, sent

    def test_mode_change_read_back_matches(self):
        row, sent = self.run_change("mode", {"onu_mode": "Bridging"}, [raw(), raw(mode="Bridging")])
        self.assertEqual(row["result"], "succeeded")
        self.assertEqual(sent[1], ("POST", f"/api/onu/update_onu_mode/{ONU}", {"onu_mode": "Bridging"}))

    def test_read_back_mismatch_is_flagged(self):
        row, _ = self.run_change("mode", {"onu_mode": "Bridging"}, [raw(), raw()])
        self.assertEqual(row["result"], "mismatch")

    def test_a_later_step_failing_says_what_was_done(self):
        fail = lambda path: smartolt.SmartOLTError("OLT wasn't able to process the command") if "wan_mode" in path else None
        with self.assertRaises(smartolt.SmartOLTError) as e:
            self.run_change("mode", {"vlan": 50, "wan_mode": "dhcp"}, [raw()], post=fail)
        self.assertIn("Already done: done", str(e.exception))
        self.assertEqual(self.audits[-1][1][1], "failed")

    def test_passwords_never_reach_the_audit_row(self):
        self.run_change("mode", {"wan_mode": "pppoe", "username": "JS98", "password": "Sup3rSecret"},
                        [raw(wan_mode="DHCP"), raw(username="JS98")])
        self.assertNotIn("Sup3rSecret", repr(self.audits))
        self.assertNotIn("secret-in-smartolt", repr(self.audits))

    def test_location_keeps_address_and_contact_out_of_the_audit(self):
        row, _ = self.run_change("location", {"contact": "0711111111", "address_or_comment": "Plot 9"},
                                 [raw(), raw(contact="0711111111", address="Plot 9")])
        self.assertEqual(row["result"], "succeeded")
        text = repr(self.audits)
        for private in ("0711111111", "Plot 9", "0700000001", "Plot 7"):
            self.assertNotIn(private, text)

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
            self.assertTrue(onu_config.enabled("location"))
        with mock.patch.dict(os.environ, {"SMARTOLT_WRITES_ENABLED": "false"}):
            with self.assertRaises(smartolt.WritesDisabled):
                onu_config.change(ONU, "resync", {}, 1)
        self.assertEqual(self.audits, [])

    def test_password_reveal_is_recorded_without_the_password(self):
        with mock.patch.object(smartolt, "call", return_value={"status": True, "onu_details": raw()}):
            self.assertEqual(onu_config.pppoe_password(ONU, 1), "secret-in-smartolt")
        self.assertEqual(self.audits[0][1][0], "view_password")
        self.assertNotIn("secret-in-smartolt", repr(self.audits))


class Endpoint(unittest.TestCase):
    def test_request_shape(self):
        api.ConfigRequest(change="port", port="eth_0/1", mode="access", vlan=10)
        api.ConfigRequest(change="vlans", vlans=[50, 51])
        for bad in ({"change": "restore"}, {"change": "port", "vlan": 5000}, {"change": "resync", "extra": 1},
                    {"change": "location", "odb_port": 0}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                api.ConfigRequest(**bad)

    def test_a_bad_fit_is_a_400(self):
        with mock.patch.object(onu_config, "change", side_effect=onu_config.ChangeError("no such port")):
            with self.assertRaises(HTTPException) as e:
                api.onu_change(ONU, api.ConfigRequest(change="resync"), current_user={"id": 1, "role": "admin"})
        self.assertEqual(e.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
