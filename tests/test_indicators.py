import unittest
from pathlib import Path

try:
    import talib  # noqa: F401
    TALIB_AVAILABLE = True
except Exception:
    TALIB_AVAILABLE = False

import numpy as np
import pandas as pd


@unittest.skipUnless(TALIB_AVAILABLE, "TA-Lib is not installed in this test runtime")
class IndicatorTests(unittest.TestCase):
    def test_indicator_columns_created(self):
        from strategy.indicators import add_5m_indicators, normalize_ohlcv
        from strategy.settings import load_strategy_settings

        settings = load_strategy_settings(Path(__file__).resolve().parents[1] / "strategy.yaml")
        n = 80
        dt = pd.date_range("2026-08-24 09:15", periods=n, freq="5min")
        base = np.linspace(100, 110, n)
        df = pd.DataFrame({
            "datetime": dt,
            "open": base - 0.1,
            "high": base + 0.3,
            "low": base - 0.3,
            "close": base,
            "volume": np.linspace(1000, 1800, n),
        })
        out = add_5m_indicators(normalize_ohlcv(df, "TEST", "5m"), settings)
        for col in ("ema_5m", "rsi_5m", "roc_5m", "rvol", "vwap_session", "vwap_weekly", "supertrend", "st_direction"):
            self.assertIn(col, out.columns)


if __name__ == "__main__":
    unittest.main()
