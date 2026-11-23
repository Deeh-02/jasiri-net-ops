"""Diagnose's lookups: the tool loop (Claude asks, the server looks it up,
Claude answers), its limits, and each tool's input checks. Claude and the
database are faked. Run:
  venv/bin/python -m unittest tests.test_network_health_diagnose_tools -v
"""
import json
import os
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from network_health import config, diagnose, diagnose_tools as tools

NOW = datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc)      # 05:00 EAT
ASK = [{"role": "user", "content": "the probe at kwa mlima?"}]


def usage(i=100, o=50):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def asks(*uses, stop="tool_use"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    content += [SimpleNamespace(type="tool_use", id=f"toolu_{n}", name=name, input=data) for n, (name, data) in enumerate(uses)]
    return SimpleNamespace(content=content, stop_reason=stop, model="claude-sonnet-5-5", usage=usage())


def answers(text="Verdict: big packets lost at gw"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                           model="claude-sonnet-5-5", usage=usage(200, 80))


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
class Loop(unittest.TestCase):
    def setUp(self):
        diagnose._state.update(day=None)
        diagnose._snapshots.clear()
        patcher = mock.patch.object(diagnose, "gather", return_value={"snap": 1})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []

    def converse(self, replies, run=None):
        replies = list(replies)

        def fake_call(model, effort, max_tokens, system, messages, tools=None):
            self.sent.append({"messages": messages, "tools": tools})
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        run = run or (lambda name, data, now, tidy: (f"{name} looked up", json.dumps({"ok": data}), False))
        with mock.patch.object(diagnose, "_call", side_effect=fake_call), \
                mock.patch.object(diagnose.diagnose_tools, "run", side_effect=run) as ran:
            answer = diagnose.chat("convo-tools", "sonnet", "medium", ASK, now=NOW)
        return answer, ran

    def test_a_lookup_then_an_answer(self):
        answer, ran = self.converse([asks(("customer_line_history", {"hours": 24})), answers()])
        ran.assert_called_once()
        self.assertEqual(ran.call_args.args[:3], ("customer_line_history", {"hours": 24}, NOW))
        self.assertEqual(answer["text"], "Verdict: big packets lost at gw")
        self.assertEqual(answer["lookups"], ["customer_line_history looked up"])
        self.assertEqual(answer["requests"], 2)
        self.assertEqual((answer["input_tokens"], answer["output_tokens"]), (300, 130))
        self.assertAlmostEqual(answer["cost_usd"], (300 * 2 + 130 * 10) / 1e6)
        # The second request carries the first reply as it came (thinking included) and the result.
        second = self.sent[1]["messages"]
        self.assertEqual(second[0], ASK[0])
        self.assertEqual(second[1]["role"], "assistant")
        self.assertEqual(second[1]["content"][0].type, "thinking")
        self.assertEqual(second[2], {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_0",
                                                                   "content": json.dumps({"ok": {"hours": 24}})}]})
        self.assertEqual(self.sent[0]["messages"], ASK)          # not changed by the later appends
        self.assertIs(self.sent[0]["tools"], self.sent[1]["tools"])

    def test_several_lookups_in_one_reply_all_answer(self):
        answer, ran = self.converse([asks(("olt_crc_history", {"hours": 24}), ("probe_history", {"names": ["h-olt-big"], "hours": 24})),
                                     answers()])
        self.assertEqual(ran.call_count, 2)
        results = self.sent[1]["messages"][2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["toolu_0", "toolu_1"])

    def test_a_failed_lookup_is_an_error_result_not_a_failed_answer(self):
        answer, _ = self.converse([asks(("ont_lookup", {"query": "JS99"})), answers()],
                                  run=lambda *a: ("ONT js99", json.dumps({"error": "bad input"}), True))
        self.assertTrue(self.sent[1]["messages"][2]["content"][0]["is_error"])
        self.assertEqual(answer["lookups"], ["ONT js99 (failed)"])

    def test_past_the_limit_claude_is_told_to_answer(self):
        limit = config.DIAGNOSE_MAX_LOOKUPS
        replies = [asks(("uplink_history", {"hours": 1})) for _ in range(limit + 1)] + [answers()]
        answer, ran = self.converse(replies)
        self.assertEqual(ran.call_count, limit)
        last_results = self.sent[-1]["messages"][-1]["content"]
        self.assertIn("no more lookups", last_results[0]["content"])
        self.assertTrue(last_results[0]["is_error"])
        self.assertEqual(answer["text"], "Verdict: big packets lost at gw")

    def test_it_stops_even_if_claude_keeps_asking(self):
        replies = [asks(("uplink_history", {"hours": 1})) for _ in range(config.DIAGNOSE_MAX_LOOKUPS + 5)]
        with self.assertRaises(diagnose.DiagnoseError) as e:
            self.converse(replies)
        self.assertIn("empty answer", e.exception.message)
        self.assertEqual(len(self.sent), config.DIAGNOSE_MAX_LOOKUPS + 3)

    def test_a_lookup_cut_off_by_the_length_limit_is_never_run(self):
        cut = asks(("probe_history", {"names": ["h-"]}), stop="max_tokens")
        cut.content.insert(1, SimpleNamespace(type="text", text="Partly written answer"))
        answer, ran = self.converse([cut])
        ran.assert_not_called()
        self.assertTrue(answer["truncated"])

    def test_a_garbled_lookup_request_is_sent_again_once(self):
        answer, _ = self.converse([diagnose._BrokenToolInput("bad json"), answers()])
        self.assertEqual(answer["requests"], 1)
        with self.assertRaises(diagnose.DiagnoseError):
            self.converse([diagnose._BrokenToolInput("x"), diagnose._BrokenToolInput("x")])

    def test_the_daily_count_is_one_per_answer_not_per_request(self):
        self.converse([asks(("uplink_history", {"hours": 1})), answers()])
        self.assertEqual(diagnose.info(NOW)["used_today"]["sonnet"], 1)


