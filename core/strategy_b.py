from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from execution.models import ContractCandidate
from strategy.market_state import evaluate_market_state


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
    setup_kind: str = "FULL_SIGNAL"
    context_15m: str = ""
    setup_5m: str = ""
    setup_score: float = 0.0
    setup_reasons: List[str] = field(default_factory=list)

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
class FifteenMinuteContext:
    state: str
    bull_confidence: float
    bear_confidence: float
    reasons: List[str]


@dataclass(frozen=True)
class MTFSetupResult:
    symbol: str
    candle_time: str
    direction: Optional[str]
    decision: str
    model: str
    score_pct: float
    context_15m: str
    blockers: List[str]
    reasons: List[str]
    metrics: Dict[str, object]


@dataclass(frozen=True)
class MTFTriggerDiagnostics:
    elapsed_seconds: int
    expires_in_seconds: int
    setup_time: str
    trigger_time: str
    delay_seconds: int
    close_1m: float
    previous_high_1m: float
    previous_low_1m: float
    vwap_1m: float
    atr_1m: float
    momentum_1m: float
    volume_1m: float
    previous_volume_1m: float
    rvol_1m: float
    distance_from_trigger: float
    extension_atr: float
    allowed_extension_atr: float
    direction: bool
    break_trigger: bool
    momentum: bool
    vwap: bool
    context: bool
    extension: bool
    stale_data: bool
    action: str


@dataclass(frozen=True)
class MTFTriggerResult:
    triggered: bool
    cancel: bool
    reason: str
    close_price: float = 0.0
    atr_1m: float = 0.0
    diagnostics: Optional[MTFTriggerDiagnostics] = None


@dataclass(frozen=True)
class StrategyStats:
    signals: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    pending_setups: int = 0
    triggers: int = 0
    executed: int = 0
    expired: int = 0
    cancelled: int = 0
    invalidated: int = 0
    target1_hits: int = 0
    stop_exits: int = 0
    other_exits: int = 0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
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


def _latest_completed_index(df: pd.DataFrame, timeframe_minutes: int) -> Tuple[int, pd.Timestamp]:
    if len(df) < 2:
        raise ValueError("Need at least two candles")
    last_ts = _timestamp(df.iloc[-1]["datetime_parsed"])
    now = pd.Timestamp.now(tz=last_ts.tzinfo)
    age_seconds = (now - last_ts).total_seconds()
    idx = len(df) - 2 if age_seconds < timeframe_minutes * 60 else len(df) - 1
    return idx, _timestamp(df.iloc[idx]["datetime_parsed"])


def _mtf_config(settings) -> Dict[str, object]:
    return settings.strategy.get("strategy_b_mtf", {})


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


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
            setup_kind=str(getattr(result, "decision", "") or "FULL_SIGNAL"),
            context_15m=str(getattr(result, "context_15m", "") or result.metrics.get("context_15m", "")),
            setup_5m=str(getattr(result, "decision", "") or ""),
            setup_score=float(getattr(result, "score_pct", 0.0) or 0.0),
            setup_reasons=list(getattr(result, "reasons", []) or []),
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


def evaluate_15m_context(
    df_5m: pd.DataFrame,
    idx_5m: int,
    df_15m: pd.DataFrame,
    idx_15m: int,
    settings,
) -> FifteenMinuteContext:
    state = evaluate_market_state(df_5m, idx_5m, df_15m, idx_15m, settings)
    row15 = df_15m.iloc[idx_15m]
    close15 = _safe_float(row15["close"])
    ema15 = _safe_float(row15["ema_15m"])
    rsi15 = _safe_float(row15["rsi_15m"])
    roc15 = _safe_float(row15["roc_15m"])
    adx15 = _safe_float(row15["adx_15m"])
    cfg = settings.strategy.get("macro", {})
    adx_min = float(cfg.get("adx_min", 22.0))
    bull_rsi = float(cfg.get("rsi_bull_min", 50.0))
    bear_rsi = float(cfg.get("rsi_bear_max", 50.0))
    bull = close15 > ema15 and roc15 > 0 and rsi15 > bull_rsi and adx15 >= adx_min
    bear = close15 < ema15 and roc15 < 0 and rsi15 < bear_rsi and adx15 >= adx_min
    if bull and not bear:
        context = "BULL"
    elif bear and not bull:
        context = "BEAR"
    else:
        context = "NEUTRAL"
    reasons = list(state.reasons)
    reasons.append(f"15M_{context}")
    return FifteenMinuteContext(context, state.bull_confidence, state.bear_confidence, reasons)


