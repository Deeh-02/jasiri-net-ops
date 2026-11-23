"""The Customer line (probe) card: the `box` / `boxspeed` records, their two
alerts, the 7-day card and the manual markers.
Run: venv/bin/python -m unittest tests.test_network_health_box -v
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from network_health import config, feed, rules, service

from tests.test_network_health_service import NOW, FakeStore

DEV = "home-kwamlima"


def box_body(big_loss=0.0, small_loss=0.0, dev=DEV, extra=()):
    return {"dev": dev, "probes": [
        {"name": "gw", "rtt_ms": 2.35, "jitter_ms": None, "loss_pct": small_loss},
        {"name": "gw-big", "rtt_ms": 2.71, "loss_pct": big_loss},
        {"name": "core", "rtt_ms": 3.1, "loss_pct": 0.0}, *extra]}


def record(kind, body, ts="2026-11-23T10:00:00+03:00", key="k1"):
    return {"key": key, "ts": ts, "from_ip": "102.0.27.188", "kind": kind, kind: body, "raw": "x"}


class Parse(unittest.TestCase):
    def test_box_keeps_every_probe_with_its_dev(self):
        parsed = feed.parse_record(record("box", box_body(big_loss=15.0)))
        self.assertEqual(parsed["kind"], "box")
        self.assertEqual({p["name"]: p["loss_pct"] for p in parsed["probes"]}, {"gw": 0.0, "gw-big": 15.0, "core": 0.0})
        self.assertTrue(all(p["dev"] == DEV for p in parsed["probes"]))
        self.assertIsNone(parsed["speed"])

    def test_an_outage_reading_keeps_its_null_round_trip(self):
        body = {"dev": DEV, "probes": [{"name": "gw", "rtt_ms": None, "loss_pct": 100}]}
        probe = feed.parse_record(record("box", body))["probes"][0]
        self.assertEqual((probe["loss_pct"], probe["rtt_ms"]), (100, None))

    def test_boxspeed(self):
        body = {"dev": DEV, "plan_mbps": 15, "cf_mbps": 7.21, "fra_mbps": 0.13}
        parsed = feed.parse_record(record("boxspeed", body))
        self.assertEqual(parsed["speed"], {"dev": DEV, "plan_mbps": 15.0, "cf_mbps": 7.21, "fra_mbps": 0.13})
        self.assertIsNone(parsed["probes"])
        parsed = feed.parse_record(record("boxspeed", {"dev": DEV, "plan_mbps": 15, "cf_mbps": -1, "fra_mbps": "slow"}))
        self.assertIsNone(parsed["speed"]["cf_mbps"])
        self.assertIsNone(parsed["speed"]["fra_mbps"])

    def test_fra_before_the_probe_fix_is_dropped_and_after_is_kept(self):
        extra = [{"name": "fra", "rtt_ms": None, "loss_pct": 100}, {"name": "fra-big", "rtt_ms": None, "loss_pct": 100}]
        before = feed.parse_record(record("box", box_body(extra=extra), ts="2026-11-23T03:29:00+03:00"))
        self.assertEqual([p["name"] for p in before["probes"]], ["gw", "gw-big", "core"])
        after = feed.parse_record(record("box", box_body(extra=extra), ts="2026-11-23T03:30:00+03:00"))
        self.assertEqual([p["name"] for p in after["probes"]], ["gw", "gw-big", "core", "fra", "fra-big"])

    def test_unplaceable_records_are_skipped(self):
        for bad in (record("box", {"probes": box_body()["probes"]}), record("box", {"dev": DEV, "probes": []}),
                    record("box", {"dev": DEV, "probes": [{"name": "fra", "loss_pct": 100}]}, ts="2026-11-22T10:00:00+03:00"),
                    record("box", None), record("boxspeed", {"plan_mbps": 15}), record("boxspeed", None)):
            with self.subTest(bad=bad), self.assertRaises(feed.SkipRecord):
                feed.parse_record(bad)


def box_probes(big_loss, small_loss=0.0):
    return feed.parse_record(record("box", box_body(big_loss=big_loss, small_loss=small_loss)))["probes"]


def run(checks):
    state, seen = rules.new_state(), []
    for check in checks:
        state, transition = rules.step(state, check)
        seen.append(transition)
    return seen


class Rules(unittest.TestCase):
    def test_big_packet_loss_of_10_three_readings_in_a_row_fires(self):
        checks = [rules.check_box_big_loss(box_probes(loss)) for loss in (10.0, 15.0, 10.0)]
        self.assertEqual(run(checks), [None, None, "fire"])
        self.assertEqual(checks[0]["device"], "box")
        self.assertEqual(checks[0]["subject"], DEV)

    def test_under_10_is_fine_and_a_good_reading_resets_the_count(self):
        self.assertFalse(rules.check_box_big_loss(box_probes(9.9))["verdict"])
        checks = [rules.check_box_big_loss(box_probes(loss)) for loss in (15.0, 15.0, 0.0, 15.0, 15.0)]
        self.assertNotIn("fire", run(checks))

    def test_a_dead_line_is_not_big_packet_loss(self):
        self.assertIsNone(rules.check_box_big_loss(box_probes(100.0, small_loss=100.0))["verdict"])

    def test_no_gw_big_probe_says_nothing(self):
        probes = [p for p in box_probes(50.0) if p["name"] != "gw-big"]
        self.assertIsNone(rules.check_box_big_loss(probes)["verdict"])
        self.assertIsNone(rules.check_box_big_loss([]))

    def speed(self, cf, plan=15.0, dev=DEV):
        return {"dev": dev, "plan_mbps": plan, "cf_mbps": cf, "fra_mbps": 0.1}

    def test_slow_is_under_30_pct_of_plan_three_readings_in_a_row(self):
        self.assertEqual(run([rules.check_box_slow(self.speed(4.0)) for _ in range(3)]), [None, None, "fire"])
        self.assertFalse(rules.check_box_slow(self.speed(4.5))["verdict"])        # exactly 30 % is fine
        self.assertTrue(rules.check_box_slow(self.speed(4.49))["verdict"])

    def test_slow_says_nothing_without_a_number_or_a_plan(self):
        self.assertIsNone(rules.check_box_slow(self.speed(None))["verdict"])
        self.assertIsNone(rules.check_box_slow(self.speed(3.0, plan=None))["verdict"])
        self.assertIsNone(rules.check_box_slow(self.speed(3.0, plan=0))["verdict"])
        self.assertIsNone(rules.check_box_slow({"cf_mbps": 1, "plan_mbps": 15}))

    def test_both_rules_have_labels_and_text_sms(self):
        for rule in rules.BOX_RULES:
            self.assertIn(rule, rules.RULE_LABELS)
            self.assertIn(rule, config.SMS_RULES)
            self.assertIn(rule, rules.INSIDE_RULES)
        self.assertEqual(rules.RULE_LABELS["BOX_BIG_LOSS"], "Customer path losing big packets (CRS-OLT link?)")
        self.assertEqual(rules.RULE_LABELS["BOX_SLOW"], "Customer line slow")


def box_row(minutes_ago, big_loss=0.0):
    return {"ts": NOW - timedelta(minutes=minutes_ago), "kind": "box", "probes": box_probes(big_loss)}


def speed_row(minutes_ago, cf, plan=15.0):
    return {"ts": NOW - timedelta(minutes=minutes_ago), "kind": "boxspeed",
            "speed": {"dev": DEV, "plan_mbps": plan, "cf_mbps": cf, "fra_mbps": 0.1}}


@mock.patch.dict(os.environ, {"NH_SMS_MODE": "live", "NH_ALERT_PHONES": "0712000000"})
class Flow(unittest.TestCase):
    def evaluate(self, fake, new):
        with mock.patch.object(service, "store", fake), mock.patch.object(service, "send_in_background") as send:
            service.evaluate(new)
        return send.call_args.args[0] if send.called else []

    def test_big_packet_loss_texts_once_when_it_fires_and_again_when_it_clears(self):
        rows = [box_row(m, big_loss=15.0) for m in (10, 5, 0)]
        fake = FakeStore(rows)
        sent = self.evaluate(fake, rows)
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT Customer line: Customer path losing big packets (CRS-OLT link?): 15% of "
                          "1400-byte pings lost at Kwa Mlima. Since 21:30"])
        fake.now = NOW + timedelta(minutes=15)
        good = [{**box_row(-m), "probes": box_probes(0.0)} for m in (5, 10, 15)]
        fake.rows += good
        sent = self.evaluate(fake, good)
        self.assertTrue(any("back to normal" in m for _, m in sent), sent)

    def test_two_readings_are_not_enough(self):
        rows = [box_row(m, big_loss=20.0) for m in (5, 0)]
        self.assertEqual(self.evaluate(FakeStore(rows), rows), [])

    def test_a_slow_line_texts_after_three_runs(self):
        rows = [speed_row(m, cf=1.9) for m in (60, 30, 0)]
        sent = self.evaluate(FakeStore(rows), rows)
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT Customer line: Customer line slow: 1.9Mbps of 15Mbps plan at Kwa Mlima. "
                          "Since 21:30"])

    def test_log_only_mode_never_sends_and_a_backfill_never_texts(self):
        rows = [box_row(m, big_loss=15.0) for m in (310, 305, 300)]
        fake = FakeStore(rows)
        self.assertEqual(self.evaluate(fake, rows), [])
        self.assertEqual(fake.alerts[0]["reason"], "old reading (backfill)")
        with mock.patch.dict(os.environ, {"NH_SMS_MODE": "log_only"}):
            fresh = [box_row(m, big_loss=15.0) for m in (10, 5, 0)]
            fake = FakeStore(fresh)
            self.assertEqual(self.evaluate(fake, fresh), [])
            self.assertEqual(fake.alerts[0]["reason"], "log-only mode")


class FakeBoxStore:
    """store, for service.box_card: just the card's reads."""

    def __init__(self, devs=(DEV,), speed=(), markers=()):
        self.devs, self.speeds, self.markers = list(devs), list(speed), list(markers)
        self.asked = []

    def now_utc(self): return NOW

    def transaction(self):
        from contextlib import nullcontext
        return nullcontext(None)

    def box_devs(self, cur, since): return self.devs
    def box_markers(self, cur): return self.markers
    def box_loss_series(self, cur, dev, since, bucket):
        self.asked.append((dev, since, bucket))
        return [{"t": NOW, "name": "gw-big", "loss": 15.0, "rtt": 2.7, "n": 6}]
    def box_speeds(self, cur, dev, since): return self.speeds
    def box_latest(self, cur, dev): return NOW, [{"name": "gw", "loss_pct": 0.0, "rtt_ms": 2.3}]


