"""Access network: PON ports, ONTs and PPPoE sessions (owner, 2026-11-22).
Parsing, light levels, ONT and PPPoE events, groups on a PON, matching,
light degrading, field jobs, splitter groups, and the flow end to end with
the in-memory store.
Run: venv/bin/python -m unittest tests.test_network_health_access -v
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from network_health import access, config, feed, inside, rules, service
from tests.test_network_health_service import NOW, FakeStore

T0 = datetime(2026, 11, 22, 7, 0, tzinfo=timezone.utc)
LIGHT_TEXT, POWER_TEXT = "LOSi/LOBi (light loss)", "dying-gasp (power)"


def ont(pon, ont_id, status="online", rx=-20.0, olt=-22.0, desc=None, distance=1000, cause=None, text=None):
    return {"pon": pon, "ont_id": ont_id, "status": status, "rx_dbm": rx if status == "online" else None,
            "olt_rx_dbm": olt if status == "online" else None, "distance_m": distance, "last_down_cause": cause,
            "last_down_text": text, "desc": desc}


def state(pon, ont_id, status="online", rx=-20.0, olt=-22.0, descr=None, distance=1000, since=T0, ever=True):
    return {"pon": pon, "ont_id": ont_id, "descr": descr, "status": status, "rx_dbm": rx, "olt_rx_dbm": olt,
            "distance_m": distance, "last_down_cause": None, "last_down_text": None, "first_seen_at": T0,
            "last_seen_at": since, "status_since": since, "ever_online": ever, "last_online_at": since}


class Parsing(unittest.TestCase):
    def record(self, kind, body):
        return {"key": "k1", "ts": "2026-11-22T10:00:00+03:00", "kind": kind, "from_ip": "1.2.3.4", "raw": "x",
                kind: body}

    def test_the_contract_examples(self):
        parsed = feed.parse_record(self.record("ont", {
            "pon": "0/3/5", "ont_id": 4, "status": "online", "rx_dbm": -32.21, "olt_rx_dbm": -36.99,
            "distance_m": 4380, "last_down_cause": 13, "last_down_text": POWER_TEXT, "desc": "Stima_Hotspot"}))
        self.assertEqual(parsed["ont"]["ont_id"], 4)
        self.assertEqual(parsed["ont"]["rx_dbm"], -32.21)
        self.assertEqual(parsed["ont"]["desc"], "Stima_Hotspot")
        pon = feed.parse_record(self.record("pon", {"pon": "0/3/5", "online": 4, "offline": 0}))["pon"]
        self.assertEqual(pon, {"pon": "0/3/5", "online": 4, "offline": 0})
        ppp = feed.parse_record(self.record("ppp", {"dev": "CCR2004", "count": 3, "names": ["JS16", "Stima_Hotspot",
                                                                                           "JS16", ""]}))["ppp"]
        self.assertEqual(ppp, {"dev": "ccr2004", "count": 3, "names": ["JS16", "Stima_Hotspot"]})

    def test_nulls_and_nonsense(self):
        parsed = feed.parse_record(self.record("ont", {"pon": "0/3/5", "ont_id": 1, "status": "weird",
                                                       "rx_dbm": -400, "olt_rx_dbm": None, "distance_m": -5}))["ont"]
        self.assertEqual((parsed["status"], parsed["rx_dbm"], parsed["olt_rx_dbm"], parsed["distance_m"]),
                         ("unknown", None, None, None))

    def test_unplaceable_records_are_skipped(self):
        for kind, body in (("ont", {"pon": "0/3/5"}), ("ont", {"ont_id": 3}), ("pon", {"online": 3}),
                           ("ppp", {"dev": "ccr2116", "count": 3}), ("ppp", {"names": []}), ("ont", "x")):
            with self.subTest(kind=kind, body=body), self.assertRaises(feed.SkipRecord):
                feed.parse_record(self.record(kind, body))

    def test_crs_link_device_is_lower_cased(self):
        parsed = feed.parse_record(self.record("crs_link", {"device": "CCR2116", "interface": "sfp-sfpplus3",
                                                            "state": "down"}))
        self.assertEqual(parsed["crs_link"]["device"], "ccr2116")


class RouterPorts(unittest.TestCase):
    def test_only_the_crs326_olt_port_alerts(self):
        self.assertIsNotNone(rules.check_olt_link({"device": "crs326", "interface": "sfp-sfpplus3", "state": "down"}))
        self.assertIsNotNone(rules.check_olt_link({"device": None, "interface": "sfp-sfpplus3", "state": "down"}))
        self.assertIsNone(rules.check_olt_link({"device": "ccr2116", "interface": "sfp-sfpplus3", "state": "down"}))

    def test_events_pair_per_device_and_are_labelled(self):
        def link(minutes, device, state):
            return {"ts": T0 + timedelta(minutes=minutes),
                    "crs_link": {"device": device, "interface": "sfp-sfpplus3", "state": state, "speed": None}}
        rows = inside.link_events([link(0, "crs326", "down"), link(1, "ccr2116", "down"), link(2, "ccr2116", "up")])
        router, olt = rows
        self.assertEqual((router["status"], router["outage_s"], router["is_olt"]), ("back", 60, False))
        self.assertEqual((router["label"], router["device_label"]), ("CCR2116 sfp-sfpplus3", "CCR2116"))
        self.assertEqual((olt["status"], olt["is_olt"], olt["label"]), ("down", True, "OLT uplink"))
        self.assertEqual(inside.port_label("sfp-sfpplus4", "ccr2116"), "CCR2116 → CRS326")


class Light(unittest.TestCase):
    def test_ont_side(self):
        self.assertEqual([access.rx_level(v) for v in (None, -7.9, -8, -24.9, -25.5, -27, -27.1, -32.21)],
                         [None, "overloaded", "good", "good", "weak", "weak", "bad", "bad"])

    def test_olt_side(self):
        self.assertEqual([access.olt_rx_level(v) for v in (-27.9, -28.5, -30, -30.1, -36.99)],
                         ["good", "weak", "weak", "bad", "bad"])

    def test_down_reasons(self):
        self.assertEqual([access.down_kind(t) for t in (LIGHT_TEXT, POWER_TEXT, "ONT reset", "LOFi", None)],
                         ["light", "power", "reboot", "other", None])


class OntEvents(unittest.TestCase):
    def test_a_new_ont_is_a_baseline(self):
        new, event = access.apply_ont(None, ont("0/3/5", 4, desc="Stima_Hotspot"), T0)
        self.assertIsNone(event)
        self.assertEqual((new["status_since"], new["ever_online"], new["descr"]), (T0, True, "Stima_Hotspot"))

    def test_down_then_back_with_the_outage(self):
        old = state("0/3/5", 4, descr="Stima_Hotspot")
        down, event = access.apply_ont(old, ont("0/3/5", 4, "offline", cause=13, text=POWER_TEXT), T0 + timedelta(minutes=5))
        self.assertEqual((event["kind"], event["name"], event["detail"]["why"]), ("ont_down", "Stima_Hotspot", "power"))
        self.assertEqual(down["distance_m"], 1000)       # an offline ONT reports none: the last one is kept
        back, event = access.apply_ont(down, ont("0/3/5", 4), T0 + timedelta(minutes=25))
        self.assertEqual((event["kind"], event["detail"]["outage_s"], event["detail"]["first_time"]),
                         ("ont_back", 1200, False))

    def test_late_and_unknown_readings_are_skipped(self):
        old = state("0/3/5", 4, since=T0)
        self.assertEqual(access.apply_ont(old, ont("0/3/5", 4, "offline"), T0), (None, None))
        self.assertEqual(access.apply_ont(old, ont("0/3/5", 4, "unknown"), T0 + timedelta(minutes=5)), (None, None))

    def test_stale_registration(self):
        new, _ = access.apply_ont(None, ont("0/2/0", 3, "offline", distance=None), T0)
        self.assertTrue(access.is_stale(new))
        back, event = access.apply_ont(new, ont("0/2/0", 3), T0 + timedelta(minutes=5))
        self.assertTrue(event["detail"]["first_time"])
        self.assertFalse(access.is_stale(back))


def down(pon, ont_id, why="light", minutes=0, name=None):
    return {"pon": pon, "ont_id": ont_id, "name": name or f"JS{ont_id:02d}", "at": T0 + timedelta(minutes=minutes),
            "detail": {"why": why, "text": LIGHT_TEXT if why == "light" else POWER_TEXT}}


class Groups(unittest.TestCase):
    def test_polls_split_by_gap_and_pon(self):
        clusters = access.poll_clusters([down("0/3/5", 1), down("0/3/5", 2, minutes=0.5), down("0/3/5", 3, minutes=5),
                                         down("0/3/1", 1)])
        self.assertEqual([len(c) for c in clusters["0/3/5"]], [2, 1])
        self.assertEqual(len(clusters["0/3/1"]), 1)

    def test_kinds(self):
        three_light = [down("p", 1), down("p", 2), down("p", 3, "power")]
        self.assertEqual(access.classify_group(three_light, 2, 6), "fibre")
        self.assertEqual(access.classify_group([down("p", 1, "power"), down("p", 2, "power"), down("p", 3)], 2, 6),
                         "power")
        self.assertEqual(access.classify_group([down("p", 1), down("p", 2)], 0, 2), "whole")
        self.assertIsNone(access.classify_group([down("p", 1)], 0, 1))          # one ONT on its own PON
        self.assertIsNone(access.classify_group([down("p", 1), down("p", 2)], 3, 5))
        self.assertIsNone(access.classify_group([down("p", i, "reboot") for i in (1, 2, 3)], 3, 6))
        self.assertIsNone(access.classify_group([down("p", 1), down("p", 2)], 0, 0))   # no readings: can't say

    def test_recovery_clears_when_at_most_a_quarter_is_left(self):
        onts = {f"p:{i}": state("p", i, "offline") for i in range(1, 5)}
        self.assertEqual(access.group_recovery(list(onts), onts, T0), (4, None))
        for i, minutes in ((1, 30), (2, 20), (3, 40)):
            onts[f"p:{i}"].update(status="online", status_since=T0 + timedelta(minutes=minutes))
        self.assertEqual(access.group_recovery(list(onts), onts, T0), (1, T0 + timedelta(minutes=40)))

    def test_a_second_group_joins_the_open_incident(self):
        first = access.group_check("fibre", "p", [down("p", 1), down("p", 2), down("p", 3)])
        joined = access.group_check("fibre", "p", [down("p", 3), down("p", 4)], first["facts"])
        self.assertEqual(joined["facts"]["onts"], ["p:1", "p:2", "p:3", "p:4"])
        self.assertEqual(joined["facts"]["light"], 4)
        self.assertEqual((first["rule"], first["severity"]), ("PON_FIBRE_FAULT", "RED"))


def session(present=True, minutes=0, missing_minutes=None):
    return {"present": present, "first_seen_at": T0, "last_seen_at": T0 + timedelta(minutes=minutes),
            "missing_since": None if missing_minutes is None else T0 + timedelta(minutes=missing_minutes)}


class Ppp(unittest.TestCase):
    def test_dropped_back_and_new(self):
        sessions = {"JS16": session(), "Stima_Hotspot": session(), "JS03": session(False, 0, 5)}
        new, events, before, dropped = access.apply_ppp(sessions, ["JS16", "JS03", "JS50"], T0 + timedelta(minutes=25))
        self.assertEqual((before, dropped), (2, ["Stima_Hotspot"]))
        self.assertEqual([(e["kind"], e["name"]) for e in events], [("ppp_dropped", "Stima_Hotspot"), ("ppp_back", "JS03")])
        self.assertEqual(events[1]["detail"]["after_s"], 1200)
        self.assertTrue(events[0]["detail"]["site"])
        self.assertTrue(new["JS50"]["present"])

    def test_mass_drop(self):
        self.assertTrue(access.mass_drop_check("ccr2116", 20, 14, ["a"] * 6, 5)["verdict"])
        self.assertFalse(access.mass_drop_check("ccr2116", 20, 15, ["a"] * 5, 5)["verdict"])
        self.assertFalse(access.mass_drop_check("ccr2116", 4, 0, ["a"] * 4, 5)["verdict"])      # too few to say
        self.assertIsNone(access.mass_drop_check("ccr2116", 20, 0, ["a"] * 20, 60)["verdict"])  # not "within 5 min"
        # Open: holds until back to 70 % of the count before the drop.
        self.assertTrue(access.mass_drop_check("ccr2116", 13, 13, [], 5, open_before=20)["verdict"])
        self.assertFalse(access.mass_drop_check("ccr2116", 13, 14, [], 5, open_before=20)["verdict"])

    def test_sites_only(self):
        checks = access.site_checks("ccr2004", {"Stima_Hotspot": session(False, 0, 5), "JS40": session(False)},
                                    T0 + timedelta(minutes=10))
        self.assertEqual([(c["subject"], c["verdict"], c["facts"]["minutes"]) for c in checks],
                         [("Stima_Hotspot", True, 5.0)])


class Matching(unittest.TestCase):
    SESSIONS = {"ccr2116": {"JS16": {}, "JS5": {}, "Mama_Hotspot": {}},
                "ccr2004": {"Stima_HOTSPOT": {}, "KwaMlima_Hotspot": {}, "Twin_Hotspot": {}, "Twin Cyber": {}}}

    def test_codes_and_names(self):
        self.assertEqual([access.js_code(t) for t in ("JS26", "js-5 Mama", "JSX", "AJS12", "JS016")],
                         ["JS26", "JS05", None, None, "JS16"])
        self.assertEqual(access.site_key("Kwa Mlima Cyber"), "kwamlima")
        self.assertEqual(access.site_key("KwaMlima_HOTSPOT"), "kwamlima")
        onts = {"a:1": {"descr": "JS16 Njeri"}, "a:2": {"descr": "JS05"}, "a:3": {"descr": "Stima_Hotspot"},
                "a:4": {"descr": "Kwa Mlima Cyber"}, "a:5": {"descr": "Twin"}, "a:6": {"descr": None},
                "a:7": {"descr": "JS99"}}
        matches = access.match_onts(onts, self.SESSIONS, {"a:7": "Mama_Hotspot", "a:6": None})
        self.assertEqual(matches["a:1"], {"ppp": "JS16", "dev": "ccr2116", "how": "code"})
        self.assertEqual(matches["a:2"]["ppp"], "JS5")
        self.assertEqual(matches["a:3"], {"ppp": "Stima_HOTSPOT", "dev": "ccr2004", "how": "name"})
        self.assertEqual(matches["a:4"]["ppp"], "KwaMlima_Hotspot")
        self.assertNotIn("a:5", matches)                     # two sessions match: none chosen
        self.assertEqual(matches["a:6"]["how"], "manual_none")
        self.assertEqual(matches["a:7"], {"ppp": "Mama_Hotspot", "dev": "ccr2116", "how": "manual"})

    def test_name_and_description_joined_by_the_olt(self):
        sessions = {"ccr2004": {"Njeri_House": {}, "Ndambaki": {}}, "ccr2116": {"JS01": {}}}
        onts = {"a:1": {"descr": "Njeri House _CYBER_descr_Njeri House Cyber"},
                "a:2": {"descr": "Ndambaki_CYBER_descr_Ndambaki Cyber"},
                "a:3": {"descr": "JS01_descr_Charles", "pon": "a", "ont_id": 3}}
        matches = access.match_onts(onts, sessions, {})
        self.assertEqual([matches[k]["ppp"] for k in ("a:1", "a:2", "a:3")], ["Njeri_House", "Ndambaki", "JS01"])
        self.assertEqual(access.ont_name(onts["a:3"]), "JS01 - Charles")

    def test_line_up_no_internet(self):
        onts = {"a:1": state("a", 1, descr="JS16"), "a:2": state("a", 2, "offline", descr="JS17")}
        matches = {"a:1": {"ppp": "JS16", "dev": "ccr2116"}, "a:2": {"ppp": "JS17", "dev": "ccr2116"}}
        sessions = {"JS16": session(False, 0, 5), "JS17": session(False, 0, 5)}
        checks = access.line_checks("ccr2116", sessions, onts, matches, T0 + timedelta(minutes=20))
        self.assertEqual([(c["subject"], c["verdict"]) for c in checks], [("a:1", True), ("a:2", False)])
        self.assertEqual(checks[0]["facts"]["minutes"], 15)


class Degrading(unittest.TestCase):
    HOUR = T0

    def rows(self, ont_id, usual, recent, pon="p"):
        rows = [{"pon": pon, "ont_id": ont_id, "hour": self.HOUR - timedelta(hours=h), "rx": recent} for h in range(1, 25)]
        rows += [{"pon": pon, "ont_id": ont_id, "hour": self.HOUR - timedelta(hours=h), "rx": usual}
                 for h in range(25, 24 * 8 + 1)]
        return rows

    def test_drop_per_ont_and_shared(self):
        rows = self.rows(1, -20, -23.5) + self.rows(2, -21, -23.2) + self.rows(3, -19, -19.5)
        drops = access.light_drops(rows, self.HOUR)
        self.assertAlmostEqual(drops["p:1"]["drop"], 3.5)
        checks = {(c["rule"], c["subject"]): c for c in access.degrade_checks(drops, {})}
        self.assertTrue(checks[("ONT_LIGHT_DEGRADING", "p:1")]["verdict"])
        self.assertFalse(checks[("ONT_LIGHT_DEGRADING", "p:2")]["verdict"])     # 2.2 dB: not on its own
        shared = checks[("PON_LIGHT_DEGRADING", "p")]
        self.assertTrue(shared["verdict"])
        self.assertEqual(shared["facts"]["names"], ["ONT p:1", "ONT p:2"])

    def test_too_little_history_says_nothing(self):
        rows = [r for r in self.rows(1, -20, -26) if r["hour"] > self.HOUR - timedelta(days=2)]
        self.assertEqual(access.light_drops(rows, self.HOUR), {})


def view(pon, ont_id, rx=-20.0, olt=-22.0, status="online", distance=1000, name=None):
    o = state(pon, ont_id, status, rx, olt, descr=name, distance=distance)
    return access.ont_view(o)


class FieldJobs(unittest.TestCase):
    def test_kinds(self):
        views = [view("0/3/5", 1, -26, -29), view("0/3/5", 2, -28, -29), view("0/3/5", 3, -20),
                 view("0/2/0", 1, -20, -33, name="Garage Hotspot Escarpment"), view("0/2/0", 2, -6.1, -12),
                 view("0/2/0", 3, -18, -19), view("0/1/0", 1, -28.5, -31), view("0/1/0", 2, -15, -16),
                 view("0/1/0", 3, -15, -16), view("0/4/0", 1, status="offline", name="JS04")]
        jobs = access.field_jobs(views, {"0/4/0:1"})
        self.assertEqual([(j["kind"], j["pon"]) for j in jobs],
                         [("pon_light", "0/3/5"), ("light_loss", "0/4/0"), ("ont_bad", "0/1/0"), ("one_way", "0/2/0"),
                          ("attenuator", "0/2/0")])
        self.assertIn("transmitter", jobs[3]["detail"])

    def test_a_one_way_ont_is_not_a_shared_fibre_job(self):
        views = [view("p", 1, -20, -33), view("p", 2, -18, -19)]
        self.assertEqual([j["kind"] for j in access.field_jobs(views, set())], ["one_way"])


class Splitters(unittest.TestCase):
    def test_distance_and_down_together(self):
        views = [view("p", 1, distance=4380), view("p", 2, distance=4392), view("p", 3, distance=4445),
                 view("p", 4, distance=9000), view("p", 5, distance=12000), view("q", 1, distance=4385)]
        counts = access.together_counts([{"pon": "p", "ont_id": 4, "cluster": c} for c in ("a", "b")]
                                        + [{"pon": "p", "ont_id": 5, "cluster": c} for c in ("a", "b")])
        groups = access.splitter_groups(views, counts)
        self.assertEqual([(g["pon"], g["onts"]) for g in groups], [("p", ["p:1", "p:2"]), ("p", ["p:4", "p:5"])])
        self.assertTrue(groups[0]["by_distance"])
        self.assertEqual((groups[1]["together"], groups[1]["by_together"], groups[1]["by_distance"]), (2, True, False))


def ont_row(minutes, pon, ont_id, status="online", text=None, desc=None, rx=-20.0):
    reading = ont(pon, ont_id, status, rx=rx, desc=desc or f"JS{ont_id:02d}", text=text,
                  cause=None if text is None else (13 if text == POWER_TEXT else 2))
    return {"ts": NOW - timedelta(minutes=minutes) + timedelta(seconds=ont_id), "kind": "ont", "ont": reading}


def ppp_row(minutes, dev, names):
    return {"ts": NOW - timedelta(minutes=minutes) + timedelta(seconds=30), "kind": "ppp",
            "ppp": {"dev": dev, "count": len(names), "names": names}}


@mock.patch.dict(os.environ, {"NH_SMS_MODE": "live", "NH_ALERT_PHONES": "0712000000"})
class Flow(unittest.TestCase):
    def setUp(self):
        service._light_checked_hour = None
        self.fake = FakeStore()

    def evaluate(self, new, quiet=False):
        """One ingest, 40 s after its newest row: the rows are stored first
        (as save_batch does), then judged. quiet: nothing has been received
        for 3 minutes (the shipper's posts stopped carrying ONT readings)."""
        self.fake.rows += new
        self.fake.now = max(r["ts"] for r in new) + timedelta(seconds=40)
        self.fake.ont_received = self.fake.now - timedelta(minutes=3 if quiet else 0)
        with mock.patch.object(service, "store", self.fake), \
                mock.patch.object(service, "send_in_background") as send:
            service.evaluate(new)
        return [message for _, message in send.call_args.args[0]] if send.called else []

    def poll(self, minutes, pon, statuses, text=LIGHT_TEXT):
        return [ont_row(minutes, pon, i, s, text if s == "offline" else None) for i, s in enumerate(statuses, 1)]

    def open_rules(self):
        return sorted((k[1], k[2]) for k, s in self.fake.states.items() if s["is_active"])

    def test_a_fibre_cut_texts_once_and_clears_when_they_are_back(self):
        self.evaluate(self.poll(20, "0/3/1", ["online"] * 5))
        self.assertEqual(self.evaluate(self.poll(15, "0/3/1", ["offline"] * 3 + ["online"] * 2)), [])  # poll not complete
        sent = self.evaluate(self.poll(10, "0/3/1", ["offline"] * 3 + ["online"] * 2))
        self.assertEqual(sent, ["JNET ALERT OLT: Fibre/splitter fault likely on PON 0/3/1: JS01, JS02, JS03. Since 21:15"])
        self.assertEqual(self.open_rules(), [("PON_FIBRE_FAULT", "0/3/1")])
        self.assertEqual(self.evaluate(self.poll(5, "0/3/1", ["online", "offline", "offline", "online", "online"])), [])
        sent = self.evaluate(self.poll(0, "0/3/1", ["online", "online", "offline", "online", "online"]))
        self.assertEqual(sent, ["JNET OK OLT: fibre on PON 0/3/1 back to normal. Lasted 15min"])
        self.assertEqual(self.open_rules(), [])

    def test_a_poll_split_across_two_posts_is_still_one_group(self):
        self.evaluate(self.poll(20, "0/3/1", ["online"] * 5))
        rows = self.poll(15, "0/3/1", ["offline"] * 3 + ["online"] * 2)
        self.evaluate(rows[:2])
        self.evaluate(rows[2:])
        sent = self.evaluate(self.poll(10, "0/3/1", ["offline"] * 3 + ["online"] * 2))
        self.assertEqual(len(sent), 1)
        self.assertIn("JS01, JS02, JS03", sent[0])

    def test_quiet_feed_settles_the_poll_without_the_next_one(self):
        self.evaluate(self.poll(20, "p", ["online"] * 3))
        self.assertEqual(self.evaluate(self.poll(15, "p", ["offline"] * 3, text=POWER_TEXT)), [])
        sent = self.evaluate([ppp_row(12, "ccr2116", [])], quiet=True)
        self.assertEqual(sent, ["JNET ALERT OLT: Whole PON p down (3 ONTs, dying-gasp): power cut in the area likely. Since 21:15"])

    def test_one_ont_light_loss_is_a_page_warning_and_power_is_only_listed(self):
        self.evaluate(self.poll(20, "p", ["online"] * 4))
        self.evaluate(self.poll(15, "p", ["offline", "online", "online", "online"]))
        self.evaluate(self.poll(10, "p", ["offline", "online", "online", "online"]))
        self.assertEqual(self.open_rules(), [("ONT_LIGHT_LOSS", "p:1")])
        self.assertEqual(self.fake.alerts, [])
        self.evaluate([ont_row(5, "p", 2, "offline", POWER_TEXT)])
        self.evaluate(self.poll(0, "p", ["online"] * 4))
        self.assertEqual(self.open_rules(), [])
        downs = [e for e in self.fake.access_events if e["kind"] == "ont_down"]
        self.assertEqual([(e["name"], e["detail"]["group"]) for e in downs], [("JS01", "single"), ("JS02", "single")])

    def test_a_site_missing_two_readings_texts_and_a_line_without_internet_warns(self):
        self.fake.onts = {("p", 1): state("p", 1, descr="Stima_Hotspot", since=NOW - timedelta(hours=1)),
                          ("p", 2): state("p", 2, descr="JS16", since=NOW - timedelta(hours=1))}
        self.evaluate([ppp_row(25, "ccr2004", ["Stima_Hotspot"]), ppp_row(25, "ccr2116", ["JS16"] + [f"x{i}" for i in range(9)])])
        self.assertEqual(self.evaluate([ppp_row(20, "ccr2004", [])]), [])
        sent = self.evaluate([ppp_row(15, "ccr2004", [])])
        self.assertEqual(sent, ["JNET ALERT CCR2004: Site Stima_Hotspot offline (PPPoE). Since 21:10"])
        for minutes in (20, 15, 10):
            self.evaluate([ppp_row(minutes, "ccr2116", [f"x{i}" for i in range(9)])])
        self.assertNotIn(("LINE_NO_INTERNET", "p:2"), self.open_rules())
        self.evaluate([ppp_row(5, "ccr2116", [f"x{i}" for i in range(9)])])
        self.assertIn(("LINE_NO_INTERNET", "p:2"), self.open_rules())
        self.assertEqual(len(self.fake.alerts), 1)            # the line warning is page only

    def test_a_mass_drop_texts(self):
        names = [f"JS{i:02d}" for i in range(20)]
        self.evaluate([ppp_row(10, "ccr2116", names)])
        sent = self.evaluate([ppp_row(5, "ccr2116", names[:12])])
        self.assertEqual(sent, ["JNET ALERT CCR2116: Mass PPPoE drop, 8 of 20 sessions gone (RADIUS/router?). Since 21:25"])
        self.assertEqual(self.evaluate([ppp_row(0, "ccr2116", names[:13])]), [])     # still under 70 %
        self.assertEqual(self.evaluate([ppp_row(-5, "ccr2116", names[:14])]),
                         ["JNET OK CCR2116: PPPoE sessions back to normal. Lasted 10min"])

    def test_overview_keeps_one_customers_line_out_of_the_banner_and_cards(self):
        actives = [{"device": "olt", "rule": "ONT_LIGHT_LOSS", "subject": "p:1", "severity": "AMBER",
                    "started_at": NOW, "value": None, "facts": {"name": "JS01"}, "is_flapping": False},
                   {"device": "ccr2004", "rule": "SITE_PPP_DOWN", "subject": "Stima_Hotspot", "severity": "AMBER",
                    "started_at": NOW, "value": 5, "facts": {"name": "Stima_Hotspot", "minutes": 5}, "is_flapping": False}]
        fake = mock.Mock()
        fake.page_basics.return_value = ({"last_post_at": NOW, "last_sent_at": NOW, "source": "fra-1", "from_ip": None},
                                         {}, {"probe": NOW, "inner": NOW})
        fake.active_rules.return_value = actives
        fake.alerts_since.return_value = []
        fake.now_utc.return_value = NOW
        fake.eat_midnight.return_value = NOW
        with mock.patch.object(service, "store", fake):
            overview = service.overview()
        self.assertEqual(overview["banner"]["rule"], "SITE_PPP_DOWN")
        self.assertEqual([d["active"] for d in overview["devices"]], [[], [], []])


class Sms(unittest.TestCase):
    def test_every_access_alert_texts(self):
        for rule in ("PON_DOWN", "PON_FIBRE_FAULT", "PON_POWER_CUT", "PON_LIGHT_DEGRADING", "SITE_PPP_DOWN",
                     "PPP_MASS_DROP"):
            self.assertIn(rule, config.SMS_RULES)
        for rule in rules.ACCESS_WARNINGS:
            self.assertNotIn(rule, config.SMS_RULES)
