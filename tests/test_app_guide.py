"""The "how do I…" guide behind the Ask assistant (db/app_guide.py)."""
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from db import app_guide, assistant_tools

ROOT = Path(__file__).resolve().parent.parent
ADMIN = {"id": 1, "role": "admin"}
TECH = {"id": 2, "role": "technician", "role_id": 1}


def perms(*granted):
    return mock.patch("routers.permissions.user_has_permission",
                      side_effect=lambda user, section, action: (section, action) in granted)


def ask(user, topic=None):
    args = {} if topic is None else {"topic": topic}
    return json.loads(assistant_tools.run_tool("get_how_to", json.dumps(args), user))


def titles(result):
    return [g["title"] for g in result.get("guides", [])]


class Content(unittest.TestCase):
    def test_every_guide_is_complete_and_ids_are_unique(self):
        self.assertEqual(len(app_guide.BY_ID), len(app_guide.GUIDES))
        for g in app_guide.GUIDES:
            self.assertTrue(g["title"] and g["words"] and g["steps"], g["id"])
            self.assertTrue(all(isinstance(s, str) and s.endswith((".", "…")) for s in g["steps"]), g["id"])

    def test_menu_names_exist_in_the_sidebar(self):
        """A renamed page breaks a guide: keep `where` in step with index.html."""
        html = (ROOT / "static/index.html").read_text()
        sections = set(re.findall(r'nav-section-title"[^>]*>\s*<span>([^<]+)</span>', html))
        pages = set(re.findall(r'<span class="nav-link-text">([^<]+)</span>', html))
        self.assertIn("Network", sections)
        for g in app_guide.GUIDES:
            if g["where"]:
                section, page = g["where"]
                self.assertIn(section, sections, g["id"])
                self.assertIn(page, pages, g["id"])
                self.assertIn(f"{section} → {page}", " ".join(g["steps"]), g["id"])

    def test_permissions_are_ones_the_app_uses(self):
        roles = (ROOT / "static/js/roles.js").read_text()
        pages = (ROOT / "static/js/common.js").read_text()
        for g in app_guide.GUIDES:
            needs = g["needs"] if isinstance(g["needs"], list) else []
            for section, action in needs + list(g.get("also", [])):
                where = roles + pages
                self.assertRegex(where, rf'(key|section): "{section}"|\["{section}", "{action}"\]', g["id"])
                self.assertRegex(where, rf'"{action}"', g["id"])


class ByRole(unittest.TestCase):
    def test_everyone_gets_the_tool_and_the_basics(self):
        with perms():
            result = ask(TECH)
        self.assertIn("Change your password", result["topics"])
        self.assertNotIn("Send an SMS to customers (broadcast)", result["topics"])

    def test_broadcast_for_someone_who_may_send(self):
        with perms(("sites", "send_broadcasts")):
            result = ask(TECH, "how do I send an sms to all customers?")
        self.assertEqual(titles(result)[0], "Send an SMS to customers (broadcast)")
        self.assertIn("Alerts → Broadcast", " ".join(result["guides"][0]["steps"]))

    def test_not_on_your_account_when_the_role_cannot(self):
        with perms():
            result = ask(TECH, "how do I send a broadcast")
        self.assertEqual(result["guides"], [])
        self.assertEqual(result["not_on_your_account"], ["Send an SMS to customers (broadcast)"])

    def test_money_guides_are_never_mentioned_to_someone_without_them(self):
        with perms():
            for topic in ("how do I see revenue", "how do I change a package price"):
                result = ask(TECH, topic)
                self.assertEqual(result["guides"], [], topic)
                text = json.dumps(result).lower()
                self.assertNotIn("revenue report", text, topic)
                self.assertNotIn("package prices", text, topic)
                self.assertNotIn("not_on_your_account", result, topic)

    def test_olt_buttons_need_network_and_olt_too(self):
        with perms(("olt", "control")):
            self.assertEqual(ask(TECH, "reboot an onu")["guides"], [])
        with perms(("olt", "control"), ("sites", "view_network")):
            self.assertEqual(titles(ask(TECH, "reboot an onu"))[0], "Reboot, disable or enable an ONU")

    def test_admin_only_guide(self):
        with perms(("activity_log", "submit")):
            self.assertNotIn("Add or change activity types", titles(ask(TECH, "activity types")))
        self.assertIn("Add or change activity types", titles(ask(ADMIN, "activity types")))

    def test_admin_gets_everything(self):
        self.assertEqual(len(ask(ADMIN)["topics"]), len(app_guide.GUIDES))

    def test_common_questions_find_the_right_guide(self):
        cases = {
            "how do I move a battery to a site": "move_battery",
            "where do I issue cable for an installation": "issue_materials",
            "how to submit a job report": "submit_report",
            "how do I authorize a new onu": "olt_authorize",
            "how can I give a technician access": "roles",
            "I forgot how to change my password": "change_password",
            "how do I add a new staff user": "add_user",
            "how do I return leftover materials": "return_materials",
            "how do I mark a battery arrived": "track_movement",
            "how do I link an onu to a customer": "olt_link",
        }
        for question, guide_id in cases.items():
            self.assertEqual(titles(ask(ADMIN, question))[0], app_guide.BY_ID[guide_id]["title"], question)

    def test_nothing_matches_gives_the_list(self):
        with perms():
            result = ask(TECH, "zzzz qqqq")
        self.assertEqual(result["guides"], [])
        self.assertIn("Change your password", result["topics"])

    def test_bad_arguments(self):
        self.assertIn("error", ask(ADMIN, "x" * 300))
        self.assertIn("error", json.loads(assistant_tools.run_tool("get_how_to", '{"nope": 1}', ADMIN)))


if __name__ == "__main__":
    unittest.main()
