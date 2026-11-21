"""Network Health's flow end to end, with the database replaced by an
in-memory fake: readings in -> incidents opened/closed -> SMS rows logged.
Run: venv/bin/python -m unittest tests.test_network_health_service -v
"""
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from network_health import service

NOW = datetime(2026, 11, 20, 18, 30, tzinfo=timezone.utc)


class FakeStore:
    """Just enough of network_health.store, in memory."""

    def __init__(self, readings=(), last_post_at=NOW, last_received=None):
        self.rows = list(readings)
        self.states, self.incidents, self.alerts = {}, {}, []
        self.last_post_at = last_post_at
        self.last_received = last_received or {"probe": NOW}
        self.now = NOW

    @contextmanager
    def transaction(self):
        yield None

    def now_utc(self):
        return self.now

    def lock_evaluation(self, cur): pass
    def load_states(self, cur): return {k: dict(v) for k, v in self.states.items()}
    def save_state(self, cur, key, state): self.states[key] = dict(state)
    def settings(self, cur): return {"uplink_capacity_mbps": 350.0, "provider_switched_at": None}
    def latency_baseline(self, cur, probe, since): return {}, None
    def optic_baseline(self, cur, since, until): return None, 0
    def night_speed_baseline(self, cur, since, until): return None, 0
    def feed(self, cur): return {"last_post_at": self.last_post_at}
    def last_received_at(self, cur, kind): return self.last_received.get(kind)
    def previous_reading(self, cur, kind, before): return None

    def readings(self, cur, kinds, since, until):
        return [r for r in self.rows if r["kind"] in kinds and since < r["ts"] <= until]

    def olt_readings(self, cur, since, until):
        return [{"ts": r["ts"], "olt": r["olt"]} for r in self.rows if r["kind"] == "olt" and since < r["ts"] <= until]

    def last_olt_success_received(self, cur): return None

    def area_readings(self, cur, since, until):
        return [{"ts": r["ts"], "area": r["area"]} for r in self.rows if r["kind"] == "area" and since < r["ts"] <= until]

    def area_baselines(self, cur, since, until):
        return {5: {hour: 12.0 for hour in range(24)}}, {5: NOW - timedelta(days=3)}

    def area_names(self, cur): return {5: "Njeri House"}

    def lanlink_readings(self, cur, since, until):
        return [{"ts": r["ts"], "lanlink": r["lanlink"]} for r in self.rows
                if r["kind"] == "lanlink" and since < r["ts"] <= until]

    def cleared_since(self, cur, key, since):
        return any(i["key"] == key and i["ended_at"] and i["ended_at"] >= since for i in self.incidents.values())

    def open_incident(self, cur, check, at, is_flapping):
        incident_id = len(self.incidents) + 1
        self.incidents[incident_id] = {"key": (check["device"], check["rule"], check["subject"]),
                                       "severity": check["severity"], "started_at": at, "ended_at": None,
                                       "is_flapping": is_flapping, "fire_sms_sent": False}
        return incident_id

    def update_incident(self, cur, incident_id, check, is_lower_worse): pass

    def raise_incident_severity(self, cur, incident_id, severity):
        self.incidents[incident_id]["severity"] = severity

    def incident_fire_sent(self, cur, incident_id):
        incident = self.incidents[incident_id]
        return incident["fire_sms_sent"], incident["is_flapping"]

    def close_incident(self, cur, incident_id, at):
        incident = self.incidents[incident_id]
        incident["ended_at"] = at
        return incident

    def mark_fire_sms_sent(self, cur, ids):
        for incident_id in ids:
            self.incidents[incident_id]["fire_sms_sent"] = True

    def alert_context(self, cur, now):
        return {"sent_today": 0, "cap_notice_sent": False, "recent_fires": {}}

    def log_alerts(self, cur, rows, recipient_count):
        self.alerts += rows
        return list(range(len(self.alerts) - len(rows), len(self.alerts)))


def probe_row(minutes_ago, loss=0, down=100):
    return {"ts": NOW - timedelta(minutes=minutes_ago), "kind": "probe",
            "load": {"down_mbps": down, "up_mbps": 10, "cpu_pct": 5},
            "probes": [{"name": "p-gw", "rtt_ms": None if loss >= 100 else 2.0, "jitter_ms": 0.2, "loss_pct": loss},
                       {"name": "p-cf", "rtt_ms": 20.0, "jitter_ms": 1.0, "loss_pct": 0}]}


