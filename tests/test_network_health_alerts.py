"""Network Health's SMS cost rules: which events text, de-dupe of clears,
flapping, combining, the daily cap, log-only mode, and a failing SMS
provider never raising. Run:
  venv/bin/python -m unittest tests.test_network_health_alerts -v
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from network_health import alerts

NOW = datetime(2026, 11, 20, 20, 40, tzinfo=timezone.utc)


def event(kind="fire", rule="UPLINK_CONGESTED", device="ccr2116", severity="AMBER", incident_id=1, **overrides):
    base = {"kind": kind, "device": device, "rule": rule, "subject": "", "severity": severity,
            "facts": {"pct": 91, "minutes": 5}, "started_at": NOW - timedelta(minutes=30), "ended_at": NOW,
            "record_ts": NOW, "incident_id": incident_id, "is_flapping": False, "fire_sms_sent": kind == "clear"}
    base.update(overrides)
    return base


def context(**overrides):
    base = {"now": NOW, "mode": "live", "cap": 20, "sent_today": 0, "cap_notice_sent": False,
            "recent_fires": {}, "recipients": 1}
    base.update(overrides)
    return base


class WhatTexts(unittest.TestCase):
    def test_sms_rule_fires_and_clears(self):
        rows = alerts.plan([event(), event("clear", incident_id=2)], context())
        self.assertEqual([(r["kind"], r["outcome"]) for r in rows], [("fire", "pending"), ("clear", "pending")])

    def test_amber_dashboard_rules_get_no_row(self):
        for rule in ("LINK_ERRORS", "JITTER_HIGH", "CPU_HIGH", "OPTIC_DEGRADING"):
            self.assertEqual(alerts.plan([event(rule=rule)], context()), [])
        self.assertEqual(alerts.plan([event(rule="LOSS", facts={"loss": 3, "probe": "p-gw"})], context()), [])

    def test_any_red_texts(self):
        rows = alerts.plan([event(rule="LOSS", severity="RED", facts={"loss": 40, "probe": "p-gw"})], context())
        self.assertEqual(rows[0]["outcome"], "pending")

    def test_clear_of_a_red_incident_texts(self):
        rows = alerts.plan([event("clear", rule="LOSS", severity="AMBER", peak_severity="RED")], context())
        self.assertEqual(rows[0]["outcome"], "pending")


class Suppressed(unittest.TestCase):
    def test_backfill_never_texts(self):
        rows = alerts.plan([event(record_ts=NOW - timedelta(hours=3))], context())
        self.assertEqual((rows[0]["outcome"], rows[0]["reason"]), ("suppressed", "old reading (backfill)"))

    def test_flapping(self):
        rows = alerts.plan([event(is_flapping=True)], context())
        self.assertEqual(rows[0]["reason"], "flapping")

    def test_log_only_mode(self):
        rows = alerts.plan([event()], context(mode="log_only"))
        self.assertEqual(rows[0]["reason"], "log-only mode")

    def test_clear_without_a_sent_fire(self):
        rows = alerts.plan([event("clear", fire_sms_sent=False)], context())
        self.assertEqual(rows[0]["reason"], "its alert was not sent")

    def test_no_recipients(self):
        rows = alerts.plan([event()], context(recipients=0))
        self.assertEqual(rows[0]["reason"], "NH_ALERT_PHONES is empty")


class Combining(unittest.TestCase):
    def test_three_fires_at_once_send_one_sms(self):
        events = [event(rule=r, incident_id=i) for i, r in enumerate(("UPLINK_CONGESTED", "LATENCY_HIGH", "SPEED_LOW"))]
        events[2]["facts"] = {"failed": True}
        rows = alerts.plan(events, context())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "combined")
        self.assertEqual(rows[0]["incident_ids"], [0, 1, 2])
        self.assertIn("3 problems on CCR2116", rows[0]["message"])

    def test_third_fire_within_two_minutes_of_two_sent(self):
        rows = alerts.plan([event()], context(recent_fires={"ccr2116": 2}))
        self.assertEqual(rows[0]["kind"], "combined")

    def test_two_fires_stay_separate(self):
        rows = alerts.plan([event(), event(rule="LATENCY_HIGH", incident_id=2, facts={"ms": 90})], context())
        self.assertEqual([r["kind"] for r in rows], ["fire", "fire"])

    def test_other_devices_are_not_combined(self):
        rows = alerts.plan([event(), event(rule="ROUTER_SILENT", device="ccr2004", facts={"minutes": 10})],
                           context(recent_fires={"ccr2004": 0, "ccr2116": 1}))
        self.assertEqual([r["kind"] for r in rows], ["fire", "fire"])


class DailyCap(unittest.TestCase):
    def test_over_the_cap_sends_one_pause_notice(self):
        rows = alerts.plan([event(), event(rule="LATENCY_HIGH", incident_id=2, facts={"ms": 90})],
                           context(sent_today=20))
        kinds = [(r["kind"], r["outcome"]) for r in rows]
        self.assertEqual(kinds, [("cap", "pending"), ("fire", "suppressed"), ("fire", "suppressed")])
        self.assertIn("paused until midnight", rows[0]["message"])

    def test_pause_notice_only_once_a_day(self):
        rows = alerts.plan([event()], context(sent_today=20, cap_notice_sent=True))
        self.assertEqual([(r["kind"], r["reason"]) for r in rows], [("fire", "daily cap reached")])

    def test_last_one_under_the_cap_still_goes(self):
        rows = alerts.plan([event()], context(sent_today=19))
        self.assertEqual(rows[0]["outcome"], "pending")

    def test_cap_from_environment(self):
        with mock.patch.dict(os.environ, {"NH_SMS_DAILY_CAP": "5"}):
            self.assertEqual(alerts.daily_cap(), 5)
        with mock.patch.dict(os.environ, {"NH_SMS_DAILY_CAP": "lots"}):
            self.assertEqual(alerts.daily_cap(), 20)


class Delivery(unittest.TestCase):
    def test_sent_if_any_phone_accepts(self):
        replies = iter([(False, {"error": "bad number"}), (True, {"status": "success"})])
        outcome, reason = alerts.deliver("hi", ["1", "2"], sender=lambda phone, text: next(replies))
        self.assertEqual(outcome, "sent")
        self.assertIn("1 of 2", reason)

    def test_provider_errors_never_raise(self):
        def broken(phone, text):
            raise RuntimeError("provider down")
        self.assertEqual(alerts.deliver("hi", ["1"], sender=broken), ("failed", "provider down"))

    def test_mode_and_phones_from_environment(self):
        with mock.patch.dict(os.environ, {"NH_SMS_MODE": "", "NH_ALERT_PHONES": " 0712 , ,0733 "}):
            self.assertEqual(alerts.sms_mode(), "log_only")
            self.assertEqual(alerts.alert_phones(), ["0712", "0733"])
        with mock.patch.dict(os.environ, {"NH_SMS_MODE": "LIVE"}):
            self.assertEqual(alerts.sms_mode(), "live")


if __name__ == "__main__":
    unittest.main()
