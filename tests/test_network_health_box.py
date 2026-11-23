"""The Customer line (probe) card: the `box` / `boxspeed` records, their two
alerts, the 7-day card and the manual markers.
Run: venv/bin/python -m unittest tests.test_network_health_box -v
"""
import os
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest import mock

from network_health import boxline, config, feed, rules, service

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


def reading(minutes_ago, fra_big=0.0, gw_big=0.0, fra=0.0, gw=0.0, dev=DEV, fra_probes=True):
    """One `box` reading as the rule sees it: {ts, probes}."""
    probes = [{"name": "gw", "loss_pct": gw, "rtt_ms": 2.0, "dev": dev}, {"name": "gw-big", "loss_pct": gw_big, "rtt_ms": 2.5, "dev": dev},
              {"name": "core", "loss_pct": 0.0, "rtt_ms": 3.0, "dev": dev}]
    if fra_probes:
        probes += [{"name": "fra", "loss_pct": fra, "rtt_ms": 200.0, "dev": dev}, {"name": "fra-big", "loss_pct": fra_big, "rtt_ms": 201.0, "dev": dev}]
    return {"ts": NOW - timedelta(minutes=minutes_ago), "probes": probes}


def window(**kw):
    """Six readings (5 minutes apart: 30 minutes), every one with the same losses."""
    return [reading(m, **kw) for m in (25, 20, 15, 10, 5, 0)]


def box_probes(big_loss, small_loss=0.0):
    return feed.parse_record(record("box", box_body(big_loss=big_loss, small_loss=small_loss)))["probes"]


def run(checks):
    state, seen = rules.new_state(), []
    for check in checks:
        state, transition = rules.step(state, check)
        seen.append(transition)
    return state, seen


class Rules(unittest.TestCase):
    def verdict(self, readings):
        return rules.check_box_big_loss(readings)["verdict"]

    def test_frankfurt_big_packets_at_8_pct_over_30_minutes_is_bad(self):
        self.assertTrue(self.verdict(window(fra_big=8.0)))
        self.assertIsNone(self.verdict(window(fra_big=7.9)))             # in the hold band, not fine yet
        check = rules.check_box_big_loss(window(fra_big=12.0))
        self.assertEqual((check["device"], check["subject"], check["value"]), ("box", DEV, 12.0))
        self.assertEqual((check["facts"]["probe"], check["facts"]["minutes"], check["facts"]["fra_big"]), ("fra-big", 30, 12.0))

    def test_the_first_router_at_10_pct_is_bad_on_its_own(self):
        self.assertTrue(self.verdict(window(gw_big=10.0)))
        self.assertIsNone(self.verdict(window(gw_big=9.9)))
        self.assertEqual(rules.check_box_big_loss(window(gw_big=15.0))["facts"]["probe"], "gw-big")

    def test_it_is_the_average_not_one_reading(self):
        # One 45 % reading among six averages 7.5 %: under 8. Two make 15 %.
        one = window()
        one[5]["probes"][4]["loss_pct"] = 45.0
        self.assertIsNone(self.verdict(one))
        two = window()
        two[4]["probes"][4]["loss_pct"] = two[5]["probes"][4]["loss_pct"] = 45.0
        self.assertTrue(self.verdict(two))

    def test_it_clears_only_below_half_of_both_lines(self):
        self.assertFalse(self.verdict(window(fra_big=3.9, gw_big=4.9)))
        self.assertIsNone(self.verdict(window(fra_big=4.0, gw_big=0.0)))     # exactly half is still the hold band
        self.assertIsNone(self.verdict(window(fra_big=0.0, gw_big=5.0)))
        self.assertFalse(self.verdict(window()))

    def test_hysteresis_fires_holds_in_the_band_and_clears(self):
        steps = [window(fra_big=10.0), window(fra_big=6.0), window(fra_big=5.0), window(fra_big=3.0)]
        state, seen = run([rules.check_box_big_loss(w) for w in steps])
        self.assertEqual(seen, ["fire", None, None, "clear"])         # the band between the lines changes nothing
        self.assertFalse(state["is_active"])

    def test_a_quiet_band_never_fires(self):
        _, seen = run([rules.check_box_big_loss(window(fra_big=6.0)) for _ in range(4)])
        self.assertEqual(seen, [None] * 4)

    def test_too_few_readings_say_nothing(self):
        self.assertIsNone(self.verdict(window(fra_big=50.0)[:4]))
        self.assertTrue(self.verdict(window(fra_big=50.0)[1:]))               # five of the six is enough

    def test_a_dead_path_is_not_big_packet_loss(self):
        self.assertIsNone(self.verdict(window(fra_big=100.0, fra=100.0, gw_big=0.0)))        # fra dead, gw fine: nothing known to be bad
        self.assertTrue(self.verdict(window(fra_big=100.0, fra=100.0, gw_big=12.0)))         # gw still judged
        self.assertIsNone(self.verdict(window(gw_big=100.0, gw=100.0, fra_probes=False)))     # everything dead

    def test_no_frankfurt_probe_judges_the_router_alone(self):
        self.assertTrue(self.verdict(window(gw_big=12.0, fra_probes=False)))
        self.assertFalse(self.verdict(window(fra_probes=False)))
        self.assertIsNone(rules.check_box_big_loss([]))

    def speed(self, cf, plan=15.0, dev=DEV):
        return {"dev": dev, "plan_mbps": plan, "cf_mbps": cf, "fra_mbps": 0.1}

    def test_slow_is_under_30_pct_of_plan_three_readings_in_a_row(self):
        self.assertEqual(run([rules.check_box_slow(self.speed(4.0)) for _ in range(3)])[1], [None, None, "fire"])
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