def evaluate_mtf_setup(
    symbol: str,
    df_5m: pd.DataFrame,
    df_15m: pd.DataFrame,
    hhmm: str,
    settings,
) -> MTFSetupResult:
    idx5, ts5 = _latest_completed_index(df_5m, 5)
    idx15, _ = _latest_completed_index(df_15m, 15)
    row5 = df_5m.iloc[idx5]
    row15 = df_15m.iloc[idx15]
    required = [
        row5.get("ema_5m"), row5.get("rsi_5m"), row5.get("roc_5m"),
        row5.get("vwap_session"), row5.get("vwap_weekly"), row5.get("rvol"),
        row5.get("st_direction"), row15.get("ema_15m"), row15.get("rsi_15m"),
        row15.get("roc_15m"), row15.get("adx_15m"),
    ]
    if any(pd.isna(x) for x in required):
        return MTFSetupResult(symbol, str(ts5), None, "NONE", "MTF_SETUP", 0.0, "NEUTRAL", ["INDICATORS_NOT_READY"], [], {})

    cfg = _mtf_config(settings)
    rvol_req = float(cfg.get("setup_rvol_min", 0.90))
    bull_rsi = float(cfg.get("setup_rsi_bull_min_5m", 52.0))
    bear_rsi = float(cfg.get("setup_rsi_bear_max_5m", 48.0))
    min_score = float(cfg.get("setup_min_score", 70.0))
    require_vwap = bool(cfg.get("setup_require_session_vwap", True))
    require_supertrend = bool(cfg.get("setup_require_supertrend", False))

    context = evaluate_15m_context(df_5m, idx5, df_15m, idx15, settings)
    close5 = _safe_float(row5["close"])
    ema5 = _safe_float(row5["ema_5m"])
    rsi5 = _safe_float(row5["rsi_5m"])
    roc5 = _safe_float(row5["roc_5m"])
    rvol5 = _safe_float(row5["rvol"])
    vwap5 = _safe_float(row5["vwap_session"])
    weekly_vwap = _safe_float(row5["vwap_weekly"])
    st_direction = str(row5["st_direction"])

    bull_checks = {
        "CONTEXT": context.state in {"BULL", "NEUTRAL"},
        "EMA": close5 > ema5,
        "MOMENTUM": roc5 > 0 and rsi5 >= bull_rsi,
        "VWAP": close5 > vwap5 if require_vwap else True,
        "RVOL": rvol5 >= rvol_req,
        "SUPERTREND": st_direction == "BULL" if require_supertrend else True,
    }
    bear_checks = {
        "CONTEXT": context.state in {"BEAR", "NEUTRAL"},
        "EMA": close5 < ema5,
        "MOMENTUM": roc5 < 0 and rsi5 <= bear_rsi,
        "VWAP": close5 < vwap5 if require_vwap else True,
        "RVOL": rvol5 >= rvol_req,
        "SUPERTREND": st_direction == "BEAR" if require_supertrend else True,
    }
    weights = {
        "CONTEXT": 25.0,
        "EMA": 20.0,
        "MOMENTUM": 20.0,
        "VWAP": 15.0,
        "RVOL": 10.0,
        "SUPERTREND": 10.0,
    }

    def score(checks: Dict[str, bool]) -> float:
        return sum(weight for key, weight in weights.items() if checks.get(key, False))

    bull_score = score(bull_checks)
    bear_score = score(bear_checks)
    if bull_score >= bear_score:
        direction = "BULL"
        checks = bull_checks
        score_pct = bull_score
        setup_name = "BULL_SETUP"
    else:
        direction = "BEAR"
        checks = bear_checks
        score_pct = bear_score
        setup_name = "BEAR_SETUP"

    blockers = [key for key, passed in checks.items() if not passed]
    decision = setup_name if score_pct >= min_score and not blockers else "NONE"
    reasons = [key for key, passed in checks.items() if passed]
    metrics = {
        "close_5m": round(close5, 4),
        "ema_5m": round(ema5, 4),
        "rsi_5m": round(rsi5, 2),
        "roc_5m": round(roc5, 3),
        "rvol": round(rvol5, 3),
        "rvol_required": round(rvol_req, 3),
        "vwap_session": round(vwap5, 4),
        "vwap_weekly": round(weekly_vwap, 4),
        "st_direction": st_direction,
        "close_15m": round(_safe_float(row15["close"]), 4),
        "ema_15m": round(_safe_float(row15["ema_15m"]), 4),
        "rsi_15m": round(_safe_float(row15["rsi_15m"]), 2),
        "roc_15m": round(_safe_float(row15["roc_15m"]), 3),
        "adx_15m": round(_safe_float(row15["adx_15m"]), 2),
        "context_15m": context.state,
        "context_bull_confidence": context.bull_confidence,
        "context_bear_confidence": context.bear_confidence,
        "context_reasons": "|".join(context.reasons),
        "setup_checks": checks,
        "setup_score": score_pct,
    }
    return MTFSetupResult(
        symbol=symbol,
        candle_time=str(ts5),
        direction=direction if decision != "NONE" else None,
        decision=decision,
        model="MTF_SETUP",
        score_pct=score_pct,
        context_15m=context.state,
        blockers=blockers,
        reasons=reasons,
        metrics=metrics,
    )


