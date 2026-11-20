"""The /network/health endpoints' doors: the shipper's token, the status key,
body limits, and "2xx only once stored". The database is faked: these test
the HTTP side; the parsing and rules have their own tests. Run:
  venv/bin/python -m unittest tests.test_network_health_ingest -v
"""
import json
import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from network_health import config, router, service

TOKEN = "shipper-secret-token"
GOOD = {"Authorization": f"Bearer {TOKEN}"}


def client():
    app = FastAPI()
    app.include_router(router.router)
    return TestClient(app)


@mock.patch.dict(os.environ, {"NETWORK_HEALTH_TOKEN": TOKEN, "NH_STATUS_KEY": "status-key"})
class Ingest(unittest.TestCase):
    def setUp(self):
        router._recent_posts.clear()
        patcher = mock.patch.object(service, "ingest", return_value={"stored": 0, "duplicates": 0, "ignored": 0,
                                                                     "skipped": 0})
        self.ingest = patcher.start()
        self.addCleanup(patcher.stop)

    def test_wrong_or_missing_token_is_401_and_nothing_stored(self):
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN + "x"}):
            response = client().post("/network/health/ingest", json={"records": []}, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(TOKEN, response.text)
        self.ingest.assert_not_called()

    def test_unset_token_refuses_everything(self):
        with mock.patch.dict(os.environ, {"NETWORK_HEALTH_TOKEN": ""}):
            response = client().post("/network/health/ingest", json={"records": []}, headers={"Authorization": "Bearer "})
        self.assertEqual(response.status_code, 401)

    def test_empty_heartbeat_is_accepted(self):
        response = client().post("/network/health/ingest", headers=GOOD,
                                 json={"source": "fra-1", "sent_at": "2026-11-20T15:40:01+00:00", "records": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ingest.call_args.args[0], "fra-1")

    def test_unknown_envelope_fields_are_ignored(self):
        response = client().post("/network/health/ingest", headers=GOOD, json={"records": [], "version": 9})
        self.assertEqual(response.status_code, 200)

    def test_not_json_is_refused(self):
        response = client().post("/network/health/ingest", headers=GOOD, content=b"{not json")
        self.assertEqual(response.status_code, 422)
        self.ingest.assert_not_called()

    def test_too_large_is_refused(self):
        body = json.dumps({"records": [], "pad": "x" * (config.MAX_BODY_BYTES + 1)})
        response = client().post("/network/health/ingest", headers={**GOOD, "Content-Type": "application/json"},
                                 content=body)
        self.assertEqual(response.status_code, 413)

    def test_storage_failure_is_not_2xx_so_the_shipper_resends(self):
        self.ingest.side_effect = RuntimeError("database down")
        response = client().post("/network/health/ingest", headers=GOOD, json={"records": []})
        self.assertEqual(response.status_code, 503)

    def test_rate_limit(self):
        with mock.patch.object(config, "INGEST_LIMIT_PER_MINUTE", 2):
            codes = [client().post("/network/health/ingest", headers=GOOD, json={"records": []}).status_code
                     for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


@mock.patch.dict(os.environ, {"NETWORK_HEALTH_TOKEN": TOKEN, "NH_STATUS_KEY": "status-key"})
class Status(unittest.TestCase):
    def test_key_required(self):
        with mock.patch.object(service, "status") as status:
            self.assertEqual(client().get("/network/health/status?key=wrong").status_code, 401)
            self.assertEqual(client().get("/network/health/status").status_code, 401)
            status.assert_not_called()

    def test_ingest_token_is_not_the_status_key(self):
        with mock.patch.object(service, "status", return_value=(200, {"status": "ok"})):
            self.assertEqual(client().get(f"/network/health/status?key={TOKEN}").status_code, 401)

    def test_200_or_503(self):
        with mock.patch.object(service, "status", return_value=(200, {"status": "ok"})):
            self.assertEqual(client().get("/network/health/status?key=status-key").status_code, 200)
        with mock.patch.object(service, "status", return_value=(503, {"status": "problem"})):
            self.assertEqual(client().get("/network/health/status?key=status-key").status_code, 503)

    def test_page_reads_need_a_login(self):
        self.assertIn(client().get("/network/health/overview").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
