import unittest
import pandas as pd

from strategy.model_trend_continuation import evaluate_trend_continuation
from strategy.settings import StrategySettings


class TrendContinuationTests(unittest.TestCase):
    def settings(self):
        return StrategySettings(raw={"strategy": {"trend_continuation": {
            "enabled": True, "lookback_bars": 3, "min_adx_15m": 25,
            "min_body_atr": 0.20, "max_ema_extension_atr": 1.75,
            "min_close_location": 0.60, "rsi_bull_min": 55, "rsi_bull_max": 74,
            "rsi_bear_min": 26, "rsi_bear_max": 45,
        }}})

    def test_bear_trend_continuation_passes(self):
        df = pd.DataFrame([
            {"open": 101, "high": 101.2, "low": 100.3, "close": 100.6, "ema_5m": 101.0, "atr": 0.8, "rsi_5m": 40, "roc_5m": -0.3},
            {"open": 100.6, "high": 100.8, "low": 99.9, "close": 100.1, "ema_5m": 100.8, "atr": 0.8, "rsi_5m": 38, "roc_5m": -0.4},
            {"open": 100.1, "high": 100.2, "low": 99.4, "close": 99.6, "ema_5m": 100.5, "atr": 0.8, "rsi_5m": 36, "roc_5m": -0.5},
            {"open": 99.7, "high": 99.8, "low": 98.9, "close": 99.0, "ema_5m": 100.2, "atr": 0.8, "rsi_5m": 33, "roc_5m": -0.7},
        ])
        row15 = pd.Series({"adx_15m": 30})
        d = evaluate_trend_continuation(df, 3, row15, "BEAR", self.settings())
        self.assertTrue(d.passed)

    def test_rejects_overextended_bear(self):
        df = pd.DataFrame([
            {"open": 101, "high": 101.2, "low": 100.3, "close": 100.6, "ema_5m": 101.0, "atr": 0.5, "rsi_5m": 40, "roc_5m": -0.3},
            {"open": 100.6, "high": 100.8, "low": 99.9, "close": 100.1, "ema_5m": 100.8, "atr": 0.5, "rsi_5m": 38, "roc_5m": -0.4},
            {"open": 100.1, "high": 100.2, "low": 99.4, "close": 99.6, "ema_5m": 100.5, "atr": 0.5, "rsi_5m": 36, "roc_5m": -0.5},
            {"open": 99.0, "high": 99.1, "low": 96.8, "close": 97.0, "ema_5m": 100.2, "atr": 0.5, "rsi_5m": 24, "roc_5m": -1.5},
        ])
        row15 = pd.Series({"adx_15m": 35})
        d = evaluate_trend_continuation(df, 3, row15, "BEAR", self.settings())
        self.assertFalse(d.passed)
        self.assertFalse(d.details["extension"])
        self.assertFalse(d.details["rsi_band"])


if __name__ == "__main__":
    unittest.main()