class Card(unittest.TestCase):
    def card(self, **kwargs):
        store = FakeBoxStore(**kwargs)
        with mock.patch.object(service, "store", store):
            return service.box_card(), store

    def test_the_card_covers_seven_days_and_names_the_line(self):
        result, store = self.card(speed=[{"t": NOW, "plan_mbps": 15.0, "cf_mbps": 7.5, "fra_mbps": 0.1}])
        self.assertEqual((result["dev"], result["label"]), (DEV, "Kwa Mlima"))
        self.assertEqual(store.asked[0], (DEV, NOW - timedelta(days=7), 1800))
        self.assertEqual(result["speed_now"]["pct_of_plan"], 50.0)
        self.assertEqual(result["latest"]["probes"]["gw"], {"loss": 0.0, "rtt": 2.3})
        self.assertEqual(result["thresholds"], {"big_loss_pct": 10, "big_loss_in_a_row": 3, "slow_pct": 30,
                                                "slow_in_a_row": 3})

    def test_no_readings_yet_is_an_empty_card_not_an_error(self):
        result, _ = self.card(devs=[])
        self.assertIsNone(result["dev"])
        self.assertEqual((result["loss"], result["speed"], result["speed_now"], result["latest"]), ([], [], None, None))

    def test_percent_of_plan_is_unknown_without_a_cloudflare_number_or_plan(self):
        for run in ({"t": NOW, "plan_mbps": 15.0, "cf_mbps": None, "fra_mbps": None},
                    {"t": NOW, "plan_mbps": None, "cf_mbps": 3.0, "fra_mbps": None}):
            result, _ = self.card(speed=[run])
            self.assertIsNone(result["speed_now"]["pct_of_plan"])

    def test_markers_come_with_the_card(self):
        marker = {"id": "ab12", "at": NOW, "label": "CRS-OLT DAC swapped"}
        result, _ = self.card(markers=[marker])
        self.assertEqual(result["markers"], [marker])

    def test_an_unknown_dev_falls_back_to_the_newest_reporter(self):
        store = FakeBoxStore()
        with mock.patch.object(service, "store", store):
            self.assertEqual(service.box_card("nobody")["dev"], DEV)