def olt_row(minutes_ago, uptime, rx, crc):
    return {"ts": NOW - timedelta(minutes=minutes_ago), "kind": "olt",
            "olt": {"uptime_s": uptime, "up_rx": rx, "up_crc": crc}}


def link_row(seconds_ago, state, interface="sfp-sfpplus3"):
    return {"ts": NOW - timedelta(seconds=seconds_ago), "kind": "crs_link",
            "crs_link": {"device": "crs326", "interface": interface, "state": state, "speed": None}}


@mock.patch.dict(os.environ, {"NH_SMS_MODE": "live", "NH_ALERT_PHONES": "0712000000"})
class Flow(unittest.TestCase):
    def evaluate(self, fake, new):
        with mock.patch.object(service, "store", fake), \
                mock.patch.object(service, "send_in_background") as send:
            service.evaluate(new)
        return send.call_args.args[0] if send.called else []

    def test_congestion_fires_once_and_texts(self):
        rows = [probe_row(m, down=330) for m in range(5, -1, -1)]
        fake = FakeStore(rows)
        sent = self.evaluate(fake, rows)
        opened = [i for i in fake.incidents.values() if i["key"][1] == "UPLINK_CONGESTED"]
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("uplink", sent[0][1])
        self.assertTrue(opened[0]["fire_sms_sent"])

    def test_loss_amber_then_red_texts_on_red(self):
        rows = [probe_row(4), probe_row(3), probe_row(2, loss=10)]
        fake = FakeStore(rows)
        self.assertEqual(self.evaluate(fake, rows), [])          # AMBER: dashboard only
        worse = [probe_row(1, loss=100), probe_row(0, loss=100)]
        fake.rows += worse
        sent = self.evaluate(fake, worse)
        self.assertEqual(len(sent), 1)
        self.assertIn("no reply from Airtel gateway", sent[0][1])
        loss = [i for i in fake.incidents.values() if i["key"][1] == "LOSS"]
        self.assertEqual((len(loss), loss[0]["severity"]), (1, "RED"))

    def test_clear_closes_the_incident_and_texts(self):
        busy = [probe_row(m, down=330) for m in range(12, 6, -1)]
        quiet = [probe_row(m, down=50) for m in range(6, -1, -1)]
        fake = FakeStore(busy + quiet)
        self.evaluate(fake, busy)
        sent = self.evaluate(fake, quiet)
        self.assertTrue(any("back to normal" in message for _, message in sent))
        self.assertIsNotNone(next(i for i in fake.incidents.values() if i["key"][1] == "UPLINK_CONGESTED")["ended_at"])

    def test_backfill_opens_incidents_but_never_texts(self):
        rows = [probe_row(m, down=330) for m in range(185, 179, -1)]
        fake = FakeStore(rows)
        self.assertEqual(self.evaluate(fake, rows), [])
        self.assertEqual(fake.alerts[0]["reason"], "old reading (backfill)")

    def test_a_late_reading_is_not_judged_again(self):
        rows = [probe_row(m, down=330) for m in range(5, -1, -1)]
        fake = FakeStore(rows)
        self.evaluate(fake, rows)
        before = dict(fake.states)
        self.evaluate(fake, [probe_row(10, down=330)])
        self.assertEqual(before, fake.states)

    def test_silent_router_and_feed(self):
        fake = FakeStore(last_received={"probe": NOW - timedelta(minutes=15)})
        sent = self.evaluate(fake, [])
        self.assertEqual(len(sent), 1)
        self.assertIn("CCR2116: no readings", sent[0][1])
        fake.now = NOW + timedelta(minutes=1)
        fake.last_post_at = NOW - timedelta(minutes=12)
        sent = self.evaluate(fake, [])
        self.assertIn("Frankfurt", sent[0][1])

    def test_olt_damaging_frames_twice_in_a_row_texts_once(self):
        rows = [olt_row(10, 1000, 0, 0), olt_row(5, 1300, 900, 100), olt_row(0, 1600, 1800, 200)]
        fake = FakeStore(rows)
        sent = self.evaluate(fake, rows)
        self.assertEqual([message for _, message in sent],
                         ["JNET ALERT OLT: OLT uplink damaging frames, 10.0% of frames to customers (CRS326-OLT link). "
                          "Since 21:30"])

    def test_olt_link_down_then_back_after_minutes_and_seconds(self):
        down, up = link_row(133, "down"), link_row(0, "up")
        fake = FakeStore([down, up])
        self.assertIn("OLT link DOWN", self.evaluate(fake, [down])[0][1])
        self.assertEqual(self.evaluate(fake, [up])[0][1], "JNET OK OLT: OLT link back after 2 min 13 s")
        other = link_row(0, "down", "sfp-sfpplus4")
        self.assertEqual(self.evaluate(FakeStore([other]), [other]), [])

    def test_olt_restart_texts_but_its_clear_does_not(self):
        before, after, later = olt_row(10, 626553.26, 9000, 9), olt_row(5, 120, 10, 0), olt_row(0, 420, 20, 0)
        fake = FakeStore([before, after, later])
        sent = self.evaluate(fake, [before, after])
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT OLT: OLT restarted (power switchover?), had been up 7d6h. Back up at 21:23"])
        self.assertEqual(self.evaluate(fake, [later]), [])
        restart = next(i for i in fake.incidents.values() if i["key"][1] == "OLT_RESTART")
        self.assertIsNotNone(restart["ended_at"])

    def test_ccr2004_path_out_down_texts_once_as_down(self):
        rows = [{"ts": NOW - timedelta(minutes=m), "kind": "inner", "load": {"cpu_pct": 5},
                 "probes": [{"name": "i-fra", "rtt_ms": None, "jitter_ms": None, "loss_pct": 100},
                            {"name": "i-core", "rtt_ms": None, "jitter_ms": None, "loss_pct": 100}]} for m in (1, 0)]
        sent = self.evaluate(FakeStore(rows), rows)
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT CCR2004: CCR2004 path out DOWN, no reply on i-fra. Since 21:30"])

    def test_a_silent_area_texts(self):
        # NOW is 21:30 Nairobi; 37.5 MB in 5 min = 1 Mbps, then nothing for 10 min.
        rows = [{"ts": NOW - timedelta(minutes=m), "kind": "area",
                 "area": {"vlan": 5, "if": "v5", "down_bytes": b, "up_bytes": 0}}
                for m, b in ((15, 0), (10, 37_500_000), (5, 37_500_000), (0, 37_500_000))]
        sent = self.evaluate(FakeStore(rows), rows)
        self.assertEqual([m for _, m in sent],
                         ["JNET ALERT CCR2004: Area Njeri House has gone silent: possible power or fibre cut at the "
                          "site. Since 21:30"])

    def test_an_inside_link_going_down_texts(self):
        rows = [{"ts": NOW - timedelta(minutes=m), "kind": "lanlink",
                 "lanlink": {"dev": "ccr2116", "port": "sfp-sfpplus4", "link_downs": d}} for m, d in ((5, 3), (0, 4))]
        sent = self.evaluate(FakeStore(rows), rows)
        self.assertEqual([m for _, m in sent], ["JNET ALERT CCR2116: Link CCR2116 to CRS326 went down. Since 21:30"])

    def test_box_probes_never_alert(self):
        rows = [{"ts": NOW - timedelta(minutes=m), "kind": "hop",
                 "probes": [{"name": n, "rtt_ms": None, "jitter_ms": None, "loss_pct": 100}
                            for n in ("h-crs", "h-2004", "h-olt", "h-olt-big")]} for m in (2, 1, 0)]
        fake = FakeStore(rows)
        self.assertEqual(self.evaluate(fake, rows), [])
        self.assertEqual(fake.incidents, {})

    def test_log_only_mode_sends_nothing(self):
        rows = [probe_row(m, down=330) for m in range(5, -1, -1)]
        fake = FakeStore(rows)
        with mock.patch.dict(os.environ, {"NH_SMS_MODE": "log_only"}):
            self.assertEqual(self.evaluate(fake, rows), [])
        self.assertEqual(fake.alerts[0]["reason"], "log-only mode")


