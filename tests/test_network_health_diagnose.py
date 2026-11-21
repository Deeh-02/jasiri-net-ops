"""The "Diagnose" card: the summary sent to Claude, the daily limits, showing
the last answer again, error handling, and who may run the detailed analysis.
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

    def test_question_goes_after_the_data_and_before_the_task(self):
        text = diagnose._user_message("quick", {"a": 1}, "Njeri House slow")
        self.assertLess(text.index('{"a":1}'), text.index("Njeri House slow"))
        self.assertLess(text.index("Njeri House slow"), text.index("quick check"))


def message(text="Verdict:\n- all good", stop="end_turn", model="claude-sonnet-5-5"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, model=model,
                           usage=SimpleNamespace(input_tokens=7000, output_tokens=500))


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
class Run(unittest.TestCase):
    def setUp(self):
        diagnose._state.update(day=None, running=set(), last={})
        for target, value in (("gather", {"snap": 1}), ("_call", message())):
            patcher = mock.patch.object(diagnose, target, return_value=value)
            setattr(self, target.strip("_"), patcher.start())
            self.addCleanup(patcher.stop)

    def test_quick_uses_sonnet_at_medium_and_deep_uses_opus(self):
        diagnose.run("quick", now=NOW)
        model, effort, max_tokens, system, user = self.call.call_args.args
        self.assertEqual((model, effort), ("claude-sonnet-5-5", "medium"))
        self.call.return_value = message(model="claude-opus-5-5")
        diagnose.run("deep", now=NOW)
        self.assertEqual(self.call.call_args.args[0], "claude-opus-5-5")
        self.assertEqual(self.gather.call_args.args[0], "deep")

    def test_answer_has_text_cost_and_model(self):
        a = diagnose.run("quick", now=NOW)
        self.assertEqual(a["text"], "Verdict:\n- all good")
        self.assertEqual(a["model_label"], "Claude Sonnet 5.5")
        self.assertAlmostEqual(a["cost_usd"], (7000 * 2 + 500 * 10) / 1e6)
        self.assertFalse(a["reused"])
        self.assertFalse(a["truncated"])

    def test_fallback_model_is_shown_and_cost_unknown(self):
        self.call.return_value = message(model="claude-opus-4-8")
        a = diagnose.run("quick", now=NOW)
        self.assertEqual(a["model_label"], "claude-opus-4-8")
        self.assertIsNone(a["cost_usd"])

    def test_same_button_within_5_minutes_shows_the_last_answer_free(self):
        diagnose.run("quick", now=NOW)
        again = diagnose.run("quick", now=NOW + timedelta(minutes=4))
        self.assertTrue(again["reused"])
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(diagnose.info(NOW)["used_today"]["quick"], 1)
        diagnose.run("quick", now=NOW + timedelta(minutes=6))
        self.assertEqual(self.call.call_count, 2)

    def test_a_question_always_asks_again(self):
        diagnose.run("quick", now=NOW)
        diagnose.run("quick", "why slow?", now=NOW + timedelta(minutes=1))
        self.assertEqual(self.call.call_count, 2)
        self.assertIn("why slow?", self.call.call_args.args[4])

    def test_daily_limit_and_reset_at_nairobi_midnight(self):
        with mock.patch.dict(config.DIAGNOSE_DAILY_LIMIT, {"deep": 2}):
            diagnose.run("deep", "a", now=NOW)
            diagnose.run("deep", "b", now=NOW)
            with self.assertRaises(diagnose.DiagnoseError) as e:
                diagnose.run("deep", "c", now=NOW)
            self.assertEqual(e.exception.status, 429)
            # 21:00 UTC = midnight in Nairobi.
            diagnose.run("deep", "d", now=datetime(2026, 11, 21, 21, 1, tzinfo=timezone.utc))
        self.assertEqual(self.call.call_count, 3)

    def test_a_failed_call_doesnt_count(self):
        self.call.side_effect = diagnose.DiagnoseError(504, "Couldn't reach Anthropic")
        with self.assertRaises(diagnose.DiagnoseError):
            diagnose.run("quick", now=NOW)
        info = diagnose.info(NOW)
        self.assertEqual(info["used_today"]["quick"], 0)
        self.assertEqual(info["running"], [])

    def test_refusal_and_empty_answers_are_errors(self):
        for msg in (message(stop="refusal"), message(text="  ")):
            self.call.return_value = msg
            with self.assertRaises(diagnose.DiagnoseError) as e:
                diagnose.run("quick", "q", now=NOW)
            self.assertEqual(e.exception.status, 502)

    def test_cut_short_is_flagged(self):
        self.call.return_value = message(stop="max_tokens")
        self.assertTrue(diagnose.run("quick", now=NOW)["truncated"])

    def test_one_at_a_time_per_button(self):
        diagnose._state["running"].add("deep")
        with self.assertRaises(diagnose.DiagnoseError) as e:
            diagnose.run("deep", now=NOW)
        self.assertEqual(e.exception.status, 409)

    def test_no_key_means_no_call(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(diagnose.DiagnoseError) as e:
                diagnose.run("quick", now=NOW)
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
        with mock.patch.object(diagnose.anthropic, "Anthropic", return_value=client):
            diagnose._call("claude-opus-5-5", "high", 64000, "sys", "user text")
        kwargs = client.beta.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["output_config"], {"effort": "high"})
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertNotIn("thinking", kwargs)
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "user text"}])


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
        patcher = mock.patch.object(diagnose, "run", return_value={"text": "ok"})
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_viewer_can_run_quick_not_deep(self):
        c = self.client(admin=False)
        self.assertEqual(c.post("/network/health/diagnose", json={"depth": "quick"}).status_code, 200)
        self.assertEqual(c.post("/network/health/diagnose", json={"depth": "deep"}).status_code, 403)
        self.assertEqual(self.run.call_count, 1)
        self.assertFalse(c.get("/network/health/diagnose").json()["can_deep"])

    def test_admin_can_run_deep(self):
        c = self.client(admin=True)
        r = c.post("/network/health/diagnose", json={"depth": "deep", "question": "why?"})
        self.assertEqual(r.status_code, 200)
        self.run.assert_called_once_with("deep", "why?")
        self.assertTrue(c.get("/network/health/diagnose").json()["can_deep"])

    def test_errors_keep_their_status_and_message(self):
        self.run.side_effect = diagnose.DiagnoseError(429, "Today's 10 detailed analyses are used up")
        r = self.client(admin=True).post("/network/health/diagnose", json={"depth": "deep"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("used up", r.json()["detail"])

    def test_long_question_and_bad_depth_are_refused(self):
        c = self.client(admin=True)
        self.assertEqual(c.post("/network/health/diagnose", json={"question": "x" * 501}).status_code, 422)
        self.assertEqual(c.post("/network/health/diagnose", json={"depth": "max"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
