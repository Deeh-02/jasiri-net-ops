"""Dashboard summaries (db/home.py) and who may see which (routers/home.py).
The reducers are pure: they are given what the area's own page loads."""
import json
import unittest
from datetime import datetime
from unittest import mock

from fastapi import HTTPException

from db import home
from routers import home as api

ADMIN = {"id": 1, "role": "admin"}
TECH = {"id": 2, "role": "technician", "role_id": 1}


def perms(*granted):
    return mock.patch.object(api, "user_has_permission",
                             side_effect=lambda user, section, action: user["role"] == "admin" or (section, action) in granted)


def site(id, name, state, sessions=0, since="2026-11-10T08:00:00Z"):
    return {"id": id, "name": name, "state": state, "sessions": sessions, "state_since": since}


class Sites(unittest.TestCase):
    ROWS = [site(1, "Mwiki", "up", 30), site(2, "Benbro", "up", 12), site(3, "Hunters", "down", 25, "2026-11-09T08:00:00Z"),
            site(4, "ACK", "down", 0, "2026-11-12T08:00:00Z"), site(5, "Garage", "flapping", 4), site(6, "Stima", "unknown")]

    def test_counts_by_state_in_a_fixed_order(self):
        s = home.sites(self.ROWS)
        self.assertEqual((s["total"], s["up"]), (6, 2))
        self.assertEqual([(p["label"], p["count"], p["tone"]) for p in s["states"]],
                         [("Up", 2, "good"), ("Unstable", 1, "warn"), ("Down", 2, "bad"), ("Not reporting", 1, "idle")])

    def test_down_sites_longest_first(self):
        self.assertEqual([d["name"] for d in home.sites(self.ROWS)["down"]], ["Hunters", "ACK"])

    def test_a_down_sites_last_count_is_not_people_online(self):
        s = home.sites(self.ROWS)
        self.assertEqual(s["people_online"], 46)                       # 30 + 12 + 4, not Hunters' 25
        self.assertEqual([b["name"] for b in s["busiest"]], ["Mwiki", "Benbro", "Garage"])

    def test_more_down_than_fit(self):
        rows = [site(i, f"S{i:02}", "down") for i in range(9)]
        s = home.sites(rows)
        self.assertEqual((len(s["down"]), s["down_more"]), (home.DOWN_SITES_SHOWN, 3))

    def test_no_sites(self):
        s = home.sites([])
        self.assertEqual((s["total"], s["states"], s["people_online"]), (0, [], 0))


class People(unittest.TestCase):
    def test_an_hour_nobody_counted_is_a_gap_not_a_zero(self):
        fleet = {"people": [{"hour": "2026-11-10T06:00:00Z", "avg": 100}, {"hour": "2026-11-10T09:00:00Z", "avg": 300}],
                 "totals": {"peak_people": 320, "peak_at": "2026-11-10T09:00:00Z"}}
        p = home.people(fleet)
        self.assertEqual([x["people"] for x in p["points"]], [100, None, None, 300])
        self.assertEqual(p["points"][1]["t"], "2026-11-10T07:00:00Z")
        self.assertEqual((p["average"], p["peak"]), (200, 320))

    def test_nothing_counted(self):
        p = home.people({"people": [], "totals": {}})
        self.assertEqual((p["points"], p["average"], p["peak"]), ([], None, None))


class Fibre(unittest.TestCase):
    def test_status_and_signal(self):
        listing = {"configured": True, "total": 4, "counts": {"Online": 2, "LOS": 1, "Power fail": 1}, "weak": 1,
                   "status_stale": False, "status_polled_at": "2026-11-14T10:00:00+00:00",
                   "onus": [{"signal_quality": "Very good"}, {"signal_quality": "Very good"}, {"signal_quality": "Critical"},
                            {"signal_quality": None}]}
        f = home.fibre(listing)
        self.assertEqual((f["total"], f["online"], f["weak"], f["stale"]), (4, 2, 1, False))
        self.assertEqual([(p["label"], p["count"], p["tone"]) for p in f["status"]],
                         [("Online", 2, "good"), ("Power fail", 1, "warn"), ("LOS", 1, "bad")])
        self.assertEqual([(p["label"], p["count"]) for p in f["signal"]], [("Very good", 2), ("Critical", 1)])

    def test_not_connected(self):
        self.assertEqual(home.fibre({"configured": False}), {"configured": False})