class RoutesCard(unittest.TestCase):
    def test_rows_labels_and_limits(self):
        route = {"host": "www.youtube.com", "ip": "142.251.157.4", "status": "ok", "last_ms": 180, "hops": []}
        store = mock.Mock(now_utc=mock.Mock(return_value=NOW),
                          route_records=mock.Mock(return_value=[{"ts": NOW, "route": route}]))
        with mock.patch.object(service, "store", store):
            card = service.routes_card()
        store.route_records.assert_called_once_with(NOW - timedelta(days=30))
        self.assertEqual([(s["host"], s["verdict"]) for s in card["sites"]], [("www.youtube.com", "abroad")])
        self.assertEqual(card["labels"]["kenya"], "Kenya")
        self.assertEqual(card["limits"], {"kenya_under_ms": 15, "za_from_ms": 50, "za_under_ms": 110,
                                          "abroad_from_ms": 140})


class InsideCard(unittest.TestCase):
    def card(self, readings, last_success):
        store = mock.Mock(now_utc=mock.Mock(return_value=NOW), olt_readings=mock.Mock(return_value=readings),
                          olt_restarts=mock.Mock(return_value=[]), crs_link_records=mock.Mock(return_value=[]),
                          olt_last_reads=mock.Mock(return_value=(NOW, last_success)),
                          active_rules=mock.Mock(return_value=[]), area_names=mock.Mock(return_value={}),
                          area_series=mock.Mock(return_value={}), area_readings=mock.Mock(return_value=[]),
                          lanlink_readings=mock.Mock(return_value=[]), hop_series=mock.Mock(return_value=[]))
        store.transaction.return_value.__enter__ = mock.Mock(return_value=None)
        store.transaction.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(service, "store", store):
            return service.inside_card("24h")

    def test_current_value_level_and_failed_polls(self):
        rows = [olt_row(m, 1000 + (30 - m) * 60, (30 - m) * 1000, (30 - m) * 100) for m in (30, 25, 20)]
        rows.append(olt_row(15, None, None, None))
        rows += [olt_row(m, 1000 + (30 - m) * 60, (30 - m) * 1000, (30 - m) * 100) for m in (10, 5)]
        card = self.card(rows, NOW - timedelta(minutes=5))
        self.assertEqual(card["crc"]["current"]["level"], "red")
        self.assertAlmostEqual(card["crc"]["current"]["pct"], 100 / 1100 * 100)
        self.assertEqual((card["snmp"]["polls"], card["snmp"]["failed_polls"]), (6, 1))
        self.assertFalse(card["snmp"]["is_stale"])

    def test_stale_after_15_minutes_and_nothing_yet(self):
        card = self.card([], NOW - timedelta(minutes=15))
        self.assertTrue(card["snmp"]["is_stale"])
        self.assertIsNone(card["crc"]["current"])
        self.assertEqual(card["crc"]["series"], [])


class Overview(unittest.TestCase):
    """Before the shipper has ever posted, nothing may show as green."""

    def overview(self, last_post_at, latest):
        feed_row = {"last_post_at": last_post_at, "last_sent_at": None, "source": None, "from_ip": None}
        store = mock.Mock(page_basics=mock.Mock(return_value=(feed_row, {}, latest)),
                          active_rules=mock.Mock(return_value=[]), alerts_since=mock.Mock(return_value=[]),
                          eat_midnight=mock.Mock(return_value=NOW), now_utc=mock.Mock(return_value=NOW))
        with mock.patch.object(service, "store", store):
            return service.overview()

    def test_nothing_received_yet_is_waiting_not_green(self):
        result = self.overview(None, {})
        self.assertTrue(result["waiting"])
        self.assertEqual({d["status"] for d in result["devices"]}, {"NOT_REPORTING"})

    def test_reporting_devices_are_green_and_silent_ones_not(self):
        result = self.overview(NOW, {"probe": NOW})
        self.assertFalse(result["waiting"])
        self.assertEqual([d["status"] for d in result["devices"]], ["GREEN", "GREEN", "NOT_REPORTING"])


if __name__ == "__main__":
    unittest.main()
