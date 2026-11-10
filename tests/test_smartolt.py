"""SmartOLT (Phase 6): reading SmartOLT's answers, scrubbing secrets, the
client's error and rate-limit handling, the poll schedule, the Configured
list, the live View reads and the authorize flow. No database and no network:
SmartOLT and the tables are faked. Sample rows are made up but shaped exactly
like the real responses captured on 2026-11-10/11. Run:
  venv/bin/python -m unittest tests.test_smartolt -v
"""
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

from pydantic import ValidationError

from db import smartolt
from db.connection import EAT
from routers import olt as olt_api

ENV = {"SMARTOLT_URL": "https://example.smartolt.com/", "SMARTOLT_API_KEY": "not-a-real-key"}
NOW = datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc)


def item(**overrides):
    base = {"unique_external_id": "HWTC00A0001", "sn": "HWTC00A0001", "olt_id": "1", "board": "0", "port": "3",
            "onu": "12", "zone_id": "21", "name": "Test_Customer01", "address": "somewhere",
            "contact": "0700000000", "odb_id": None, "status": "Online",
            "last_status_change": "2026-11-10 21:03:11.123456", "status_observed_at": "2026-11-10 22:14:57",
            "signal_1310": "-17.62 dBm", "signal": "Very good", "signal_1490": "-15.52 dBm"}
    base.update(overrides)
    return base


def detail_item(**overrides):
    base = item(signal_1310=-25.86, signal_1490=-24.08, olt_name="Test OLT", pon_type="gpon", gpon_channel="gpon",
                onu_type_name="HG8546M", zone_name="Test Zone", odb_name="", odb_port=None, mode="Routing",
                wan_mode="PPPoE", vlan="51", tr069="Enabled", administrative_status="Enabled",
                catv="Not supported by ONU-Type", voip_service="Disabled", authorization_date="2026-06-15 17:00:48",
                username="test-user", password="not-a-real-password", latitude="-1.0", longitude="37.0",
                service_ports=[{"service_port": "0", "vlan": "51", "upload_speed": "1G", "download_speed": "1G"},
                               {"service_port": "48", "vlan": "10", "upload_speed": "1G", "download_speed": "1G"}],
                ethernet_ports=[{"port": "eth_0/1", "admin_state": "Enabled", "mode": "LAN", "dhcp": "No control", "vlan": ""}],
                wifi_ports=[{"port": "wifi_0/1", "ssid": None, "password": "not-a-real-wifi-password"}],
                distance="550")
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, bad_json=False, content=b""):
        self.status_code, self._body, self.headers, self._bad, self.content = status_code, body, headers or {}, bad_json, content

    def json(self):
        if self._bad:
            raise ValueError("no JSON")
        return self._body


def ok(rows, key="response", **extra):
    return FakeResponse(200, {"status": True, "response_code": "success", key: rows, **extra})


class Parsing(unittest.TestCase):
    def test_dbm(self):
        self.assertEqual(smartolt.parse_dbm("-17.62 dBm"), Decimal("-17.62"))
        self.assertEqual(smartolt.parse_dbm("-30 dBm"), Decimal("-30"))
        self.assertEqual(smartolt.parse_dbm(-25.86), Decimal("-25.86"))    # the details call sends numbers
        for blank in ("-", "", None, "Very good", "n/a", True):
            self.assertIsNone(smartolt.parse_dbm(blank))

    def test_time_is_nairobi(self):
        t = smartolt.parse_time("2026-11-10 21:03:11.123456")
        self.assertEqual(t.utcoffset(), timedelta(hours=3))
        self.assertEqual(t.astimezone(timezone.utc).hour, 18)
        self.assertEqual(smartolt.parse_time("2026-11-10 22:14:57"), datetime(2026, 11, 10, 22, 14, 57, tzinfo=EAT))
        self.assertEqual(smartolt.parse_time("2026-11-07 17:51:22+03:00").astimezone(timezone.utc).hour, 14)
        self.assertIsNone(smartolt.parse_time("yesterday"))
        self.assertIsNone(smartolt.parse_time(None))

    def test_status_rows_leave_address_and_contact_behind(self):
        row = smartolt.status_rows([item()])[0]
        self.assertNotIn("address", row)
        self.assertNotIn("contact", row)
        self.assertEqual((row["board"], row["port"], row["onu"]), (0, 3, 12))

    def test_rows_without_an_id_are_skipped(self):
        self.assertEqual(len(smartolt.status_rows([item(unique_external_id=""), "junk", item()])), 1)

    def test_signal_rows(self):
        row = smartolt.signal_rows([item(signal_1310="-", signal_1490="-", signal="-")])[0]
        self.assertIsNone(row["signal_1310_dbm"])
        self.assertIsNone(row["signal_quality"])

    def test_details_row_keeps_only_list_fields(self):
        d = smartolt.details_rows([detail_item()])[0]["details"]
        self.assertEqual(d["vlans"], [51, 10])
        self.assertEqual(d["zone_name"], "Test Zone")
        self.assertIsNone(d["odb_name"])
        self.assertTrue(d["authorization_date"].endswith("+03:00"))
        text = repr(d)
        for secret in ("not-a-real-password", "test-user", "somewhere", "0700000000", "37.0"):
            self.assertNotIn(secret, text)


