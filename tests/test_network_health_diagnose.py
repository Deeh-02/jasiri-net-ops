"""Diagnose, the chat with Claude on the Network Health page: the summary
sent to Claude, the conversation checks, the snapshot kept per conversation
(prompt cache), daily limits, error handling, and who may use Opus.
Claude is never called: _call and gather are faked. Run:
  venv/bin/python -m unittest tests.test_network_health_diagnose -v
"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from network_health import config, diagnose, router
from routers.auth import get_current_user

NOW = datetime(2026, 11, 21, 15, 44, tzinfo=timezone.utc)   # 18:44 EAT


def at(minutes_ago):
    return NOW - timedelta(minutes=minutes_ago)


def probe_rows(name, rows):
    return [{"t": at(m), "name": name, "rtt": rtt, "loss": loss, "jitter": 0.2, "n": 1} for m, rtt, loss in rows]


def inputs():
    overview = {"devices": [{"device": "ccr2116", "label": "CCR2116", "status": "AMBER", "last_seen": at(1),
                             "active": []}],
                "feed": {"last_sent_at": at(1), "from_ip": "203.0.113.9"},
                "sms": {"log": [{"message": "JNET ALERT secret text"}], "recipients": 2}}
    charts = {
        "settings": {"uplink_capacity_mbps": 350.0},
        "thresholds": {"uplink_pct": 85},
        "load": [{"t": at(m), "down": d, "up": 20.0, "down_max": d + 20, "ppp": 32.0, "cpu": 3.0}
                 for m, d in ((5, 300.0), (10, 280.0), (90, 100.0), (600, 50.0))],
        "probes": probe_rows("p-fra", [(5, 203.0, 0.0), (10, 204.0, 10.0), (120, None, 100.0)])
                  + probe_rows("p-gw", [(5, 1.0, 0.0)]),
        "link": [{"t": at(5), "kind": "link", "rx": -6.2, "tx": -5.0}, {"t": at(60), "kind": "link", "rx": -6.9, "tx": -5.0}],
        "dns": [{"t": at(5), "name": "g8", "server": "8.8.8.8", "ms": 10.0, "fails": 0},
                {"t": at(30), "name": "g8", "server": "8.8.8.8", "ms": 50.0, "fails": 1}],
        "speed": [{"t": at(300), "kind": "speed", "mbps": 64.5}],
        "inner_cpu": [{"t": at(5), "cpu": 9.0}],
    }
    inside = {
        "crc": {"series": [{"t": at(5), "pct": 10.5}, {"t": at(70), "pct": 9.0}],
                "current": {"t": at(3), "pct": 10.5, "level": "red"}, "range_pct": 9.9,
                "green_under_pct": 0.01, "red_over_pct": 1},
        "snmp": {"is_stale": False}, "restarts": [], "link_events": [],
        "thresholds": {"path_loss_pct": 20},
        "hop": probe_rows("h-olt-big", [(5, 1.7, 12.5), (20, 2.0, 8.0)]),
        "lanlinks": [{"dev": "crs326", "port": "sfp-sfpplus3", "label": "CRS326 → OLT", "day": {"rx_err": 14}}],
        "areas": [{"vlan": 5, "name": "Njeri House", "is_total": False, "down_mbps": 4.0, "up_mbps": 1.0, "at": at(3),
                   "spark": [{"t": at(m), "down": d, "up": 0.5} for m, d in ((15, 4.0), (30, 6.0), (300, 20.0))]}],
    }
    routes_card = {"sites": [{"host": "www.youtube.com", "slot": "night", "checked_at": at(600), "status": "ok",
                              "verdict": "kenya", "last_ms": 10.2, "last_hop_address": "142.251.156.4",
                              "reached_target": True, "change": None,
                              "hops": [{"hop": 1, "address": "102.0.27.189", "ms": 1.0}]}]}
    incidents = [{"device": "ccr2116", "rule": "UPLINK_CONGESTED", "subject": "", "severity": "AMBER",
                  "started_at": at(13), "ended_at": None, "peak_value": 88.4, "is_flapping": True,
                  "facts": {"pct": 88}, "fire_sms_sent": False}]
    active = [{"device": "olt", "device_label": "OLT", "rule": "OLT_CRC", "label": "OLT uplink damaging frames",
               "subject": "", "severity": "AMBER", "started_at": at(120), "text": "OLT uplink damaging frames, 10.5%",
               "is_flapping": False}]
    return overview, charts, inside, routes_card, incidents, active


def summary(detailed=False):
    return diagnose.summarise(NOW, *inputs(), detailed=detailed)


class Summary(unittest.TestCase):
    def test_windows_are_over_the_right_readings(self):
        s = summary()
        self.assertEqual(s["uplink"]["15m"]["down"], {"avg": 290.0, "max": 300.0})
        self.assertEqual(s["uplink"]["1h"]["down"]["avg"], 290.0)
        self.assertEqual(s["uplink"]["24h"]["down"]["max"], 300.0)
        self.assertEqual(s["uplink"]["capacity_mbps"], 350.0)
        fra = s["probes_out"]["p-fra"]
        self.assertEqual(fra["15m"]["loss"], {"avg": 5.0, "max": 10.0})
        self.assertEqual(fra["no_reply_buckets_24h"], 1)
        self.assertEqual(fra["last"]["rtt"], 203.0)
        self.assertEqual(s["probes_to_boxes"]["h-olt-big"]["1h"]["loss"]["max"], 12.5)
        self.assertEqual(s["dns"]["g8"]["fails_24h"], 1)
        self.assertEqual(s["optics"]["link"]["rx_24h_min"], -6.9)
        self.assertEqual(s["areas"][0]["down_24h_median"], 6.0)

    def test_times_are_nairobi_text(self):
        s = summary()
        self.assertEqual(s["now"], "Sat 21 Nov 18:44")
        self.assertEqual(s["active_problems"][0]["since"], "Sat 21 Nov 16:44")

    def test_no_sms_log_phone_numbers_or_addresses_of_the_feed(self):
        text = json.dumps(summary(detailed=True))
        self.assertNotIn("secret text", text)
        self.assertNotIn("203.0.113.9", text)
        self.assertNotIn("recipients", text)

    def test_hourly_series_only_in_the_detailed_one(self):
        quick, deep = summary(), summary(detailed=True)
        self.assertNotIn("hourly_series_start_at", quick)
        self.assertNotIn("hourly_loss", quick["probes_out"]["p-fra"])
        self.assertNotIn("hops", quick["routes"][0])
        self.assertEqual(len(deep["hourly_series_start_at"]), 24)
        self.assertEqual(deep["hourly_series_start_at"][-1], "Sat 21 Nov 18:00")
        self.assertEqual(len(deep["probes_out"]["p-fra"]["hourly_loss"]), 24)
        self.assertEqual(deep["probes_out"]["p-fra"]["hourly_loss"][-1], 5.0)
        self.assertEqual(deep["routes"][0]["hops"], [[1, "102.0.27.189", 1.0]])

    def test_empty_inputs_dont_crash(self):
        s = diagnose.summarise(NOW, {}, {}, {}, {}, [], [], detailed=True)
        self.assertEqual(s["probes_out"], {})
        self.assertIsNone(s["uplink"]["15m"])

    def test_snapshot_block_is_cached_and_follows_the_instructions(self):
        system = diagnose._system('{"a":1}', NOW)
        self.assertEqual(system[0]["text"], diagnose.SYSTEM)
        self.assertNotIn("cache_control", system[0])
        self.assertIn("Sat 21 Nov 18:44", system[1]["text"])
        self.assertTrue(system[1]["text"].endswith('{"a":1}'))
        self.assertEqual(system[1]["cache_control"], {"type": "ephemeral"})

    def test_instructions_keep_it_to_network_health(self):
        self.assertIn("only covers network health", diagnose.SYSTEM)
        self.assertIn('starting "Quick check"', diagnose.SYSTEM)
        self.assertIn('starting "Detailed analysis"', diagnose.SYSTEM)


def message(text="Verdict:\n- all good", stop="end_turn", model="claude-sonnet-5-5", read=0, written=0):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, model=model,
                           usage=SimpleNamespace(input_tokens=500, output_tokens=400,
                                                 cache_read_input_tokens=read, cache_creation_input_tokens=written))


ASK = [{"role": "user", "content": "Quick check: anything wrong?"}]
FOLLOW_UP = ASK + [{"role": "assistant", "content": "Verdict: all good"}, {"role": "user", "content": "And the OLT?"}]


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
class Chat(unittest.TestCase):
    def setUp(self):
        diagnose._state.update(day=None)
        diagnose._snapshots.clear()
        for target, value in (("gather", {"snap": 1}), ("_call", message())):
            patcher = mock.patch.object(diagnose, target, return_value=value)
            setattr(self, target.strip("_"), patcher.start())
            self.addCleanup(patcher.stop)

    def ask(self, messages=ASK, model="sonnet", effort="medium", convo="convo-0001", now=NOW):
        return diagnose.chat(convo, model, effort, messages, now=now)

    def test_model_and_effort_are_the_ones_asked_for(self):
        self.ask()
        model, effort, max_tokens, system, messages = self.call.call_args.args
        self.assertEqual((model, effort, max_tokens), ("claude-sonnet-5-5", "medium", 16000))
        self.assertEqual(messages, ASK)
        self.call.return_value = message(model="claude-opus-5-5")
        self.ask(model="opus", effort="high")
        self.assertEqual(self.call.call_args.args[:3], ("claude-opus-5-5", "high", 32000))

    def test_answer_has_text_cost_and_model(self):
        self.call.return_value = message(read=10000, written=0)
        a = self.ask()
        self.assertEqual(a["text"], "Verdict:\n- all good")
        self.assertEqual(a["model_label"], "Claude Sonnet 5.5")
        self.assertEqual(a["cache_read_tokens"], 10000)
        self.assertAlmostEqual(a["cost_usd"], (500 * 2 + 10000 * 0.10 + 400 * 10) / 1e6)
        self.assertFalse(a["truncated"])

    def test_cache_writes_cost_a_quarter_more(self):
        p = config.DIAGNOSE_PRICES["claude-opus-5-5"]
        usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                                cache_creation_input_tokens=1_000_000)
        self.assertAlmostEqual(diagnose.cost_usd("claude-opus-5-5", usage), p["in"] * 1.25)

    def test_fallback_model_is_shown_and_cost_unknown(self):
        self.call.return_value = message(model="claude-opus-4-8")
        a = self.ask()
        self.assertEqual(a["model_label"], "claude-opus-4-8")
        self.assertIsNone(a["cost_usd"])

    def test_snapshot_is_kept_per_conversation_then_refreshed(self):
        self.ask(now=NOW)
        first_system = self.call.call_args.args[3]
        self.ask(FOLLOW_UP, now=NOW + timedelta(minutes=9))
        self.assertEqual(self.gather.call_count, 1)
        self.assertEqual(self.call.call_args.args[3], first_system)     # byte-for-byte: the cache hits
        self.ask(FOLLOW_UP, now=NOW + timedelta(minutes=11))
        self.assertEqual(self.gather.call_count, 2)
        self.ask(convo="other-convo", now=NOW + timedelta(minutes=11))
        self.assertEqual(self.gather.call_count, 3)

    def test_old_snapshots_are_dropped(self):
        self.ask(convo="old-convo-1", now=NOW)
        self.ask(convo="new-convo-1", now=NOW + timedelta(minutes=25))
        self.assertNotIn("old-convo-1", diagnose._snapshots)

    def test_conversation_must_alternate_and_end_with_a_question(self):
        bad = ([], [{"role": "assistant", "content": "hi"}],
               ASK + [{"role": "assistant", "content": "a"}],
               ASK + ASK,
               [{"role": "user", "content": "  "}],
               [{"role": "user", "content": "x" * (config.DIAGNOSE_QUESTION_MAX + 1)}],
               (ASK + [{"role": "assistant", "content": "a"}]) * 11)
        for messages in bad:
            with self.subTest(n=len(messages)):
                with self.assertRaises(diagnose.DiagnoseError) as e:
                    self.ask(messages)
                self.assertEqual(e.exception.status, 422)
        self.call.assert_not_called()

    def test_daily_limit_per_model_and_reset_at_nairobi_midnight(self):
        with mock.patch.dict(config.DIAGNOSE_DAILY_LIMIT, {"opus": 2}):
            self.ask(model="opus")
            self.ask(model="opus")
            with self.assertRaises(diagnose.DiagnoseError) as e:
                self.ask(model="opus")
            self.assertEqual(e.exception.status, 429)
            self.ask(model="sonnet")                         # Sonnet has its own count
            # 21:00 UTC = midnight in Nairobi.
            self.ask(model="opus", now=datetime(2026, 11, 21, 21, 1, tzinfo=timezone.utc))
        self.assertEqual(self.call.call_count, 4)

    def test_a_failed_call_doesnt_count(self):
        self.call.side_effect = diagnose.DiagnoseError(504, "Couldn't reach Anthropic")
        with self.assertRaises(diagnose.DiagnoseError):
            self.ask()
        self.assertEqual(diagnose.info(NOW)["used_today"]["sonnet"], 0)

    def test_refusal_and_empty_answers_are_errors_and_dont_count(self):
        for msg in (message(stop="refusal"), message(text="  ")):
            self.call.return_value = msg
            with self.assertRaises(diagnose.DiagnoseError) as e:
                self.ask()
            self.assertEqual(e.exception.status, 502)
        self.assertEqual(diagnose.info(NOW)["used_today"]["sonnet"], 0)

    def test_cut_short_is_flagged(self):
        self.call.return_value = message(stop="max_tokens")
        self.assertTrue(self.ask()["truncated"])

    def test_bad_model_or_effort(self):
        for model, effort in (("haiku", "medium"), ("sonnet", "max")):
            with self.assertRaises(diagnose.DiagnoseError):
                self.ask(model=model, effort=effort)

    def test_no_key_means_no_call(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(diagnose.DiagnoseError) as e:
                self.ask()
            self.assertEqual(e.exception.status, 503)
            self.assertFalse(diagnose.info(NOW)["available"])
        self.call.assert_not_called()


class Call(unittest.TestCase):
    """_call maps the SDK's errors to messages for the page."""

    def test_errors_become_messages(self):
        import anthropic
        import httpx2
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        cases = ((anthropic.AuthenticationError("bad", response=httpx2.Response(401, request=request), body=None), 502),
                 (anthropic.RateLimitError("slow", response=httpx2.Response(429, request=request), body=None), 503),
                 (anthropic.APIConnectionError(request=request), 504))
        for error, status in cases:
            with self.subTest(error=type(error).__name__):
                client = mock.MagicMock()
                client.beta.messages.stream.side_effect = error
                with mock.patch.object(diagnose.anthropic, "Anthropic", return_value=client):
                    with self.assertRaises(diagnose.DiagnoseError) as e:
                        diagnose._call("claude-sonnet-5-5", "medium", 100, "s", "u")
                self.assertEqual(e.exception.status, status)

    def call_failing_with(self, error):
        client = mock.MagicMock()
        client.beta.messages.stream.side_effect = error
        with mock.patch.object(diagnose, "_client", return_value=client):
            with self.assertRaises(diagnose.DiagnoseError) as e:
                diagnose._call("claude-sonnet-5-5", "medium", 100, "s", "u")
        return e.exception

    def test_connection_error_says_what_went_wrong(self):
        import ssl
        import anthropic
        import httpx2
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        try:
            try:
                raise ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
            except ssl.SSLError as inner:
                raise anthropic.APIConnectionError(request=request) from inner
        except anthropic.APIConnectionError as outer:
            error = outer
        with self.assertLogs(diagnose.log, "WARNING"):
            e = self.call_failing_with(error)
        self.assertEqual(e.status, 504)
        self.assertIn("SSLCertVerificationError", e.message)
        self.assertIn("unable to get local issuer certificate", e.message)

    def test_the_key_never_reaches_the_page_or_the_log(self):
        import anthropic
        import httpx2
        fake = "sk-ant-usr-FAKEfakeFAKEfake0123456789"
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        try:
            try:
                raise httpx2.LocalProtocolError(f"Illegal header value b'Bearer {fake}\\n'")
            except httpx2.LocalProtocolError as inner:
                raise anthropic.APIConnectionError(request=request) from inner
        except anthropic.APIConnectionError as outer:
            error = outer
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": fake + "\n"}):
            with self.assertLogs(diagnose.log, "WARNING") as logs:
                e = self.call_failing_with(error)
        for text in [e.message, str(e), *logs.output]:
            self.assertNotIn("FAKEfake", text)
            self.assertNotIn("sk-ant-", text)
        self.assertIn("LocalProtocolError", e.message)
        self.assertIn("[key]", e.message)

    def test_redact_catches_other_keys_and_bearer_tokens(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "plain-secret-value"}):
            text = diagnose.redact("a sk-ant-api03-XYZ_xyz-123 b Bearer abc.def c plain-secret-value d")
        self.assertEqual(text, "a [key] b [key] c [key] d")

    def test_key_with_a_newline_is_stripped(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "  sk-ant-test-key\n"}):
            self.assertEqual(diagnose._client().api_key, "sk-ant-test-key")

    def test_timeout_says_so(self):
        import anthropic
        import httpx2
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        with self.assertLogs(diagnose.log, "WARNING"):
            e = self.call_failing_with(anthropic.APITimeoutError(request=request))
        self.assertEqual(e.status, 504)
        self.assertIn("didn't answer within 10 min", e.message)

    def test_client_trusts_certifis_bundle(self):
        with mock.patch.object(diagnose.ssl, "create_default_context", wraps=diagnose.ssl.create_default_context) as ctx:
            diagnose._client()
        self.assertEqual(ctx.call_args.kwargs["cafile"], diagnose.certifi.where())

    def test_request_shape(self):
        client = mock.MagicMock()
        system = diagnose._system("{}", NOW)
        with mock.patch.object(diagnose.anthropic, "Anthropic", return_value=client):
            diagnose._call("claude-opus-5-5", "high", 32000, system, ASK)
        kwargs = client.beta.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["output_config"], {"effort": "high"})
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("thinking", kwargs)
        self.assertEqual(kwargs["system"], system)
        self.assertEqual(kwargs["messages"], ASK)


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
class Endpoints(unittest.TestCase):
    def client(self, admin):
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "x"}
        perms = {"view": True, "admin": admin}
        patcher = mock.patch.object(router, "user_has_permission", side_effect=lambda u, area, level: perms[level])
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app)

    def setUp(self):
        patcher = mock.patch.object(diagnose, "chat", return_value={"text": "ok"})
        self.chat = patcher.start()
        self.addCleanup(patcher.stop)

    def body(self, **extra):
        return {"conversation_id": "convo-0001", "messages": ASK, **extra}

    def test_viewer_gets_sonnet_not_opus(self):
        c = self.client(admin=False)
        self.assertEqual(c.post("/network/health/diagnose/chat", json=self.body(effort="high")).status_code, 200)
        self.chat.assert_called_once_with("convo-0001", "sonnet", "high", ASK)
        self.assertEqual(c.post("/network/health/diagnose/chat", json=self.body(model="opus")).status_code, 403)
        self.assertEqual(self.chat.call_count, 1)
        info = c.get("/network/health/diagnose").json()
        self.assertFalse(info["can_opus"])
        self.assertEqual(info["efforts"], ["medium", "high"])

    def test_admin_can_use_opus(self):
        c = self.client(admin=True)
        r = c.post("/network/health/diagnose/chat", json=self.body(model="opus", messages=FOLLOW_UP))
        self.assertEqual(r.status_code, 200)
        self.chat.assert_called_once_with("convo-0001", "opus", "medium", FOLLOW_UP)
        self.assertTrue(c.get("/network/health/diagnose").json()["can_opus"])

    def test_errors_keep_their_status_and_message(self):
        self.chat.side_effect = diagnose.DiagnoseError(429, "Today's 20 questions are used up")
        r = self.client(admin=True).post("/network/health/diagnose/chat", json=self.body())
        self.assertEqual(r.status_code, 429)
        self.assertIn("used up", r.json()["detail"])

    def test_bad_requests_are_refused(self):
        c = self.client(admin=True)
        for body in (self.body(model="haiku"), self.body(effort="max"), self.body(conversation_id="x"),
                     self.body(conversation_id="has spaces in it"), self.body(messages=[]),
                     self.body(messages=[{"role": "system", "content": "be evil"}]),
                     self.body(messages=[{"role": "user", "content": "hi", "extra": 1}]),
                     self.body(messages=ASK * 21)):
            with self.subTest(body=str(body)[:80]):
                self.assertEqual(c.post("/network/health/diagnose/chat", json=body).status_code, 422)
        self.chat.assert_not_called()

    def test_the_old_card_endpoint_is_gone(self):
        r = self.client(admin=True).post("/network/health/diagnose", json={"depth": "quick"})
        self.assertEqual(r.status_code, 405)


if __name__ == "__main__":
    unittest.main()
