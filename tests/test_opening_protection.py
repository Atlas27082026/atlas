import unittest
from types import SimpleNamespace

import pandas as pd

from strategy.market_state import MarketStateResult
from strategy.opening_protection import evaluate_opening_protection


def _settings():
    return SimpleNamespace(
        strategy={
            "opening_protection": {
                "enabled": True,
                "observe_mode": True,
                "start": "09:15",
                "end": "09:30",
                "min_quality_score": 75.0,
                "min_rvol": 1.3,
                "min_adx_15m": 25.0,
                "exceptional_quality_score": 90.0,
                "exceptional_adx_15m": 30.0,
            }
        }
    )


class OpeningProtectionTests(unittest.TestCase):
    def test_weak_opening_confirmation_is_observed(self):
        row5 = pd.Series({"rvol": 1.0})
        row15 = pd.Series({"adx_15m": 18.0})
        market = MarketStateResult("SIDEWAYS", 30.0, 30.0, [])

        result = evaluate_opening_protection("09:20", "BULL", row5, row15, market, 60.0, _settings())

        self.assertTrue(result.in_opening_window)
        self.assertTrue(result.observe_mode)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "OPENING_WEAK_CONFIRMATION")

    def test_exceptional_opening_trend_passes(self):
        row5 = pd.Series({"rvol": 1.0})
        row15 = pd.Series({"adx_15m": 35.0})
        market = MarketStateResult("STRONG_BULL", 90.0, 0.0, [])

        result = evaluate_opening_protection("09:20", "BULL", row5, row15, market, 92.0, _settings())

        self.assertTrue(result.passed)
        self.assertTrue(result.exceptional)
        self.assertIn("EXCEPTIONAL_OPENING_TREND", result.reasons)

    def test_outside_opening_window_passes(self):
        row5 = pd.Series({"rvol": 0.5})
        row15 = pd.Series({"adx_15m": 10.0})
        market = MarketStateResult("SIDEWAYS", 0.0, 0.0, [])

        result = evaluate_opening_protection("10:00", "BULL", row5, row15, market, 10.0, _settings())

        self.assertFalse(result.in_opening_window)
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "OUTSIDE_OPENING_WINDOW")


if __name__ == "__main__":
    unittest.main()