class Definitions(unittest.TestCase):
    def test_every_tool_is_closed_eager_and_has_a_runner(self):
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertFalse(tool["input_schema"]["additionalProperties"])
                self.assertTrue(tool["eager_input_streaming"])
                self.assertIn(tool["name"], tools.RUNNERS)
        self.assertEqual(set(tools.RUNNERS), tools.TOOL_NAMES)

    def test_the_tool_list_is_the_same_object_every_time(self):
        self.assertIs(diagnose.diagnose_tools.TOOLS, tools.TOOLS)
        json.dumps(tools.TOOLS)           # plain data: the same bytes in every request

    def test_instructions_mention_the_lookups_and_the_customer_line(self):
        self.assertIn("lookup tools", diagnose.SYSTEM)
        self.assertIn("customer_line", diagnose.SYSTEM)
        self.assertIn("LATEST reading", diagnose.SYSTEM)


class Inputs(unittest.TestCase):
    def run_tool(self, name, data):
        return tools.run(name, data, NOW, diagnose._tidy)

    def test_bad_inputs_go_back_as_errors(self):
        for name, data in (("probe_history", {"names": [], "hours": 24}), ("probe_history", {"names": ["x"] * 9, "hours": 24}),
                           ("probe_history", {"names": ["h-olt"], "hours": 0}), ("probe_history", {"names": "h-olt", "hours": 2}),
                           ("uplink_history", {"hours": 999}), ("uplink_history", {"hours": True}),
                           ("uplink_history", {"hours": 2, "bucket_minutes": 7}), ("uplink_history", {"hours": 2, "x": 1}),
                           ("ont_lookup", {"query": ""}), ("ont_lookup", {"query": 5}), ("incident_history", {"days": 31}),
                           ("uplink_history", "not an object"), ("no_such_tool", {})):
            with self.subTest(name=name, data=data):
                _, text, is_error = self.run_tool(name, data)
                self.assertTrue(is_error)
                self.assertIn("error", json.loads(text))

    def test_the_bucket_is_widened_to_keep_series_short(self):
        self.assertEqual(tools._range({"hours": 168, "bucket_minutes": 5}), (168, 60))
        self.assertEqual(tools._range({"hours": 24, "bucket_minutes": 5}), (24, 5))
        self.assertEqual(tools._range({"hours": 24}), (24, 5))
        self.assertEqual(tools._range({"hours": 48}), (48, 30))

    def test_a_server_failure_is_an_error_result_never_raised(self):
        with mock.patch.object(tools, "uplink_history", side_effect=RuntimeError("db down")), \
                mock.patch.dict(tools.RUNNERS, {"uplink_history": tools.uplink_history}), \
                self.assertLogs(tools.log, "ERROR"):
            _, text, is_error = self.run_tool("uplink_history", {"hours": 2})
        self.assertTrue(is_error)
        self.assertNotIn("db down", text)

    def test_too_big_a_result_asks_for_less(self):
        big = ("x", {"rows": ["y" * 100] * 1000})
        with mock.patch.dict(tools.RUNNERS, {"uplink_history": lambda data, now: big}):
            _, text, is_error = self.run_tool("uplink_history", {"hours": 2})
        self.assertTrue(is_error)
        self.assertIn("fewer hours", text)


