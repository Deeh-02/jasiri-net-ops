"""The OLT page's PON tab and Network Health's site visits: both come from what
SmartOLT reports (status and signal grade), not from OPS's own light limits.
Run: venv/bin/python -m unittest tests.test_olt_pons -v
"""
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import olt_pons, smartolt
from tests.test_smartolt import NOW, row


def onu(id_, board=1, port=1, status="Online", grade="Very good", dbm=-20.0, disabled=False, name=None):
    return {"id": id_, "sn": id_, "name": name or f"name {id_}", "status": status, "board": board, "port": port,
            "onu": 1, "signal_1310_dbm": dbm, "signal_quality": grade, "last_status_change": "2026-11-23T10:00:00+03:00",
            "administrative_status": "Disabled" if disabled else "Enabled"}


class PonRows(unittest.TestCase):
    def test_one_row_per_port_counted_from_the_olts_statuses_and_grades(self):
        rows = olt_pons.pon_rows([
            onu("a"), onu("b", status="LOS", grade=None, dbm=None), onu("c", status="Power fail", grade=None),
            onu("d", status="Offline", grade=None), onu("e", grade="Warning", dbm=-28.0),
            onu("f", board=1, port=2), onu("g", board=0, port=7, grade="Critical", dbm=-33.0)])
        self.assertEqual([r["label"] for r in rows], ["0/0/7", "0/1/1", "0/1/2"])      # numeric order
        first = rows[1]
        self.assertEqual((first["key"], first["total"], first["online"], first["los"], first["power_fail"],
                          first["offline"]), ("1/1", 5, 2, 1, 1, 1))
        self.assertEqual((first["very_good"], first["warning"], first["critical"], first["no_reading"]), (1, 1, 0, 3))

    def test_the_level_is_the_olts_verdict(self):
        level = lambda *onus: olt_pons.pon_rows(list(onus))[0]["level"]   # noqa: E731
        self.assertEqual(level(onu("a"), onu("b")), "good")
        self.assertEqual(level(onu("a"), onu("b", grade="Warning")), "warn")
        self.assertEqual(level(onu("a"), onu("b", grade="Critical")), "bad")
        self.assertEqual(level(onu("a"), onu("b", status="LOS", grade=None)), "bad")
        self.assertEqual(level(onu("a", status="Power fail", grade=None)), "unknown")
        # A very different dBm changes nothing: only SmartOLT's grade counts.
        self.assertEqual(level(onu("a", dbm=-33.0, grade="Very good")), "good")
        self.assertEqual(level(onu("a", dbm=-12.0, grade="Critical")), "bad")

    def test_onus_without_a_port_are_left_out(self):
        self.assertEqual(olt_pons.pon_rows([onu("a", board=None, port=None)]), [])


