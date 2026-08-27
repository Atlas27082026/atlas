from dataclasses import dataclass
from typing import Optional

from config import AppConfig
from core.state import DailyState


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, config: AppConfig):
        self.config = config

    def strategy_capital_base(self, state: DailyState) -> float:
        configured = float(getattr(self.config.risk, "strategy_capital_base", 0.0) or 0.0)
        return configured if configured > 0 else state.session_start_balance

    def daily_loss_limit(self, state: DailyState) -> float:
        return self.strategy_capital_base(state) * self.config.risk.daily_max_loss_pct

    def can_open_new_trade(
        self,
        state: DailyState,
        managed_open_positions: int,
        strategy_pnl: Optional[float],
        underlying: Optional[str] = None,
    ) -> RiskDecision:
        """Apply risk only to positions owned by this strategy.

        External/manual broker positions are deliberately excluded. They remain
        visible at the account level but can never be closed, counted, or adopted
        by this risk manager without an explicit ownership record.
        """
        risk = self.config.risk

        if state.daily_trade_count >= risk.max_daily_trades:
            return RiskDecision(False, "MAX_DAILY_TRADES")

        if managed_open_positions >= risk.max_open_positions:
            return RiskDecision(False, "MAX_MANAGED_OPEN_POSITIONS")

        if state.consecutive_losses >= risk.max_consecutive_losses:
            return RiskDecision(False, "MAX_CONSECUTIVE_LOSSES")

        if underlying and underlying in state.traded_underlyings:
            return RiskDecision(False, "UNDERLYING_ALREADY_TRADED")

        # Sprint 4 paper P&L is strategy-owned risk and therefore must obey the
        # same daily-loss gate as live managed positions. Manual broker P&L is
        # never passed here. Live mode additionally fails closed if owned P&L is
        # unavailable.
        if strategy_pnl is None:
            if not risk.dry_run and risk.fail_closed_on_risk_data_error:
                return RiskDecision(False, "STRATEGY_PNL_UNAVAILABLE")
        elif strategy_pnl <= -self.daily_loss_limit(state):
            return RiskDecision(False, "STRATEGY_DAILY_LOSS_LIMIT")

        return RiskDecision(True, "OK")

    def capital_for_trade(self, available_balance: float) -> float:
        return max(0.0, available_balance * self.config.risk.capital_per_trade_fraction)
