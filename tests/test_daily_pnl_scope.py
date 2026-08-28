import tempfile
import unittest
from datetime import date
from pathlib import Path

from config import AppConfig, CredentialsConfig, RiskConfig
from core.paper_positions import PaperPosition, PaperPositionStore
from core.risk import RiskManager
from core.state import DailyState, StateStore


def _position(
    trade_id,
    opened_at,
    entry_price,
    quantity,
    *,
    status="CLOSED",
    realized_pnl=0.0,
    last_price=0.0,
    last_marked_at="",
    closed_at="",
):
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
        entry_price=entry_price,
        initial_quantity=quantity,
        open_quantity=quantity if status in {"OPEN", "PARTIAL"} else 0,
        stop_price=entry_price * 0.925,
        target_price=entry_price * 1.15,
        opened_at=opened_at,
        status=status,
        realized_pnl=realized_pnl,
        last_price=last_price,
        last_marked_at=last_marked_at,
        closed_at=closed_at,
    )


def _risk():
    return RiskManager(AppConfig(
        credentials=CredentialsConfig("client", "pin", "secret"),
        risk=RiskConfig(dry_run=True, strategy_capital_base=150000.0, daily_max_loss_pct=0.015),
    ))


class DailyPnlScopeTests(unittest.TestCase):
    def test_yesterday_loss_and_today_no_trades_is_zero_for_risk(self):
        with tempfile.TemporaryDirectory() as td:
            store = PaperPositionStore(Path(td) / "paper.json")
            store.save([
                _position(
                    "yesterday-loss",
                    "2026-08-27T10:00:00",
                    100.0,
                    50,
                    realized_pnl=-5031.0,
                    closed_at="2026-08-27T10:30:00",
                )
            ])

            self.assertEqual(store.strategy_pnl(), -5031.0)
            self.assertEqual(store.strategy_pnl_for_date("2026-08-28"), 0.0)
            decision = _risk().can_open_new_trade(
                DailyState("2026-08-28", session_start_balance=150000.0),
                managed_open_positions=0,
                strategy_pnl=store.strategy_pnl_for_date("2026-08-28"),
            )
            self.assertTrue(decision.allowed)

    def test_today_losses_exceed_threshold_blocks_and_latches(self):
        with tempfile.TemporaryDirectory() as td:
            paper = PaperPositionStore(Path(td) / "paper.json")
            paper.save([
                _position(
                    "today-loss",
                    "2026-08-28T10:00:00",
                    100.0,
                    50,
                    realized_pnl=-2300.0,
                    closed_at="2026-08-28T10:30:00",
                )
            ])
            state_store = StateStore(Path(td) / "daily_state.json")
            state = DailyState("2026-08-28", session_start_balance=150000.0)
            risk = _risk()

            today_pnl = paper.strategy_pnl_for_date(state.trading_date)
            if today_pnl <= -risk.daily_loss_limit(state):
                state.daily_loss_locked = True
                state_store.save(state)

            decision = risk.can_open_new_trade(state, 0, today_pnl)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "STRATEGY_DAILY_LOSS_LOCKED")

    def test_historical_losses_do_not_trigger_today_lock(self):
        with tempfile.TemporaryDirectory() as td:
            paper = PaperPositionStore(Path(td) / "paper.json")
            paper.save([
                _position(
                    "old-loss",
                    "2026-08-27T10:00:00",
                    100.0,
                    50,
                    realized_pnl=-10000.0,
                    closed_at="2026-08-27T10:30:00",
                )
            ])
            state = DailyState("2026-08-28", session_start_balance=150000.0)
            today_pnl = paper.strategy_pnl_for_date(state.trading_date)

            self.assertEqual(today_pnl, 0.0)
            self.assertFalse(state.daily_loss_locked)
            self.assertTrue(_risk().can_open_new_trade(state, 0, today_pnl).allowed)

    def test_today_unrealized_pnl_counts(self):
        with tempfile.TemporaryDirectory() as td:
            paper = PaperPositionStore(Path(td) / "paper.json")
            paper.save([
                _position(
                    "today-open-loss",
                    "2026-08-28T10:00:00",
                    100.0,
                    50,
                    status="OPEN",
                    last_price=80.0,
                    last_marked_at="2026-08-28T10:15:00",
                )
            ])

            self.assertEqual(paper.strategy_pnl_for_date("2026-08-28"), -1000.0)

    def test_same_day_restart_preserves_lock(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "daily_state.json")
            state = DailyState(date.today().isoformat(), session_start_balance=150000.0, daily_loss_locked=True)
            store.save(state)

            reloaded = store.load_or_create(150000.0)
            self.assertTrue(reloaded.daily_loss_locked)


if __name__ == "__main__":
    unittest.main()
