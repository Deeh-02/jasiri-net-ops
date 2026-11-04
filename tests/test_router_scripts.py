"""Lint for the CCR2116 monitor scripts — trusted code on a production core
router, so this is the gate every edit to router/ops-live.rsc and
router/ops-sessions.rsc must pass. Also checks the render tool and the
uninstall file. Run from the repo root:
  venv/bin/python -m unittest tests.test_router_scripts -v
"""
import importlib.util
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MONITOR_SCRIPTS = [REPO / "router" / "ops-live.rsc", REPO / "router" / "ops-sessions.rsc"]
UNINSTALL = REPO / "router" / "uninstall-ops-monitor.rsc"

# Read-only commands the monitor may run. Anything else fails the build.
ALLOWED = [
    ("interface", "monitor-traffic"), ("interface", "ethernet", "monitor"),
    ("interface", "find"), ("interface", "get"), ("interface", "print"),
    ("ppp", "active", "find"), ("ppp", "active", "get"), ("ppp", "active", "print"),
    ("system", "resource", "get"), ("system", "resource", "print"),
    ("system", "health", "find"), ("system", "health", "get"), ("system", "health", "print"),
    ("system", "clock", "get"),
    ("ip", "route", "find"), ("ip", "route", "get"), ("ip", "route", "print"),
    ("ip", "arp", "find"), ("ip", "arp", "get"), ("ip", "arp", "print"),
    ("ping",), ("tool", "fetch"),   # not flood-ping: the CCR2116 device-mode blocks it
]
FORBIDDEN_VERBS = re.compile(
    r"(?<![\w:$\"-])(add|set|remove|enable|disable|reset|reboot|shutdown|import|export|upgrade)(?![\w-])")
FORBIDDEN_BUILTINS = re.compile(r":(execute|parse)\b")
STRING = re.compile(r'"(?:\\.|[^"\\])*"')
COMMAND = re.compile(r"/([a-z][\w-]*(?: [a-z][\w-]*)*)")