def _setup_context_still_valid(setup: PendingSetup, df_5m: pd.DataFrame, df_15m: pd.DataFrame, settings) -> Tuple[bool, str, str]:
    current = evaluate_mtf_setup(setup.symbol, df_5m, df_15m, _clock(_now()), settings)
    if current.decision == "NONE":
        return False, "5M_CONTEXT_INVALIDATED", current.context_15m
    if current.direction != setup.direction:
        return False, "5M_DIRECTION_FLIPPED", current.context_15m
    return True, "OK", current.context_15m


def evaluate_mtf_trigger(
    setup: PendingSetup,
    df_1m: pd.DataFrame,
    max_extension_atr: float,
    df_5m: Optional[pd.DataFrame] = None,
    df_15m: Optional[pd.DataFrame] = None,
    settings=None,
) -> MTFTriggerResult:
    if len(df_1m) < 2:
        return MTFTriggerResult(False, False, "ONE_MINUTE_NOT_READY")

    context_state = setup.context_15m or ""
    if df_5m is not None and df_15m is not None and settings is not None:
        valid, reason, context_state = _setup_context_still_valid(setup, df_5m, df_15m, settings)
        if not valid:
            return MTFTriggerResult(False, True, reason)

    out = df_1m.copy()
    out["datetime_parsed"] = _timestamp_series(out["datetime_parsed"])
    out["vwap_1m"] = _session_vwap(out)
    idx = len(out) - 1
    row = out.iloc[idx]
    prev = out.iloc[idx - 1]
    close = _safe_float(row["close"])
    open_ = _safe_float(row["open"])
    prev_high = _safe_float(prev["high"])
    prev_low = _safe_float(prev["low"])
    atr = _atr_1m(out, idx)
    allowed_extension_atr = float(max_extension_atr)
    volume = _safe_float(row["volume"])
    prev_volume = _safe_float(prev["volume"])
    rvol = volume / prev_volume if prev_volume > 0 else 0.0
    current_time = _timestamp(row["datetime_parsed"]).to_pydatetime()
    created = _parse_dt(setup.created_at) or current_time
    expires = _parse_dt(setup.expires_at) or current_time

    if setup.direction == "BULL":
        direction_ok = close > open_
        break_trigger = close > prev_high
        momentum_ok = close > open_
        vwap_ok = close > _safe_float(row["vwap_1m"])
        context_ok = context_state not in {"BEAR", "STRONG_BEAR"}
        distance_from_trigger = close - prev_high
    else:
        direction_ok = close < open_
        break_trigger = close < prev_low
        momentum_ok = close < open_
        vwap_ok = close < _safe_float(row["vwap_1m"])
        context_ok = context_state not in {"BULL", "STRONG_BULL"}
        distance_from_trigger = prev_low - close

    extension_atr = distance_from_trigger / atr if atr > 0 else 0.0
    extension_ok = atr <= 0 or extension_atr <= allowed_extension_atr
    stale_ok = volume > 0
    triggered = all([direction_ok, break_trigger, momentum_ok, vwap_ok, context_ok, extension_ok, stale_ok])
    reason = "PREV_1M_HIGH_BREAK" if setup.direction == "BULL" else "PREV_1M_LOW_BREAK"
    if not context_ok:
        reason = "15M_CONTEXT_CONTRADICTORY"
    elif not extension_ok:
        reason = "ENTRY_EXTENSION_EXCEEDED"
    elif not triggered:
        reason = "WAITING_FOR_1M_TRIGGER"

    diagnostics = MTFTriggerDiagnostics(
        elapsed_seconds=max(0, int((current_time - created).total_seconds())),
        expires_in_seconds=max(0, int((expires - current_time).total_seconds())),
        setup_time=_clock(setup.created_at),
        trigger_time=current_time.strftime("%H:%M:%S"),
        delay_seconds=max(0, int((current_time - created).total_seconds())),
        close_1m=close,
        previous_high_1m=prev_high,
        previous_low_1m=prev_low,
        vwap_1m=_safe_float(row["vwap_1m"]),
        atr_1m=atr,
        momentum_1m=close - open_,
        volume_1m=volume,
        previous_volume_1m=prev_volume,
        rvol_1m=rvol,
        distance_from_trigger=distance_from_trigger,
        extension_atr=extension_atr,
        allowed_extension_atr=allowed_extension_atr,
        direction=direction_ok,
        break_trigger=break_trigger,
        momentum=momentum_ok,
        vwap=vwap_ok,
        context=context_ok,
        extension=extension_ok,
        stale_data=stale_ok,
        action="TRIGGER" if triggered else "CANCEL" if not context_ok else "WAIT",
    )
    return MTFTriggerResult(triggered, not context_ok, reason, close, atr, diagnostics)


