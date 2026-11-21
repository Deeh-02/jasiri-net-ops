"""Where content is served from: the classification and the "changed" rule.
Pure code, no database. Run:
  venv/bin/python -m unittest tests.test_network_health_routes -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from network_health import feed, routes

EAT = timezone(timedelta(hours=3))


def at(day, hour, minute=0):
    """A moment on 2026-11-<day> in Nairobi time."""
    return datetime(2026, 11, day, hour, minute, tzinfo=EAT).astimezone(timezone.utc)


def record(ts, last_ms, host="www.youtube.com", status="ok"):
    return {"ts": ts, "route": {"host": host, "ip": None if status == "failed" else "142.251.157.4",
                                "status": status, "last_ms": last_ms, "hops": []}}


class WhereServed(unittest.TestCase):
    def test_under_15_ms_is_kenya(self):
        self.assertEqual(routes.where_served(7.2), routes.KENYA)
        self.assertEqual(routes.where_served(14.99), routes.KENYA)

    def test_15_up_to_140_is_elsewhere_in_africa_or_unclear(self):
        self.assertEqual(routes.where_served(15), routes.AFRICA)
        self.assertEqual(routes.where_served(139.9), routes.AFRICA)

    def test_140_and_over_is_abroad(self):
        self.assertEqual(routes.where_served(140), routes.ABROAD)
        self.assertEqual(routes.where_served(203.3), routes.ABROAD)

    def test_no_number_is_no_verdict(self):
        self.assertIsNone(routes.where_served(None))

    def test_a_failed_check_has_no_verdict_even_with_a_number(self):
        self.assertIsNone(routes.verdict_of({"status": "failed", "last_ms": 7}))
        self.assertIsNone(routes.verdict_of(None))

    def test_50_up_to_110_is_south_africa_likely(self):
        self.assertEqual(routes.where_served(49.9), routes.AFRICA)
        self.assertEqual(routes.where_served(50), routes.SOUTH_AFRICA)
        self.assertEqual(routes.where_served(72), routes.SOUTH_AFRICA)      # 1.1.1.1 today
        self.assertEqual(routes.where_served(109.9), routes.SOUTH_AFRICA)
        self.assertEqual(routes.where_served(110), routes.AFRICA)

    def test_every_verdict_has_a_word(self):
        self.assertEqual(set(routes.VERDICT_LABELS), {routes.KENYA, routes.AFRICA, routes.SOUTH_AFRICA, routes.ABROAD})
        self.assertEqual(routes.VERDICT_LABELS[routes.SOUTH_AFRICA], "South Africa (likely)")


class InternationalPath(unittest.TestCase):
    def frankfurt(self, hops, status="ok", last_ms=203.1):
        return {"ts": at(21, 21), "route": {"host": "195.238.122.164", "ip": "195.238.122.164", "status": status,
                                            "last_ms": last_ms, "hops": hops}}

    def test_labels_and_group(self):
        records = [record(at(21, 21), 7, host="www.youtube.com"), record(at(21, 21), 72, host="1.1.1.1"),
                   self.frankfurt([])]
        rows = routes.site_rows(records)
        self.assertEqual([(r["host"], r["label"], r["group"]) for r in rows], [
            ("www.youtube.com", "www.youtube.com", routes.CONTENT),
            ("195.238.122.164", "Frankfurt (our server)", routes.INTERNATIONAL),
            ("1.1.1.1", "Cloudflare DNS", routes.INTERNATIONAL)])
        self.assertEqual(rows[2]["verdict"], routes.SOUTH_AFRICA)

    def test_content_sites_come_first_even_unknown_ones(self):
        records = [record(at(21, 21), 72, host="1.1.1.1"), record(at(21, 21), 7, host="example.org")]
        self.assertEqual([r["group"] for r in routes.site_rows(records)], [routes.CONTENT, routes.INTERNATIONAL])

    def test_stopping_at_the_hop_limit_is_not_a_failure(self):
        hops = [{"hop": n, "address": None if n == 20 else f"10.0.0.{n}", "ms": None if n == 20 else n * 10.0}
                for n in range(1, 21)]
        row = routes.site_rows([self.frankfurt(hops, last_ms=190.0)])[0]
        self.assertEqual(row["verdict"], routes.ABROAD)
        self.assertIs(row["reached_target"], False)
        self.assertEqual((row["last_hop_number"], row["last_hop_address"]), (19, "10.0.0.19"))

    def test_an_unusual_status_with_a_last_hop_still_has_a_verdict(self):
        self.assertEqual(routes.verdict_of({"status": "incomplete", "last_ms": 190}), routes.ABROAD)

    def test_reaching_the_target(self):
        hops = [{"hop": 1, "address": "102.0.27.189", "ms": 1.0}, {"hop": 2, "address": "195.238.122.164", "ms": 203.0}]
        self.assertIs(routes.site_rows([self.frankfurt(hops)])[0]["reached_target"], True)
        self.assertIsNone(routes.reached_target({"ip": None, "hops": hops}))


class Slot(unittest.TestCase):
    def test_21_00_is_peak_and_04_00_is_night(self):
        self.assertEqual(routes.slot(at(20, 21, 0)), routes.PEAK)
        self.assertEqual(routes.slot(at(20, 21, 4)), routes.PEAK)
        self.assertEqual(routes.slot(at(21, 4, 0)), routes.NIGHT)
        self.assertEqual(routes.slot(at(21, 4, 3)), routes.NIGHT)


class Changed(unittest.TestCase):
    def rows(self, records):
        return {row["host"]: row for row in routes.site_rows(records)}

    def test_was_kenya_now_abroad(self):
        row = self.rows([record(at(20, 21), 7.2), record(at(21, 21), 180)])["www.youtube.com"]
        self.assertEqual(row["change"], {"was": routes.KENYA, "now": routes.ABROAD})
        self.assertEqual(row["previous_checked_at"], at(20, 21))

    def test_same_verdict_is_no_change(self):
        row = self.rows([record(at(20, 21), 7.2), record(at(21, 21), 9.0)])["www.youtube.com"]
        self.assertIsNone(row["change"])

    def test_peak_is_only_compared_with_peak(self):
        # Night served from Kenya, peak from abroad both days: the latest peak
        # matches the previous peak, so nothing changed.
        row = self.rows([record(at(20, 21), 180), record(at(21, 4), 7.2), record(at(21, 21), 185)])["www.youtube.com"]
        self.assertIsNone(row["change"])
        self.assertEqual(row["slot"], routes.PEAK)

    def test_night_is_only_compared_with_night(self):
        row = self.rows([record(at(20, 4), 7.2), record(at(20, 21), 180), record(at(21, 4), 160)])["www.youtube.com"]
        self.assertEqual(row["change"], {"was": routes.KENYA, "now": routes.ABROAD})
        self.assertEqual(row["previous_checked_at"], at(20, 4))

    def test_a_failed_latest_check_is_not_a_change(self):
        row = self.rows([record(at(20, 21), 7.2), record(at(21, 21), None, status="failed")])["www.youtube.com"]
        self.assertIsNone(row["change"])
        self.assertIsNone(row["verdict"])
        self.assertEqual(row["status"], "failed")

    def test_a_failed_previous_check_is_skipped_for_the_one_before(self):
        row = self.rows([record(at(19, 21), 7.2), record(at(20, 21), None, status="failed"),
                         record(at(21, 21), 180)])["www.youtube.com"]
        self.assertEqual(row["change"], {"was": routes.KENYA, "now": routes.ABROAD})
        self.assertEqual(row["previous_checked_at"], at(19, 21))

    def test_the_first_check_has_nothing_to_compare(self):
        row = self.rows([record(at(21, 21), 180)])["www.youtube.com"]
        self.assertIsNone(row["change"])
        self.assertIsNone(row["previous_checked_at"])

    def test_order_does_not_matter_and_sites_are_separate(self):
        records = [record(at(21, 21), 180), record(at(21, 21), 6, host="api.safaricom.co.ke"),
                   record(at(20, 21), 7.2), record(at(20, 21), 6, host="api.safaricom.co.ke")]
        rows = self.rows(records)
        self.assertEqual(rows["www.youtube.com"]["change"], {"was": routes.KENYA, "now": routes.ABROAD})
        self.assertIsNone(rows["api.safaricom.co.ke"]["change"])

    def test_rows_follow_the_contract_order(self):
        records = [record(at(21, 21), 6, host=h) for h in
                   ("example.org", "api.safaricom.co.ke", "www.tiktok.com", "www.facebook.com", "www.youtube.com")]
        self.assertEqual([r["host"] for r in routes.site_rows(records)],
                         ["www.youtube.com", "www.facebook.com", "www.tiktok.com", "api.safaricom.co.ke", "example.org"])


class Parse(unittest.TestCase):
    def raw(self, route):
        return {"key": "k1", "ts": "2026-11-20T21:00:04+03:00", "from_ip": "102.0.27.188", "kind": "route",
                "raw": "ROUTE ...", "route": route}

    def test_the_contract_example(self):
        parsed = feed.parse_record(self.raw({
            "host": "www.youtube.com", "ip": "142.251.157.4", "status": "ok", "last_ms": 7.2,
            "hops": [{"hop": 1, "address": "102.0.27.189", "ms": 1.0}, {"hop": 2, "address": None, "ms": None}],
            "extra": "ignored"}))
        self.assertEqual(parsed["kind"], "route")
        self.assertEqual(parsed["route"], {
            "host": "www.youtube.com", "ip": "142.251.157.4", "status": "ok", "last_ms": 7.2,
            "hops": [{"hop": 1, "address": "102.0.27.189", "ms": 1.0}, {"hop": 2, "address": None, "ms": None}]})

    def test_failed_comes_with_nulls(self):
        parsed = feed.parse_record(self.raw({"host": "www.tiktok.com", "ip": None, "status": "failed",
                                             "last_ms": None}))
        self.assertEqual(parsed["route"], {"host": "www.tiktok.com", "ip": None, "status": "failed",
                                           "last_ms": None, "hops": []})

    def test_without_a_host_or_route_object_it_is_skipped(self):
        with self.assertRaises(feed.SkipRecord):
            feed.parse_record(self.raw({"ip": "1.2.3.4", "status": "ok", "last_ms": 5}))
        with self.assertRaises(feed.SkipRecord):
            feed.parse_record(self.raw(None))


if __name__ == "__main__":
    unittest.main()
