"""Every Network Health SMS fits ONE segment: <= 160 characters, GSM-7 basic
characters only, filled with the longest realistic values.
Run: venv/bin/python -m unittest tests.test_network_health_messages -v
"""
import unittest
from datetime import datetime, timezone

from network_health import messages, rules

SINCE = datetime(2026, 11, 20, 20, 40, tzinfo=timezone.utc)
LONGEST_DEVICE = max(rules.DEVICE_LABELS, key=lambda d: len(rules.DEVICE_LABELS[d]))
# The worst values each rule's facts can realistically carry.
LONG_FACTS = {
    "UPLINK_CONGESTED": [{"pct": 999.9, "minutes": 5}],
    "SPEED_LOW": [{"mbps": 9999.9, "usual": 9999.9, "load": 99999}, {"failed": True}],
    "LATENCY_HIGH": [{"ms": 99999, "usual": 99999, "probe": "p-fra"}, {"ms": 99999, "usual": None, "limit": 99999,
                                                                       "probe": "p-fra"}],
    "LOSS": [{"loss": 100, "probe": "p-gw", "outage": False}, {"loss": 100, "probe": "p-gw", "outage": True}],
    "JITTER_HIGH": [{"ms": 99999, "probe": "p-gw"}],
    "CPU_HIGH": [{"pct": 100}],
    "OPTIC_DEGRADING": [{"rx": -40.123, "usual": -39.9}],
    "LINK_DOWN": [{"link_downs": 99999}],
    "LINK_ERRORS": [{"errors": 99999}],
    "DNS_ROUTER": [{"ms": 99999, "fails": 2}, {"ms": 99999, "fails": 0}],
    "ROUTER_SILENT": [{"minutes": 99999}],
    "FEED_SILENT": [{"minutes": 99999}],
    "OLT_CRC": [{"pct": 100}, {"pct": 0.0001}],
    "OLT_RESTART": [{"previous_uptime_s": 9_999_999_999, "uptime_s": 99999}, {"previous_uptime_s": None}],
    "OLT_LINK_DOWN": [{"interface": "x" * 40}],
    "OLT_SNMP_SILENT": [{"minutes": 99999}],
    "INNER_PATH_LOSS": [{"loss": 100, "probe": "x" * 40}],
    "INNER_PATH_DOWN": [{"loss": 100, "probe": "x" * 40}],
    "CUSTOMER_PATH_LOSS": [{"loss": 100, "probe": "x" * 40}],
    "CUSTOMER_PATH_DOWN": [{"loss": 100, "probe": "x" * 40}],
    "AREA_SILENT": [{"area": "x" * 80, "vlan": 9999, "mbps": 0, "usual": 99999}],
    "LANLINK_DOWN": [{"label": "x" * 80, "count": 99999}],
    "LANLINK_DAMAGE": [{"label": "x" * 80, "count": 9_999_999}],
    "LANLINK_DROPS": [{"label": "x" * 80, "count": 9_999_999}],
}