class Scrub(unittest.TestCase):
    def test_removes_every_password_at_any_depth(self):
        cleaned = smartolt.scrub(detail_item())
        text = repr(cleaned)
        self.assertNotIn("not-a-real-password", text)
        self.assertNotIn("not-a-real-wifi-password", text)
        self.assertNotIn("latitude", cleaned)
        self.assertEqual(cleaned["username"], "test-user")

    def test_view_details_never_carry_a_password(self):
        shaped = smartolt.shape_details(smartolt.scrub(detail_item()))
        self.assertNotIn("password", repr(shaped).lower().replace("pppoe_username", ""))
        self.assertEqual(shaped["pppoe_username"], "test-user")
        self.assertEqual(shaped["attached_vlans"], [51, 10])
        self.assertEqual(shaped["distance_m"], 550)
        self.assertEqual(shaped["signal_1490_dbm"], -24.08)


@mock.patch.dict("os.environ", ENV)
class Client(unittest.TestCase):
    def setUp(self):
        self.sleep = mock.patch.object(smartolt, "_sleep").start()
        self.addCleanup(mock.patch.stopall)

    def send(self, *responses):
        return mock.patch.object(smartolt, "_send", side_effect=list(responses)).start()

    def test_sends_the_key_in_x_token_and_strips_the_trailing_slash(self):
        send = self.send(ok([item()]))
        self.assertEqual(len(smartolt.fetch("status")), 1)
        method, url = send.call_args.args
        self.assertEqual((method, url), ("GET", "https://example.smartolt.com/api/onu/get_onus_statuses"))
        self.assertEqual(send.call_args.kwargs["headers"]["X-Token"], "not-a-real-key")

    def test_details_pages_until_the_last(self):
        send = self.send(ok([item()], key="onus", total_pages=2), ok([item(unique_external_id="X2")], key="onus", total_pages=2))
        self.assertEqual(len(smartolt.fetch("details")), 2)
        self.assertEqual([c.kwargs["params"]["page"] for c in send.call_args_list], [1, 2])

    def test_status_false_is_an_error(self):
        self.send(FakeResponse(200, {"status": False, "error": "Invalid token"}))
        with self.assertRaisesRegex(smartolt.SmartOLTError, "Invalid token"):
            smartolt.fetch("status")

    def test_http_error_and_bad_json(self):
        self.send(FakeResponse(500, bad_json=True))
        with self.assertRaisesRegex(smartolt.SmartOLTError, "HTTP 500"):
            smartolt.fetch("signal")
        self.send(FakeResponse(200, bad_json=True))
        with self.assertRaisesRegex(smartolt.SmartOLTError, "wasn't JSON"):
            smartolt.fetch("signal")

    def test_429_waits_retry_after_then_retries_once(self):
        send = self.send(FakeResponse(429, headers={"Retry-After": "7"}), ok([]))
        self.assertEqual(smartolt.fetch("status"), [])
        self.sleep.assert_called_once_with(7)
        self.assertEqual(send.call_count, 2)

    def test_429_twice_gives_up_with_the_wait(self):
        self.send(FakeResponse(429, headers={"Retry-After": "5"}), FakeResponse(429, headers={"Retry-After": "5"}))
        with self.assertRaises(smartolt.SmartOLTError) as e:
            smartolt.fetch("status")
        self.assertEqual(e.exception.retry_after, 5)

    def test_long_retry_after_isnt_waited_out(self):
        self.send(FakeResponse(429, headers={"Retry-After": "3600"}))
        with self.assertRaises(smartolt.SmartOLTError) as e:
            smartolt.fetch("status")
        self.sleep.assert_not_called()
        self.assertEqual(e.exception.retry_after, 3600)

    def test_image(self):
        self.send(FakeResponse(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG"))
        self.assertEqual(smartolt.call("GET", "/x", image=True), b"\x89PNG")

    def test_not_configured(self):
        with mock.patch.dict("os.environ", {"SMARTOLT_API_KEY": ""}):
            with self.assertRaisesRegex(smartolt.SmartOLTError, "isn't configured"):
                smartolt.fetch("status")


@mock.patch.dict("os.environ", ENV)
class Schedule(unittest.TestCase):
    def test_skips_smartolt_when_the_table_is_fresh(self):
        with mock.patch.object(smartolt, "last_polled", return_value=NOW - timedelta(minutes=2)), \
             mock.patch.object(smartolt, "fetch") as fetch, mock.patch.object(smartolt, "_jitter", return_value=0):
            wait = smartolt.poll_once("status", now=NOW)
        fetch.assert_not_called()
        self.assertEqual(wait, 240)

    def test_polls_and_saves_when_due(self):
        with mock.patch.object(smartolt, "last_polled", return_value=NOW - timedelta(minutes=31)), \
             mock.patch.object(smartolt, "fetch", return_value=[detail_item()]), \
             mock.patch.object(smartolt, "save", return_value=1) as save, \
             mock.patch.object(smartolt, "_jitter", return_value=0):
            wait = smartolt.poll_once("details", now=NOW)
        self.assertEqual(save.call_args.args[0], "details")
        self.assertEqual(save.call_args.args[1][0]["details"]["vlans"], [51, 10])
        self.assertEqual(wait, 1800)

    def test_poll_if_due_starts_each_kind_once_and_never_blocks(self):
        started = []
        release = threading.Event()

        def slow_run(kind):
            started.append(kind)
            release.wait(2)
            with smartolt._lock:
                smartolt._running.discard(kind)
                smartolt._next_at[kind] = float("inf")

        with mock.patch.object(smartolt, "_run", side_effect=slow_run), \
             mock.patch.dict(smartolt._next_at, {k: 0.0 for k in smartolt.KINDS}):
            smartolt.poll_if_due()
            smartolt.poll_if_due()      # all still running: nothing new starts
            release.set()
            for t in [t for t in threading.enumerate() if t.name.startswith("smartolt-")]:
                t.join(2)
        self.assertEqual(sorted(started), ["details", "signal", "status"])

    def test_a_failure_waits_before_the_next_try(self):
        with mock.patch.object(smartolt, "poll_once", side_effect=smartolt.SmartOLTError("down", retry_after=600)), \
             mock.patch.dict(smartolt._next_at, {"status": 0.0}), mock.patch("time.monotonic", return_value=1000.0):
            smartolt._running.add("status")
            smartolt._run("status")
            self.assertEqual(smartolt._next_at["status"], 1600.0)
        self.assertNotIn("status", smartolt._running)

    def test_does_nothing_without_a_key(self):
        with mock.patch.dict("os.environ", {"SMARTOLT_URL": ""}), mock.patch("threading.Thread") as thread:
            smartolt.poll_if_due()
        thread.assert_not_called()


def row(id_, status="Online", changed=None, quality=None, s1310=None, details=None,
        status_at=NOW - timedelta(minutes=3), signal_at=NOW - timedelta(minutes=10), details_at=NOW - timedelta(minutes=20)):
    return {"unique_external_id": id_, "sn": id_, "olt_id": 1, "board": 0, "port": 1, "onu": 2, "name": f"name {id_}",
            "status": status, "last_status_change": changed,
            "signal_1310_dbm": Decimal(s1310) if s1310 else None, "signal_1490_dbm": None,
            "signal_quality": quality, "details": details, "status_polled_at": status_at,
            "signal_polled_at": signal_at, "details_polled_at": details_at}


@mock.patch.dict("os.environ", ENV)
class ConfiguredList(unittest.TestCase):
    def test_counts_and_rows(self):
        rows = [
            row("a", details={"zone_name": "Z1", "vlans": [51], "authorization_date": "2026-11-01T10:00:00+03:00", "mode": "Routing"}),
            row("b", "LOS", changed=NOW - timedelta(hours=5), details={"authorization_date": "2026-11-09T10:00:00+03:00"}),
            row("c", quality="Critical", s1310="-33.9"),
            row("d", quality="Warning", s1310="-30.5"),
        ]
        p = smartolt.build_list(rows, NOW)
        self.assertEqual(p["counts"], {"Online": 3, "LOS": 1})
        self.assertEqual(p["weak"], 2)
        self.assertEqual([o["id"] for o in p["onus"]][:2], ["b", "a"])     # newest authorization first
        b = p["onus"][0]
        self.assertEqual((b["for_s"], b["location"]), (18000, "0/1/2"))
        self.assertEqual(p["onus"][1]["zone"], "Z1")
        self.assertFalse(p["status_stale"])

    def test_stale_times(self):
        p = smartolt.build_list([row("a", status_at=NOW - timedelta(minutes=16), signal_at=NOW - timedelta(minutes=40),
                                     details_at=None)], NOW)
        self.assertTrue(p["status_stale"])
        self.assertFalse(p["signal_stale"])
        self.assertTrue(p["details_stale"])

    def test_empty_table(self):
        p = smartolt.build_list([], NOW)
        self.assertEqual((p["total"], p["onus"]), (0, []))
        self.assertTrue(p["status_stale"])


class LiveStatus(unittest.TestCase):
    def test_shape_leaves_macs_out(self):
        j = {"Optical status": {"Rx optical power(dBm)": "-24.08", "Tx optical power(dBm)": "2.63", "Temperature(C)": "47",
                                "OLT Rx ONT optical power(dBm)": "-25.69"},
             "ONU details": {"Run state": "online", "ONT distance(m)": "550", "Last down cause": "LOSi/LOBi",
                             "ONT online duration": "3 day(s), 6 hour(s)"},
             "History": {"01": {"Auth at": "2026-10-27 06:00:00+03:00", "Offline at": "2026-10-29 16:00:00+03:00", "Cause": "LOSi"},
                         "02": {"Auth at": "2026-10-29 16:05:00+03:00", "Offline at": "", "Cause": "ONU is currently online"}},
             "ONU LAN Interfaces status": {"2": {"Type": "FE", "Speed": "100", "Link state": "up"},
                                           "1": {"Type": "GE", "Speed": "-", "Link state": "down"}},
             "MACs on OLT from this ONU": {"1": {"MAC": "aa:bb:cc:dd:ee:ff"}}}
        s = smartolt.shape_live_status(j)
        self.assertEqual(s["onu"]["distance_m"], "550")
        self.assertEqual(s["optical"]["olt_rx_dbm"], "-25.69")
        self.assertEqual([h["n"] for h in s["history"]], ["02", "01"])      # newest first
        self.assertEqual([p["port"] for p in s["lan_ports"]], ["1", "2"])
        self.assertNotIn("aa:bb", repr(s))

    def test_bad_ids_never_reach_smartolt(self):
        with mock.patch.object(smartolt, "call") as call:
            for bad in ("../x", "a/b", "", "x" * 80):
                with self.assertRaises(ValueError):
                    smartolt.onu_details(bad)
                with self.assertRaises(ValueError):
                    smartolt.onu_graph(bad, "traffic", "daily")
            with self.assertRaises(ValueError):
                smartolt.onu_graph("HWTC1", "running_config", "daily")
        call.assert_not_called()


REQUEST = {"olt_id": 1, "pon_type": "gpon", "sn": "HWTC00A0009", "board": 0, "port": 3, "onu_type": "HG8546M",
           "onu_mode": "Routing", "vlan": 51, "zone": "Test Zone", "odb": None, "name": "New_Customer",
           "address_or_comment": None, "onu_external_id": None, "upload_speed_profile_name": None,
           "download_speed_profile_name": None}


@mock.patch.dict("os.environ", ENV)
class Authorize(unittest.TestCase):
    def setUp(self):
        self.start = mock.patch.object(smartolt, "_audit_start", return_value=7).start()
        self.finish = mock.patch.object(smartolt, "_audit_finish").start()
        self.save = mock.patch.object(smartolt, "save").start()
        self.addCleanup(mock.patch.stopall)
        smartolt.forget("unconfigured")

    def test_refused_when_writes_are_off(self):
        with mock.patch.dict("os.environ", {"SMARTOLT_WRITES_ENABLED": "false"}), mock.patch.object(smartolt, "call") as call:
            with self.assertRaises(smartolt.WritesDisabled):
                smartolt.authorize(dict(REQUEST), user_id=1)
        call.assert_not_called()
        self.start.assert_not_called()

    def test_success_is_read_back_and_audited(self):
        got = detail_item(sn="HWTC00A0009", unique_external_id="HWTC00A0009", port="3", name="New_Customer",
                          zone_name="Test Zone", vlan="51")
        calls = []

        def fake_call(method, path, **kw):
            calls.append((method, path, kw))
            if method == "POST":
                return {"status": True, "response": "ONU configuration saved"}
            return {"status": True, "onus": [got]}

        with mock.patch.object(smartolt, "call", side_effect=fake_call):
            r = smartolt.authorize(dict(REQUEST), user_id=1)
        self.assertEqual(r["result"], "succeeded")
        method, path, kw = calls[0]
        self.assertEqual((method, path), ("POST", "/api/onu/authorize_onu"))
        self.assertEqual(kw["data"]["zone"], "Test Zone")
        self.assertNotIn("odb", kw["data"])                       # empty fields aren't sent
        self.assertEqual(calls[1][2]["params"]["sn"], "HWTC00A0009")
        args = self.finish.call_args.args
        self.assertEqual((args[0], args[1]), (7, "succeeded"))
        self.assertNotIn("not-a-real-password", repr(self.finish.call_args))
        self.assertEqual(self.save.call_count, 3)
        self.assertTrue(all(c.kwargs["full_list"] is False for c in self.save.call_args_list))

    def test_mismatch_is_flagged(self):
        got = detail_item(sn="HWTC00A0009", port="3", name="New_Customer", zone_name="Other Zone", vlan="51")
        with mock.patch.object(smartolt, "call", side_effect=[{"status": True, "response": "ONU configuration saved"},
                                                              {"status": True, "onus": [got]}]):
            r = smartolt.authorize(dict(REQUEST), user_id=1)
        self.assertEqual(r["result"], "mismatch")
        self.assertEqual([m["field"] for m in r["mismatches"]], ["zone"])

    def test_failure_is_audited_and_raised(self):
        with mock.patch.object(smartolt, "call", side_effect=smartolt.SmartOLTError("SmartOLT refused the request: PON port is full")):
            with self.assertRaisesRegex(smartolt.SmartOLTError, "PON port is full"):
                smartolt.authorize(dict(REQUEST), user_id=1)
        self.assertEqual(self.finish.call_args.args[1], "failed")
        self.assertNotIn(REQUEST["sn"], smartolt._authorizing)

    def test_one_at_a_time_per_serial(self):
        smartolt._authorizing.add(REQUEST["sn"])
        try:
            with mock.patch.object(smartolt, "call") as call:
                with self.assertRaisesRegex(smartolt.SmartOLTError, "already being authorized"):
                    smartolt.authorize(dict(REQUEST), user_id=1)
            call.assert_not_called()
        finally:
            smartolt._authorizing.discard(REQUEST["sn"])


class AuthorizeValidation(unittest.TestCase):
    def test_accepts_a_normal_request(self):
        olt_api.AuthorizeRequest(**{k: v for k, v in REQUEST.items() if v is not None})

    def test_rejects_bad_characters_and_extra_fields(self):
        for bad in ({"name": "Robert'); DROP TABLE"}, {"zone": "Zone<script>"}, {"sn": "HW TC"},
                    {"onu_mode": "Anything"}, {"vlan": 5000}, {"password": "x"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                olt_api.AuthorizeRequest(**{**{k: v for k, v in REQUEST.items() if v is not None}, **bad})


if __name__ == "__main__":
    unittest.main()
