"""LOS alerts and the shared alert schedule: which PON ports count as one
cut, how many fast reminders are due, and when the twice-daily reminder
fires. Pure rules, no database. Run:
  venv/bin/python -m unittest tests.test_onu_alerts -v
"""
import unittest
from unittest import mock
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


class Deliveries(unittest.TestCase):
    """Every LOS message is logged for Reports > SMS Status, failures too."""
    def run_send(self, sms=(True, {"id": 1}), whatsapp=(False, {"error": "off"})):
        cur = mock.MagicMock()
        people = [{"id": 7, "in_app": True, "sms": True, "whatsapp": True, "phone": "0700000000"}]
        with mock.patch.object(onu_alerts, "create_notification"), \
             mock.patch.object(onu_alerts, "send_sms", return_value=sms), \
             mock.patch.object(onu_alerts, "send_whatsapp", return_value=whatsapp):
            onu_alerts._send(cur, 42, "onu_los", people, "t", "m", "olt")
        return [c.args[1] for c in cur.execute.call_args_list if "INSERT INTO alert_deliveries" in c.args[0]]

    def test_each_channel_logged_against_the_alert(self):
        rows = self.run_send()
        self.assertEqual([(r[0], r[1], r[2], r[3], r[5]) for r in rows],
                         [(42, 7, "in_app", "onu_los", "sent"), (42, 7, "sms", "onu_los", "sent"),
                          (42, 7, "whatsapp", "onu_los", "not_configured")])

    def test_a_log_failure_never_stops_the_sms(self):
        cur = mock.MagicMock()
        cur.execute.side_effect = lambda q, *a: (_ for _ in ()).throw(RuntimeError("db")) if "INSERT" in q else None
        people = [{"id": 7, "in_app": True, "sms": True, "whatsapp": False, "phone": "0700000000"}]
        with mock.patch.object(onu_alerts, "create_notification"), \
             mock.patch.object(onu_alerts, "send_sms", return_value=(True, {})) as sms:
            onu_alerts._send(cur, 42, "onu_los", people, "t", "m", "olt")
        sms.assert_called_once()

    def test_failed_sms_is_logged_as_failed(self):
        self.assertEqual(self.run_send(sms=(False, {"error": "x"}))[1][5], "failed")

    def test_message_kinds(self):
        t = {k: "{{WHO}}" for k in ("onu_los", "onu_los_still", "pon_los", "pon_los_still")}
        a = {"pon": None, "started_at": T0}
        m = [{"who": "Jane (JS1)", "name": "ONU1", "sn": "S", "id": "X", "location": "0/1/2"}]
        with mock.patch.object(onu_alerts, "render", side_effect=lambda tpl, v: tpl):
            self.assertEqual(onu_alerts._message(t, a, m, True, T0)[0], "onu_los")
            self.assertEqual(onu_alerts._message(t, a, m, False, T0)[0], "onu_los_still")
            self.assertEqual(onu_alerts._message(t, dict(a, pon="0/1"), m, True, T0)[0], "pon_los")


class SitesWithLos(unittest.TestCase):
    """Hotspot sites down because of LOS: the LOS alert speaks for them. The
    real queries were run against Postgres; here, the switches and failures."""
    def test_switched_off_means_site_alerts_as_before(self):
        cur = mock.MagicMock()
        with mock.patch.dict("os.environ", {"ONU_LOS_ALERTS_ENABLED": "false"}):
            self.assertEqual(onu_alerts.los_sites(cur), set())
            self.assertFalse(onu_alerts.site_outage_was_los(cur, 1, T0, T0))
        cur.execute.assert_not_called()

    def test_stale_onu_status_means_site_alerts_as_before(self):
        cur = mock.MagicMock()
        cur.fetchone.return_value = (T0 - timedelta(hours=1),)
        self.assertEqual(onu_alerts.los_sites(cur, now=T0), set())

    def test_a_failure_falls_back_to_site_alerts(self):
        cur = mock.MagicMock()
        cur.execute.side_effect = lambda q, *a: (_ for _ in ()).throw(RuntimeError("db")) if "onu_customer_links" in q else None
        cur.fetchone.return_value = (T0,)
        self.assertEqual(onu_alerts.los_sites(cur, now=T0), set())
        self.assertFalse(onu_alerts.site_outage_was_los(cur, 1, T0, T0))
        self.assertIn("ROLLBACK TO SAVEPOINT los_sites", [c.args[0] for c in cur.execute.call_args_list])


if __name__ == "__main__":
    unittest.main()