class SiteVisits(unittest.TestCase):
    def kinds(self, *onus):
        return [(v["kind"], v["pon_label"]) for v in olt_pons.site_visits(list(onus))]

    def test_nothing_wrong_is_no_visit(self):
        self.assertEqual(self.kinds(onu("a"), onu("b"), onu("c", status="Power fail", grade=None),
                                    onu("d", status="Offline", grade=None)), [])

    def test_a_lone_los_critical_and_warning_each_get_a_visit(self):
        visits = olt_pons.site_visits([onu("a", status="LOS", grade=None, dbm=None), onu("b", port=2, grade="Critical", dbm=-33.0),
                                       onu("c", port=3, grade="Warning", dbm=-29.0), onu("d", port=3)])
        self.assertEqual([(v["kind"], v["urgency"]) for v in visits],
                         [("los", "bad"), ("critical", "bad"), ("warning", "warn")])
        self.assertEqual(visits[1]["signal_dbm"], -33.0)
        self.assertEqual(visits[1]["onus"], ["b"])
        self.assertIn("name b", visits[1]["title"])

    def test_several_los_on_one_pon_is_one_visit_for_the_pon(self):
        visits = olt_pons.site_visits([onu("a", status="LOS", grade=None), onu("b", status="LOS", grade=None),
                                       onu("c"), onu("d", port=2, status="LOS", grade=None)])
        self.assertEqual([(v["kind"], v["pon_label"]) for v in visits], [("pon_los", "0/1/1"), ("los", "0/1/2")])
        self.assertEqual(visits[0]["onus"], ["a", "b"])
        self.assertIn("2 of 3 ONUs", visits[0]["title"])

    def test_half_the_graded_onus_weak_is_a_pon_visit_and_hides_their_own(self):
        visits = olt_pons.site_visits([onu("a", grade="Warning"), onu("b", grade="Critical"), onu("c"), onu("d")])
        self.assertEqual([(v["kind"], v["urgency"]) for v in visits], [("pon_weak", "bad")])
        self.assertEqual(visits[0]["onus"], ["a", "b"])
        # Fewer than half: each weak ONU is its own visit.
        visits = olt_pons.site_visits([onu("a", grade="Warning"), onu("b", grade="Warning"), onu("c"), onu("d"), onu("e")])
        self.assertEqual([v["kind"] for v in visits], ["warning", "warning"])

    def test_warning_only_pon_is_not_urgent(self):
        visits = olt_pons.site_visits([onu("a", grade="Warning"), onu("b", grade="Warning")])
        self.assertEqual((visits[0]["kind"], visits[0]["urgency"]), ("pon_weak", "warn"))

    def test_power_fail_offline_and_disabled_are_not_visits(self):
        self.assertEqual(self.kinds(onu("a", status="Power fail", grade=None), onu("b", status="Offline", grade=None),
                                    onu("c", status="LOS", grade=None, disabled=True),
                                    onu("d", grade="Critical", disabled=True)), [])

    def test_a_weak_grade_on_an_offline_onu_is_stale_and_ignored(self):
        self.assertEqual(self.kinds(onu("a", status="Offline", grade="Critical")), [])

    def test_most_urgent_first(self):
        kinds = [v["kind"] for v in olt_pons.site_visits([
            onu("w", port=4, grade="Warning"), onu("c", port=3, grade="Critical"), onu("l", port=2, status="LOS", grade=None),
            onu("x", port=1, grade="Warning"), onu("y", port=1, grade="Warning"),
            onu("p", port=5, status="LOS", grade=None), onu("q", port=5, status="LOS", grade=None)])]
        self.assertEqual(kinds, ["pon_los", "pon_weak", "los", "critical", "warning"])


class Wiring(unittest.TestCase):
    def test_the_configured_list_carries_the_pon_rows(self):
        rows = [row("a", quality="Very good"), row("b", "LOS")]
        listing = smartolt.build_list(rows, NOW)
        self.assertEqual([p["label"] for p in listing["pons"]], ["0/0/1"])
        self.assertEqual((listing["pons"][0]["total"], listing["pons"][0]["los"], listing["pons"][0]["level"]), (2, 1, "bad"))

    def client(self, allowed):
        from routers import network, olt
        from routers.auth import get_current_user
        app = FastAPI()
        app.include_router(olt.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "x"}
        patcher = mock.patch.object(network, "user_has_permission", return_value=allowed)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app)

    def test_site_visits_endpoint(self):
        listing = smartolt.build_list([row("a", "LOS"), row("b", quality="Critical", s1310="-33.9")], NOW)
        client = self.client(True)
        with mock.patch.object(smartolt, "poll_if_due"), mock.patch.object(smartolt, "configured_list", return_value=listing):
            response = client.get("/olt/visits")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([v["kind"] for v in body["visits"]], ["los", "critical"])
        self.assertEqual((body["total"], body["status_stale"]), (2, False))

    def test_site_visits_need_the_network_permission(self):
        with mock.patch.object(smartolt, "configured_list") as read:
            self.assertEqual(self.client(False).get("/olt/visits").status_code, 403)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
