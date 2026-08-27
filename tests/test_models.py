import unittest
from pathlib import Path

import pandas as pd

from strategy.model_breakout import evaluate_breakout
from strategy.model_vwap import evaluate_vwap_pullback
from strategy.settings import load_strategy_settings


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_strategy_settings(Path(__file__).resolve().parents[1] / "strategy.yaml")

    def test_vwap_bull_rejection(self):
        row = pd.Series({
            "open": 100.0, "high": 102.0, "low": 99.9, "close": 101.7,
            "vwap_session": 100.0, "vwap_weekly": 99.0,
        })
        self.assertTrue(evaluate_vwap_pullback(row, "BULL", self.settings).passed)

    def test_breakout_bull(self):
        df = pd.DataFrame([
            {"high": 100.1, "low": 99.7, "close": 100.0, "vwap_weekly": 99.0},
            {"high": 100.2, "low": 99.8, "close": 100.1, "vwap_weekly": 99.0},
            {"high": 100.15, "low": 99.75, "close": 100.0, "vwap_weekly": 99.0},
            {"high": 100.25, "low": 99.85, "close": 100.1, "vwap_weekly": 99.0},
            {"high": 101.0, "low": 100.1, "close": 100.8, "vwap_weekly": 99.0},
        ])
        self.assertTrue(evaluate_breakout(df, 4, "BULL", self.settings).passed)


if __name__ == "__main__":
    unittest.main()
