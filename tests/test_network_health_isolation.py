"""Network Health is its own domain. This fails if it imports anything from
the project beyond:
  - routers.auth.get_current_user and routers.permissions.user_has_permission
  - db.alert_channels.send_sms (the one SMS function)
  - db.connection.db_cursor (the shared connection helper; owner-approved)
or if anything other than main.py and the CCR2116 heartbeat (one tick call,
at the owner's request) imports it. Third-party libraries and the standard
library are fine. Run:
  venv/bin/python -m unittest tests.test_network_health_isolation -v
"""
import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "network_health"
PROJECT_PACKAGES = ("routers", "db", "tools", "tests", "main")
ALLOWED = {
    "routers.auth": {"get_current_user"},
    "routers.permissions": {"user_has_permission"},
    "db.alert_channels": {"send_sms"},
    "db.connection": {"db_cursor"},
}
# Who may import network_health, and exactly what.
ALLOWED_IMPORTERS = {
    "main.py": {"network_health": {"router"}},
    "routers/network.py": {"network_health.service": {"tick"}},
}


def imports_of(path):
    """[(module, names)] for every import in a file."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [(alias.name, set()) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.append((node.module, {alias.name for alias in node.names}))
        elif isinstance(node, ast.ImportFrom):
            found.append(("." * node.level + (node.module or ""), {alias.name for alias in node.names}))
    return found


class NetworkHealthImportsOnlyWhatItMay(unittest.TestCase):
    def test_package_imports(self):
        files = sorted(PACKAGE.glob("*.py"))
        self.assertTrue(files)
        for path in files:
            for module, names in imports_of(path):
                with self.subTest(file=path.name, module=module):
                    self.assertFalse(module.startswith("."), "use absolute network_health.* imports")
                    top = module.split(".")[0]
                    if top == "network_health" or top not in PROJECT_PACKAGES:
                        continue
                    self.assertIn(module, ALLOWED, f"{path.name} imports {module}")
                    self.assertTrue(names, f"{path.name}: import names from {module}, not the module")
                    self.assertLessEqual(names, ALLOWED[module], f"{path.name} imports {names} from {module}")

    def test_nothing_else_imports_it(self):
        for path in REPO.rglob("*.py"):
            relative = path.relative_to(REPO).as_posix()
            if relative.startswith(("venv/", "network_health/", "tests/")) or "/." in relative:
                continue
            for module, names in imports_of(path):
                if module.split(".")[0] != "network_health":
                    continue
                with self.subTest(file=relative, module=module):
                    allowed = ALLOWED_IMPORTERS.get(relative, {})
                    self.assertIn(module, allowed, f"{relative} imports {module}")
                    self.assertLessEqual(names, allowed[module])

    def test_page_script_imports_only_common(self):
        script = (REPO / "static" / "js" / "network-health.js").read_text()
        sources = re.findall(r"""^\s*import\b[^;]*?from\s+["']([^"']+)["']""", script, re.MULTILINE)
        self.assertEqual(set(sources), {"./common.js"})

    def test_no_other_page_script_imports_it(self):
        for path in (REPO / "static" / "js").glob("*.js"):
            if path.name in ("network-health.js", "app.js"):
                continue
            with self.subTest(file=path.name):
                self.assertNotIn("network-health.js", path.read_text())


if __name__ == "__main__":
    unittest.main()
