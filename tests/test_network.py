"""Network page (CCR2116): ingest checks, the status rules, outages, the
session-batch swap and the customer join. No database and no network — the
data layer is replaced by fakes. Run from the repo root:
  venv/bin/python -m unittest tests.test_network -v
"""
import asyncio
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException
from starlette.requests import Request

from db import network
from db import network_logic as logic
from routers import network as api

CFG = logic.thresholds()
NOW = datetime(2026, 11, 4, 9, 0, 0)
TOKEN = "test-token-not-real"
ROUTER = {"slug": "ccr2116", "name": "CCR2116", "is_enabled": True, "uplink_capacity_mbps": 350.0,
          "ppp_router_id": 1}


def live(**overrides):
    message = {
        "router": "ccr2116", "seq": 1, "uptime_s": 5000,
        "uplink": {"interface": "sfp-sfpplus1", "running": True, "link_rate": "10Gbps",
                   "rx_bps": 120_000_000, "tx_bps": 20_000_000,
                   "rx_errors": 0, "tx_errors": 0, "rx_drops": 0, "tx_drops": 0,
                   "sfp": {"temp_c": "41C", "rx_dbm": "-6.12dBm", "tx_dbm": -2.0}},
        "gateway": {"address": "41.90.0.1", "source": "auto", "alive": True, "loss_pct": 0,
                    "rtt_ms_avg": 3, "rtt_ms_max": 5, "arp": "reachable"},
        "system": {"cpu_pct": 7, "mem_used_pct": 30, "temp_c": "45", "routeros": "7.24.2"},
        "pppoe_online": 210,
        "events": [],
    }
    for key, value in overrides.items():
        message[key] = value
    return message


def live_links(**overrides):
    """A live message in the multi-link shape: Airtel (active) and Veenet (standby)."""
    message = live()
    del message["uplink"], message["gateway"]
    message["links"] = [
        {"interface": "sfp-sfpplus1", "label": "airtel Primary", "active": True, "distance": 1,
         "running": True, "link_rate": "1Gbps", "rx_bps": 120_000_000, "tx_bps": 20_000_000,
         "gateway": {"address": "102.0.27.189", "source": "auto", "alive": True, "loss_pct": 0,
                     "rtt_ms_avg": 1.0, "rtt_ms_max": 1.1, "arp": "reachable"}},
        {"interface": "sfp-sfpplus3", "label": "veenet PRIMARY", "active": False, "distance": 2,
         "running": True, "link_rate": "1Gbps", "rx_bps": 0, "tx_bps": 0,
         "gateway": {"address": "102.204.89.183", "source": "auto", "alive": True, "loss_pct": 0,
                     "rtt_ms_avg": 2.0, "rtt_ms_max": 2.5, "arp": "reachable"}},
    ]
    message.update(overrides)
    return message


