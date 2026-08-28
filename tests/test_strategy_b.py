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
    evaluate_confirmation,
    expire_pending_setups,
    format_confirmation_check,
    format_confirmation_passed,
    format_pending_expired,
    mark_setup_cancelled,
    mark_setup_executed,
    strategy_b_summary_report,
)
from execution.models import ContractCandidate, LiquidityAssessment, QuoteSnapshot


MARKET_TZ = ZoneInfo("Asia/Kolkata")


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