def format_mtf_check(setup: PendingSetup, result: MTFTriggerResult) -> str:
    d = result.diagnostics
    direction = setup.direction or "NONE"
    if d is None:
        return f"[B] MTF CHECK | {setup.symbol} | {direction} | action=WAIT | reason={result.reason}"
    return (
        f"[B] MTF CHECK | {setup.symbol} | {direction} | "
        f"15m_context={setup.context_15m or 'N/A'} | "
        f"5m_setup={setup.setup_5m or setup.setup_kind} | "
        f"1m_direction={_pass_fail(d.direction)} | "
        f"1m_break={_pass_fail(d.break_trigger)} | "
        f"1m_momentum={_pass_fail(d.momentum)} | "
        f"1m_vwap={_pass_fail(d.vwap)} | "
        f"extension={d.extension_atr:.2f}ATR | "
        f"allowed_extension={d.allowed_extension_atr:.2f}ATR | "
        f"close_1m={d.close_1m:.2f} | prev_high_1m={d.previous_high_1m:.2f} | "
        f"prev_low_1m={d.previous_low_1m:.2f} | vwap_1m={d.vwap_1m:.2f} | "
        f"atr_1m={d.atr_1m:.2f} | action={d.action} | reason={result.reason}"
    )


def format_mtf_trigger(setup: PendingSetup, result: MTFTriggerResult) -> str:
    d = result.diagnostics
    if d is None:
        return f"[B] MTF TRIGGER | {setup.symbol} | {setup.direction} | reason={result.reason}"
    return (
        f"[B] MTF TRIGGER | {setup.symbol} | {setup.direction} | "
        f"setup_time={d.setup_time} | trigger_time={d.trigger_time} | "
        f"delay_seconds={d.delay_seconds} | trigger_price={d.close_1m:.2f} | "
        f"reason={result.reason} | extension={d.extension_atr:.2f}ATR"
    )


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
    target1_hits = sum(1 for p in positions if getattr(p, "target1_hit", False))
    stop_exits = sum(1 for p in closed if str(getattr(p, "exit_reason", "")).upper() == "STOP")
    other_exits = sum(1 for p in closed if str(getattr(p, "exit_reason", "")).upper() not in {"", "STOP"})
    avg_winner = gross_profit / wins if wins else 0.0
    avg_loser = -(gross_loss / losses) if losses else 0.0

    if pending_store is None:
        return StrategyStats(
            signals=trades,
            trades=trades,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 2),
            net_pnl=round(net_pnl, 2),
            profit_factor=round(profit_factor, 4),
            expectancy=round(expectancy, 2),
            target1_hits=target1_hits,
            stop_exits=stop_exits,
            other_exits=other_exits,
            avg_winner=round(avg_winner, 2),
            avg_loser=round(avg_loser, 2),
        )

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
        triggers=len(executed),
        executed=len(executed),
        expired=len(expired),
        cancelled=sum(1 for s in setups if s.status == "CANCELLED"),
        invalidated=sum(1 for s in setups if s.status == "CANCELLED" and "INVALID" in str(s.exit_reason).upper()),
        target1_hits=target1_hits,
        stop_exits=stop_exits,
        other_exits=other_exits,
        avg_winner=round(avg_winner, 2),
        avg_loser=round(avg_loser, 2),
        average_entry_delay=round(sum(delays) / len(delays), 2) if delays else 0.0,
        average_entry_improvement=round(sum(improvements) / len(improvements), 4) if improvements else 0.0,
        average_wait_before_expiry=round(sum(expiry_waits) / len(expiry_waits), 2) if expiry_waits else 0.0,
    )


