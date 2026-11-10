"""SmartOLT, Phase 6A: reading SmartOLT's answers, the client's error and
rate-limit handling, the poll schedule and the panel rules. No database and
no network: SmartOLT and the table are faked. The sample rows are made up but
shaped exactly like the real responses captured on 2026-11-10. Run:
  venv/bin/python -m unittest tests.test_smartolt -v
"""
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

from db import smartolt
from db.connection import EAT

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


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, bad_json=False):
        self.status_code, self._body, self.headers, self._bad = status_code, body, headers or {}, bad_json

    def json(self):
        if self._bad:
            raise ValueError("no JSON")
        return self._body


def ok(rows):
    return FakeResponse(200, {"status": True, "response_code": "success", "response": rows})


class Parsing(unittest.TestCase):
    def test_dbm(self):
        self.assertEqual(smartolt.parse_dbm("-17.62 dBm"), Decimal("-17.62"))
        self.assertEqual(smartolt.parse_dbm("-30 dBm"), Decimal("-30"))
        self.assertEqual(smartolt.parse_dbm("-8.1dBm"), Decimal("-8.1"))
        for blank in ("-", "", None, "Very good", "n/a"):
            self.assertIsNone(smartolt.parse_dbm(blank))

    def test_time_is_nairobi(self):
        t = smartolt.parse_time("2026-11-10 21:03:11.123456")
        self.assertEqual(t.utcoffset(), timedelta(hours=3))
        self.assertEqual(t.astimezone(timezone.utc).hour, 18)
        self.assertEqual(smartolt.parse_time("2026-11-10 22:14:57"), datetime(2026, 11, 10, 22, 14, 57, tzinfo=EAT))
        self.assertIsNone(smartolt.parse_time("yesterday"))
        self.assertIsNone(smartolt.parse_time(None))

    def test_status_rows_leave_address_and_contact_behind(self):
        row = smartolt.status_rows([item()])[0]
        self.assertNotIn("address", row)
        self.assertNotIn("contact", row)
        self.assertEqual((row["board"], row["port"], row["onu"]), (0, 3, 12))
        self.assertEqual(row["status"], "Online")

    def test_rows_without_an_id_are_skipped(self):
        self.assertEqual(smartolt.status_rows([item(unique_external_id=""), "junk", item()]).__len__(), 1)

    def test_signal_rows(self):
        row = smartolt.signal_rows([item(signal_1310="-", signal_1490="-", signal="-")])[0]
        self.assertIsNone(row["signal_1310_dbm"])
        self.assertIsNone(row["signal_quality"])
        row = smartolt.signal_rows([item(signal="Warning", signal_1310="-30.5 dBm")])[0]
        self.assertEqual((row["signal_quality"], row["signal_1310_dbm"]), ("Warning", Decimal("-30.5")))


@mock.patch.dict("os.environ", ENV)
class Client(unittest.TestCase):
    def test_sends_the_key_in_x_token_and_strips_the_trailing_slash(self):
        get = mock.Mock(return_value=ok([item()]))
        rows = smartolt.fetch("status", get=get)
        self.assertEqual(len(rows), 1)
        url = get.call_args.args[0]
        self.assertEqual(url, "https://example.smartolt.com/api/onu/get_onus_statuses")
        self.assertEqual(get.call_args.kwargs["headers"]["X-Token"], "not-a-real-key")

    def test_status_false_is_an_error(self):
        get = mock.Mock(return_value=FakeResponse(200, {"status": False, "error": "Invalid token"}))
        with self.assertRaisesRegex(smartolt.SmartOLTError, "Invalid token"):
            smartolt.fetch("status", get=get)

    def test_http_error_and_bad_json(self):
        with self.assertRaisesRegex(smartolt.SmartOLTError, "HTTP 500"):
            smartolt.fetch("signal", get=mock.Mock(return_value=FakeResponse(500)))
        with self.assertRaisesRegex(smartolt.SmartOLTError, "wasn't JSON"):
            smartolt.fetch("signal", get=mock.Mock(return_value=FakeResponse(200, bad_json=True)))

    def test_429_waits_retry_after_then_retries_once(self):
        get = mock.Mock(side_effect=[FakeResponse(429, headers={"Retry-After": "7"}), ok([])])
        sleep = mock.Mock()
        self.assertEqual(smartolt.fetch("status", get=get, sleep=sleep), [])
        sleep.assert_called_once_with(7)
        self.assertEqual(get.call_count, 2)

    def test_429_twice_gives_up_with_the_wait(self):
        get = mock.Mock(return_value=FakeResponse(429, headers={"Retry-After": "5"}))
        with self.assertRaises(smartolt.SmartOLTError) as e:
            smartolt.fetch("status", get=get, sleep=mock.Mock())
        self.assertEqual(e.exception.retry_after, 5)
        self.assertEqual(get.call_count, 2)

    def test_long_retry_after_isnt_waited_out(self):
        sleep = mock.Mock()
        get = mock.Mock(return_value=FakeResponse(429, headers={"Retry-After": "3600"}))
        with self.assertRaises(smartolt.SmartOLTError) as e:
            smartolt.fetch("status", get=get, sleep=sleep)
        sleep.assert_not_called()
        self.assertEqual(e.exception.retry_after, 3600)

    def test_not_configured(self):
        with mock.patch.dict("os.environ", {"SMARTOLT_API_KEY": ""}):
            with self.assertRaisesRegex(smartolt.SmartOLTError, "isn't configured"):
                smartolt.fetch("status", get=mock.Mock())