def code_only(text):
    """The script with full-line comments and string literals removed, so a
    word inside a comment or a JSON key can't trip (or hide from) the lint."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return STRING.sub('""', "\n".join(lines))


def commands(text):
    return [tuple(m.group(1).split(" ")) for m in COMMAND.finditer(code_only(text))]


def is_allowed(command):
    return any(command[:len(a)] == a for a in ALLOWED)


class AllowList(unittest.TestCase):
    def test_only_read_only_commands(self):
        for path in MONITOR_SCRIPTS:
            found = commands(path.read_text())
            self.assertTrue(found, path.name)
            bad = [" ".join(c) for c in found if not is_allowed(c)]
            self.assertEqual(bad, [], f"{path.name} uses commands outside the allow-list")

    def test_no_write_verbs_anywhere_in_the_code(self):
        for path in MONITOR_SCRIPTS:
            code = code_only(path.read_text())
            self.assertEqual(FORBIDDEN_VERBS.findall(code), [], path.name)
            self.assertEqual(FORBIDDEN_BUILTINS.findall(code), [], path.name)

    def test_no_sensitive_menus(self):
        for path in MONITOR_SCRIPTS:
            code = code_only(path.read_text())
            for menu in ("ppp secret", "system script", "system scheduler", "user", "file", "ip firewall"):
                self.assertNotIn(f"/{menu}", code, f"{path.name} touches /{menu}")

    def test_every_fetch_is_safe(self):
        for path in MONITOR_SCRIPTS:
            fetches = [line for line in path.read_text().splitlines()
                       if "/tool fetch" in line and not line.lstrip().startswith("#")]
            self.assertTrue(fetches, path.name)
            for line in fetches:
                self.assertIn("output=none", line)          # never a file on flash
                self.assertIn("check-certificate=yes", line)
                self.assertIn("idle-timeout=", line)
                for banned in ("keep-result", "check-certificate=no", "dst-path", "upload=", "mode=ftp"):
                    self.assertNotIn(banned, line)

    def test_gateway_check_uses_ping_as_value_not_flood_ping(self):
        # The 2116's device-mode refuses /tool flood-ping, and the script would
        # swallow the error and report a healthy gateway as 100% lost.
        text = MONITOR_SCRIPTS[0].read_text()
        self.assertNotIn("/tool flood-ping", code_only(text))
        self.assertIn("/ping $addr count=3 interval=200ms as-value", text)

    def test_the_lint_itself_catches_writes(self):
        sample = ':local x 1\n:set x 2\n/ppp active remove [find]\n/system reboot\n# /user add (comment)\n:put "/file print"\n'
        bad = [c for c in commands(sample) if not is_allowed(c)]
        self.assertEqual(bad, [("ppp", "active", "remove"), ("system", "reboot")])
        self.assertEqual(FORBIDDEN_VERBS.findall(code_only(sample)), ["remove", "reboot"])
        self.assertEqual(commands("/interface monitor-traffic $x once as-value"), [("interface", "monitor-traffic")])


class NoSecrets(unittest.TestCase):
    def test_repo_copies_hold_placeholders_and_start_as_dry_runs(self):
        for path in MONITOR_SCRIPTS:
            text = path.read_text()
            self.assertIn(':local ingestToken "PASTE-NETWORK_INGEST_TOKEN_CCR2116-HERE"', text)
            self.assertIn("YOUR-RENDER-APP.onrender.com", text)
            self.assertIn(":local dryRun true", text)
            self.assertNotIn("password", code_only(text).lower())

    def test_logging_never_includes_the_token(self):
        for path in MONITOR_SCRIPTS:
            for line in path.read_text().splitlines():
                if ":log" in line:
                    self.assertNotIn("Token", line, path.name)


def load_render_tool():
    spec = importlib.util.spec_from_file_location("render_router_script", REPO / "tools" / "render_router_script.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RenderTool(unittest.TestCase):
    def setUp(self):
        self.tool = load_render_tool()
        self.source = MONITOR_SCRIPTS[0].read_text()

    def test_fills_url_token_and_mode(self):
        out = self.tool.render(self.source, "https://ops.example.com/network/ingest", "A" * 40, "ccr2116", live=True,
                               gateway_ip="41.90.0.1")
        self.assertIn(':local ingestUrl "https://ops.example.com/network/ingest"', out)
        self.assertIn(f':local ingestToken "{"A" * 40}"', out)
        self.assertIn(":local dryRun false", out)
        self.assertIn(':local gatewayIp "41.90.0.1"', out)
        self.assertNotIn("PASTE-NETWORK_INGEST_TOKEN", out)

    def test_refuses_tokens_that_would_break_the_header(self):
        for bad in ("", "short", "has,comma" + "x" * 30, 'has"quote' + "x" * 30, "has space" + "x" * 30):
            with self.assertRaises(ValueError, msg=bad):
                self.tool.check_token(bad)
        self.assertEqual(self.tool.check_token("A-b_9" * 8), "A-b_9" * 8)

    def test_https_only(self):
        with self.assertRaises(ValueError):
            self.tool.ingest_url("http://ops.example.com")
        self.assertEqual(self.tool.ingest_url("https://ops.example.com/"), "https://ops.example.com/network/ingest")

    def test_rendered_files_are_gitignored(self):
        self.assertIn("router-rendered/", (REPO / ".gitignore").read_text().splitlines())


class Uninstall(unittest.TestCase):
    def test_removes_everything_the_monitor_creates(self):
        text = UNINSTALL.read_text()
        self.assertIn('/system scheduler remove [find where name="ops-live" || name="ops-sessions"]', text)
        self.assertIn('/system script remove [find where name="ops-live" || name="ops-sessions"]', text)
        self.assertIn('/system script environment remove [find where name~"^opsNm"]', text)
        for path in MONITOR_SCRIPTS:
            for name in set(re.findall(r":global (\w+)", path.read_text())):
                self.assertTrue(name.startswith("opsNm"), f"{path.name}: global {name} would survive uninstall")


if __name__ == "__main__":
    unittest.main()