class Customers(unittest.TestCase):
    NOW = datetime(2026, 11, 14, 12, 0, 0)

    def rows(self):
        c = lambda status, plan, expiry, enabled=True: {"name": "Jane Private", "phone": "0700000000", "username": "jane",
                                                        "account_status": status, "plan": plan, "expiry": expiry, "enabled": enabled}
        return [c("Active", "10 Mbps", "2026-11-16T09:00:00Z"), c("active", "10 Mbps", "2026-12-20T09:00:00.123456Z"),
                c("Active", "5 Mbps", "2026-11-13T09:00:00Z"), c("Expired", "5 Mbps", "2026-11-01T09:00:00Z", enabled=False),
                c("Active", None, None)]

    def test_counts(self):
        c = home.customers(self.rows(), now=self.NOW)
        self.assertEqual((c["total"], c["active"], c["switched_off"]), (5, 4, 1))
        self.assertEqual([(p["label"], p["count"]) for p in c["status"]], [("Active", 4), ("Expired", 1)])
        self.assertEqual(c["expiring_soon"], 1)                        # the 16th; the 13th has already passed
        self.assertEqual(c["by_plan"][0], {"label": "10 Mbps", "count": 2})
        self.assertIn({"label": "No package", "count": 1}, c["by_plan"])

    def test_no_customer_detail_leaves_the_server(self):
        text = json.dumps(home.customers(self.rows(), now=self.NOW))
        for private in ("Jane", "0700000000", "jane"):
            self.assertNotIn(private, text)

    def test_small_packages_fold_into_other(self):
        rows = [{"account_status": "Active", "plan": f"Plan {i}", "expiry": None, "enabled": True} for i in range(9)]
        plans = home.customers(rows, now=self.NOW)["by_plan"]
        self.assertEqual(len(plans), home.PLANS_SHOWN + 1)
        self.assertEqual(plans[-1], {"label": "Other packages", "count": 3})


class Batteries(unittest.TestCase):
    ROWS = [{"status": "Deployed", "charge_status": "charged", "site_online": True},
            {"status": "Deployed", "charge_status": "low", "site_online": False},
            {"status": "At Base", "charge_status": "charging", "site_online": None},
            {"status": "In Transit", "charge_status": "unknown", "site_online": None}]

    def test_where_and_charge(self):
        b = home.batteries(self.ROWS, moving=2)
        self.assertEqual([(p["label"], p["count"]) for p in b["where"]], [("Deployed", 2), ("At Base", 1), ("In Transit", 1)])
        self.assertEqual([(p["label"], p["count"]) for p in b["charge"]],
                         [("Charged", 1), ("Charging", 1), ("Low", 1), ("Not recorded", 1)])
        self.assertEqual((b["total"], b["at_dark_sites"], b["moving"]), (4, 1, 2))

    def test_movements_hidden_from_someone_who_cannot_see_them(self):
        self.assertIsNone(home.batteries(self.ROWS)["moving"])


class Activity(unittest.TestCase):
    def test_reduced(self):
        overview = {"start": "2026-11-08", "end": "2026-11-14", "total": 5,
                    "over_time": [{"period": "2026-11-08", "count": 2}, {"period": "2026-11-09", "count": 3}],
                    "by_category": [{"category": "Support", "count": 1}, {"category": "Installs", "count": 4}, {"category": "None", "count": 0}],
                    "by_technician": [{"name": "A", "count": 5}, {"name": "B", "count": 0}],
                    "outcomes": {"still_open": 2}}
        a = home.activity(overview)
        self.assertEqual((a["total"], a["still_open"], a["technicians"]), (5, 2, 1))
        self.assertEqual(a["by_category"], [{"label": "Installs", "count": 4}, {"label": "Support", "count": 1}])
        self.assertEqual(a["per_day"][1], {"day": "2026-11-09", "count": 3})


class Stock(unittest.TestCase):
    def item(self, name, on_hand, level, category="Connectors"):
        return {"name": name, "on_hand_qty": on_hand, "reorder_level": level, "category_name": category}

    def test_low_is_at_or_under_the_reorder_level_emptiest_first(self):
        s = home.stock([self.item("Patch", 26, None), self.item("SC", 8, 20), self.item("Splitter", 0, 5), self.item("Cable", 50, 10),
                        self.item("Clips", 10, 10)])
        self.assertEqual([r["name"] for r in s["low"]], ["Splitter", "SC", "Clips"])
        self.assertEqual((s["items"], s["with_level"], s["low_count"], s["out"]), (5, 4, 3, 1))

    def test_no_levels_set_flags_nothing(self):
        s = home.stock([self.item("Patch", 0, None)])
        self.assertEqual((s["low_count"], s["with_level"]), (0, 0))