def box_row(minutes_ago, **losses):
    return {"ts": NOW - timedelta(minutes=minutes_ago), "kind": "box", "probes": reading(minutes_ago, **losses)["probes"]}


def speed_row(minutes_ago, cf, plan=15.0):
    return {"ts": NOW - timedelta(minutes=minutes_ago), "kind": "boxspeed",
            "speed": {"dev": DEV, "plan_mbps": plan, "cf_mbps": cf, "fra_mbps": 0.1}}


@mock.patch.dict(os.environ, {"NH_SMS_MODE": "live", "NH_ALERT_PHONES": "0712000000"})
class Flow(unittest.TestCase):
    def evaluate(self, fake, new):
        with mock.patch.object(service, "store", fake), mock.patch.object(service, "send_in_background") as send:
            service.evaluate(new)
        return send.call_args.args[0] if send.called else []

    BAD = (20, 15, 10, 5, 0)

    @staticmethod
    def advance(fake, minutes):
        """Time passes with the feed and the CCR2116 still reporting (so no silence alert)."""
        fake.now = NOW + timedelta(minutes=minutes)
        fake.last_post_at = fake.now
        fake.last_received = {"probe": fake.now}

    def test_it_texts_once_when_the_30_minute_average_crosses_and_again_when_it_clears(self):
        rows = [box_row(m, fra_big=12.0) for m in self.BAD]
        fake = FakeStore(rows)
        sent = self.evaluate(fake, rows)
        # Five readings of the six are enough to judge: it fires at the fifth, now.
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT Customer line: Customer path losing big packets (CRS-OLT link?): 12% big-ping "
                          "loss to Frankfurt (30min avg), Kwa Mlima. Since 21:30"])
        self.assertEqual(len(fake.incidents), 1)
        self.advance(fake, 26)
        good = [box_row(-m) for m in (5, 10, 15, 20, 25)]
        fake.rows += good
        sent = self.evaluate(fake, good)
        self.assertTrue(any("back to normal" in m for _, m in sent), sent)
        self.assertIsNotNone(next(iter(fake.incidents.values()))["ended_at"])

    def test_the_first_router_alone_can_fire_it(self):
        rows = [box_row(m, gw_big=15.0) for m in self.BAD]
        sent = self.evaluate(FakeStore(rows), rows)
        self.assertIn("to first router", sent[0][1])
        self.assertIn("15%", sent[0][1])

    def test_one_bad_reading_in_the_window_does_not_text(self):
        rows = [box_row(m) for m in (25, 20, 15, 10, 5)] + [box_row(0, fra_big=45.0)]   # 45 over six readings is 7.5 %
        self.assertEqual(self.evaluate(FakeStore(rows), rows), [])

    def test_the_band_between_the_lines_holds_the_incident_open(self):
        rows = [box_row(m, fra_big=12.0) for m in self.BAD]
        fake = FakeStore(rows)
        self.evaluate(fake, rows)
        self.advance(fake, 31)
        middling = [box_row(-m, fra_big=5.0) for m in (5, 10, 15, 20, 25, 30)]      # 5 % is over half of 8, under 8
        fake.rows += middling
        self.assertEqual(self.evaluate(fake, middling), [])
        self.assertIsNone(next(iter(fake.incidents.values()))["ended_at"])

    def test_a_slow_line_texts_after_three_runs(self):
        rows = [speed_row(m, cf=1.9) for m in (60, 30, 0)]
        sent = self.evaluate(FakeStore(rows), rows)
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT Customer line: Customer line slow: 1.9Mbps of 15Mbps plan at Kwa Mlima. "
                          "Since 21:30"])

    def test_log_only_mode_never_sends_and_a_backfill_never_texts(self):
        rows = [box_row(m, fra_big=12.0) for m in (320, 315, 310, 305, 300)]
        fake = FakeStore(rows)
        self.assertEqual(self.evaluate(fake, rows), [])
        self.assertEqual(fake.alerts[0]["reason"], "old reading (backfill)")
        with mock.patch.dict(os.environ, {"NH_SMS_MODE": "log_only"}):
            fresh = [box_row(m, fra_big=12.0) for m in self.BAD]
            fake = FakeStore(fresh)
            self.assertEqual(self.evaluate(fake, fresh), [])
            self.assertEqual(fake.alerts[0]["reason"], "log-only mode")


