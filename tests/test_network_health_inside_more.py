"""Inside network, part 2: areas (per-VLAN Mbps, silent areas), inside links
(port counters), path probes (i-fra*, h-cust*), and the loss-scale fix.
Run: venv/bin/python -m unittest tests.test_network_health_inside_more -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from network_health import config, feed, inside, rules

T0 = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)      # 10:00 in Nairobi


def area(minutes, vlan, down, up=0):
    return {"ts": T0 + timedelta(minutes=minutes), "area": {"vlan": vlan, "if": f"v{vlan}", "down_bytes": down,
                                                            "up_bytes": up}}


def lan(minutes, dev, port, **counters):
    return {"ts": T0 + timedelta(minutes=minutes), "lanlink": {"dev": dev, "port": port, **counters}}


def run(checks):
    state, seen = rules.new_state(), []
    for check in checks:
        state, transition = rules.step(state, check)
        seen.append(transition)
    return seen


class AreaRates(unittest.TestCase):
    def test_mbps_is_the_delta_times_8_over_the_seconds_per_vlan(self):
        # 37.5 MB in 300 s = 1 Mbps; another VLAN in between doesn't pair with it.
        steps = inside.area_rates([area(0, 5, 1_000), area(0, 10, 0), area(5, 5, 1_000 + 37_500_000, 75_000_000)])
        self.assertEqual([s["down_mbps"] for s in steps], [None, None, 1.0])
        self.assertEqual(steps[2]["up_mbps"], 2.0)

    def test_a_counter_that_went_down_is_a_reset_skipped(self):
        steps = inside.area_rates([area(0, 5, 9_000_000, 5), area(5, 5, 100, 37_500_005)])
        self.assertIsNone(steps[1]["down_mbps"])
        self.assertEqual(steps[1]["up_mbps"], 1.0)

    def test_names(self):
        names = {5: "Njeri House", 99: "Street 9"}
        self.assertEqual([inside.area_name(v, names) for v in (5, 99, 123)],
                         ["Njeri House", "CCR2004 total", "VLAN 123"])


class AreaSilent(unittest.TestCase):
    FIRST_SEEN = T0 - timedelta(days=3)

    def check(self, rate, minutes=0, vlan=5, usual=12.0, first_seen=FIRST_SEEN):
        step = {"ts": T0 + timedelta(minutes=minutes), "down_mbps": rate}
        return rules.check_area_silent(vlan, "Njeri House", step, usual, first_seen)

    def test_two_silent_readings_in_a_row_fire(self):
        self.assertEqual(run([self.check(0.01), self.check(0.0, 5)]), [None, "fire"])
        self.assertEqual(self.check(0.01)["subject"], "Njeri House (VLAN 5)")

    def test_traffic_back_clears(self):
        self.assertEqual(run([self.check(0.01), self.check(0.0, 5), self.check(3.2, 10)]), [None, "fire", "clear"])

    def test_an_always_quiet_vlan_never_fires(self):
        self.assertFalse(self.check(0.0, usual=0.8)["verdict"])
        self.assertFalse(self.check(0.0, usual=None)["verdict"])

    def test_not_at_night_not_vlan_99_not_without_two_days(self):
        night = {"ts": datetime(2026, 11, 21, 20, 30, tzinfo=timezone.utc), "down_mbps": 0}     # 23:30 EAT
        self.assertIsNone(rules.check_area_silent(5, "x", night, 12, self.FIRST_SEEN)["verdict"])
        early = {"ts": datetime(2026, 11, 21, 3, 59, tzinfo=timezone.utc), "down_mbps": 0}     # 06:59 EAT
        self.assertIsNone(rules.check_area_silent(5, "x", early, 12, self.FIRST_SEEN)["verdict"])
        self.assertIsNone(self.check(0.0, vlan=99)["verdict"])
        self.assertIsNone(self.check(0.0, first_seen=T0 - timedelta(days=1))["verdict"])
        self.assertIsNone(self.check(None)["verdict"])


class LanLinks(unittest.TestCase):
    def steps(self, *readings):
        return inside.lanlink_steps(list(readings))

    def test_increases_per_port_and_resets_count_as_zero(self):
        steps = self.steps(lan(0, "crs326", "sfp-sfpplus1", link_downs=19, rx_err=8, rx_fcs=None),
                           lan(5, "crs326", "sfp-sfpplus1", link_downs=20, rx_err=2, rx_fcs=None))
        self.assertIsNone(steps[0]["increases"])
        self.assertEqual(steps[1]["increases"]["link_downs"], 1)
        self.assertEqual(steps[1]["increases"]["rx_err"], 0)
        self.assertIsNone(steps[1]["increases"]["rx_fcs"])
        self.assertEqual(steps[1]["label"], "CRS326 → CCR2116")

    def test_link_down_damage_and_drops(self):
        step = self.steps(lan(0, "ccr2116", "sfp-sfpplus4", link_downs=1, rx_fcs=0, rx_err=0, tx_drop=0),
                          lan(5, "ccr2116", "sfp-sfpplus4", link_downs=2, rx_fcs=101, rx_err=5, tx_drop=1001))[1]
        verdicts = {c["rule"]: c["verdict"] for c in rules.check_lanlink(step)}
        self.assertEqual(verdicts, {"LANLINK_DOWN": True, "LANLINK_DAMAGE": True, "LANLINK_DROPS": True})
        self.assertNotIn("LANLINK_DROPS", config.SMS_RULES)

    def test_at_the_lines_is_fine_and_missing_counters_say_nothing(self):
        step = self.steps(lan(0, "ccr2004", "sfp-sfpplus1", link_downs=3, rx_fcs=0, tx_drop=0),
                          lan(5, "ccr2004", "sfp-sfpplus1", link_downs=3, rx_fcs=100, tx_drop=1000))[1]
        verdicts = {c["rule"]: c["verdict"] for c in rules.check_lanlink(step)}
        self.assertEqual(verdicts, {"LANLINK_DOWN": False, "LANLINK_DAMAGE": False, "LANLINK_DROPS": False})
        step = self.steps(lan(0, "ccr2004", "sfp-sfpplus1"), lan(5, "ccr2004", "sfp-sfpplus1"))[1]
        self.assertEqual({c["verdict"] for c in rules.check_lanlink(step)}, {None})

    def test_a_long_gap_is_not_in_5_min(self):
        step = self.steps(lan(0, "crs326", "sfp-sfpplus2", link_downs=1), lan(40, "crs326", "sfp-sfpplus2", link_downs=2))[1]
        self.assertIsNone(rules.check_lanlink(step)[0]["verdict"])

    def test_the_olt_port_leaves_link_down_to_its_own_events(self):
        step = self.steps(lan(0, "crs326", "sfp-sfpplus3", link_downs=19), lan(5, "crs326", "sfp-sfpplus3", link_downs=20))[1]
        self.assertNotIn("LANLINK_DOWN", [c["rule"] for c in rules.check_lanlink(step)])

    def test_rows_sum_the_day_and_keep_the_last_5_min(self):
        readings = [lan(m, "crs326", "sfp-sfpplus3", link_downs=19, rx_err=e) for m, e in ((0, 8), (5, 20), (10, 3), (15, 7))]
        readings.append(lan(15, "ccr2116", "sfp-sfpplus4", link_downs=0))
        rows = inside.lanlink_rows(readings, T0)
        self.assertEqual([r["label"] for r in rows], ["CCR2116 → CRS326", "CRS326 → OLT"])
        olt = rows[1]
        self.assertEqual(olt["totals"]["rx_err"], 7)
        self.assertEqual(olt["last"]["rx_err"], 4)
        self.assertEqual(olt["day"]["rx_err"], 12 + 0 + 4)       # the reset (20 -> 3) counts 0


def olt_reading(minutes, rx, crc, uptime=100_000):
    return {"ts": T0 + timedelta(minutes=minutes), "olt": {"uptime_s": uptime + minutes * 60, "up_rx": rx, "up_crc": crc}}


class Cables(unittest.TestCase):
    """One row per physical cable, both ends: a port counts errors only on
    frames it RECEIVES, so damage A -> B shows at B."""
    NOW = T0 + timedelta(minutes=15)

    def cables(self, lanlinks, olts):
        steps = inside.walk_olt(olts)
        rows = inside.cable_rows(lanlinks, steps, olts, self.NOW, T0 - timedelta(hours=9))
        return {c["name"]: c for c in rows}

    def olt_cable(self, crs_errors=0):
        lans = [lan(m, "crs326", "sfp-sfpplus3", link_downs=19, rx_fcs=0, rx_err=e, tx_drop=0)
                for m, e in ((0, 0), (5, crs_errors), (10, crs_errors), (15, crs_errors))]
        # 13 % of the frames damaged in each 5 minutes; 1,000 + 150 more frames, then the same again.
        olts = [olt_reading(0, 0, 0), olt_reading(5, 870, 130), olt_reading(10, 1740, 260), olt_reading(15, 2610, 390)]
        return self.cables(lans, olts)["CRS326 ↔ OLT"]

    def test_the_four_cables_in_order(self):
        names = list(self.cables([], []))
        self.assertEqual(names, ["CCR2116 ↔ CRS326", "CCR2004 ↔ CRS326", "CRS326 ↔ OLT", "CRS326 ↔ Proxmox"])

    def test_crs_to_olt_is_the_olts_crc_counter_and_red(self):
        cable = self.olt_cable()
        way = cable["a_to_b"]                      # errors RECEIVED at the OLT
        self.assertEqual((way["from"], way["to"], way["read_at"], way["level"]), ("CRS326", "OLT", "OLT", "red"))
        self.assertEqual(way["received"]["last"], 130)
        self.assertAlmostEqual(way["received"]["last_pct"], 13.0)
        self.assertEqual(way["received"]["day"], 390)
        self.assertAlmostEqual(way["received"]["day_pct"], 13.0)
        self.assertEqual(way["received"]["total"], 390)

    def test_olt_to_crs_is_the_crs_ports_own_errors_and_green_at_zero(self):
        way = self.olt_cable()["b_to_a"]
        self.assertEqual((way["from"], way["to"], way["level"]), ("OLT", "CRS326", "green"))
        self.assertEqual((way["received"]["last"], way["received"]["day"]), (0, 0))

    def test_fcs_and_rx_errors_are_added_and_a_rise_in_5_min_is_red(self):
        lans = [lan(10, "ccr2116", "sfp-sfpplus4", rx_fcs=0, rx_err=0), lan(15, "ccr2116", "sfp-sfpplus4", rx_fcs=3, rx_err=4)]
        cable = self.cables(lans, [])["CCR2116 ↔ CRS326"]
        # CCR2116 is end A: B -> A (CRS326 -> CCR2116) is what the CCR2116 received.
        self.assertEqual(cable["b_to_a"]["received"]["last"], 7)
        self.assertEqual(cable["b_to_a"]["level"], "red")
        # The CRS326's end isn't reported here, so A -> B isn't read.
        self.assertIsNone(cable["a_to_b"]["received"])
        self.assertIsNone(cable["a_to_b"]["level"])

    def test_errors_earlier_in_the_day_but_none_now_is_amber(self):
        lans = [lan(m, "crs326", "sfp-sfpplus2", link_downs=0, rx_fcs=f, rx_err=0, tx_drop=0)
                for m, f in ((0, 0), (5, 30), (10, 30), (15, 30))]
        way = self.cables(lans, [])["CCR2004 ↔ CRS326"]["a_to_b"]
        self.assertEqual((way["received"]["last"], way["received"]["day"], way["level"]), (0, 30, "amber"))

    def test_lifetime_totals_alone_never_colour_a_direction(self):
        lans = [lan(m, "crs326", "sfp-sfpplus2", rx_fcs=5000, rx_err=0) for m in (0, 5, 10, 15)]
        way = self.cables(lans, [])["CCR2004 ↔ CRS326"]["a_to_b"]
        self.assertEqual((way["level"], way["received"]["total"], way["received"]["day"]), ("green", 5000, 0))

    def test_link_downs_and_tx_drops_are_per_end(self):
        lans = [lan(m, "crs326", "sfp-sfpplus2", link_downs=d, tx_drop=t) for m, d, t in ((0, 4, 0), (5, 5, 20), (10, 5, 20), (15, 5, 20))]
        lans += [lan(m, "ccr2004", "sfp-sfpplus1", link_downs=2, tx_drop=7) for m in (0, 5, 10, 15)]
        ends = {e["label"]: e for e in self.cables(lans, [])["CCR2004 ↔ CRS326"]["ends"]}
        self.assertEqual((ends["CRS326"]["link_downs"], ends["CRS326"]["tx_drop"]),
                         ({"day": 1, "total": 5}, {"day": 20, "total": 20}))
        self.assertEqual((ends["CCR2004"]["link_downs"]["day"], ends["CCR2004"]["tx_drop"]["total"]), (0, 7))

    def test_the_olt_end_has_no_link_down_or_tx_counters(self):
        ends = {e["label"]: e for e in self.olt_cable()["ends"]}
        self.assertTrue(ends["OLT"]["reported"])
        self.assertIsNone(ends["OLT"]["link_downs"])
        self.assertEqual(ends["CRS326"]["link_downs"]["total"], 19)

    def test_the_proxmox_end_is_not_monitored(self):
        lans = [lan(m, "crs326", "sfp-sfpplus4", rx_fcs=0, rx_err=0) for m in (10, 15)]
        cable = self.cables(lans, [])["CRS326 ↔ Proxmox"]
        self.assertFalse(cable["ends"][1]["reported"])
        self.assertIsNone(cable["a_to_b"]["received"])            # what Proxmox receives: unknown
        self.assertEqual(cable["b_to_a"]["level"], "green")        # what the CRS326 receives from it

    def test_an_old_reading_is_not_a_5_minute_figure(self):
        lans = [lan(-60, "crs326", "sfp-sfpplus2", rx_fcs=0), lan(-55, "crs326", "sfp-sfpplus2", rx_fcs=50)]
        end = self.cables(lans, [])["CCR2004 ↔ CRS326"]["ends"][1]
        self.assertTrue(end["stale"])
        self.assertIsNone(end["received"]["last"])
        self.assertEqual(end["received"]["day"], 50)

    def test_a_restart_or_failed_olt_read_leaves_the_olt_direction_unread(self):
        failed = [olt_reading(0, 100, 0), {"ts": T0 + timedelta(minutes=15), "olt": {"uptime_s": None, "up_rx": None, "up_crc": None}}]
        way = self.cables([], failed)["CRS326 ↔ OLT"]["a_to_b"]
        self.assertIsNone(way["received"]["last"])
        self.assertIsNone(way["level"])

    def test_an_unlisted_port_shows_as_its_own_row(self):
        lans = [lan(10, "ccr2004", "sfp-sfpplus3", rx_fcs=0), lan(15, "ccr2004", "sfp-sfpplus3", rx_fcs=0)]
        names = list(self.cables(lans, []))
        self.assertEqual(names[-1], "CCR2004 sfp-sfpplus3")


class Paths(unittest.TestCase):
    def checks(self, loss, rtt=5.0, name="i-fra"):
        return rules.check_path({"name": name, "loss_pct": loss, "rtt_ms": rtt}, "ccr2004", "INNER_PATH_LOSS",
                                "INNER_PATH_DOWN")

    def test_loss_of_20_pct_three_readings_in_a_row_fires(self):
        self.assertEqual(run([self.checks(20)[0], self.checks(30)[0], self.checks(20)[0]]), [None, None, "fire"])
        self.assertFalse(self.checks(10)[0]["verdict"])

    def test_two_outage_readings_fire_down_and_not_loss(self):
        outage = self.checks(100, rtt=None)
        self.assertIsNone(outage[0]["verdict"])
        self.assertEqual(run([self.checks(100, None)[1], self.checks(100, None)[1]]), [None, "fire"])
        self.assertEqual(run([self.checks(100, None)[1], self.checks(100, None)[1], self.checks(0)[1]]),
                         [None, "fire", "clear"])


class LossScale(unittest.TestCase):
    """Real percentages from 21 Nov 2026 17:13: 1 of 10 pings lost is 10."""

    def parse(self, loss, rtt):
        return feed.parse_record({"key": "k", "ts": "2026-11-21T18:00:00+03:00", "kind": "probe",
                                  "probes": [{"name": "p-gw", "rtt_ms": rtt, "jitter_ms": 0.2, "loss_pct": loss}]})["probes"][0]

    def test_one_lost_ping_keeps_its_round_trip(self):
        self.assertEqual((self.parse(10, 2.1)["loss_pct"], self.parse(10, 2.1)["rtt_ms"]), (10, 2.1))

    def test_full_outage_is_100_and_null(self):
        probe = self.parse(100, None)
        self.assertEqual((probe["loss_pct"], probe["rtt_ms"]), (100, None))
        readings = [{"ts": T0 + timedelta(minutes=m), "probes": [probe]} for m in range(5)]
        check = rules.check_loss(readings, T0 + timedelta(minutes=4), "p-gw")
        self.assertTrue(check["facts"]["outage"])
        self.assertEqual(check["severity"], rules.RED)

    def test_the_old_tenths_scale_is_never_judged(self):
        self.assertIsNone(self.parse(1000, None)["loss_pct"])


class Parse(unittest.TestCase):
    def record(self, kind, **body):
        return {"key": "k", "ts": "2026-11-21T18:00:00+03:00", "kind": kind, **body}

    def test_hop_area_lanlink(self):
        hop = feed.parse_record(self.record("hop", probes=[{"name": "h-crs", "rtt_ms": 0.25, "jitter_ms": 0.08,
                                                             "loss_pct": 0.0}]))
        self.assertEqual(hop["probes"][0]["name"], "h-crs")
        self.assertIsNone(hop["load"])
        got = feed.parse_record(self.record("area", area={"vlan": 5, "if": "sfp-sfpplus1-v5",
                                                         "down_bytes": 4487076792201, "up_bytes": 529441235403}))
        self.assertEqual(got["area"], {"vlan": 5, "if": "sfp-sfpplus1-v5", "down_bytes": 4487076792201,
                                       "up_bytes": 529441235403})
        got = feed.parse_record(self.record("lanlink", lanlink={"dev": "crs326", "port": "sfp-sfpplus3", "link_downs": 19,
                                                               "rx_fcs": 0, "rx_err": 8, "rx_overflow": None, "tx_drop": 0}))
        self.assertEqual(got["lanlink"], {"dev": "crs326", "port": "sfp-sfpplus3", "link_downs": 19, "rx_fcs": 0,
                                          "rx_err": 8, "rx_overflow": None, "tx_drop": 0})

    def test_unplaceable_records_are_skipped(self):
        for kind, body in (("area", {"area": {"down_bytes": 1}}), ("lanlink", {"lanlink": {"port": "x"}}),
                           ("area", {}), ("lanlink", {})):
            with self.subTest(kind=kind, body=body), self.assertRaises(feed.SkipRecord):
                feed.parse_record(self.record(kind, **body))


if __name__ == "__main__":
    unittest.main()