class Uplink(unittest.TestCase):
    def test_no_router(self):
        self.assertEqual(home.uplink(None, None), {"configured": False})

    def test_reduced(self):
        overview = {"router": {"name": "CCR2116"}, "status": {"level": "amber", "reasons": [{"text": "Router CPU at 91%"}]},
                    "total": {"download_mbps": 410.5, "upload_mbps": 60, "peak_today_download_mbps": 500}, "received_at": "x"}
        history = {"points": [{"t": "2026-11-14T10:00:00Z", "download_mbps_avg": 400, "upload_mbps_avg": None, "cpu_pct_avg": 9}]}
        u = home.uplink(overview, history)
        self.assertEqual((u["level"], u["reason"], u["download_mbps"]), ("amber", "Router CPU at 91%", 410.5))
        self.assertEqual(u["points"], [{"t": "2026-11-14T10:00:00Z", "download": 400, "upload": None}])


class Revenue(unittest.TestCase):
    def test_money_stays_as_the_report_formatted_it(self):
        trend = {"as_of": "x", "overview": {"total": "KSh 20,000", "average": "KSh 10,000", "average_value": 10000},
                 "trend": {"label": "Increasing"},
                 "points": [{"start": "2026-11-13", "label": "Fri 13/11", "name": "Fri 13 Nov", "value": 11000, "display": "KSh 11,000",
                             "partial": False, "vs_average_pct": 10, "band": {"low": 1}, "rolling_avg": 3.2},
                            {"start": "2026-11-14", "label": "Sat 14/11", "name": "Sat 14 Nov", "value": 1200, "display": "KSh 1,200",
                             "partial": True, "vs_average_pct": None}]}
        r = home.revenue(trend)
        self.assertEqual((r["today"], r["direction"], r["overview"]["average"]), ("KSh 1,200", "Increasing", "KSh 10,000"))
        self.assertNotIn("band", r["points"][0])


class WhoSeesWhat(unittest.TestCase):
    def test_admin_gets_every_section(self):
        self.assertEqual(api.sections(current_user=ADMIN)["sections"], list(api.SECTIONS))

    def test_no_permissions_no_sections(self):
        with perms():
            self.assertEqual(api.sections(current_user=TECH)["sections"], [])

    def test_each_section_follows_its_own_pages_permission(self):
        cases = {("sites", "view_status"): ["sites", "people"], ("sites", "view_revenue"): ["revenue"],
                 ("sites", "view_network"): ["uplink", "fibre"], ("sites", "send_broadcasts"): ["customers"],
                 ("batteries", "view"): ["batteries"], ("activity_log", "view_overview"): ["activity"]}
        for grant, expected in cases.items():
            with perms(grant):
                self.assertEqual(api.sections(current_user=TECH)["sections"], expected, grant)

    def test_stock_needs_inventory_and_one_of_its_pages(self):
        with perms(("inventory_items", "view_stock")):
            self.assertEqual(api.sections(current_user=TECH)["sections"], [])
        with perms(("inventory_items", "view"), ("inventory_items", "view_stock")):
            self.assertEqual(api.sections(current_user=TECH)["sections"], ["stock"])

    def test_revenue_is_refused_without_the_revenue_permission(self):
        with perms(("sites", "view_status")), mock.patch.object(api.revenue_report, "trend") as trend:
            with self.assertRaises(HTTPException) as refused:
                api.section("revenue", current_user=TECH)
            self.assertEqual(refused.exception.status_code, 403)
            trend.assert_not_called()

    def test_unknown_section(self):
        with self.assertRaises(HTTPException) as missing:
            api.section("payroll", current_user=ADMIN)
        self.assertEqual(missing.exception.status_code, 404)

    def test_movement_count_only_with_the_movements_permission(self):
        with perms(("batteries", "view")), mock.patch.object(api.batteries, "get_all_batteries", return_value=[]), \
                mock.patch.object(api.batteries, "get_active_movement_count", return_value=7) as count:
            self.assertIsNone(api.section("batteries", current_user=TECH)["moving"])
            count.assert_not_called()
        with perms(("batteries", "view"), ("movements", "view")), mock.patch.object(api.batteries, "get_all_batteries", return_value=[]), \
                mock.patch.object(api.batteries, "get_active_movement_count", return_value=7):
            self.assertEqual(api.section("batteries", current_user=TECH)["moving"], 7)


if __name__ == "__main__":
    unittest.main()