def request(body, token=TOKEN, extra_headers=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    headers = {"content-type": "application/json", "content-length": str(len(raw)), **(extra_headers or {})}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    scope = {"type": "http", "method": "POST", "path": "/network/ingest", "query_string": b"",
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request(scope, receive)


class Ingest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"NETWORK_INGEST_TOKEN_CCR2116": TOKEN})
        self.env.start()
        self.router = mock.patch.object(api.db, "get_router", return_value=dict(ROUTER))
        self.router.start()
        self.live = mock.patch.object(api.db, "record_live", return_value={"gateway": "alive"})
        self.record_live = self.live.start()
        self.parts = mock.patch.object(api.db, "record_session_part", return_value={"complete": False})
        self.record_part = self.parts.start()
        api._recent_posts.clear()

    def tearDown(self):
        mock.patch.stopall()

    def post(self, body, **kwargs):
        return asyncio.run(api.ingest(request(body, **kwargs)))

    def status_of(self, body, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            self.post(body, **kwargs)
        return caught.exception.status_code

    def test_a_good_live_message_is_stored(self):
        self.assertEqual(self.post(live())["ok"], True)
        stored = self.record_live.call_args.args[1]
        # The single-link shape older scripts send is stored as one link.
        link = stored["links"][0]
        self.assertEqual((link["interface"], link["active"], link["rx_bps"]), ("sfp-sfpplus1", True, 120_000_000))
        self.assertEqual(link["sfp"]["rx_dbm"], -6.12)   # "-6.12dBm" -> number
        self.assertEqual(link["gateway"]["address"], "41.90.0.1")
        self.assertEqual(stored["system"]["temp_c"], 45)
        self.assertIsNone(stored["uplink"])

    def test_a_message_with_several_links_is_stored_as_sent(self):
        self.post(live_links())
        stored = self.record_live.call_args.args[1]
        self.assertEqual([(l["interface"], l["label"], l["active"], l["distance"]) for l in stored["links"]],
                         [("sfp-sfpplus1", "airtel Primary", True, 1), ("sfp-sfpplus3", "veenet PRIMARY", False, 2)])

    def test_links_and_the_old_shape_cannot_be_mixed_or_repeated(self):
        both = {**live_links(), "uplink": live()["uplink"], "gateway": live()["gateway"]}
        twice = live_links()
        twice["links"] = [twice["links"][0], dict(twice["links"][0])]
        neither = live()
        del neither["uplink"], neither["gateway"]
        for body in (both, twice, neither, {**live_links(), "links": []}):
            self.assertEqual(self.status_of(body), 422)

    def test_wrong_or_missing_token_is_401(self):
        self.assertEqual(self.status_of(live(), token=None), 401)
        self.assertEqual(self.status_of(live(), token="guess"), 401)
        with mock.patch.dict("os.environ", {"NETWORK_INGEST_TOKEN_CCR2116": ""}):
            self.assertEqual(self.status_of(live()), 401)   # no token configured: nothing accepted
        self.record_live.assert_not_called()

    def test_a_token_only_works_for_its_own_router(self):
        with mock.patch.dict("os.environ", {"NETWORK_INGEST_TOKEN_CCR2004": "other"}):
            self.assertEqual(self.status_of(live(router="ccr2004"), token=TOKEN), 401)

    def test_unknown_or_disabled_router_is_rejected(self):
        with mock.patch.dict("os.environ", {"NETWORK_INGEST_TOKEN_GHOST": "g"}), \
                mock.patch.object(api.db, "get_router", return_value=None):
            self.assertEqual(self.status_of(live(router="ghost"), token="g"), 403)
        with mock.patch.object(api.db, "get_router", return_value={**ROUTER, "is_enabled": False}):
            self.assertEqual(self.status_of(live()), 403)

    def test_bad_payloads_are_4xx_never_500(self):
        self.assertEqual(self.status_of(b"not json"), 400)
        self.assertEqual(self.status_of(b"x" * (api.MAX_BODY_BYTES + 1)), 413)
        self.assertEqual(self.status_of(["a", "list"]), 401)
        broken = [
            live(uptime_s=-1),
            live(uplink={**live()["uplink"], "rx_bps": -5}),
            live(gateway={**live()["gateway"], "source": "guess"}),
            live(system={"cpu_pct": 140}),
            live(extra_field=1),
            live(events=[{"event_id": "e1", "type": "reboot", "duration_s": 1, "ended_ago_s": 1}]),
            {"router": "ccr2116", "hello": "world"},
            {"router": "ccr2116", "batch_id": "b1", "part": 3, "parts": 2, "rows": []},
            {"router": "ccr2116", "batch_id": "b1", "part": 1, "parts": 1, "rows": [{"name": ""}]},
            {"router": "ccr2116", "batch_id": "b1", "part": 1, "parts": 1, "rows": [{"name": "x", "password": "p"}]},
        ]
        for body in broken:
            self.assertIn(self.status_of(body), (400, 422), body)
        self.record_live.assert_not_called()
        self.record_part.assert_not_called()

    def test_session_parts_go_to_the_batch_store(self):
        body = {"router": "ccr2116", "batch_id": "u5000", "part": 1, "parts": 2,
                "rows": [{"name": "john", "address": "10.0.0.5", "caller_id": "AA:BB", "uptime_s": 60,
                          "service": "pppoe", "session_id": "0x81"}]}
        self.post(body)
        slug, stored = self.record_part.call_args.args
        self.assertEqual((slug, stored["batch_id"], stored["parts"]), ("ccr2116", "u5000", 2))

    def test_rate_limit(self):
        for _ in range(api.INGEST_LIMIT_PER_MINUTE):
            self.post(live())
        self.assertEqual(self.status_of(live()), 429)


class SfpUnits(unittest.TestCase):
    def test_milli_dbm_from_the_ccr2116_becomes_dbm(self):
        # Real dry-run output on RouterOS 7.24.2: rx -6291, tx -5015.
        sfp = api.Sfp.model_validate({"temp_c": "28", "rx_dbm": "-6291", "tx_dbm": "-5015"})
        self.assertEqual((sfp.temp_c, sfp.rx_dbm, sfp.tx_dbm), (28.0, -6.291, -5.015))

    def test_values_already_in_dbm_are_left_alone(self):
        for given, want in ((-6.291, -6.291), ("-6.123dBm", -6.123), (-23, -23.0), (None, None)):
            self.assertEqual(api.Sfp.model_validate({"rx_dbm": given}).rx_dbm, want, given)


class BetweenReadings(unittest.TestCase):
    def test_uptime_going_down_is_a_restart(self):
        self.assertTrue(logic.is_restart(5000, 30))
        self.assertFalse(logic.is_restart(5000, 5015))
        self.assertFalse(logic.is_restart(None, 30))

    def test_one_failed_check_is_not_down_two_are(self):
        memory = {"address": "41.90.0.1", "fail_streak": 0, "seen_alive": True}
        down = {"address": "41.90.0.1", "alive": False}
        streak, seen = logic.next_gateway_memory(memory, down)
        self.assertEqual(logic.gateway_view(down, streak, seen, CFG)["state"], "missed")
        streak, seen = logic.next_gateway_memory({**memory, "fail_streak": streak}, down)
        self.assertEqual(logic.gateway_view(down, streak, seen, CFG)["state"], "not_responding")
        streak, seen = logic.next_gateway_memory({**memory, "fail_streak": streak}, {**down, "alive": True})
        self.assertEqual((streak, seen), (0, True))

    def test_a_gateway_that_never_answers_ping_is_not_red(self):
        silent = {"address": "41.90.0.1", "alive": False, "arp": "reachable"}
        memory = {"address": "41.90.0.1", "fail_streak": 0, "seen_alive": False}
        for _ in range(5):
            streak, seen = logic.next_gateway_memory(memory, silent)
            memory = {**memory, "fail_streak": streak, "seen_alive": seen}
        self.assertEqual(logic.gateway_view(silent, streak, seen, CFG)["state"], "no_ping")

    def test_the_ccr2116_gateway_state_delay_still_counts_as_seen_in_arp(self):
        # Real dry-run output: the Airtel gateway ignored ping and showed "delay".
        silent = {"address": "102.0.27.189", "alive": False, "arp": "delay"}
        for state in ("delay", "stale", "probe", "reachable"):
            view = logic.gateway_view({**silent, "arp": state}, 5, False, CFG)
            self.assertEqual(view["state"], "no_ping", state)
        for state in ("incomplete", "failed", None):
            view = logic.gateway_view({**silent, "arp": state}, 5, False, CFG)
            self.assertEqual(view["state"], "not_responding", state)

    def test_a_new_gateway_address_starts_over(self):
        memory = {"address": "41.90.0.1", "fail_streak": 4, "seen_alive": True}
        self.assertEqual(logic.next_gateway_memory(memory, {"address": "10.9.9.1", "alive": False}), (1, False))

    def test_no_gateway_address_says_so(self):
        view = logic.gateway_view({"address": None, "source": "none"}, 0, False, CFG)
        self.assertEqual(view["state"], "not_detectable")
        self.assertIn("gateway_ip", view["label"])

    def test_quiet_period_cause_comes_from_uptime(self):
        before = NOW - timedelta(minutes=4)
        stayed_up = logic.quiet_period(before, NOW, restarted=False, uptime_s=9000, cfg=CFG)
        self.assertEqual(stayed_up["details"]["cause"], "connectivity")
        self.assertEqual(stayed_up["details"]["duration_s"], 240)
        rebooted = logic.quiet_period(before, NOW, restarted=True, uptime_s=40, cfg=CFG)
        self.assertEqual(rebooted["details"]["cause"], "restart")
        self.assertIsNone(logic.quiet_period(NOW - timedelta(seconds=30), NOW, False, 1, CFG))

    def test_counters_rising(self):
        before = logic.links_of(live())[0]
        after = logic.links_of(live(uplink={**live()["uplink"], "rx_errors": 3}))[0]
        self.assertTrue(logic.counters_rose(before, after, restarted=False))
        self.assertFalse(logic.counters_rose(before, after, restarted=True))
        self.assertFalse(logic.counters_rose(before, before, restarted=False))

    def test_router_events_use_relative_times(self):
        started, ended = logic.router_event_times({"duration_s": 120, "ended_ago_s": 30}, NOW)
        self.assertEqual((started, ended), (NOW - timedelta(seconds=150), NOW - timedelta(seconds=30)))


def latest(received_ago_s=5, **live_overrides):
    return {"live": live(**live_overrides), "live_received_at": NOW - timedelta(seconds=received_ago_s)}


def entry(link, primary=True, streak=0, seen=True, minutes=(), capacity=350, source="admin", rose=None):
    return {"interface": link["interface"], "label": link.get("label"), "reading": link, "primary": primary,
            "gateway_fail_streak": streak, "gateway_seen_alive": seen, "counters_rose_at": rose,
            "recent_minutes": list(minutes), "capacity_mbps": capacity, "capacity_source": source}


def single(streak=0, seen=True, minutes=(), source="admin", rose=None, **live_overrides):
    """(latest row, [one link entry]) for the single-link shape."""
    row = latest(**live_overrides)
    return row, [entry(logic.links_of(row["live"])[0], streak=streak, seen=seen, minutes=minutes,
                       source=source, rose=rose)]


class Status(unittest.TestCase):
    def check(self, pair):
        return logic.status(pair[0], pair[1], NOW, CFG)

    def test_green(self):
        self.assertEqual(self.check(single())["level"], "green")

    def test_not_reporting_after_60_seconds(self):
        result = self.check((latest(received_ago_s=61), []))
        self.assertEqual((result["level"], result["state"]), ("red", "not_reporting"))
        self.assertIn("possible upstream outage", result["reasons"][0]["text"])

    def test_three_red_states_are_different(self):
        not_reporting = self.check((latest(received_ago_s=90), []))["state"]
        gateway_down = self.check(single(streak=2, gateway={**live()["gateway"], "alive": False}))["state"]
        no_clients = self.check(single(pppoe_online=0))
        self.assertEqual({not_reporting, gateway_down}, {"not_reporting", "gateway_down"})
        self.assertEqual(no_clients["level"], "green")   # 0 clients is not an outage

    def test_uplink_not_running_is_red(self):
        pair = single(uplink={**live()["uplink"], "running": False})
        self.assertEqual(self.check(pair)["state"], "uplink_down")

    def test_busy_needs_two_minutes_against_the_contracted_rate(self):
        hot = {"rx_bps_avg": 300_000_000, "tx_bps_avg": 10_000_000}      # 85.7% of 350 Mbps
        calm = {"rx_bps_avg": 100_000_000, "tx_bps_avg": 10_000_000}
        self.assertEqual(self.check(single(minutes=[hot, hot]))["level"], "amber")
        self.assertEqual(self.check(single(minutes=[hot, calm]))["level"], "green")
        self.assertEqual(self.check(single(minutes=[hot]))["level"], "green")
        self.assertEqual(logic.utilization_pct(300_000_000, 350), 85.7)

    def test_an_estimated_capacity_never_raises_a_busy_warning(self):
        # Port speed is not the contract: judging "busy" against it would be a guess.
        full = {"rx_bps_avg": 340_000_000, "tx_bps_avg": 10_000_000}
        self.assertEqual(self.check(single(minutes=[full, full], source="auto"))["level"], "green")

    def test_amber_reasons(self):
        cases = [
            single(system={**live()["system"], "cpu_pct": 85}),
            single(gateway={**live()["gateway"], "loss_pct": 33}),
            single(gateway={**live()["gateway"], "rtt_ms_avg": 80}),
            single(internet={"target": "1.1.1.1", "alive": False, "loss_pct": 100, "rtt_ms_avg": None}),
            single(rose=NOW - timedelta(seconds=30)),
        ]
        for pair in cases:
            self.assertEqual(self.check(pair)["level"], "amber", pair)
        self.assertEqual(self.check(single(rose=NOW - timedelta(minutes=10)))["level"], "green")

    def test_never_reported(self):
        self.assertEqual(self.check((None, []))["state"], "never_reported")


class TwoLinks(unittest.TestCase):
    """Airtel (primary, distance 1) and Veenet (backup, distance 2)."""

    def pair(self, airtel=None, veenet=None, airtel_streak=0, veenet_streak=0):
        message = live_links()
        airtel_link, veenet_link = message["links"]
        airtel_link.update(airtel or {})
        veenet_link.update(veenet or {})
        row = {"live": message, "live_received_at": NOW - timedelta(seconds=5)}
        return row, [entry(airtel_link, primary=True, streak=airtel_streak),
                     entry(veenet_link, primary=False, streak=veenet_streak)]

    def check(self, pair):
        return logic.status(pair[0], pair[1], NOW, CFG)

    def test_both_links_fine_is_green(self):
        self.assertEqual(self.check(self.pair())["level"], "green")

    def test_the_primary_link_failing_is_red_and_names_the_link(self):
        down = {"gateway": {**live_links()["links"][0]["gateway"], "alive": False}}
        result = self.check(self.pair(airtel=down, airtel_streak=2))
        self.assertEqual((result["level"], result["state"]), ("red", "gateway_down"))
        self.assertTrue(result["reasons"][0]["text"].startswith("airtel Primary:"))

    def test_the_backup_link_failing_is_only_amber(self):
        down = {"gateway": {**live_links()["links"][1]["gateway"], "alive": False}}
        result = self.check(self.pair(veenet=down, veenet_streak=2))
        self.assertEqual(result["level"], "amber")
        self.assertEqual(result["reasons"][0]["code"], "standby_gateway_down")
        self.assertIn("no failover", result["reasons"][0]["text"])

    def test_traffic_on_the_backup_is_called_out(self):
        result = self.check(self.pair(airtel={"active": False}, veenet={"active": True, "rx_bps": 90_000_000}))
        self.assertEqual(result["level"], "amber")
        self.assertIn("on_backup", [r["code"] for r in result["reasons"]])
        self.assertIn("veenet PRIMARY", [r["text"] for r in result["reasons"] if r["code"] == "on_backup"][0])

    def test_total_traffic_adds_the_links_once_each(self):
        links = live_links()["links"]
        links[1]["rx_bps"], links[1]["tx_bps"] = 5_000_000, 1_000_000
        self.assertEqual(logic.total_traffic(links), (125_000_000, 21_000_000))
        self.assertEqual(logic.total_traffic(links + [links[0]]), (125_000_000, 21_000_000))


class ChartRanges(unittest.TestCase):
    def test_the_chart_offers_1h_6h_24h_and_7d(self):
        self.assertEqual(list(logic.HISTORY_RANGES), ["1h", "6h", "24h", "7d"])
        look_back, bucket = logic.HISTORY_RANGES["6h"]
        self.assertEqual((look_back, bucket), (timedelta(hours=6), 2))
        self.assertLessEqual(look_back / timedelta(minutes=bucket), 400)   # a sensible number of points

    def test_an_unknown_range_is_refused(self):
        with self.assertRaises(ValueError):
            network.uplink_history("ccr2116", "30d")


class Links(unittest.TestCase):
    def test_the_old_message_shape_is_one_link(self):
        links = logic.links_of(live())
        self.assertEqual([(l["interface"], l["active"], l["gateway"]["address"]) for l in links],
                         [("sfp-sfpplus1", True, "41.90.0.1")])
        self.assertEqual(logic.links_of({}), [])

    def test_port_speed_text(self):
        for text, want in (("1Gbps", 1000.0), ("10Gbps", 10000.0), ("100Mbps", 100.0), ("2.5Gbps", 2500.0),
                           (None, None), ("auto", None)):
            self.assertEqual(logic.parse_rate_mbps(text), want, text)

    def test_the_primary_is_the_lowest_distance(self):
        links = live_links()["links"]
        self.assertEqual(logic.primary_interface(links), "sfp-sfpplus1")
        self.assertEqual(logic.primary_interface(list(reversed(links))), "sfp-sfpplus1")
        self.assertEqual(logic.primary_interface([{"interface": "a"}, {"interface": "b"}]), "a")
        self.assertIsNone(logic.primary_interface([]))

    def test_capacity_starts_at_port_speed_and_an_admin_figure_sticks(self):
        self.assertEqual(logic.next_capacity(None, 1000.0), (1000.0, "auto"))
        self.assertEqual(logic.next_capacity({"capacity_mbps": 1000.0, "capacity_source": "auto"}, 10000.0),
                         (10000.0, "auto"))     # the port was upgraded: the estimate follows
        self.assertEqual(logic.next_capacity({"capacity_mbps": 350.0, "capacity_source": "admin"}, 10000.0),
                         (350.0, "admin"))      # the router never overrides the admin


def event(kind, start_min, end_min, source="ops"):
    return {"type": kind, "source": source, "details": {},
            "started_at": NOW - timedelta(minutes=start_min),
            "ended_at": None if end_min is None else NOW - timedelta(minutes=end_min)}


class Outages(unittest.TestCase):
    def test_router_reported_gateway_down_replaces_the_overlapping_gap(self):
        gap = event("not_reporting", 30, 25)
        reported = event("gateway_down", 29, 25, source="router")
        other = event("not_reporting", 120, 118)
        merged = logic.merge_outages([gap, reported, other])
        self.assertEqual([(e["type"], e["source"]) for e in merged],
                         [("gateway_down", "router"), ("not_reporting", "ops")])

    def test_serialised_event_says_reported_after_reconnecting(self):
        out = network.serialise_event({**event("gateway_down", 10, 5, source="router"), "id": 1}, NOW)
        self.assertTrue(out["reported_after_reconnecting"])
        self.assertEqual(out["duration_s"], 300)


class SessionBatches(unittest.TestCase):
    def test_swap_only_when_every_part_is_in(self):
        parts = [{"part": 2, "rows": [{"name": "c"}]}, {"part": 1, "rows": [{"name": "a"}, {"name": "b"}]}]
        self.assertIsNone(logic.assemble_batch(parts[:1], 2))
        self.assertEqual([r["name"] for r in logic.assemble_batch(parts, 2)], ["a", "b", "c"])
        self.assertIsNone(logic.assemble_batch(parts, 3))

    def test_an_empty_list_is_one_part_with_no_rows(self):
        self.assertEqual(logic.assemble_batch([{"part": 1, "rows": []}], 1), [])


@contextmanager
def fake_cursor():
    yield (None, None)


class PppoeJoin(unittest.TestCase):
    SESSIONS = [{"name": "John.K", "address": "10.10.0.5", "uptime_s": 300, "caller_id": "AA:BB:CC:DD:EE:FF"},
                {"name": "mary", "address": "10.10.0.6", "uptime_s": 60},
                {"name": "stranger", "address": "10.10.0.7", "uptime_s": 10}]

    def pppoe(self, customers):
        with mock.patch.object(network, "get_router", return_value=dict(ROUTER)), \
                mock.patch.object(network, "db_cursor", fake_cursor), \
                mock.patch.object(network, "_latest", return_value={"sessions": self.SESSIONS,
                                                                    "sessions_received_at": NOW}), \
                mock.patch.object(network.customers, "match_usernames", return_value=customers) as match:
            return network.pppoe("ccr2116", now=NOW), match

    def test_join_statuses_and_unknown_kept(self):
        result, match = self.pppoe({
            "john.k": {"name": "John Kamau", "plan": "10Mbps", "expiry": NOW + timedelta(days=3),
                       "account_status": "Active", "enabled": True},
            "mary": {"name": "Mary W", "plan": "5Mbps", "expiry": NOW - timedelta(days=1),
                     "account_status": "Active", "enabled": True},
        })
        self.assertEqual(match.call_args.args, (1, {"john.k", "mary", "stranger"}))
        rows = {r["username"]: r for r in result["sessions"]}
        self.assertEqual(rows["John.K"]["status"], "active")
        self.assertEqual(rows["John.K"]["display_name"], "John Kamau")
        self.assertEqual(rows["mary"]["status"], "expired")
        self.assertEqual(rows["stranger"]["status"], "unknown")
        self.assertIsNone(rows["stranger"]["display_name"])
        self.assertEqual(result["counts"], {"online": 3, "active": 1, "expired_but_online": 1, "unknown": 1})
        self.assertFalse(result["is_stale"])

    def test_changing_expiry_changes_the_badge_on_the_next_read(self):
        customer = {"name": "Mary W", "plan": "5Mbps", "account_status": "Active", "enabled": True}
        before, _ = self.pppoe({"mary": {**customer, "expiry": NOW - timedelta(days=1)}})
        after, _ = self.pppoe({"mary": {**customer, "expiry": NOW + timedelta(days=30)}})
        status = lambda r: next(s["status"] for s in r["sessions"] if s["username"] == "mary")
        self.assertEqual((status(before), status(after)), ("expired", "active"))

    def test_no_phone_mac_or_password_in_the_response(self):
        result, _ = self.pppoe({})
        text = json.dumps(result, default=str).lower()
        for word in ("phone", "password", "aa:bb:cc", "caller"):
            self.assertNotIn(word, text)

    def test_statuses(self):
        self.assertEqual(logic.customer_status(None, NOW), ("unknown", None))
        self.assertEqual(logic.customer_status({"account_status": "Expired", "enabled": True}, NOW)[0], "expired")
        self.assertEqual(logic.customer_status({"account_status": "Active", "enabled": False}, NOW)[0], "expired")
        self.assertEqual(logic.customer_status({"account_status": "Active", "enabled": True, "expiry": None},
                                               NOW)[0], "active")


class ReadPermission(unittest.TestCase):
    def test_admin_passes_others_need_the_permission(self):
        admin = {"id": 1, "role": "admin", "role_id": None}
        self.assertIs(api.require_network_view(admin), admin)
        tech = {"id": 2, "role": "technician", "role_id": 5}
        with mock.patch("routers.permissions.db.check_role_permission", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                api.require_network_view(tech)
        self.assertEqual(caught.exception.status_code, 403)
        with mock.patch("routers.permissions.db.check_role_permission", return_value=True) as check:
            self.assertIs(api.require_network_view(tech), tech)
        self.assertEqual(check.call_args.args, (5, "sites", "view_network"))

    def test_every_read_route_is_guarded(self):
        for route in api.router.routes:
            if route.path == "/network/ingest":
                continue
            guards = [d.call for d in route.dependant.dependencies]
            if route.path in ("/network/uplinks/capacity", "/network/uplinks/service"):
                # Correcting a link's contracted speed, or switching it off, is the admin's call.
                self.assertIn(api.require_admin, guards, route.path)
                continue
            self.assertIn(api.require_network_view, guards, route.path)


if __name__ == "__main__":
    unittest.main()
