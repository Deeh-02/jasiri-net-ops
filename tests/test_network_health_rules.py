"""Network Health's detection rules: every rule fires, holds and clears.
Pure code, no database. Run:
  venv/bin/python -m unittest tests.test_network_health_rules -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from network_health import config, rules

T0 = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)   # 21:00 in Nairobi


def minute(n):
    return T0 + timedelta(minutes=n)


def probe_reading(n, down=100, up=20, cpu=5, probes=None):
    probes = probes if probes is not None else [
        {"name": "p-gw", "rtt_ms": 2.0, "jitter_ms": 0.5, "loss_pct": 0},
        {"name": "p-cf", "rtt_ms": 20.0, "jitter_ms": 1.0, "loss_pct": 0},
    ]
    return {"ts": minute(n), "load": {"down_mbps": down, "up_mbps": up, "cpu_pct": cpu}, "probes": probes}


def probe(name, rtt=20.0, loss=0, jitter=1.0):
    return {"name": name, "rtt_ms": rtt, "jitter_ms": jitter, "loss_pct": loss}


def run(checks):
    """Feeds checks through step() from a fresh state; returns the transitions."""
    state, transitions = rules.new_state(), []
    for check in checks:
        state, transition = rules.step(state, check)
        transitions.append(transition)
    return state, transitions


class Hysteresis(unittest.TestCase):
    def check(self, verdict, severity=rules.AMBER, rule="CPU_HIGH", immediate=False):
        return rules.make_check("ccr2116", rule, verdict, severity=severity, immediate=immediate)

    def test_fires_holds_and_clears_after_three_good(self):
        state, seen = run([self.check(True), self.check(True), self.check(False), self.check(False),
                           self.check(False)])
        self.assertEqual(seen, ["fire", "hold", "hold", "hold", "clear"])
        self.assertFalse(state["is_active"])

    def test_a_good_check_in_between_resets_the_clear_count(self):
        _, seen = run([self.check(True), self.check(False), self.check(False), self.check(True),
                       self.check(False), self.check(False)])
        self.assertNotIn("clear", seen)

    def test_unknown_leaves_the_state_alone(self):
        state, seen = run([self.check(True), self.check(None), self.check(None)])
        self.assertEqual(seen, ["fire", None, None])
        self.assertTrue(state["is_active"])

    def test_amber_to_red_is_worse(self):
        _, seen = run([self.check(True), self.check(True, severity=rules.RED)])
        self.assertEqual(seen, ["fire", "worse"])

    def test_link_errors_need_three_in_a_row_unless_immediate(self):
        _, seen = run([self.check(True, rule="LINK_ERRORS")] * 3)
        self.assertEqual(seen, [None, None, "fire"])
        _, seen = run([self.check(True, rule="LINK_ERRORS", immediate=True)])
        self.assertEqual(seen, ["fire"])

    def test_silence_clears_on_the_first_good_check(self):
        _, seen = run([self.check(True, rule="ROUTER_SILENT", immediate=True), self.check(False, rule="ROUTER_SILENT")])
        self.assertEqual(seen, ["fire", "clear"])


class Uplink(unittest.TestCase):
    def test_busy_for_five_minutes_fires(self):
        readings = [probe_reading(n, down=320) for n in range(5)]
        check = rules.check_uplink(readings, minute(4), 350)
        self.assertTrue(check["verdict"])
        self.assertAlmostEqual(check["value"], 91.43, places=1)

    def test_one_busy_minute_in_five_does_not(self):
        readings = [probe_reading(n, down=340 if n == 4 else 150) for n in range(5)]
        self.assertFalse(rules.check_uplink(readings, minute(4), 350)["verdict"])

    def test_upload_counts_too(self):
        readings = [probe_reading(n, down=10, up=330) for n in range(5)]
        self.assertTrue(rules.check_uplink(readings, minute(4), 350)["verdict"])

    def test_too_few_readings_is_unknown(self):
        readings = [probe_reading(n, down=340) for n in range(2)]
        self.assertIsNone(rules.check_uplink(readings, minute(1), 350)["verdict"])


class Latency(unittest.TestCase):
    def readings(self, rtt, name="p-fra", count=10):
        return [probe_reading(n, probes=[probe(name, rtt=rtt)]) for n in range(count)]

    def test_judged_on_frankfurt(self):
        self.assertEqual(config.LATENCY_PROBE, "p-fra")
        self.assertIsNone(rules.check_latency(self.readings(900, name="p-cf"), minute(9), 203)["verdict"])

    def test_fallback_limit_before_a_week_of_data(self):
        self.assertTrue(rules.check_latency(self.readings(260), minute(9), None)["verdict"])
        self.assertFalse(rules.check_latency(self.readings(240), minute(9), None)["verdict"])

    def test_forty_ms_over_the_usual_for_this_hour(self):
        self.assertTrue(rules.check_latency(self.readings(244), minute(9), 203)["verdict"])
        self.assertFalse(rules.check_latency(self.readings(242), minute(9), 203)["verdict"])

    def test_averaged_over_ten_minutes(self):
        # 4 slow minutes out of 10 average 233 ms: under 203 + 40.
        readings = [probe_reading(n, probes=[probe("p-fra", rtt=278 if n >= 6 else 203)]) for n in range(10)]
        self.assertFalse(rules.check_latency(readings, minute(9), 203)["verdict"])
        # The same 4 minutes judged over a 5-minute window would have fired.
        self.assertGreater((278 * 4 + 203) / 5, 203 + config.LATENCY_OVER_USUAL_MS)

    def test_outage_readings_are_left_out(self):
        readings = [probe_reading(n, probes=[probe("p-fra", rtt=None, loss=100)]) for n in range(10)]
        self.assertIsNone(rules.check_latency(readings, minute(9), 203)["verdict"])


class Loss(unittest.TestCase):
    def readings(self, losses, name="p-gw"):
        return [probe_reading(n, probes=[probe(name, rtt=None if loss >= 100 else 2.0, loss=loss)])
                for n, loss in enumerate(losses)]

    def test_one_lost_ping_in_five_minutes_is_not_over_the_line(self):
        self.assertFalse(rules.check_loss(self.readings([0, 10, 0, 0, 0]), minute(4), "p-gw")["verdict"])

    def test_average_over_five_minutes(self):
        check = rules.check_loss(self.readings([0, 10, 0, 10, 0]), minute(4), "p-gw")
        self.assertTrue(check["verdict"])                 # 4 % average
        self.assertEqual(check["severity"], rules.AMBER)

    def test_older_readings_fall_out_of_the_window(self):
        self.assertFalse(rules.check_loss(self.readings([10, 10, 0, 0, 0, 0, 0]), minute(6), "p-gw")["verdict"])

    def test_no_loss_is_fine(self):
        self.assertFalse(rules.check_loss(self.readings([0, 0, 0, 0, 0]), minute(4), "p-gw")["verdict"])

    def test_heavy_loss_is_red(self):
        check = rules.check_loss(self.readings([0, 0, 0, 60, 70]), minute(4), "p-gw")
        self.assertEqual(check["severity"], rules.RED)

    def test_one_minute_without_reply_is_amber_two_are_red(self):
        check = rules.check_loss(self.readings([0, 0, 0, 0, 100]), minute(4), "p-gw")
        self.assertEqual((check["severity"], check["facts"]["outage"]), (rules.AMBER, True))
        check = rules.check_loss(self.readings([0, 0, 0, 100, 100]), minute(4), "p-gw")
        self.assertEqual((check["severity"], check["facts"]["outage"]), (rules.RED, True))

    def test_only_its_own_probe(self):
        self.assertIsNone(rules.check_loss(self.readings([100, 100, 100], name="p-fra"), minute(2), "p-gw")["verdict"])

    def test_new_provider_probes_never_judged(self):
        names = [c["subject"] for c in rules.probe_checks([], minute(0), 350, None)]
        self.assertFalse(any(n.endswith(config.NEW_PROVIDER_SUFFIX) for n in names))


class Jitter(unittest.TestCase):
    def test_fires_on_average_above_30(self):
        readings = [probe_reading(n, probes=[probe("p-cf", jitter=40)]) for n in range(5)]
        self.assertTrue(rules.check_jitter(readings, minute(4), "p-cf")["verdict"])
        readings = [probe_reading(n, probes=[probe("p-cf", jitter=10)]) for n in range(5)]
        self.assertFalse(rules.check_jitter(readings, minute(4), "p-cf")["verdict"])


class Cpu(unittest.TestCase):
    def test_both_routers(self):
        readings = [probe_reading(n, cpu=90) for n in range(5)]
        self.assertTrue(rules.check_cpu(readings, minute(4), "ccr2116")["verdict"])
        inner = [{"ts": minute(n), "load": {"cpu_pct": 20}, "probes": []} for n in range(5)]
        check = rules.check_cpu(inner, minute(4), "ccr2004")
        self.assertEqual((check["device"], check["verdict"]), ("ccr2004", False))


class Dns(unittest.TestCase):
    def run_of(self, worst_ms=1.6, fails=0, name="rt"):
        return {"resolvers": [{"name": "g8", "server": "8.8.8.8", "worst_ms": 10000, "fails": 2},
                              {"name": name, "server": "router", "worst_ms": worst_ms, "fails": fails}]}

    def checks(self, *runs):
        return [rules.check_dns_router(run) for run in runs]

    def test_one_failed_run_does_not_fire_two_do(self):
        _, seen = run(self.checks(self.run_of(10000, 1), self.run_of(10000, 1)))
        self.assertEqual(seen, [None, "fire"])

    def test_slow_twice_in_a_row_fires(self):
        _, seen = run(self.checks(self.run_of(501), self.run_of(900)))
        self.assertEqual(seen, [None, "fire"])

    def test_500_ms_exactly_is_not_slow(self):
        self.assertFalse(rules.check_dns_router(self.run_of(500))["verdict"])

    def test_a_good_run_in_between_resets(self):
        _, seen = run(self.checks(self.run_of(900), self.run_of(2), self.run_of(900)))
        self.assertEqual(seen, [None, None, None])

    def test_clears_after_two_good_runs(self):
        _, seen = run(self.checks(self.run_of(900), self.run_of(900), self.run_of(2), self.run_of(2)))
        self.assertEqual(seen, [None, "fire", "hold", "clear"])

    def test_only_the_router_resolver_counts(self):
        # g8 fails in every run above; that alone never makes the check bad.
        self.assertFalse(rules.check_dns_router(self.run_of(2))["verdict"])
        self.assertIsNone(rules.check_dns_router(self.run_of(name="q9"))["verdict"])
        self.assertIsNone(rules.check_dns_router(None)["verdict"])

    def test_page_only(self):
        check = rules.check_dns_router(self.run_of(10000, 2))
        self.assertEqual(check["severity"], rules.AMBER)
        self.assertNotIn("DNS_ROUTER", config.SMS_RULES)
        self.assertEqual(check["facts"], {"ms": 10000, "fails": 2})


class Link(unittest.TestCase):
    BASE = {"rx_dbm": -6.3, "link_downs": 7, "rx_fcs": 0, "rx_err_events": 0, "rx_overflow": 0, "tx_drop": 58}

    def test_link_down_fires_at_once_and_clears_on_the_next_quiet_reading(self):
        dropped = {**self.BASE, "link_downs": 8}
        check = rules.check_link_down(self.BASE, dropped)
        self.assertTrue(check["verdict"] and check["immediate"])
        self.assertEqual(check["facts"]["link_downs"], 1)
        _, seen = run([check, rules.check_link_down(dropped, dropped)])
        self.assertEqual(seen, ["fire", "clear"])

    def test_link_down_texts_and_errors_do_not(self):
        self.assertIn("LINK_DOWN", config.SMS_RULES)
        self.assertNotIn("LINK_ERRORS", config.SMS_RULES)

    def test_a_link_drop_is_not_a_link_error(self):
        self.assertFalse(rules.check_link_errors(self.BASE, {**self.BASE, "link_downs": 8})["verdict"])

    def test_errors_rising(self):
        check = rules.check_link_errors(self.BASE, {**self.BASE, "rx_fcs": 3, "tx_drop": 60})
        self.assertTrue(check["verdict"])
        self.assertFalse(check["immediate"])
        self.assertEqual(check["facts"]["errors"], 5)

    def test_a_counter_going_down_is_a_reset_not_an_error(self):
        reset = {**self.BASE, "tx_drop": 0, "link_downs": 0}
        self.assertFalse(rules.check_link_errors(self.BASE, reset)["verdict"])
        self.assertFalse(rules.check_link_down(self.BASE, reset)["verdict"])

    def test_first_reading_is_unknown(self):
        self.assertIsNone(rules.check_link_errors(None, self.BASE)["verdict"])
        self.assertIsNone(rules.check_link_down(None, self.BASE)["verdict"])
        self.assertIsNone(rules.check_link_down(self.BASE, {**self.BASE, "link_downs": None})["verdict"])

    def test_optic_floor_and_drop(self):
        self.assertTrue(rules.check_optic({"rx_dbm": -26}, None, 0)["verdict"])
        self.assertTrue(rules.check_optic({"rx_dbm": -8.6}, -6.3, 100)["verdict"])
        self.assertFalse(rules.check_optic({"rx_dbm": -7.0}, -6.3, 100)["verdict"])
        # Not enough history: only the floor counts.
        self.assertFalse(rules.check_optic({"rx_dbm": -9.0}, -6.3, 3)["verdict"])


class Speed(unittest.TestCase):
    NIGHT = datetime(2026, 11, 21, 0, 31, tzinfo=timezone.utc)      # 03:31 Nairobi
    EVENING = datetime(2026, 11, 21, 17, 31, tzinfo=timezone.utc)   # 20:31 Nairobi

    def speed(self, mbps, status="finished"):
        return {"status": status, "mbps": mbps, "duration_s": 67, "load_before_mbps": 40}

    def test_night_result_under_half_the_median_fires(self):
        self.assertTrue(rules.check_speed(self.NIGHT, self.speed(12), 30, 7)["verdict"])
        self.assertFalse(rules.check_speed(self.NIGHT, self.speed(25), 30, 7)["verdict"])

    def test_failed_night_test_fires(self):
        self.assertTrue(rules.check_speed(self.NIGHT, self.speed(None, "failed"), 30, 7)["verdict"])

    def test_evening_test_is_never_judged(self):
        self.assertIsNone(rules.check_speed(self.EVENING, self.speed(1), 30, 7)["verdict"])
        self.assertIsNone(rules.check_speed(self.EVENING, self.speed(None, "failed"), 30, 7)["verdict"])

    def test_no_baseline_yet_only_failure_counts(self):
        self.assertFalse(rules.check_speed(self.NIGHT, self.speed(1), None, 0)["verdict"])

    def test_never_compared_with_capacity(self):
        self.assertFalse(rules.check_speed(self.NIGHT, self.speed(29.9), 30, 7)["verdict"])


class Silence(unittest.TestCase):
    def test_feed_silent_after_ten_minutes(self):
        self.assertTrue(rules.check_feed_silent(minute(0), minute(10))["verdict"])
        self.assertFalse(rules.check_feed_silent(minute(0), minute(9))["verdict"])
        self.assertIsNone(rules.check_feed_silent(None, minute(9))["verdict"])

    def test_router_silent_only_while_the_feed_is_alive(self):
        self.assertTrue(rules.check_router_silent("ccr2116", minute(0), minute(11), minute(12))["verdict"])
        self.assertFalse(rules.check_router_silent("ccr2116", minute(5), minute(11), minute(12))["verdict"])
        # The feed itself is down: say nothing about the router.
        self.assertIsNone(rules.check_router_silent("ccr2116", minute(0), minute(0), minute(12))["verdict"])

    def test_a_router_that_never_reported_is_not_silent(self):
        self.assertIsNone(rules.check_router_silent("ccr2004", None, minute(11), minute(12))["verdict"])


class InsideNetwork(unittest.TestCase):
    def step(self, pct=None, restart=None, uptime=1000):
        interval = None if pct is None else {"crc_pct": pct}
        return {"interval": interval, "restart": restart, "uptime_s": uptime}

    def test_damaged_frames_over_1_pct_twice_in_a_row_fires(self):
        _, seen = run([rules.check_olt_crc(self.step(10.2)), rules.check_olt_crc(self.step(9.8))])
        self.assertEqual(seen, [None, "fire"])

    def test_1_pct_exactly_is_not_over_and_a_gap_keeps_the_count(self):
        self.assertFalse(rules.check_olt_crc(self.step(1))["verdict"])
        self.assertIsNone(rules.check_olt_crc(self.step(None))["verdict"])

    def test_clears_after_two_clean_intervals(self):
        checks = [rules.check_olt_crc(self.step(p)) for p in (10, 10, 0.001, 0.001)]
        self.assertEqual(run(checks)[1], [None, "fire", "hold", "clear"])

    def test_a_restart_fires_at_once_and_clears_on_the_next_reading(self):
        restart = {"previous_uptime_s": 626553.26, "uptime_s": 120}
        checks = [rules.check_olt_restart(self.step(restart=restart)), rules.check_olt_restart(self.step(uptime=420))]
        self.assertEqual(run(checks)[1], ["fire", "clear"])
        self.assertEqual(checks[0]["facts"], restart)
        self.assertIsNone(rules.check_olt_restart(self.step(uptime=None))["verdict"])

    def test_only_the_olt_port_is_judged(self):
        def link(interface, state):
            return {"interface": interface, "state": state}
        self.assertIsNone(rules.check_olt_link(link("sfp-sfpplus1", "down")))
        checks = [rules.check_olt_link(link("sfp-sfpplus3", "down")), rules.check_olt_link(link("sfp-sfpplus3", "up"))]
        self.assertEqual(run(checks)[1], ["fire", "clear"])
        self.assertEqual(checks[0]["subject"], "sfp-sfpplus3")

    def test_snmp_silent_after_15_minutes_of_no_complete_read(self):
        self.assertTrue(rules.check_olt_snmp(minute(0), minute(14), minute(15), minute(15))["verdict"])
        self.assertFalse(rules.check_olt_snmp(minute(1), minute(14), minute(15), minute(15))["verdict"])

    def test_snmp_not_judged_while_the_ccr2116_or_feed_is_silent_or_before_any_read(self):
        self.assertIsNone(rules.check_olt_snmp(minute(0), minute(0), minute(15), minute(15))["verdict"])
        self.assertIsNone(rules.check_olt_snmp(minute(0), minute(14), minute(0), minute(15))["verdict"])
        self.assertIsNone(rules.check_olt_snmp(None, minute(14), minute(15), minute(15))["verdict"])

    def test_all_four_text(self):
        for rule in ("OLT_CRC", "OLT_RESTART", "OLT_LINK_DOWN", "OLT_SNMP_SILENT"):
            self.assertIn(rule, config.SMS_RULES)


class Status(unittest.TestCase):
    def test_worst_active_rule(self):
        self.assertEqual(rules.device_status([]), "GREEN")
        self.assertEqual(rules.device_status([rules.AMBER, rules.RED]), "RED")
        self.assertEqual(rules.device_status([rules.RED, rules.OFFLINE]), "OFFLINE")

    def test_peak_direction(self):
        self.assertEqual(rules.worse_value("SPEED_LOW", 12, 9), 9)
        self.assertEqual(rules.worse_value("LOSS", 30, 20), 30)


if __name__ == "__main__":
    unittest.main()
