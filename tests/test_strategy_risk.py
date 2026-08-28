import unittest
from dataclasses import replace
from pathlib import Path
import tempfile

from config import AppConfig, RiskConfig
from core.risk import RiskManager
from core.state import DailyState, StateStore


class StrategyRiskTests(unittest.TestCase):
    def test_external_positions_do_not_count_against_managed_limit(self):
        cfg = AppConfig(risk=RiskConfig(dry_run=False, strategy_capital_base=150000.0))
        risk = RiskManager(cfg)
        state = DailyState(trading_date="2099-01-01", session_start_balance=10000)
        d = risk.can_open_new_trade(state, managed_open_positions=0, strategy_pnl=0.0)
        self.assertTrue(d.allowed)

    def test_paper_strategy_loss_limit_also_applies_in_dry_run(self):
        cfg = AppConfig(risk=RiskConfig(dry_run=True, strategy_capital_base=150000.0, daily_max_loss_pct=0.015))
        risk = RiskManager(cfg)
        state = DailyState(trading_date="2099-01-01", session_start_balance=150000)
        self.assertFalse(risk.can_open_new_trade(state, 0, -2300.0).allowed)
        self.assertEqual(risk.can_open_new_trade(state, 0, -2300.0).reason, "STRATEGY_DAILY_LOSS_LOCKED")

    def test_strategy_loss_limit_uses_configured_capital_not_current_available_balance(self):
        cfg = AppConfig(risk=RiskConfig(dry_run=False, strategy_capital_base=150000.0, daily_max_loss_pct=0.015))
        risk = RiskManager(cfg)
        state = DailyState(trading_date="2099-01-01", session_start_balance=11661.29)
        self.assertAlmostEqual(risk.daily_loss_limit(state), 2250.0)
        self.assertFalse(risk.can_open_new_trade(state, 0, -2300.0).allowed)
        self.assertTrue(risk.can_open_new_trade(state, 0, -2000.0).allowed)

    def test_latched_daily_loss_blocks_even_after_pnl_recovers(self):
        cfg = AppConfig(risk=RiskConfig(dry_run=True, strategy_capital_base=150000.0, daily_max_loss_pct=0.015))
        risk = RiskManager(cfg)
        state = DailyState(trading_date="2099-01-01", session_start_balance=150000, daily_loss_locked=True)
        decision = risk.can_open_new_trade(state, managed_open_positions=0, strategy_pnl=1000.0)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "STRATEGY_DAILY_LOSS_LOCKED")

    def test_paper_research_override_allows_latched_daily_loss_entries(self):
        cfg = AppConfig(risk=RiskConfig(
            dry_run=True,
            paper_ignore_daily_loss_lock=True,
            strategy_capital_base=150000.0,
            daily_max_loss_pct=0.015,
        ))
        risk = RiskManager(cfg)
        state = DailyState(trading_date="2099-01-01", session_start_balance=150000, daily_loss_locked=True)

        decision = risk.can_open_new_trade(state, managed_open_positions=0, strategy_pnl=-2300.0)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "STRATEGY_DAILY_LOSS_LOCKED")
        self.assertTrue(decision.daily_loss_override)

    def test_live_mode_never_allows_daily_loss_override(self):
        cfg = AppConfig(risk=RiskConfig(
            dry_run=False,
            paper_ignore_daily_loss_lock=True,
            strategy_capital_base=150000.0,
            daily_max_loss_pct=0.015,
        ))
        risk = RiskManager(cfg)
        state = DailyState(trading_date="2099-01-01", session_start_balance=150000, daily_loss_locked=True)

        decision = risk.can_open_new_trade(state, managed_open_positions=0, strategy_pnl=1000.0)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "STRATEGY_DAILY_LOSS_LOCKED")
        self.assertFalse(decision.daily_loss_override)

    def test_paper_research_override_does_not_bypass_other_gates(self):
        cfg = AppConfig(risk=RiskConfig(
            dry_run=True,
            paper_ignore_daily_loss_lock=True,
            max_daily_trades=1,
            max_open_positions=1,
            strategy_capital_base=150000.0,
        ))
        risk = RiskManager(cfg)
        locked = DailyState(
            trading_date="2099-01-01",
            session_start_balance=150000,
            daily_loss_locked=True,
            daily_trade_count=1,
        )
        max_trades = risk.can_open_new_trade(locked, managed_open_positions=0, strategy_pnl=-2300.0)
        self.assertFalse(max_trades.allowed)
        self.assertEqual(max_trades.reason, "MAX_DAILY_TRADES")

        locked.daily_trade_count = 0
        max_open = risk.can_open_new_trade(locked, managed_open_positions=1, strategy_pnl=-2300.0)
        self.assertFalse(max_open.allowed)
        self.assertEqual(max_open.reason, "MAX_MANAGED_OPEN_POSITIONS")

    def test_daily_loss_lock_is_persisted_for_same_trading_day(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "daily_state.json")
            state = store.load_or_create(150000.0)
            state.daily_loss_locked = True
            store.save(state)

            reloaded = store.load_or_create(150000.0)
            self.assertTrue(reloaded.daily_loss_locked)


if __name__ == "__main__":
    unittest.main()
