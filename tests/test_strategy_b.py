import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from core.paper_positions import PaperPosition, PaperPositionStore
from core.strategy_b import (
    PendingSetupStore,
    comparison_report,
    compute_strategy_stats,
    evaluate_15m_context,
    evaluate_confirmation,
    evaluate_mtf_setup,
    evaluate_mtf_trigger,
    expire_pending_setups,
    format_confirmation_check,
    format_confirmation_passed,
    format_mtf_check,
    format_mtf_trigger,
    format_pending_expired,
    mark_setup_cancelled,
    mark_setup_executed,
    strategy_b_summary_report,
)
from execution.models import ContractCandidate, LiquidityAssessment, QuoteSnapshot


MARKET_TZ = ZoneInfo("Asia/Kolkata")


def _settings():
    return SimpleNamespace(strategy={
        "market_state": {
            "ema_slope_lookback": 3,
            "structure_lookback": 4,
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
        },
        "strategy_b_mtf": {
            "setup_min_score": 70.0,
            "setup_rsi_bull_min_5m": 52.0,
            "setup_rsi_bear_max_5m": 48.0,
            "setup_rvol_min": 0.90,
            "setup_require_session_vwap": True,
            "setup_require_supertrend": False,
        },
    })


def _mtf_frames(direction="BULL", incomplete_tail=False):
    rows5 = []
    rows15 = []
    base = pd.Timestamp("2026-08-28 10:00")
    for i in range(6):
        if direction == "BULL":
            close5 = 100.0 + i
            close15 = 200.0 + i
            ema5 = close5 - 1.0
            ema15 = close15 - 1.0
            rsi5 = 58.0
            rsi15 = 60.0
            roc5 = 1.0
            roc15 = 1.0
            st = "BULL"
            vwap5 = close5 - 0.5
            weekly = close5 - 1.0
        else:
            close5 = 100.0 - i
            close15 = 200.0 - i
            ema5 = close5 + 1.0
            ema15 = close15 + 1.0
            rsi5 = 42.0
            rsi15 = 40.0
            roc5 = -1.0
            roc15 = -1.0
            st = "BEAR"
            vwap5 = close5 + 0.5
            weekly = close5 + 1.0
        rows5.append({
            "datetime_parsed": base + pd.Timedelta(minutes=5 * i),
            "open": close5 - 0.2,
            "high": close5 + 0.5,
            "low": close5 - 0.5,
            "close": close5,
            "volume": 1000 + i,
            "ema_5m": ema5,
            "rsi_5m": rsi5,
            "roc_5m": roc5,
            "vwap_session": vwap5,
            "vwap_weekly": weekly,
            "rvol": 1.1,
            "supertrend": vwap5,
            "st_direction": st,
        })
        rows15.append({
            "datetime_parsed": base + pd.Timedelta(minutes=15 * i),
            "open": close15 - 0.2,
            "high": close15 + 0.5,
            "low": close15 - 0.5,
            "close": close15,
            "volume": 1000 + i,
            "ema_15m": ema15,
            "rsi_15m": rsi15,
            "roc_15m": roc15,
            "adx_15m": 30.0,
        })
    if incomplete_tail:
        rows5[-1]["datetime_parsed"] = pd.Timestamp.now()
        rows5[-1]["close"] = 50.0 if direction == "BULL" else 150.0
        rows5[-1]["ema_5m"] = 100.0
        rows5[-1]["roc_5m"] = -2.0 if direction == "BULL" else 2.0
    return pd.DataFrame(rows5), pd.DataFrame(rows15)


def _candidate():
    contract = ContractCandidate(
        underlying="TEST",
        trading_symbol="TEST-100-CE",
        option_type="CE",
        strike=100.0,
        expiry="2026-09-29",
        security_id="123",
        exchange_segment="NSE_FNO",
    )
    return SimpleNamespace(
        contract=contract,
        quote=QuoteSnapshot("123", ltp=100.0),
        liquidity=LiquidityAssessment(True, 80.0, "A", "A", None, 10.0),
        capital_allocated=30000.0,
        quantity=20,
        lot_size=10,
        entry_limit=100.0,
        target_price=115.0,
        stop_price=92.5,
        size_fraction=1.0,
    )


def _signal():
    return SimpleNamespace(
        symbol="TEST",
        direction="BULL",
        model="BREAKOUT_CONTINUATION",
        candle_time="2026-08-28 10:00:00",
        metrics={"close_5m": 100.0, "vwap_session": 99.0},
    )


