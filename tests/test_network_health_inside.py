"""Inside network's pure logic: OLT restarts, damaged frames (with the
32-bit wrap and failed polls) and pairing CRS326 port downs with ups.
Run: venv/bin/python -m unittest tests.test_network_health_inside -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from network_health import feed, inside

T0 = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)


def olt(minutes, uptime, rx, crc):
    return {"ts": T0 + timedelta(minutes=minutes), "olt": {"uptime_s": uptime, "up_rx": rx, "up_crc": crc}}


def link(seconds, interface, state, speed=None):
    return {"ts": T0 + timedelta(seconds=seconds),
            "crs_link": {"device": "crs326", "interface": interface, "state": state, "speed": speed}}


class DamagedFrames(unittest.TestCase):
    def test_the_owners_formula(self):
        # 900 good + 100 errored frames in 5 minutes = 10 % damaged.
        steps = inside.walk_olt([olt(0, 1000, 10_000, 500), olt(5, 1300, 10_900, 600)])
        interval = steps[1]["interval"]
        self.assertEqual((interval["d_rx"], interval["d_crc"]), (900, 100))
        self.assertAlmostEqual(interval["crc_pct"], 10.0)
        self.assertIsNone(steps[0]["interval"])

    def test_the_32_bit_error_counter_wraps(self):
        steps = inside.walk_olt([olt(0, 1000, 0, 2**32 - 10), olt(5, 1300, 990, 0)])
        self.assertEqual(steps[1]["interval"]["d_crc"], 10)
        self.assertAlmostEqual(steps[1]["interval"]["crc_pct"], 1.0)

    def test_no_traffic_is_no_figure(self):
        steps = inside.walk_olt([olt(0, 1000, 5, 5), olt(5, 1300, 5, 5)])
        self.assertIsNone(steps[1]["interval"]["crc_pct"])

    def test_cleared_counters_are_skipped_not_a_wrap(self):
        steps = inside.walk_olt([olt(0, 1000, 10_000, 500), olt(5, 1300, 100, 3)])
        self.assertIsNone(steps[1]["interval"])

    def test_a_failed_poll_skips_both_its_pairs_and_counts(self):
        steps = inside.walk_olt([olt(0, 1000, 0, 0), olt(5, None, 50, 1), olt(10, 1600, 100, 2), olt(15, 1900, 200, 2)])
        self.assertEqual([s["failed"] for s in steps], [False, True, False, False])
        self.assertEqual([s["interval"] is None for s in steps], [True, True, True, False])

    def test_levels(self):
        self.assertEqual([inside.crc_level(v) for v in (None, 0, 0.0099, 0.01, 1, 1.01, 10.2)],
                         [None, "green", "green", "amber", "amber", "red", "red"])

    def test_buckets_weigh_by_frames_not_by_interval(self):
        # A busy 5 min at 10 % and a quiet 5 min at 50 %: 1,010 of 10,100 frames.
        steps = inside.walk_olt([olt(0, 1000, 0, 0), olt(5, 1300, 9_000, 1_000), olt(10, 1600, 9_090, 1_010)])
        bucket = inside.crc_buckets(steps, 1800)
        self.assertEqual(len(bucket), 1)
        self.assertAlmostEqual(bucket[0]["pct"], 1010 / 10100 * 100)
        self.assertAlmostEqual(inside.overall_pct(steps), 1010 / 10100 * 100)


class Restarts(unittest.TestCase):
    def test_uptime_going_down_is_a_restart_and_no_delta_across_it(self):
        steps = inside.walk_olt([olt(0, 626_553.26, 8_000, 900), olt(5, 120, 50, 0), olt(10, 420, 150, 0)])
        restart = steps[1]["restart"]
        self.assertEqual(restart["previous_uptime_s"], 626_553.26)
        self.assertEqual(restart["booted_at"], T0 + timedelta(minutes=5) - timedelta(seconds=120))
        self.assertIsNone(steps[1]["interval"])
        self.assertEqual(steps[2]["interval"]["d_rx"], 100)

    def test_a_restart_behind_a_failed_uptime_read_is_still_seen(self):
        steps = inside.walk_olt([olt(0, 5000, 0, 0), olt(5, None, None, None), olt(10, 200, 10, 0)])
        self.assertEqual(steps[2]["restart"]["previous_uptime_s"], 5000)

    def test_uptime_going_up_is_not_a_restart(self):
        steps = inside.walk_olt([olt(0, 5000, 0, 0), olt(5, 5300, 10, 0)])
        self.assertEqual([s["restart"] for s in steps], [None, None])


class LinkEvents(unittest.TestCase):
    def test_down_pairs_with_the_next_up_on_the_same_port(self):
        rows = inside.link_events([link(0, "sfp-sfpplus3", "down"), link(30, "sfp-sfpplus1", "down"),
                                   link(133, "sfp-sfpplus3", "up", "10G")])
        olt_row = next(r for r in rows if r["interface"] == "sfp-sfpplus3")
        self.assertEqual((olt_row["status"], olt_row["outage_s"], olt_row["speed"]), ("back", 133, "10G"))
        self.assertTrue(olt_row["is_olt"])
        self.assertEqual(olt_row["label"], "OLT uplink")
        other = next(r for r in rows if r["interface"] == "sfp-sfpplus1")
        self.assertEqual((other["status"], other["label"], other["is_olt"]), ("down", "CCR2116", False))

    def test_newest_first(self):
        rows = inside.link_events([link(0, "sfp-sfpplus2", "down"), link(5, "sfp-sfpplus2", "up"),
                                   link(60, "sfp-sfpplus2", "down"), link(70, "sfp-sfpplus2", "up")])
        self.assertEqual([r["outage_s"] for r in rows], [10, 5])

    def test_a_missed_up_and_an_unpaired_up(self):
        rows = inside.link_events([link(0, "sfp-sfpplus3", "up"), link(10, "sfp-sfpplus3", "down"),
                                   link(20, "sfp-sfpplus3", "down"), link(25, "sfp-sfpplus3", "up")])
        self.assertEqual([r["status"] for r in rows], ["back", "no_up", "up_only"])


class Parse(unittest.TestCase):
    def record(self, kind, body):
        return {"key": "k1", "ts": "2026-11-21T11:00:00+03:00", "from_ip": "102.0.27.188", "kind": kind,
                "raw": "line", kind: body}

    def test_the_contract_examples(self):
        parsed = feed.parse_record(self.record("olt", {"uptime_s": 626553.26, "up_rx": 8712156132,
                                                       "up_crc": 1062606263}))
        self.assertEqual(parsed["olt"], {"uptime_s": 626553.26, "up_rx": 8712156132, "up_crc": 1062606263})
        self.assertIsInstance(parsed["olt"]["up_rx"], int)
        parsed = feed.parse_record(self.record("crs_link", {"device": "crs326", "interface": "sfp-sfpplus3",
                                                            "state": "up", "speed": "10G"}))
        self.assertEqual(parsed["crs_link"], {"device": "crs326", "interface": "sfp-sfpplus3", "state": "up",
                                              "speed": "10G"})

    def test_a_failed_snmp_read_is_kept_as_null(self):
        parsed = feed.parse_record(self.record("olt", {"uptime_s": None, "up_rx": "x", "up_crc": 5}))
        self.assertEqual(parsed["olt"], {"uptime_s": None, "up_rx": None, "up_crc": 5})

    def test_unreadable_records_are_skipped(self):
        for kind, body in (("olt", None), ("crs_link", {"interface": "sfp-sfpplus3", "state": "flapping"}),
                           ("crs_link", {"state": "down"})):
            with self.subTest(kind=kind, body=body), self.assertRaises(feed.SkipRecord):
                feed.parse_record(self.record(kind, body))


if __name__ == "__main__":
    unittest.main()
