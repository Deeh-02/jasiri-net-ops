"""LOS alerts and the shared alert schedule: which PON ports count as one
cut, how many fast reminders are due, and when the twice-daily reminder
fires. Pure rules, no database. Run:
  venv/bin/python -m unittest tests.test_onu_alerts -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from db import alert_settings, monitoring_alerts, onu_alerts

T0 = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)   # 10:00 EAT


def los(id_, pon, seconds):
    return {"id": id_, "pon": pon, "started_at": T0 + timedelta(seconds=seconds)}


class Cuts(unittest.TestCase):
    def test_three_on_one_port_within_two_minutes_is_a_cut(self):
        self.assertEqual(onu_alerts.find_cuts([los("a", "0/3", 0), los("b", "0/3", 50), los("c", "0/3", 119)]), {"0/3"})

    def test_spread_out_or_on_different_ports_is_not(self):
        self.assertEqual(onu_alerts.find_cuts([los("a", "0/3", 0), los("b", "0/3", 60), los("c", "0/3", 400)]), set())
        self.assertEqual(onu_alerts.find_cuts([los("a", "0/3", 0), los("b", "0/4", 1), los("c", "0/5", 2)]), set())

    def test_any_two_minute_span_counts(self):
        cut = [los("a", "1/2", 0), los("b", "1/2", 600), los("c", "1/2", 650), los("d", "1/2", 700)]
        self.assertEqual(onu_alerts.find_cuts(cut), {"1/2"})


class Schedule(unittest.TestCase):
    def test_fast_checkpoints(self):
        crossed = lambda minutes: onu_alerts.checkpoints_crossed(T0, T0 + timedelta(minutes=minutes))
        self.assertEqual([crossed(m) for m in (1, 2, 14, 15, 59, 60, 179, 180, 360, 480, 2000)],
                         [0, 1, 1, 2, 3, 4, 4, 5, 6, 7, 7])

    def test_site_schedule_is_seven_messages_over_eight_hours(self):
        self.assertEqual(monitoring_alerts.SCHEDULE_MINUTES, [5, 15, 30, 60, 180, 360, 480])

    def test_daily_slots_are_8_and_16_eat(self):
        at = lambda h, m=0: datetime(2026, 11, 11, h, m, tzinfo=timezone.utc)   # UTC; EAT is +3
        self.assertEqual(alert_settings.latest_daily_slot(at(6)), at(5))         # 09:00 EAT -> 08:00 EAT
        self.assertEqual(alert_settings.latest_daily_slot(at(14)), at(13))       # 17:00 EAT -> 16:00 EAT
        self.assertEqual(alert_settings.latest_daily_slot(at(2)), at(13) - timedelta(days=1))   # 05:00 EAT -> yesterday 16:00

    def test_daily_slot_due(self):
        now = datetime(2026, 11, 11, 13, 5, tzinfo=timezone.utc)                # 16:05 EAT
        self.assertTrue(alert_settings.daily_slot_due(datetime(2026, 11, 11, 9, 0), now))     # last sent 12:00 EAT (naive UTC)
        self.assertFalse(alert_settings.daily_slot_due(datetime(2026, 11, 11, 13, 1), now))   # already sent after 16:00
        self.assertTrue(alert_settings.daily_slot_due(None, now))

    def test_since_text_and_counts(self):
        self.assertEqual(onu_alerts.since_text(T0, T0 + timedelta(hours=2)), "10:00")
        self.assertEqual(onu_alerts.since_text(T0, T0 + timedelta(days=10)), "11 Nov 10:00")
        self.assertEqual((onu_alerts.onus(1), onu_alerts.onus(4)), ("1 ONU", "4 ONUs"))

    def test_durations(self):
        self.assertEqual([onu_alerts.duration(timedelta(minutes=m)) for m in (2, 60, 75, 1500)], ["2m", "1h", "1h 15m", "1d 1h"])


if __name__ == "__main__":
    unittest.main()