def _closed_position(trade_id, pnl):
    return PaperPosition(
        trade_id=trade_id,
        underlying="TEST",
        direction="BULL",
        model="VWAP_PULLBACK",
        contract_symbol="TEST-100-CE",
        security_id="123",
        exchange_segment="NSE_FNO",
        strike=100.0,
        option_type="CE",
        expiry="2026-09-29",
        lot_size=10,
        entry_price=100.0,
        initial_quantity=10,
        open_quantity=0,
        stop_price=92.5,
        target_price=115.0,
        opened_at="2026-08-28T10:00:00",
        status="CLOSED",
        realized_pnl=pnl,
        last_price=100.0,
        last_marked_at="2026-08-28T10:30:00",
        closed_at="2026-08-28T10:30:00",
    )


class StrategyBTests(unittest.TestCase):
    def test_15m_bull_context(self):
        df5, df15 = _mtf_frames("BULL")

        context = evaluate_15m_context(df5, 5, df15, 5, _settings())

        self.assertEqual(context.state, "BULL")
        self.assertGreater(context.bull_confidence, context.bear_confidence)

    def test_15m_bear_context(self):
        df5, df15 = _mtf_frames("BEAR")

        context = evaluate_15m_context(df5, 5, df15, 5, _settings())

        self.assertEqual(context.state, "BEAR")
        self.assertGreater(context.bear_confidence, context.bull_confidence)

    def test_5m_bull_setup_creation(self):
        df5, df15 = _mtf_frames("BULL")

        setup = evaluate_mtf_setup("TEST", df5, df15, "10:30", _settings())

        self.assertEqual(setup.decision, "BULL_SETUP")
        self.assertEqual(setup.direction, "BULL")
        self.assertEqual(setup.model, "MTF_SETUP")

    def test_5m_bear_setup_creation(self):
        df5, df15 = _mtf_frames("BEAR")

        setup = evaluate_mtf_setup("TEST", df5, df15, "10:30", _settings())

        self.assertEqual(setup.decision, "BEAR_SETUP")
        self.assertEqual(setup.direction, "BEAR")

    def test_no_setup_in_conflicting_context(self):
        df5, _ = _mtf_frames("BULL")
        _, df15 = _mtf_frames("BEAR")

        setup = evaluate_mtf_setup("TEST", df5, df15, "10:30", _settings())

        self.assertEqual(setup.decision, "NONE")
        self.assertIn("CONTEXT", setup.blockers)

    def test_no_incomplete_5m_or_15m_setup_lookahead(self):
        df5, df15 = _mtf_frames("BULL", incomplete_tail=True)

        setup = evaluate_mtf_setup("TEST", df5, df15, "10:30", _settings())

        self.assertEqual(setup.decision, "BULL_SETUP")
        self.assertNotIn("50.0", str(setup.metrics["close_5m"]))

    def test_1m_bullish_trigger(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.setup_kind = "BULL_SETUP"
        setup.setup_5m = "BULL_SETUP"
        setup.context_15m = "BULL"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 100.8, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_mtf_trigger(setup, df, max_extension_atr=2.0)

        self.assertTrue(result.triggered)
        self.assertEqual(result.reason, "PREV_1M_HIGH_BREAK")
        self.assertIn("MTF TRIGGER", format_mtf_trigger(setup, result))

    def test_1m_bearish_trigger(self):
        signal = _signal()
        signal.direction = "BEAR"
        signal.metrics = {"close_5m": 100.0, "vwap_session": 101.0}
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            signal, _candidate(), expiry_minutes=5
        )
        setup.setup_kind = "BEAR_SETUP"
        setup.setup_5m = "BEAR_SETUP"
        setup.context_15m = "BEAR"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 101.0, "high": 101.5, "low": 100.0, "close": 100.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 100.4, "high": 100.5, "low": 99.0, "close": 99.5, "volume": 1200},
        ])

        result = evaluate_mtf_trigger(setup, df, max_extension_atr=2.0)

        self.assertTrue(result.triggered)
        self.assertEqual(result.reason, "PREV_1M_LOW_BREAK")

    def test_no_trigger_when_previous_high_low_not_broken(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.context_15m = "BULL"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 101.0, "low": 98.5, "close": 100.0, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 100.1, "high": 100.8, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_mtf_trigger(setup, df, max_extension_atr=2.0)

        self.assertFalse(result.triggered)
        self.assertFalse(result.diagnostics.break_trigger)

    def test_setup_invalidation(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.setup_kind = "BULL_SETUP"
        setup.setup_5m = "BULL_SETUP"
        setup.context_15m = "BULL"
        df1 = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 100.8, "low": 99.5, "close": 100.5, "volume": 1200},
        ])
        df5, _ = _mtf_frames("BEAR")
        _, df15 = _mtf_frames("BEAR")

        result = evaluate_mtf_trigger(setup, df1, 2.0, df5, df15, _settings())

        self.assertTrue(result.cancel)
        self.assertEqual(result.reason, "5M_DIRECTION_FLIPPED")

    def test_pending_setup_creation(self):
        with tempfile.TemporaryDirectory() as td:
            store = PendingSetupStore(Path(td) / "pending.json")

            setup = store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)

            self.assertEqual(setup.symbol, "TEST")
            self.assertEqual(setup.quantity, 20)
            self.assertEqual(setup.entry_limit, 100.0)
            self.assertEqual(len(store.pending()), 1)

    def test_pending_setup_expiry(self):
        with tempfile.TemporaryDirectory() as td:
            store = PendingSetupStore(Path(td) / "pending.json")
            setup = store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            setup.expires_at = "2026-08-28T10:05:00"
            store.replace(setup)

            expired = expire_pending_setups(store, now=pd.Timestamp("2026-08-28T10:06:00").to_pydatetime())

            self.assertEqual(len(expired), 1)
            self.assertEqual(store.load()[0].status, "EXPIRED")

    def test_successful_confirmation(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=2.0)

        self.assertTrue(result.confirmed)
        self.assertFalse(result.cancel)
        self.assertEqual(result.reason, "CONFIRMATION_SATISFIED")
        self.assertIsNotNone(result.diagnostics)
        self.assertTrue(result.diagnostics.trigger_cross)

    def test_aware_signal_and_current_time_do_not_crash(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.signal_timestamp = "2026-08-28T10:00:00+05:30"
        setup.created_at = "2026-08-28T10:00:00+05:30"
        setup.expires_at = "2026-08-28T10:05:00+05:30"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28T10:00:00+05:30"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28T10:01:00+05:30"), "open": 99.6, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=2.0)

        self.assertTrue(result.confirmed)
        self.assertEqual(result.diagnostics.elapsed_seconds, 60)
        self.assertEqual(result.diagnostics.expires_in_seconds, 240)

    def test_legacy_naive_signal_with_aware_current_time_do_not_crash(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.signal_timestamp = "2026-08-28T10:00:00"
        setup.created_at = "2026-08-28T10:00:00"
        setup.expires_at = "2026-08-28T10:05:00"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28T10:00:00+05:30"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28T10:01:00+05:30"), "open": 99.6, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=2.0)

        self.assertTrue(result.confirmed)
        self.assertEqual(result.diagnostics.elapsed_seconds, 60)

    def test_legacy_naive_pending_setup_is_normalized_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            store = PendingSetupStore(Path(td) / "pending.json")
            setup = store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            setup.signal_timestamp = "2026-08-28T10:00:00"
            setup.created_at = "2026-08-28T10:00:00"
            setup.expires_at = "2026-08-28T10:05:00"
            store.save([setup])

            loaded = store.load()[0]

            self.assertTrue(loaded.signal_timestamp.endswith("+05:30"))
            self.assertTrue(loaded.created_at.endswith("+05:30"))
            self.assertTrue(loaded.expires_at.endswith("+05:30"))

    def test_expiry_calculation_handles_aware_now(self):
        with tempfile.TemporaryDirectory() as td:
            store = PendingSetupStore(Path(td) / "pending.json")
            setup = store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            setup.created_at = "2026-08-28T10:00:00"
            setup.expires_at = "2026-08-28T10:05:00"
            store.save([setup])

            expired = expire_pending_setups(
                store,
                now=datetime(2026, 8, 28, 10, 6, tzinfo=MARKET_TZ),
            )

            self.assertEqual(len(expired), 1)
            self.assertTrue(expired[0].resolved_at.endswith("+05:30"))

    def test_elapsed_and_confirmation_delay_are_timezone_consistent(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.signal_timestamp = "2026-08-28T10:00:00"
        setup.created_at = "2026-08-28T10:00:30"
        setup.expires_at = "2026-08-28T10:05:30"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28T10:00:00+05:30"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28T10:01:30+05:30"), "open": 99.6, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=2.0)

        self.assertEqual(result.diagnostics.elapsed_seconds, 60)
        self.assertEqual(result.diagnostics.delay_seconds, 60)

    def test_cancellation_when_trend_invalidated(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 100.0, "low": 97.0, "close": 98.0, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=0.5)

        self.assertFalse(result.confirmed)
        self.assertTrue(result.cancel)
        self.assertEqual(result.reason, "TREND_INVALIDATED")

    def test_confirmation_check_log_contains_diagnostics(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 100.2, "low": 99.5, "close": 99.8, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=2.0)
        text = format_confirmation_check(setup, result)

        self.assertIn("[B] Confirmation Check | TEST", text)
        self.assertIn("Trigger Cross  : FAIL", text)
        self.assertIn("Status         : WAITING", text)

    def test_confirmation_passed_log_contains_pass_details(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1200},
        ])

        result = evaluate_confirmation(setup, df, max_adverse_atr=2.0)
        text = format_confirmation_passed(setup, result)

        self.assertIn("[B] Confirmation PASSED | TEST", text)
        self.assertIn("Trigger Cross : PASS", text)
        self.assertIn("Delay         :", text)

    def test_pending_expired_log_contains_reason(self):
        setup = PendingSetupStore(Path(tempfile.mkdtemp()) / "pending.json").add_from_candidate(
            _signal(), _candidate(), expiry_minutes=5
        )
        setup.status = "EXPIRED"
        setup.resolved_at = "2026-08-28T10:05:00"
        df = pd.DataFrame([
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:00"), "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1000},
            {"datetime_parsed": pd.Timestamp("2026-08-28 10:01"), "open": 99.6, "high": 100.2, "low": 99.5, "close": 99.8, "volume": 1200},
        ])
        diagnostics = evaluate_confirmation(setup, df, max_adverse_atr=2.0).diagnostics

        text = format_pending_expired(setup, diagnostics)

        self.assertIn("[B] Pending Expired | TEST", text)
        self.assertIn("Trigger Cross : NEVER", text)
        self.assertIn("Reason : Trigger never crossed", text)

    def test_independent_accounting_and_statistics(self):
        with tempfile.TemporaryDirectory() as td:
            a_store = PaperPositionStore(Path(td) / "a.json")
            b_store = PaperPositionStore(Path(td) / "b.json")
            pending_store = PendingSetupStore(Path(td) / "pending.json")
            a_store.save([_closed_position("a-win", 100.0)])
            b_store.save([_closed_position("b-loss", -50.0)])
            setup = pending_store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            mark_setup_executed(pending_store, setup, executed_price=99.0)

            a_stats = compute_strategy_stats(a_store, trading_date="2026-08-28")
            b_stats = compute_strategy_stats(b_store, pending_store, "2026-08-28")

            self.assertEqual(a_stats.net_pnl, 100.0)
            self.assertEqual(b_stats.net_pnl, -50.0)
            self.assertEqual(b_stats.pending_setups, 1)
            self.assertEqual(b_stats.executed, 1)
            self.assertGreaterEqual(b_stats.average_entry_improvement, 0.0)

    def test_comparison_report_contains_both_strategies(self):
        with tempfile.TemporaryDirectory() as td:
            a_store = PaperPositionStore(Path(td) / "a.json")
            b_store = PaperPositionStore(Path(td) / "b.json")
            pending_store = PendingSetupStore(Path(td) / "pending.json")
            a_store.save([_closed_position("a-win", 100.0)])
            b_store.save([_closed_position("b-loss", -50.0)])
            setup = pending_store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            mark_setup_cancelled(pending_store, setup, "TREND_INVALIDATED")

            report = comparison_report(
                compute_strategy_stats(a_store, trading_date="2026-08-28"),
                compute_strategy_stats(b_store, pending_store, "2026-08-28"),
            )

            self.assertIn("Strategy A", report)
            self.assertIn("Strategy B", report)
            self.assertIn("Cancelled=1", report)

    def test_strategy_b_summary_report_contains_observability_stats(self):
        with tempfile.TemporaryDirectory() as td:
            b_store = PaperPositionStore(Path(td) / "b.json")
            pending_store = PendingSetupStore(Path(td) / "pending.json")
            b_store.save([])
            setup = pending_store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            mark_setup_executed(pending_store, setup, executed_price=99.0)
            expired = pending_store.add_from_candidate(_signal(), _candidate(), expiry_minutes=5)
            expired.status = "EXPIRED"
            expired.resolved_at = "2026-08-28T10:05:00"
            pending_store.replace(expired)

            text = strategy_b_summary_report(compute_strategy_stats(b_store, pending_store, "2026-08-28"))

            self.assertIn("Strategy B Summary", text)
            self.assertIn("Pending Created      : 2", text)
            self.assertIn("Confirmed            : 1", text)
            self.assertIn("Expired              : 1", text)


if __name__ == "__main__":
    unittest.main()
