import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from core.paper_positions import PaperPosition, PaperPositionStore
from core.strategy_b import (
    PendingSetupStore,
    comparison_report,
    compute_strategy_stats,
    evaluate_confirmation,
    expire_pending_setups,
    mark_setup_cancelled,
    mark_setup_executed,
)
from execution.models import ContractCandidate, LiquidityAssessment, QuoteSnapshot


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

            self.assertEqual(expired, 1)
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


if __name__ == "__main__":
    unittest.main()
