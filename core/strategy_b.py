from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd

from execution.models import ContractCandidate


@dataclass
class PendingSetup:
    setup_id: str
    symbol: str
    direction: str
    model: str
    contract: dict
    quantity: int
    lot_size: int
    entry_limit: float
    stop_price: float
    target_price: float
    signal_timestamp: str
    signal_price: float
    signal_5m_vwap: float
    created_at: str
    expires_at: str
    status: str = "PENDING"
    exit_reason: str = ""
    resolved_at: str = ""
    executed_at: str = ""
    executed_price: float = 0.0

    @property
    def is_pending(self) -> bool:
        return self.status == "PENDING"


@dataclass(frozen=True)
class ConfirmationResult:
    confirmed: bool
    cancel: bool
    reason: str
    close_price: float = 0.0
    atr_1m: float = 0.0


@dataclass(frozen=True)
class StrategyStats:
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: float
    profit_factor: float
    expectancy: float
    pending_setups: int = 0
    executed: int = 0
    expired: int = 0
    cancelled: int = 0
    average_entry_delay: float = 0.0
    average_entry_improvement: float = 0.0


class PendingSetupStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> List[PendingSetup]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("setups", []) if isinstance(payload, dict) else payload
            return [PendingSetup(**row) for row in rows if isinstance(row, dict)]
        except Exception:
            return []

    def save(self, setups: Iterable[PendingSetup]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"setups": [asdict(s) for s in setups]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def pending(self) -> List[PendingSetup]:
        return [s for s in self.load() if s.is_pending]

    def has_pending_symbol(self, symbol: str) -> bool:
        key = str(symbol).upper().strip()
        return any(s.symbol.upper().strip() == key for s in self.pending())

    def add_from_candidate(self, result, candidate, expiry_minutes: int) -> PendingSetup:
        setups = self.load()
        now = datetime.now()
        setup = PendingSetup(
            setup_id=f"SETUP-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            symbol=result.symbol,
            direction=result.direction,
            model=result.model or "",
            contract=asdict(candidate.contract),
            quantity=int(candidate.quantity),
            lot_size=int(candidate.lot_size),
            entry_limit=float(candidate.entry_limit),
            stop_price=float(candidate.stop_price),
            target_price=float(candidate.target_price),
            signal_timestamp=str(result.candle_time),
            signal_price=float(result.metrics["close_5m"]),
            signal_5m_vwap=float(result.metrics["vwap_session"]),
            created_at=now.isoformat(timespec="seconds"),
            expires_at=(now + timedelta(minutes=int(expiry_minutes))).isoformat(timespec="seconds"),
        )
        setups.append(setup)
        self.save(setups)
        return setup

    def replace(self, updated: PendingSetup) -> None:
        setups = self.load()
        out = []
        found = False
        for setup in setups:
            if setup.setup_id == updated.setup_id:
                out.append(updated)
                found = True
            else:
                out.append(setup)
        if not found:
            out.append(updated)
        self.save(out)


def contract_from_setup(setup: PendingSetup) -> ContractCandidate:
    return ContractCandidate(**setup.contract)


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    dates = df["datetime_parsed"].dt.date
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_vol = tp * df["volume"]
    return tp_vol.groupby(dates).cumsum() / df["volume"].groupby(dates).cumsum().replace(0, pd.NA)


def _atr_1m(df: pd.DataFrame, idx: int, lookback: int = 14) -> float:
    if idx <= 0:
        return 0.0
    start = max(1, idx - lookback + 1)
    ranges = []
    for i in range(start, idx + 1):
        row = df.iloc[i]
        prev_close = float(df.iloc[i - 1]["close"])
        ranges.append(max(
            float(row["high"]) - float(row["low"]),
            abs(float(row["high"]) - prev_close),
            abs(float(row["low"]) - prev_close),
        ))
    return float(sum(ranges) / len(ranges)) if ranges else 0.0


def evaluate_confirmation(setup: PendingSetup, df_1m: pd.DataFrame, max_adverse_atr: float) -> ConfirmationResult:
    if len(df_1m) < 2:
        return ConfirmationResult(False, False, "ONE_MINUTE_NOT_READY")

    out = df_1m.copy()
    out["vwap_1m"] = _session_vwap(out)
    idx = len(out) - 1
    row = out.iloc[idx]
    prev = out.iloc[idx - 1]
    close = float(row["close"])
    open_ = float(row["open"])
    atr = _atr_1m(out, idx)
    adverse_limit = float(max_adverse_atr) * atr

    if setup.direction == "BULL":
        trend_valid = close > float(setup.signal_5m_vwap)
        adverse = atr > 0 and close > float(setup.signal_price) + adverse_limit
        confirmed = (
            close > open_
            and close > float(prev["high"])
            and close > float(row["vwap_1m"])
            and trend_valid
        )
    else:
        trend_valid = close < float(setup.signal_5m_vwap)
        adverse = atr > 0 and close < float(setup.signal_price) - adverse_limit
        confirmed = (
            close < open_
            and close < float(prev["low"])
            and close < float(row["vwap_1m"])
            and trend_valid
        )

    if not trend_valid:
        return ConfirmationResult(False, True, "TREND_INVALIDATED", close, atr)
    if adverse:
        return ConfirmationResult(False, True, "ENTRY_WORSE_THAN_0_5_ATR_1M", close, atr)
    if confirmed:
        return ConfirmationResult(True, False, "CONFIRMATION_SATISFIED", close, atr)
    return ConfirmationResult(False, False, "WAITING_FOR_1M_CONFIRMATION", close, atr)


def expire_pending_setups(store: PendingSetupStore, now: Optional[datetime] = None) -> int:
    now = now or datetime.now()
    expired = 0
    for setup in store.pending():
        if datetime.fromisoformat(setup.expires_at) <= now:
            setup.status = "EXPIRED"
            setup.exit_reason = "EXPIRED"
            setup.resolved_at = now.isoformat(timespec="seconds")
            store.replace(setup)
            expired += 1
    return expired


def mark_setup_cancelled(store: PendingSetupStore, setup: PendingSetup, reason: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    setup.status = "CANCELLED"
    setup.exit_reason = reason
    setup.resolved_at = now
    store.replace(setup)


def mark_setup_executed(store: PendingSetupStore, setup: PendingSetup, executed_price: float) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    setup.status = "EXECUTED"
    setup.resolved_at = now
    setup.executed_at = now
    setup.executed_price = float(executed_price)
    store.replace(setup)


def compute_strategy_stats(paper_store, pending_store: Optional[PendingSetupStore] = None, trading_date: str = "") -> StrategyStats:
    positions = paper_store.load()
    if trading_date:
        positions = [p for p in positions if str(p.opened_at).startswith(str(trading_date))]
    closed = [p for p in positions if not p.is_open and p.closed_at]
    trades = len(closed)
    wins = sum(1 for p in closed if p.total_pnl > 0)
    losses = sum(1 for p in closed if p.total_pnl < 0)
    gross_profit = sum(p.total_pnl for p in closed if p.total_pnl > 0)
    gross_loss = abs(sum(p.total_pnl for p in closed if p.total_pnl < 0))
    net_pnl = sum(p.total_pnl for p in closed)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0 if gross_profit == 0 else float("inf")
    win_rate = (wins / trades) * 100.0 if trades else 0.0
    expectancy = net_pnl / trades if trades else 0.0

    if pending_store is None:
        return StrategyStats(trades, wins, losses, round(win_rate, 2), round(net_pnl, 2), round(profit_factor, 4), round(expectancy, 2))

    setups = pending_store.load()
    if trading_date:
        setups = [s for s in setups if str(s.created_at).startswith(str(trading_date))]
    executed = [s for s in setups if s.status == "EXECUTED"]
    delays = []
    improvements = []
    for setup in executed:
        try:
            delays.append((datetime.fromisoformat(setup.executed_at) - datetime.fromisoformat(setup.created_at)).total_seconds())
            if setup.direction == "BEAR":
                improvements.append(float(setup.executed_price) - float(setup.signal_price))
            else:
                improvements.append(float(setup.signal_price) - float(setup.executed_price))
        except Exception:
            pass

    return StrategyStats(
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=round(win_rate, 2),
        net_pnl=round(net_pnl, 2),
        profit_factor=round(profit_factor, 4),
        expectancy=round(expectancy, 2),
        pending_setups=len(setups),
        executed=len(executed),
        expired=sum(1 for s in setups if s.status == "EXPIRED"),
        cancelled=sum(1 for s in setups if s.status == "CANCELLED"),
        average_entry_delay=round(sum(delays) / len(delays), 2) if delays else 0.0,
        average_entry_improvement=round(sum(improvements) / len(improvements), 4) if improvements else 0.0,
    )


def comparison_report(strategy_a: StrategyStats, strategy_b: StrategyStats) -> str:
    return (
        "Strategy A | "
        f"Trades={strategy_a.trades} | Wins={strategy_a.wins} | Losses={strategy_a.losses} | "
        f"Win Rate={strategy_a.win_rate:.2f}% | Net P&L={strategy_a.net_pnl:.2f} | "
        f"Profit Factor={strategy_a.profit_factor:.4f} | Expectancy={strategy_a.expectancy:.2f}\n"
        "Strategy B | "
        f"Pending Setups={strategy_b.pending_setups} | Executed={strategy_b.executed} | "
        f"Expired={strategy_b.expired} | Cancelled={strategy_b.cancelled} | "
        f"Trades={strategy_b.trades} | Wins={strategy_b.wins} | Losses={strategy_b.losses} | "
        f"Win Rate={strategy_b.win_rate:.2f}% | Net P&L={strategy_b.net_pnl:.2f} | "
        f"Profit Factor={strategy_b.profit_factor:.4f} | Expectancy={strategy_b.expectancy:.2f} | "
        f"Average Entry Delay={strategy_b.average_entry_delay:.2f}s | "
        f"Average Entry Improvement={strategy_b.average_entry_improvement:.4f}"
    )