def comparison_report(strategy_a: StrategyStats, strategy_b: StrategyStats) -> str:
    return (
        "Strategy A | "
        f"Signals={strategy_a.signals} | Entries={strategy_a.trades} | Wins={strategy_a.wins} | Losses={strategy_a.losses} | "
        f"Target1={strategy_a.target1_hits} | Stops={strategy_a.stop_exits} | Other Exits={strategy_a.other_exits} | "
        f"Win Rate={strategy_a.win_rate:.2f}% | Net P&L={strategy_a.net_pnl:.2f} | "
        f"Avg Winner={strategy_a.avg_winner:.2f} | Avg Loser={strategy_a.avg_loser:.2f} | "
        f"Profit Factor={strategy_a.profit_factor:.4f} | Expectancy={strategy_a.expectancy:.2f}\n"
        "Strategy B | "
        f"Setups={strategy_b.pending_setups} | 1m Triggers={strategy_b.triggers} | Entries={strategy_b.trades} | "
        f"Expired={strategy_b.expired} | Invalidated={strategy_b.invalidated} | Cancelled={strategy_b.cancelled} | "
        f"Wins={strategy_b.wins} | Losses={strategy_b.losses} | Target1={strategy_b.target1_hits} | "
        f"Stops={strategy_b.stop_exits} | Other Exits={strategy_b.other_exits} | "
        f"Win Rate={strategy_b.win_rate:.2f}% | Net P&L={strategy_b.net_pnl:.2f} | "
        f"Avg Winner={strategy_b.avg_winner:.2f} | Avg Loser={strategy_b.avg_loser:.2f} | "
        f"Profit Factor={strategy_b.profit_factor:.4f} | Expectancy={strategy_b.expectancy:.2f} | "
        f"Average Trigger Delay={strategy_b.average_entry_delay:.2f}s | "
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
