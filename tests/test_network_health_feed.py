"""Reading the Frankfurt shipper's records (docs/network-health-ingest-contract.md).
Run: venv/bin/python -m unittest tests.test_network_health_feed -v
"""
import unittest
from datetime import datetime, timezone

from network_health import feed


def record(**overrides):
    base = {"key": "k1", "ts": "2026-11-20T18:40:01+03:00", "from_ip": "102.0.27.188", "kind": "probe",
            "raw": "probe-log ...",
            "load": {"down_mbps": 242, "up_mbps": 27, "new_down_mbps": None, "ppp": 29, "cpu_pct": 3, "conns": 17556},
            "probes": [{"name": "p-fra", "rtt_ms": 203.309, "jitter_ms": 0.173, "loss_pct": 0}]}
    base.update(overrides)
    return base


class Records(unittest.TestCase):
    def test_probe_record(self):
        parsed = feed.parse_record(record())
        self.assertEqual(parsed["ts"], datetime(2026, 11, 20, 15, 40, 1, tzinfo=timezone.utc))
        self.assertEqual(parsed["load"]["down_mbps"], 242)
        self.assertIsNone(parsed["load"]["new_down_mbps"])
        self.assertEqual(parsed["probes"][0]["name"], "p-fra")

    def test_outage_rtt_stays_null_never_zero(self):
        parsed = feed.parse_record(record(probes=[{"name": "p-gw", "rtt_ms": None, "jitter_ms": None, "loss_pct": 100}]))
        self.assertIsNone(parsed["probes"][0]["rtt_ms"])
        zero = feed.parse_record(record(probes=[{"name": "p-gw", "rtt_ms": 0, "jitter_ms": 0, "loss_pct": 100}]))
        self.assertIsNone(zero["probes"][0]["rtt_ms"])

    def test_unknown_kind_is_ignored(self):
        self.assertIsNone(feed.parse_record(record(kind="weather")))

    def test_unknown_fields_are_ignored(self):
        parsed = feed.parse_record(record(extra="x", load={"cpu_pct": 4, "brand_new": 1}))
        self.assertEqual(parsed["load"]["cpu_pct"], 4)
        self.assertNotIn("brand_new", parsed["load"])

    def test_link_and_speed(self):
        link = feed.parse_record(record(kind="link", link={"rx_dbm": -6.324, "tx_drop": 58}))
        self.assertEqual(link["link"]["rx_dbm"], -6.324)
        speed = feed.parse_record(record(kind="speed", speed={"status": "failed", "mbps": None}))
        self.assertEqual(speed["speed"]["status"], "failed")
        self.assertIsNone(speed["speed"]["mbps"])

    def test_bad_records_are_skipped(self):
        for bad in (record(key=None), record(ts="yesterday"), record(ts="2026-11-20T18:40:01"),
                    record(kind="link", link=None), "not an object"):
            with self.assertRaises(feed.SkipRecord):
                feed.parse_record(bad)

    def test_text_is_not_a_number(self):
        parsed = feed.parse_record(record(load={"cpu_pct": "high", "ppp": True}))
        self.assertIsNone(parsed["load"]["cpu_pct"])
        self.assertIsNone(parsed["load"]["ppp"])


class Batches(unittest.TestCase):
    def test_counts_and_duplicate_keys(self):
        parsed, ignored, skipped = feed.parse_batch([record(), record(), record(key="k2", kind="weather"),
                                                     record(key="k3", ts=None)])
        self.assertEqual((len(parsed), ignored, len(skipped)), (1, 1, 1))

    def test_empty_batch_is_a_heartbeat(self):
        self.assertEqual(feed.parse_batch([]), ([], 0, []))


class DnsRecord(unittest.TestCase):
    def raw(self, dns):
        return {"key": "d1", "ts": "2026-11-21T11:05:00+03:00", "from_ip": "102.0.27.188", "kind": "dns",
                "raw": "DNS ...", "dns": dns}

    def test_the_contract_example(self):
        parsed = feed.parse_record(self.raw({"resolvers": [
            {"name": "g8", "server": "8.8.8.8", "worst_ms": 10.4, "fails": 0},
            {"name": "q9", "server": "9.9.9.9", "worst_ms": 1.8, "fails": 0},
            {"name": "rt", "server": "router", "worst_ms": 1.6, "fails": 0, "extra": 1}]}))
        self.assertEqual(parsed["kind"], "dns")
        self.assertEqual(parsed["dns"]["resolvers"][2], {"name": "rt", "server": "router", "worst_ms": 1.6, "fails": 0})

    def test_bad_entries_are_dropped_and_numbers_checked(self):
        parsed = feed.parse_record(self.raw({"resolvers": [{"server": "x"}, "junk",
                                                           {"name": "rt", "worst_ms": "slow", "fails": 2.0}]}))
        self.assertEqual(parsed["dns"]["resolvers"], [{"name": "rt", "server": None, "worst_ms": None, "fails": 2}])

    def test_without_resolvers_it_is_skipped(self):
        for dns in (None, {}, {"resolvers": "rt"}):
            with self.assertRaises(feed.SkipRecord):
                feed.parse_record(self.raw(dns))


if __name__ == "__main__":
    unittest.main()