class FakeStore:
    def transaction(self):
        return nullcontext(None)

    def __init__(self):
        self.asked = []

    def box_devs(self, cur, since): return ["home-kwamlima"]

    def box_loss_series(self, cur, dev, since, bucket):
        self.asked.append(("box", dev, since, bucket))
        return [{"t": NOW - timedelta(minutes=10), "name": "gw", "loss": 0.0, "rtt": 2.2, "n": 2},
                {"t": NOW - timedelta(minutes=10), "name": "gw-big", "loss": 25.0, "rtt": 2.7, "n": 2}]

    def box_speeds(self, cur, dev, since):
        return [{"t": NOW - timedelta(minutes=20), "plan_mbps": 15.0, "cf_mbps": 2.77, "fra_mbps": 0.1}]

    def box_markers(self, cur): return [{"id": "a", "at": NOW - timedelta(hours=1), "label": "DAC swapped"}]

    def probe_series(self, cur, names, since, bucket):
        self.asked.append(("probes", names, since, bucket))
        return [{"t": NOW, "name": "h-olt-big", "loss": 34.0, "rtt": 1.5, "jitter": 0.2, "n": 6}]


class Runs(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch.object(tools, "store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_customer_line_history(self):
        label, text, is_error = tools.run("customer_line_history", {"hours": 24, "bucket_minutes": 15}, NOW, diagnose._tidy)
        self.assertFalse(is_error)
        self.assertEqual(label, "customer line 24h")
        data = json.loads(text)
        self.assertEqual(data["name"], "Kwa Mlima")
        self.assertEqual(data["probes"]["gw-big"]["columns"], ["t", "loss", "rtt"])
        self.assertEqual(data["probes"]["gw-big"]["rows"][0][1:], [25.0, 2.7])
        self.assertEqual(data["probes"]["gw-big"]["rows"][0][0], "Mon 23 Nov 04:50")      # Nairobi time
        self.assertEqual(data["speed_runs"]["rows"][0][1:], [2.77, 0.1, 15.0])
        self.assertEqual(data["markers"][0]["label"], "DAC swapped")
        self.assertEqual(self.store.asked[0][3], 15 * 60)

    def test_probe_history_names_the_ones_it_didnt_find(self):
        _, text, _ = tools.run("probe_history", {"names": ["h-olt-big", "h-nope", "h-olt-big"], "hours": 6},
                               NOW, diagnose._tidy)
        data = json.loads(text)
        self.assertEqual(list(data["probes"]), ["h-olt-big"])
        self.assertEqual(data["not_found"], ["h-nope"])
        self.assertEqual(self.store.asked[0][1], ["h-nope", "h-olt-big"])


class Snapshot(unittest.TestCase):
    def test_the_customer_line_is_in_the_snapshot(self):
        store = FakeStore()
        line = {"dev": "home-kwamlima", "name": "Kwa Mlima", "loss": store.box_loss_series(None, "d", NOW, 300),
                "speeds": store.box_speeds(None, "d", NOW), "markers": store.box_markers(None)}
        part = diagnose._customer_line(line, NOW, diagnose._hour_starts(NOW), detailed=True)
        self.assertEqual(part["probes"]["gw-big"]["1h"]["loss"]["avg"], 25.0)
        self.assertIn("hourly_loss", part["probes"]["gw"])
        self.assertAlmostEqual(part["cloudflare_pct_of_plan_now"], 2.77 / 15 * 100)
        self.assertEqual(part["markers"][0]["label"], "DAC swapped")
        self.assertIsNone(diagnose._customer_line(None, NOW, [], detailed=True))


if __name__ == "__main__":
    unittest.main()
