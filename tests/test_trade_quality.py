import unittest
from types import SimpleNamespace

import pandas as pd

from strategy.market_state import MarketStateResult
from strategy.trade_quality import evaluate_trade_quality


def _settings():
    return SimpleNamespace(
        strategy={
            "trade_quality": {
                "weights": {
                    "trend": 30,
                    "momentum": 25,
                    "structure": 20,
                    "volume": 15,
                    "market_context": 10,
                }
            }
        }
    )


class TradeQualityTests(unittest.TestCase):
    def test_aligned_bull_trade_scores_high(self):
        row5 = pd.Series({
            "close": 105.0, "ema_5m": 102.0, "rsi_5m": 62.0, "roc_5m": 1.5,
            "rvol": 1.5, "vwap_weekly": 101.0, "st_direction": "BULL",
        })
        row15 = pd.Series({
            "close": 104.0, "ema_15m": 100.0, "rsi_15m": 58.0,
            "roc_15m": 1.0, "adx_15m": 28.0,
        })
        market = MarketStateResult("STRONG_BULL", 80.0, 5.0, ["PRICE_ABOVE_EMA"])

        result = evaluate_trade_quality(
            row5, row15, "BULL", "BREAKOUT_CONTINUATION", True, market, _settings(), 1.2
        )

        self.assertEqual(result.score, 100.0)
        self.assertIn("MARKET_STATE_ALIGNED", result.reasons)

    def test_counter_context_scores_lower(self):
        row5 = pd.Series({
            "close": 105.0, "ema_5m": 102.0, "rsi_5m": 62.0, "roc_5m": 1.5,
            "rvol": 1.5, "vwap_weekly": 101.0, "st_direction": "BULL",
        })
        row15 = pd.Series({
            "close": 104.0, "ema_15m": 100.0, "rsi_15m": 58.0,
            "roc_15m": 1.0, "adx_15m": 28.0,
        })
        market = MarketStateResult("STRONG_BEAR", 5.0, 80.0, ["PRICE_BELOW_EMA"])

        result = evaluate_trade_quality(
            row5, row15, "BULL", "BREAKOUT_CONTINUATION", True, market, _settings(), 1.2
        )

        self.assertLess(result.score, 100.0)
        self.assertIn("MARKET_STATE_AGAINST", result.reasons)


if __name__ == "__main__":
    unittest.main()