class Markers(unittest.TestCase):
    """The marker routes (an admin adds and removes them), with store replaced."""

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.auth import get_current_user
        from network_health import router
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 7, "role": "x"}
        return TestClient(app)

    def post(self, body, is_admin=True, saved="ab12cd34ef56"):
        from network_health import router
        with mock.patch.object(router, "user_has_permission", return_value=is_admin), \
                mock.patch.object(router.store, "save_box_marker", return_value=saved) as save:
            response = self.client().post("/network/health/box/markers", json=body)
        return response, save

    GOOD = {"at": "2026-11-23T10:00:00+03:00", "label": "  CRS-OLT   DAC swapped "}

    def test_an_admin_adds_a_marker_with_a_tidy_label(self):
        response, save = self.post(self.GOOD)
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True, "id": "ab12cd34ef56"}))
        at, label, user = save.call_args.args
        self.assertEqual((at, label, user), (datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc), "CRS-OLT DAC swapped", 7))

    def test_a_marker_needs_admin(self):
        response, save = self.post(self.GOOD, is_admin=False)
        self.assertEqual(response.status_code, 403)
        save.assert_not_called()

    def test_bad_markers_are_refused(self):
        for body in ({"at": "2026-11-23T10:00:00", "label": "no zone"}, {"at": "2026-11-23T10:00:00+03:00", "label": "   "},
                     {"at": "2026-11-23T10:00:00+03:00", "label": "x" * 81}, {"at": "yesterday", "label": "x"},
                     {"at": "2026-11-23T10:00:00+03:00", "label": "x", "extra": 1}):
            with self.subTest(body=body):
                response, save = self.post(body)
                self.assertEqual(response.status_code, 422)
                save.assert_not_called()

    def test_too_many_markers_is_409(self):
        response, _ = self.post(self.GOOD, saved=None)
        self.assertEqual(response.status_code, 409)

    def test_delete(self):
        from network_health import router
        for found, code in ((True, 200), (False, 404)):
            with mock.patch.object(router, "user_has_permission", return_value=True), \
                    mock.patch.object(router.store, "delete_box_marker", return_value=found):
                self.assertEqual(self.client().delete("/network/health/box/markers?id=ab12").status_code, code)
        with mock.patch.object(router, "user_has_permission", return_value=True):
            self.assertEqual(self.client().delete("/network/health/box/markers?id=../x").status_code, 422)


if __name__ == "__main__":
    unittest.main()