class FakeBoxStore:
    """store, for service.box_card: just the card's reads."""

    def __init__(self, devs=(DEV,), speed=(), markers=(), loss=None):
        self.devs, self.speeds, self.markers = list(devs), list(speed), list(markers)
        self.loss = loss if loss is not None else [
            {"t": NOW - timedelta(minutes=m), "name": name, "loss": value, "rtt": 2.0, "n": 1}
            for m in (50, 35, 20, 5) for name, value in (("fra-big", 10.0), ("fra", 0.0), ("gw-big", 20.0), ("gw", 0.0),
                                                         ("core-big", 5.0), ("core", 0.0), ("airtel-big", 0.0), ("airtel", 0.0))]
        self.asked = []

    def now_utc(self): return NOW

    def transaction(self):
        return nullcontext(None)

    def box_devs(self, cur, since): return self.devs
    def box_markers(self, cur): return self.markers
    def box_loss_series(self, cur, dev, since, bucket):
        self.asked.append((dev, since, bucket))
        return self.loss
    def box_speeds(self, cur, dev, since): return self.speeds
    def box_latest(self, cur, dev): return NOW, [{"name": "gw", "loss_pct": 0.0, "rtt_ms": 2.3}]


class Card(unittest.TestCase):
    def card(self, **kwargs):
        store = FakeBoxStore(**kwargs)
        with mock.patch.object(service, "store", store):
            return service.box_card(), store

    def run_at(self, minutes_ago, cf, fra=0.1, plan=15.0):
        return {"t": NOW - timedelta(minutes=minutes_ago), "plan_mbps": plan, "cf_mbps": cf, "fra_mbps": fra}

    def test_the_card_covers_seven_days_and_names_the_line(self):
        result, store = self.card(speed=[self.run_at(0, 7.5)])
        self.assertEqual((result["dev"], result["label"]), (DEV, "Kwa Mlima"))
        # One extra hour is read back, so the first points already have a rolling average.
        self.assertEqual(store.asked[0], (DEV, NOW - timedelta(days=7, hours=1), 300))
        self.assertEqual(result["speed_now"]["pct_of_plan"], 50.0)
        self.assertEqual(result["latest"]["probes"]["gw"], {"loss": 0.0, "rtt": 2.3})
        self.assertEqual((result["primary"], result["hops"]), ("fra", ["gw", "core", "airtel"]))
        self.assertEqual(result["thresholds"], {"fra_big_pct": 8, "gw_big_pct": 10, "clear_pct": 50, "window_minutes": 30,
                                                "slow_pct": 30, "slow_in_a_row": 3})

    def test_each_probe_has_its_raw_readings_and_a_rolling_hour_average(self):
        result, _ = self.card(loss=[{"t": NOW - timedelta(minutes=m), "name": "fra-big", "loss": v, "rtt": 1.0, "n": 1}
                                    for m, v in ((70, 40.0), (55, 0.0), (40, 10.0), (25, 20.0), (10, 30.0))])
        line = result["loss"]
        self.assertEqual(line["t"], [int((NOW - timedelta(minutes=m)).timestamp()) for m in (70, 55, 40, 25, 10)])
        probe = line["probes"]["fra-big"]
        self.assertEqual(probe["raw"], [40.0, 0.0, 10.0, 20.0, 30.0])
        # Trailing 60 minutes: 40 | 40,0 | 40,0,10 | 40,0,10,20 -> the 70-min reading has left by 10 min ago.
        self.assertEqual(probe["avg"], [40.0, 20.0, 16.7, 17.5, 15.0])

    def test_the_headline_is_the_last_hour_with_frankfurt_first(self):
        result, _ = self.card()
        head = result["headline"]
        self.assertEqual((head["hours"], head["readings"]), (1, 4))               # 50, 35, 20 and 5 minutes ago
        self.assertEqual(head["fra_big"], 10.0)
        self.assertEqual(head["hops_big"], {"gw": 20.0, "core": 5.0, "airtel": 0.0})
        self.assertEqual(head["fra"], 0.0)

    def test_no_readings_yet_is_an_empty_card_not_an_error(self):
        result, _ = self.card(devs=[])
        self.assertIsNone(result["dev"])
        self.assertEqual((result["loss"], result["speed_now"], result["latest"], result["headline"]),
                         ({"t": [], "probes": {}}, None, None, None))
        self.assertEqual(result["speed"]["t"], [])

    def test_speed_is_raw_runs_with_a_rolling_hour_average_and_the_plan_as_it_was(self):
        result, _ = self.card(speed=[self.run_at(90, 2.0, plan=10.0), self.run_at(60, 4.0), self.run_at(30, 6.0), self.run_at(0, 8.0)])
        speed = result["speed"]
        self.assertEqual(speed["cf"]["raw"], [2.0, 4.0, 6.0, 8.0])
        self.assertEqual(speed["cf"]["avg"], [2.0, 3.0, 5.0, 7.0])
        self.assertEqual(speed["plan"], [10.0, 15.0, 15.0, 15.0])

    def test_percent_of_plan_is_unknown_without_a_cloudflare_number_or_plan(self):
        for run in (self.run_at(0, None), self.run_at(0, 3.0, plan=None)):
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


class Rolling(unittest.TestCase):
    def test_a_trailing_window_leaves_out_none_and_never_counts_it_as_zero(self):
        times = [0, 600, 1200, 1800, 2400]                                # seconds
        self.assertEqual(boxline.rolling_mean(times, [10.0, None, 30.0, None, None], 1800), [10.0, 10.0, 20.0, 30.0, 30.0])
        self.assertEqual(boxline.rolling_mean(times, [None] * 5, 1800), [None] * 5)

    def test_the_window_is_the_last_n_seconds_exclusive_of_the_oldest_edge(self):
        self.assertEqual(boxline.rolling_mean([0, 300, 600], [3.0, 6.0, 9.0], 600), [3.0, 4.5, 7.5])

    def test_works_on_datetimes(self):
        times = [NOW - timedelta(minutes=10), NOW]
        self.assertEqual(boxline.rolling_mean(times, [4.0, 8.0], 3600), [4.0, 6.0])


if __name__ == "__main__":
    unittest.main()
