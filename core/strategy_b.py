from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from execution.models import ContractCandidate


MARKET_TZ = ZoneInfo("Asia/Kolkata")


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
class ConfirmationDiagnostics:
    elapsed_seconds: int
    expires_in_seconds: int
    signal_price: float
    current_price: float
    distance_pct: float
    highest_since_signal: float
    lowest_since_signal: float
    signal_time: str
    entry_time: str
    delay_seconds: int
    trigger_cross: bool
    vwap: bool
    momentum: bool
    volume: bool
    trend: bool


@dataclass(frozen=True)
class ConfirmationResult:
    confirmed: bool
    cancel: bool
    reason: str
    close_price: float = 0.0
    atr_1m: float = 0.0
    diagnostics: Optional[ConfirmationDiagnostics] = None


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
    average_wait_before_expiry: float = 0.0


def _now() -> datetime:
    return datetime.now(MARKET_TZ)


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=MARKET_TZ)
        return parsed.astimezone(MARKET_TZ)
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MARKET_TZ)
    return parsed.astimezone(MARKET_TZ)


def _dt_text(value: str) -> str:
    parsed = _parse_dt(value)
    return parsed.isoformat(timespec="seconds") if parsed else str(value or "")


def _clock(value: str) -> str:
    parsed = _parse_dt(value)
    return parsed.strftime("%H:%M:%S") if parsed else str(value)


def _timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize(MARKET_TZ)
    return ts.tz_convert(MARKET_TZ)


def _timestamp_series(values) -> pd.Series:
    return pd.Series(
        [_timestamp(value) if not pd.isna(value) else pd.NaT for value in values],
        index=values.index,
    )


class PendingSetupStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> List[PendingSetup]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("setups", []) if isinstance(payload, dict) else payload
            out = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                normalized = dict(row)
                for key in ("signal_timestamp", "created_at", "expires_at", "resolved_at", "executed_at"):
                    if normalized.get(key):
                        normalized[key] = _dt_text(normalized[key])
                out.append(PendingSetup(**normalized))
            return out
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
        now = _now()
        signal_timestamp = _dt_text(str(result.candle_time))
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
            signal_timestamp=signal_timestamp,
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


