import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.paper_positions import PaperPositionStore
from core.strategy_c import (
    MarketRegime,
    classify_gap,
    classify_opening_behavior,
    classify_relative_strength,
    context_15m_source,
    evaluate_regime,
    gap_pct,
    previous_trading_close,
    regime_permission,
)
from tests.test_strategy_b import _candidate, _mtf_frames, _settings


def _daily(prev_close=100.0, today_open=101.0):
    return pd.DataFrame([
        {"datetime_parsed": pd.Timestamp("2026-08-27 15:30"), "open": 99.0, "high": 101.0, "low": 98.0, "close": prev_close, "volume": 1000},
        {"datetime_parsed": pd.Timestamp("2026-08-31 09:15"), "open": today_open, "high": today_open + 1.0, "low": today_open - 1.0, "close": today_open, "volume": 1000},
    ])


def _one_min_gap_up_pullback():
    return pd.DataFrame([
        {"datetime_parsed": pd.Timestamp("2026-08-31 09:15"), "open": 102.0, "high": 102.4, "low": 101.6, "close": 101.7, "volume": 1000},
        {"datetime_parsed": pd.Timestamp("2026-08-31 09:16"), "open": 101.7, "high": 102.6, "low": 101.5, "close": 102.5, "volume": 1100},
    ])


def _five_min_today(vwap=101.8):
    return pd.DataFrame([
        {"datetime_parsed": pd.Timestamp("2026-08-31 09:15"), "open": 102.0, "high": 102.6, "low": 101.5, "close": 102.5, "volume": 1000, "vwap_session": vwap},
    ])