@mock.patch.dict("os.environ", ENV)
class Schedule(unittest.TestCase):
    def test_skips_smartolt_when_the_table_is_fresh(self):
        with mock.patch.object(smartolt, "last_polled", return_value=NOW - timedelta(minutes=2)), \
             mock.patch.object(smartolt, "fetch") as fetch, mock.patch.object(smartolt, "_jitter", return_value=0):
            wait = smartolt.poll_once("status", now=NOW)
        fetch.assert_not_called()
        self.assertEqual(wait, 240)

    def test_polls_and_saves_when_due(self):
        with mock.patch.object(smartolt, "last_polled", return_value=NOW - timedelta(minutes=21)), \
             mock.patch.object(smartolt, "fetch", return_value=[item()]), \
             mock.patch.object(smartolt, "save", return_value=1) as save, \
             mock.patch.object(smartolt, "_jitter", return_value=0):
            wait = smartolt.poll_once("signal", now=NOW)
        self.assertEqual(save.call_args.args[0], "signal")
        self.assertEqual(save.call_args.args[1][0]["signal_1490_dbm"], Decimal("-15.52"))
        self.assertEqual(wait, 1200)

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
             mock.patch.dict(smartolt._next_at, {"status": 0.0, "signal": 0.0}):
            smartolt.poll_if_due()
            smartolt.poll_if_due()      # both still running: nothing new starts
            release.set()
            for t in [t for t in threading.enumerate() if t.name.startswith("smartolt-")]:
                t.join(2)
        self.assertEqual(sorted(started), ["signal", "status"])

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


def row(id_, status="Online", changed=None, quality=None, s1310=None, s1490=None,
        status_at=NOW - timedelta(minutes=3), signal_at=NOW - timedelta(minutes=10)):
    return {"unique_external_id": id_, "sn": id_, "board": 0, "port": 1, "onu": 2, "name": f"name {id_}",
            "status": status, "last_status_change": changed,
            "signal_1310_dbm": Decimal(s1310) if s1310 else None, "signal_1490_dbm": Decimal(s1490) if s1490 else None,
            "signal_quality": quality, "status_polled_at": status_at, "signal_polled_at": signal_at}


@mock.patch.dict("os.environ", ENV)
class Panel(unittest.TestCase):
    def test_counts_down_list_and_weak_list(self):
        rows = [
            row("a"),
            row("b", "LOS", changed=NOW - timedelta(hours=5)),
            row("c", "Power fail", changed=NOW - timedelta(minutes=10)),
            row("d", quality="Warning", s1310="-30.5"),
            row("e", quality="Critical", s1310="-33.9"),
            row("f", quality="Warning", s1310="-31.5"),
            row("g", quality="Very good", s1310="-20"),
        ]
        p = smartolt.build_panel(rows, NOW)
        self.assertEqual(p["counts"], {"Online": 5, "LOS": 1, "Power fail": 1})
        self.assertEqual([o["id"] for o in p["down"]], ["c", "b"])          # most recent change first
        self.assertEqual(p["down"][0]["for_s"], 600)
        self.assertEqual(p["down"][0]["location"], "0/1/2")
        self.assertEqual([o["id"] for o in p["weak"]], ["e", "f", "d"])     # Critical, then weakest first
        self.assertFalse(p["status_stale"])
        self.assertFalse(p["signal_stale"])

    def test_stale_after_15_minutes_for_status_and_45_for_signal(self):
        p = smartolt.build_panel([row("a", status_at=NOW - timedelta(minutes=16),
                                      signal_at=NOW - timedelta(minutes=40))], NOW)
        self.assertTrue(p["status_stale"])
        self.assertFalse(p["signal_stale"])

    def test_empty_table(self):
        p = smartolt.build_panel([], NOW)
        self.assertEqual((p["total"], p["down"], p["weak"]), (0, [], []))
        self.assertTrue(p["status_stale"])
        self.assertIsNone(p["status_polled_at"])


if __name__ == "__main__":
    unittest.main()
