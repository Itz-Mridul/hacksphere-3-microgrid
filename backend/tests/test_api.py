"""API smoke tests. Run after: python backend/main.py"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "core"))
sys.path.insert(0, BACKEND)
sys.path.insert(0, ROOT)

from main import app  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()

    def test_health(self):
        r = self.c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("quickml_configured", body)

    def test_forecast_24(self):
        with open(os.path.join(ROOT, "frontend", "presets.json")) as f:
            presets = json.load(f)
        rows = presets["presets"][0]["rows"]
        r = self.c.post("/api/forecast", json={"rows": rows})
        self.assertEqual(r.status_code, 200, r.data)
        body = r.get_json()
        self.assertEqual(len(body["rows"]), 24)
        self.assertIn(body["model"]["source"], ("fallback", "quickml", "cache", "mixed"))
        first = body["rows"][0]
        self.assertIn("mode", first)
        self.assertIn("soc_pct", first)

    def test_predict_proxy(self):
        with open(os.path.join(ROOT, "frontend", "presets.json")) as f:
            rows = json.load(f)["presets"][0]["rows"][:3]
        r = self.c.post("/api/predict", json={"data": rows})
        self.assertEqual(r.status_code, 200, r.data)
        body = r.get_json()
        self.assertEqual(len(body["predictions"]), 3)
        self.assertTrue(all(isinstance(x, float) for x in body["predictions"]))


if __name__ == "__main__":
    unittest.main()
