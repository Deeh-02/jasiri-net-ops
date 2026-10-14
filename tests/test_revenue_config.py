"""validate() and defaults for db/revenue_config.py (no database needed).
Run from the repo root:  venv/bin/python -m unittest tests.test_revenue_config
"""
import unittest
from decimal import Decimal

from db.revenue_config import DEFAULTS, validate


class Defaults(unittest.TestCase):
    def test_defaults_match_the_spec(self):
        self.assertEqual(DEFAULTS["flat_band_pct"], Decimal(2))
        self.assertEqual(DEFAULTS["baseline_weeks"], Decimal(6))
        self.assertEqual(DEFAULTS["active_site_min_daily"], Decimal(300))
        self.assertEqual(DEFAULTS["pct_rank_min_revenue"], Decimal(1000))
        self.assertEqual((DEFAULTS["red_pct"], DEFAULTS["red_min_abs"]), (Decimal(20), Decimal(500)))
        self.assertEqual((DEFAULTS["amber_pct"], DEFAULTS["amber_min_abs"]), (Decimal(10), Decimal(250)))
        self.assertEqual((DEFAULTS["green_pct"], DEFAULTS["green_min_abs"]), (Decimal(15), Decimal(300)))
        self.assertEqual(
            (DEFAULTS["red_consecutive"], DEFAULTS["amber_consecutive"], DEFAULTS["green_consecutive"]),
            (Decimal(3), Decimal(2), Decimal(3)),
        )
        self.assertEqual(DEFAULTS["contribution_cutoff_pct"], Decimal(70))
        self.assertEqual(DEFAULTS["anomaly_k"], Decimal(3))


class Validate(unittest.TestCase):
    def test_accepts_good_values(self):
        self.assertEqual(validate("red_pct", "25"), (Decimal(25), None))
        self.assertEqual(validate("red_min_abs", 250.5), (Decimal("250.5"), None))
        self.assertEqual(validate("red_consecutive", 4), (Decimal(4), None))

    def test_rejects_bad_values(self):
        for key, value in [("red_pct", 101), ("red_pct", -1), ("red_min_abs", -5), ("baseline_weeks", 0),
                           ("baseline_weeks", 2.5), ("flat_band_pct", "abc"), ("flat_band_pct", "NaN"),
                           ("nope", 1)]:
            number, error = validate(key, value)
            self.assertIsNone(number, (key, value))
            self.assertTrue(error, (key, value))


if __name__ == "__main__":
    unittest.main()
