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
        self.assertEqual(card["limits"], {"kenya_under_ms": 15, "abroad_from_ms": 140})


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