class OneSegment(unittest.TestCase):
    def assert_one_segment(self, text):
        self.assertLessEqual(len(text), 160, text)
        self.assertTrue(messages.is_gsm7(text), f"not GSM-7: {text!r}")

    def test_every_fire_message(self):
        for rule, variants in LONG_FACTS.items():
            for facts in variants:
                with self.subTest(rule=rule, facts=facts):
                    self.assert_one_segment(messages.fire_message(LONGEST_DEVICE, rule, facts, SINCE))

    def test_every_clear_message(self):
        for rule in LONG_FACTS:
            with self.subTest(rule=rule):
                self.assert_one_segment(messages.clear_message(LONGEST_DEVICE, rule, "p-gw", 99999))

    def test_combined_and_cap(self):
        self.assert_one_segment(messages.combined_fire_message(LONGEST_DEVICE, 99, "UPLINK_CONGESTED"))
        self.assert_one_segment(messages.combined_clear_message(LONGEST_DEVICE, 99))
        self.assert_one_segment(messages.cap_message(9999))

    def test_the_owners_examples_read_right(self):
        text = messages.fire_message("ccr2116", "UPLINK_CONGESTED", {"pct": 92, "minutes": 5}, SINCE)
        self.assertEqual(text, "JNET ALERT CCR2116: uplink 92% full (5min avg). Customers slowing. Since 23:40")
        self.assertEqual(messages.clear_message("ccr2116", "SPEED_LOW", "", 25),
                         "JNET OK CCR2116: night speed test back to normal. Lasted 25min")

    def test_every_rule_has_a_template(self):
        self.assertEqual(set(LONG_FACTS), set(rules.RULE_LABELS))

    def test_new_rules_read_right(self):
        text = messages.fire_message("ccr2116", "LATENCY_HIGH", {"ms": 251, "usual": 204, "probe": "p-fra"}, SINCE)
        self.assertEqual(text, "JNET ALERT CCR2116: latency to Frankfurt 251ms, usual 204ms. Since 23:40")
        text = messages.fire_message("ccr2116", "LINK_DOWN", {"link_downs": 1}, SINCE)
        self.assertEqual(text, "JNET ALERT CCR2116: uplink link dropped 1x in 5min. Customers cut off. Since 23:40")
        self.assertEqual(messages.clear_message("ccr2116", "LINK_DOWN", "", 5),
                         "JNET OK CCR2116: uplink link back to normal. Lasted 5min")

    def test_inside_network_texts(self):
        self.assertEqual(messages.fire_message("olt", "OLT_CRC", {"pct": 10.23}, SINCE),
                         "JNET ALERT OLT: OLT uplink damaging frames, 10.2% of frames to customers (CRS326-OLT link). "
                         "Since 23:40")
        self.assertEqual(messages.fire_message("olt", "OLT_RESTART", {"previous_uptime_s": 626553.26, "uptime_s": 120},
                                               SINCE),
                         "JNET ALERT OLT: OLT restarted (power switchover?), had been up 7d6h. Back up at 23:38")
        self.assertEqual(messages.fire_message("olt", "OLT_LINK_DOWN", {"interface": "sfp-sfpplus3"}, SINCE),
                         "JNET ALERT OLT: OLT link DOWN (CRS326 sfp-sfpplus3). Customers cut off. Since 23:40")
        self.assertEqual(messages.clear_message("olt", "OLT_LINK_DOWN", "sfp-sfpplus3", 133 / 60),
                         "JNET OK OLT: OLT link back after 2 min 13 s")
        self.assertEqual(messages.fire_message("olt", "OLT_SNMP_SILENT", {"minutes": 15}, SINCE),
                         "JNET ALERT OLT: OLT not answering SNMP for 15min. Since 23:25")

    def test_inside_network_part_2_texts(self):
        self.assertEqual(messages.fire_message("ccr2004", "INNER_PATH_LOSS", {"loss": 30, "probe": "i-fra-big"}, SINCE),
                         "JNET ALERT CCR2004: CCR2004 path out losing packets, 30% loss on i-fra-big. Since 23:40")
        self.assertEqual(messages.fire_message("ccr2004", "INNER_PATH_DOWN", {"loss": 100, "probe": "i-fra"}, SINCE),
                         "JNET ALERT CCR2004: CCR2004 path out DOWN, no reply on i-fra. Since 23:40")
        self.assertEqual(messages.fire_message("ccr2004", "AREA_SILENT", {"area": "Njeri House"}, SINCE),
                         "JNET ALERT CCR2004: Area Njeri House has gone silent: possible power or fibre cut at the "
                         "site. Since 23:40")
        self.assertEqual(messages.fire_message("crs326", "LANLINK_DOWN", {"label": "CRS326 → CCR2116"}, SINCE),
                         "JNET ALERT CRS326: Link CRS326 to CCR2116 went down. Since 23:40")
        self.assertEqual(messages.clear_message("crs326", "LANLINK_DOWN", "crs326 sfp-sfpplus1", 5),
                         "JNET OK CRS326: link CRS326 to CCR2116 back to normal. Lasted 5min")

    def test_every_clear_message_with_a_long_subject(self):
        for rule in LONG_FACTS:
            with self.subTest(rule=rule):
                self.assert_one_segment(messages.clear_message(LONGEST_DEVICE, rule, "x" * 80, 99999))

    def test_silence_since_is_when_the_data_stopped(self):
        text = messages.fire_message("ccr2004", "ROUTER_SILENT", {"minutes": 10}, SINCE)
        self.assertTrue(text.endswith("Since 23:30"), text)

    def test_fit_is_the_safety_net(self):
        self.assertEqual(messages.fit("a—b"), "a?b")
        self.assertEqual(len(messages.fit("x" * 300)), 160)
        self.assertFalse(messages.is_gsm7("curly ’ quote"))
        self.assertFalse(messages.is_gsm7("[bracket]"))


if __name__ == "__main__":
    unittest.main()