def _pass_fail(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _never_pass(value: bool) -> str:
    return "PASS" if value else "NEVER"


def _expiry_reason(diagnostics: Optional[ConfirmationDiagnostics]) -> str:
    if diagnostics is None:
        return "Confirmation never occurred"
    if not diagnostics.trigger_cross:
        return "Trigger never crossed"
    if not diagnostics.vwap:
        return "VWAP confirmation never occurred"
    if not diagnostics.momentum:
        return "Momentum confirmation never occurred"
    if not diagnostics.volume:
        return "Volume confirmation never occurred"
    if not diagnostics.trend:
        return "Trend confirmation never occurred"
    return "Confirmation never occurred"


def format_confirmation_check(setup: PendingSetup, result: ConfirmationResult) -> str:
    d = result.diagnostics
    if d is None:
        return f"[B] Confirmation Check | {setup.symbol}\n\nStatus         : {result.reason}"
    status = "PASSED" if result.confirmed else "CANCELLED" if result.cancel else "WAITING"
    return (
        f"[B] Confirmation Check | {setup.symbol}\n\n"
        f"Elapsed        : {d.elapsed_seconds} sec\n"
        f"Expires In     : {d.expires_in_seconds} sec\n\n"
        f"Signal Price   : {d.signal_price:.2f}\n"
        f"Current Price  : {d.current_price:.2f}\n"
        f"Distance       : {d.distance_pct:.2f}%\n\n"
        f"Highest Since Signal : {d.highest_since_signal:.2f}\n"
        f"Lowest Since Signal  : {d.lowest_since_signal:.2f}\n\n"
        f"Trigger Cross  : {_pass_fail(d.trigger_cross)}\n"
        f"VWAP           : {_pass_fail(d.vwap)}\n"
        f"Momentum       : {_pass_fail(d.momentum)}\n"
        f"Volume         : {_pass_fail(d.volume)}\n"
        f"Trend          : {_pass_fail(d.trend)}\n\n"
        f"Status         : {status}"
    )


def format_confirmation_passed(setup: PendingSetup, result: ConfirmationResult) -> str:
    d = result.diagnostics
    if d is None:
        return f"[B] Confirmation PASSED | {setup.symbol}"
    return (
        f"[B] Confirmation PASSED | {setup.symbol}\n\n"
        f"Trigger Cross : {_pass_fail(d.trigger_cross)}\n"
        f"VWAP          : {_pass_fail(d.vwap)}\n"
        f"Momentum      : {_pass_fail(d.momentum)}\n"
        f"Volume        : {_pass_fail(d.volume)}\n"
        f"Trend         : {_pass_fail(d.trend)}\n\n"
        f"Signal Time   : {d.signal_time}\n"
        f"Entry Time    : {d.entry_time}\n"
        f"Delay         : {d.delay_seconds} sec"
    )


def format_pending_expired(setup: PendingSetup, diagnostics: Optional[ConfirmationDiagnostics]) -> str:
    signal_time = _clock(setup.signal_timestamp)
    created = _parse_dt(setup.created_at)
    resolved = _parse_dt(setup.resolved_at)
    lifetime = int((resolved - created).total_seconds()) if created and resolved else 0
    if diagnostics is None:
        return (
            f"[B] Pending Expired | {setup.symbol}\n\n"
            f"Signal Time : {signal_time}\n"
            f"Lifetime    : {lifetime} sec\n\n"
            f"Signal Price : {float(setup.signal_price):.2f}\n\n"
            "Reason : Confirmation data unavailable"
        )
    return (
        f"[B] Pending Expired | {setup.symbol}\n\n"
        f"Signal Time : {signal_time}\n"
        f"Lifetime    : {lifetime} sec\n\n"
        f"Signal Price : {diagnostics.signal_price:.2f}\n\n"
        f"Highest Since Signal : {diagnostics.highest_since_signal:.2f}\n"
        f"Lowest Since Signal  : {diagnostics.lowest_since_signal:.2f}\n\n"
        f"Trigger Cross : {_never_pass(diagnostics.trigger_cross)}\n"
        f"VWAP          : {_pass_fail(diagnostics.vwap)}\n"
        f"Momentum      : {_pass_fail(diagnostics.momentum)}\n"
        f"Volume        : {_pass_fail(diagnostics.volume)}\n"
        f"Trend         : {_pass_fail(diagnostics.trend)}\n\n"
        f"Reason : {_expiry_reason(diagnostics)}"
    )


def evaluate_confirmation(setup: PendingSetup, df_1m: pd.DataFrame, max_adverse_atr: float) -> ConfirmationResult:
    if len(df_1m) < 2:
        return ConfirmationResult(False, False, "ONE_MINUTE_NOT_READY")

    out = df_1m.copy()
    out["datetime_parsed"] = _timestamp_series(out["datetime_parsed"])
    out["vwap_1m"] = _session_vwap(out)
    idx = len(out) - 1
    row = out.iloc[idx]
    prev = out.iloc[idx - 1]
    close = float(row["close"])
    open_ = float(row["open"])
    atr = _atr_1m(out, idx)
    adverse_limit = float(max_adverse_atr) * atr
    signal_ts = _timestamp(setup.signal_timestamp)
    since_signal = out[out["datetime_parsed"] >= signal_ts]
    if since_signal.empty:
        since_signal = out
    current_time = _timestamp(row["datetime_parsed"]).to_pydatetime()
    created = _parse_dt(setup.created_at) or current_time
    expires = _parse_dt(setup.expires_at) or current_time
    signal_time = _clock(setup.signal_timestamp)
    entry_time = current_time.strftime("%H:%M:%S")
    volume_ok = float(row["volume"]) >= float(prev["volume"])

    if setup.direction == "BULL":
        trend_valid = close > float(setup.signal_5m_vwap)
        trigger_cross = close > float(prev["high"])
        vwap_ok = close > float(row["vwap_1m"])
        momentum_ok = close > open_
        adverse = atr > 0 and close > float(setup.signal_price) + adverse_limit
        confirmed = (
            momentum_ok
            and trigger_cross
            and vwap_ok
            and trend_valid
        )
    else:
        trend_valid = close < float(setup.signal_5m_vwap)
        trigger_cross = close < float(prev["low"])
        vwap_ok = close < float(row["vwap_1m"])
        momentum_ok = close < open_
        adverse = atr > 0 and close < float(setup.signal_price) - adverse_limit
        confirmed = (
            momentum_ok
            and trigger_cross
            and vwap_ok
            and trend_valid
        )

    diagnostics = ConfirmationDiagnostics(
        elapsed_seconds=max(0, int((current_time - created).total_seconds())),
        expires_in_seconds=max(0, int((expires - current_time).total_seconds())),
        signal_price=float(setup.signal_price),
        current_price=close,
        distance_pct=0.0 if float(setup.signal_price) == 0 else ((close - float(setup.signal_price)) / float(setup.signal_price)) * 100.0,
        highest_since_signal=float(since_signal["high"].max()),
        lowest_since_signal=float(since_signal["low"].min()),
        signal_time=signal_time,
        entry_time=entry_time,
        delay_seconds=max(0, int((current_time - created).total_seconds())),
        trigger_cross=trigger_cross,
        vwap=vwap_ok,
        momentum=momentum_ok,
        volume=volume_ok,
        trend=trend_valid,
    )

    if not trend_valid:
        return ConfirmationResult(False, True, "TREND_INVALIDATED", close, atr, diagnostics)
    if adverse:
        return ConfirmationResult(False, True, "ENTRY_WORSE_THAN_0_5_ATR_1M", close, atr, diagnostics)
    if confirmed:
        return ConfirmationResult(True, False, "CONFIRMATION_SATISFIED", close, atr, diagnostics)
    return ConfirmationResult(False, False, "WAITING_FOR_1M_CONFIRMATION", close, atr, diagnostics)


def expire_pending_setups(store: PendingSetupStore, now: Optional[datetime] = None) -> List[PendingSetup]:
    now = _parse_dt(now) if now else _now()
    expired: List[PendingSetup] = []
    for setup in store.pending():
        expires_at = _parse_dt(setup.expires_at)
        if expires_at and expires_at <= now:
            setup.status = "EXPIRED"
            setup.exit_reason = "EXPIRED"
            setup.resolved_at = now.isoformat(timespec="seconds")
            store.replace(setup)
            expired.append(setup)
    return expired


def mark_setup_cancelled(store: PendingSetupStore, setup: PendingSetup, reason: str) -> None:
    now = _now().isoformat(timespec="seconds")
    setup.status = "CANCELLED"
    setup.exit_reason = reason
    setup.resolved_at = now
    store.replace(setup)


def mark_setup_executed(store: PendingSetupStore, setup: PendingSetup, executed_price: float) -> None:
    now = _now().isoformat(timespec="seconds")
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
    expired = [s for s in setups if s.status == "EXPIRED"]
    delays = []
    improvements = []
    for setup in executed:
        try:
            delays.append((_parse_dt(setup.executed_at) - _parse_dt(setup.created_at)).total_seconds())
            if setup.direction == "BEAR":
                improvements.append(float(setup.executed_price) - float(setup.signal_price))
            else:
                improvements.append(float(setup.signal_price) - float(setup.executed_price))
        except Exception:
            pass
    expiry_waits = []
    for setup in expired:
        try:
            expiry_waits.append((_parse_dt(setup.resolved_at) - _parse_dt(setup.created_at)).total_seconds())
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
        expired=len(expired),
        cancelled=sum(1 for s in setups if s.status == "CANCELLED"),
        average_entry_delay=round(sum(delays) / len(delays), 2) if delays else 0.0,
        average_entry_improvement=round(sum(improvements) / len(improvements), 4) if improvements else 0.0,
        average_wait_before_expiry=round(sum(expiry_waits) / len(expiry_waits), 2) if expiry_waits else 0.0,
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


def strategy_b_summary_report(stats: StrategyStats) -> str:
    confirmation_rate = (stats.executed / stats.pending_setups) * 100.0 if stats.pending_setups else 0.0
    return (
        "===============================\n"
        "Strategy B Summary\n"
        "===============================\n\n"
        f"Pending Created      : {stats.pending_setups}\n\n"
        f"Confirmed            : {stats.executed}\n\n"
        f"Expired              : {stats.expired}\n\n"
        f"Cancelled            : {stats.cancelled}\n\n"
        f"Confirmation Rate    : {confirmation_rate:.1f}%\n\n"
        f"Average Delay        : {stats.average_entry_delay:.0f} sec\n\n"
        f"Average Wait Before Expiry : {stats.average_wait_before_expiry:.0f} sec"
    )