class StrategyCTests(unittest.TestCase):
    def test_nifty_gap_up_calculation(self):
        self.assertAlmostEqual(gap_pct(101.0, 100.0), 1.0)

    def test_nifty_gap_down_calculation(self):
        self.assertAlmostEqual(gap_pct(98.5, 100.0), -1.5)

    def test_stock_and_relative_gap_calculation(self):
        market_gap = gap_pct(101.0, 100.0)
        stock_gap = gap_pct(103.0, 100.0)

        self.assertAlmostEqual(stock_gap, 3.0)
        self.assertAlmostEqual(stock_gap - market_gap, 2.0)

    def test_relative_strength_classification(self):
        settings = _settings()

        self.assertEqual(classify_relative_strength(1.2, settings), "STRONG_RELATIVE_STRENGTH")
        self.assertEqual(classify_relative_strength(0.5, settings), "RELATIVE_STRENGTH")
        self.assertEqual(classify_relative_strength(0.1, settings), "NEUTRAL")
        self.assertEqual(classify_relative_strength(-0.5, settings), "RELATIVE_WEAKNESS")
        self.assertEqual(classify_relative_strength(-1.2, settings), "STRONG_RELATIVE_WEAKNESS")

    def test_gap_classification_sizes(self):
        settings = _settings()

        self.assertEqual(classify_gap(0.1, settings)[0], "NORMAL_OPEN")
        self.assertEqual(classify_gap(0.4, settings)[0], "SMALL_GAP_UP")
        self.assertEqual(classify_gap(1.0, settings)[0], "MEDIUM_GAP_UP")
        self.assertEqual(classify_gap(2.0, settings)[0], "LARGE_GAP_UP")
        self.assertEqual(classify_gap(-1.0, settings)[0], "MEDIUM_GAP_DOWN")

    def test_previous_session_close_selection(self):
        daily = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-26 15:30"), "close": 95.0},
            {"datetime_parsed": pd.Timestamp("2026-08-28 15:30"), "close": 100.0},
            {"datetime_parsed": pd.Timestamp("2026-08-31 09:15"), "close": 101.0},
        ])

        self.assertEqual(previous_trading_close(daily, "2026-08-31"), 100.0)

    def test_previous_and_current_session_15m_flagging(self):
        previous = pd.DataFrame([{"datetime_parsed": pd.Timestamp("2026-08-28 15:15")}])
        current = pd.DataFrame([{"datetime_parsed": pd.Timestamp("2026-08-31 09:30")}])

        self.assertEqual(context_15m_source(previous, "2026-08-31"), "PREVIOUS_SESSION_15M")
        self.assertEqual(context_15m_source(current, "2026-08-31"), "CURRENT_SESSION_15M")

    def test_incomplete_current_15m_does_not_take_over(self):
        current_forming = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 15:15")},
            {"datetime_parsed": pd.Timestamp.now()},
        ])

        self.assertEqual(context_15m_source(current_forming, "2026-08-31"), "PREVIOUS_SESSION_15M")

    def test_opening_pullback_behavior(self):
        behavior = classify_opening_behavior(
            stock_gap_pct_value=2.0,
            today_open=102.0,
            previous_close=100.0,
            df_1m=_one_min_gap_up_pullback(),
            df_5m=_five_min_today(),
            trading_date="2026-08-31",
            settings=_settings(),
        )

        self.assertEqual(behavior, "OPENING_PULLBACK")

    def test_gap_up_long_chase_block(self):
        regime = MarketRegime("2026-08-31", 1.0, "MEDIUM_GAP_UP", "GAP_UP", 2.0, "LARGE_GAP_UP", 1.0, "RELATIVE_STRENGTH", "OPENING_CONTINUATION", 80, [], "MEDIUM_GAP_UP", "PREVIOUS_SESSION_15M")

        decision = regime_permission(regime, "BULL", _settings(), "09:20")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "GAP_UP_LONG_CHASE")

    def test_gap_up_pullback_reclaim_permission(self):
        regime = MarketRegime("2026-08-31", 1.0, "MEDIUM_GAP_UP", "GAP_UP", 2.0, "LARGE_GAP_UP", 1.0, "RELATIVE_STRENGTH", "OPENING_PULLBACK", 80, [], "BULLISH_PULLBACK_OPPORTUNITY", "PREVIOUS_SESSION_15M")

        decision = regime_permission(regime, "BULL", _settings(), "09:20")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.regime_adapted)

    def test_gap_down_short_chase_block_and_rejection_permission(self):
        chase = MarketRegime("2026-08-31", -1.0, "MEDIUM_GAP_DOWN", "GAP_DOWN", -2.0, "LARGE_GAP_DOWN", -1.0, "RELATIVE_WEAKNESS", "OPENING_CONTINUATION", 80, [], "MEDIUM_GAP_DOWN", "PREVIOUS_SESSION_15M")
        pullback = MarketRegime("2026-08-31", -1.0, "MEDIUM_GAP_DOWN", "GAP_DOWN", -2.0, "LARGE_GAP_DOWN", -1.0, "RELATIVE_WEAKNESS", "OPENING_PULLBACK", 80, [], "BEARISH_REJECTION_OPPORTUNITY", "PREVIOUS_SESSION_15M")

        self.assertFalse(regime_permission(chase, "BEAR", _settings(), "09:20").allowed)
        self.assertTrue(regime_permission(pullback, "BEAR", _settings(), "09:20").allowed)

    def test_market_gap_up_stock_relative_weakness(self):
        regime = evaluate_regime(
            symbol="TEST",
            market_daily=_daily(100.0, 101.0),
            stock_daily=_daily(100.0, 99.0),
            stock_1m=_one_min_gap_up_pullback(),
            stock_5m=_five_min_today(),
            stock_15m=_mtf_frames("BEAR")[1],
            trading_date="2026-08-31",
            settings=_settings(),
        )

        self.assertEqual(regime.market_gap_class, "MEDIUM_GAP_UP")
        self.assertEqual(regime.relative_strength_class, "STRONG_RELATIVE_WEAKNESS")

    def test_market_gap_down_stock_relative_strength(self):
        regime = evaluate_regime(
            symbol="TEST",
            market_daily=_daily(100.0, 99.0),
            stock_daily=_daily(100.0, 101.0),
            stock_1m=_one_min_gap_up_pullback(),
            stock_5m=_five_min_today(),
            stock_15m=_mtf_frames("BULL")[1],
            trading_date="2026-08-31",
            settings=_settings(),
        )

        self.assertEqual(regime.market_gap_class, "MEDIUM_GAP_DOWN")
        self.assertEqual(regime.relative_strength_class, "STRONG_RELATIVE_STRENGTH")

    def test_strategy_c_independent_same_underlying_position(self):
        with tempfile.TemporaryDirectory() as td:
            a_store = PaperPositionStore(Path(td) / "a.json")
            c_store = PaperPositionStore(Path(td) / "c.json")

            a_store.add_from_candidate("TEST", "BULL", "A_MODEL", _candidate())
            c_store.add_from_candidate("TEST", "BULL", "REGIME_MTF", _candidate())

            self.assertTrue(a_store.has_open_underlying("TEST"))
            self.assertTrue(c_store.has_open_underlying("TEST"))

    def test_timezone_consistency(self):
        ts = pd.Timestamp("2026-08-31T09:30:00+05:30")

        self.assertEqual(str(ts.tzinfo), "UTC+05:30")


if __name__ == "__main__":
    unittest.main()
