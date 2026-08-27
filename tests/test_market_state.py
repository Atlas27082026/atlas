import unittest
from types import SimpleNamespace

import pandas as pd

from strategy.market_state import evaluate_market_state


def _settings():
    return SimpleNamespace(
        strategy={
            "market_state": {
                "ema_slope_lookback": 1,
                "structure_lookback": 2,
                "strong_confidence": 70.0,
                "directional_confidence": 55.0,
                "sideways_confidence_diff": 12.0,
                "strong_adx": 25.0,
                "weights": {
                    "ema_position": 15,
                    "ema_slope": 15,
                    "momentum": 20,
                    "vwap": 15,
                    "supertrend": 15,
                    "adx": 10,
                    "structure": 10,
                },
            }
        }
    )


class MarketStateTests(unittest.TestCase):
    def test_strong_bull_state(self):
        df5 = pd.DataFrame([
            {"open": 99, "high": 101, "low": 98, "close": 100, "ema_5m": 99, "rsi_5m": 56, "roc_5m": 1, "vwap_session": 99, "vwap_weekly": 98, "st_direction": "BULL"},
            {"open": 101, "high": 103, "low": 100, "close": 102, "ema_5m": 100, "rsi_5m": 58, "roc_5m": 1, "vwap_session": 100, "vwap_weekly": 99, "st_direction": "BULL"},
            {"open": 103, "high": 106, "low": 102, "close": 105, "ema_5m": 102, "rsi_5m": 62, "roc_5m": 2, "vwap_session": 101, "vwap_weekly": 100, "st_direction": "BULL"},
        ])
        df15 = pd.DataFrame([
            {"close": 100, "ema_15m": 99, "rsi_15m": 56, "roc_15m": 1, "adx_15m": 30},
            {"close": 104, "ema_15m": 101, "rsi_15m": 62, "roc_15m": 2, "adx_15m": 32},
        ])

        result = evaluate_market_state(df5, 2, df15, 1, _settings())

        self.assertEqual(result.state, "STRONG_BULL")
        self.assertGreater(result.bull_confidence, result.bear_confidence)
        self.assertIn("BULL_MOMENTUM", result.reasons)

    def test_strong_bear_state(self):
        df5 = pd.DataFrame([
            {"open": 101, "high": 102, "low": 99, "close": 100, "ema_5m": 101, "rsi_5m": 44, "roc_5m": -1, "vwap_session": 101, "vwap_weekly": 102, "st_direction": "BEAR"},
            {"open": 99, "high": 100, "low": 96, "close": 97, "ema_5m": 99, "rsi_5m": 40, "roc_5m": -2, "vwap_session": 100, "vwap_weekly": 101, "st_direction": "BEAR"},
            {"open": 96, "high": 97, "low": 93, "close": 94, "ema_5m": 97, "rsi_5m": 35, "roc_5m": -3, "vwap_session": 99, "vwap_weekly": 100, "st_direction": "BEAR"},
        ])
        df15 = pd.DataFrame([
            {"close": 100, "ema_15m": 101, "rsi_15m": 44, "roc_15m": -1, "adx_15m": 30},
            {"close": 94, "ema_15m": 99, "rsi_15m": 38, "roc_15m": -2, "adx_15m": 32},
        ])

        result = evaluate_market_state(df5, 2, df15, 1, _settings())

        self.assertEqual(result.state, "STRONG_BEAR")
        self.assertGreater(result.bear_confidence, result.bull_confidence)
        self.assertIn("BEAR_MOMENTUM", result.reasons)

    def test_sideways_when_confidence_is_mixed(self):
        df5 = pd.DataFrame([
            {"open": 100, "high": 101, "low": 99, "close": 100, "ema_5m": 100, "rsi_5m": 50, "roc_5m": 0, "vwap_session": 100, "vwap_weekly": 100, "st_direction": ""},
            {"open": 100, "high": 101, "low": 99, "close": 100, "ema_5m": 100, "rsi_5m": 50, "roc_5m": 0, "vwap_session": 100, "vwap_weekly": 100, "st_direction": ""},
            {"open": 100, "high": 101, "low": 99, "close": 100, "ema_5m": 100, "rsi_5m": 50, "roc_5m": 0, "vwap_session": 100, "vwap_weekly": 100, "st_direction": ""},
        ])
        df15 = pd.DataFrame([
            {"close": 100, "ema_15m": 100, "rsi_15m": 50, "roc_15m": 0, "adx_15m": 12},
            {"close": 100, "ema_15m": 100, "rsi_15m": 50, "roc_15m": 0, "adx_15m": 12},
        ])

        result = evaluate_market_state(df5, 2, df15, 1, _settings())

        self.assertEqual(result.state, "SIDEWAYS")
        self.assertEqual(result.bull_confidence, 0.0)
        self.assertEqual(result.bear_confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
