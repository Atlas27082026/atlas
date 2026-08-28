from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from core.strategy_b import MARKET_TZ, MTFSetupResult, MTFTriggerResult, _parse_dt, _timestamp, evaluate_mtf_setup, evaluate_mtf_trigger


@dataclass(frozen=True)
class MarketRegime:
    trading_date: str
    market_gap_pct: float
    market_gap_class: str
    market_direction: str
    stock_gap_pct: float
    stock_gap_class: str
    relative_gap_pct: float
    relative_strength_class: str
    opening_behavior: str
    confidence: float
    reasons: List[str]
    regime: str
    context_15m_source: str


@dataclass(frozen=True)
class StrategyCPermission:
    allowed: bool
    reason: str
    regime_adapted: bool = False


def _cfg(settings) -> Dict[str, object]:
    return settings.strategy.get("strategy_c_regime", {})


def _pct(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else (numerator / denominator) * 100.0


def previous_trading_close(df: pd.DataFrame, trading_date: str) -> Optional[float]:
    out = df.copy()
    out["datetime_parsed"] = pd.to_datetime(out["datetime_parsed"], errors="coerce")
    out = out.dropna(subset=["datetime_parsed", "close"]).sort_values("datetime_parsed")
    prior = out[out["datetime_parsed"].dt.date < pd.Timestamp(trading_date).date()]
    if prior.empty:
        return None
    return float(prior.iloc[-1]["close"])


def trading_day_open(df: pd.DataFrame, trading_date: str) -> Optional[float]:
    out = df.copy()
    out["datetime_parsed"] = pd.to_datetime(out["datetime_parsed"], errors="coerce")
    out = out.dropna(subset=["datetime_parsed", "open"]).sort_values("datetime_parsed")
    today = out[out["datetime_parsed"].dt.date == pd.Timestamp(trading_date).date()]
    if today.empty:
        return None
    return float(today.iloc[0]["open"])


def gap_pct(today_open: float, previous_close: float) -> float:
    return round(_pct(float(today_open) - float(previous_close), float(previous_close)), 4)


def classify_gap(gap: float, settings) -> Tuple[str, str]:
    cfg = _cfg(settings)
    normal = float(cfg.get("normal_gap_pct", 0.30))
    small = float(cfg.get("small_gap_pct", 0.75))
    medium = float(cfg.get("medium_gap_pct", 1.50))
    abs_gap = abs(float(gap))
    if abs_gap < normal:
        return "NORMAL_OPEN", "NORMAL_OPEN"
    direction = "GAP_UP" if gap > 0 else "GAP_DOWN"
    if abs_gap < small:
        size = "SMALL"
    elif abs_gap < medium:
        size = "MEDIUM"
    else:
        size = "LARGE"
    return f"{size}_{direction}", direction


def classify_relative_strength(relative_gap: float, settings) -> str:
    cfg = _cfg(settings)
    weak = float(cfg.get("relative_strength_pct", 0.35))
    strong = float(cfg.get("strong_relative_strength_pct", 1.00))
    value = float(relative_gap)
    if value >= strong:
        return "STRONG_RELATIVE_STRENGTH"
    if value >= weak:
        return "RELATIVE_STRENGTH"
    if value <= -strong:
        return "STRONG_RELATIVE_WEAKNESS"
    if value <= -weak:
        return "RELATIVE_WEAKNESS"
    return "NEUTRAL"


def context_15m_source(df_15m: pd.DataFrame, trading_date: str) -> str:
    if df_15m.empty:
        return "UNKNOWN_15M"
    out = df_15m.copy()
    out["datetime_parsed"] = pd.to_datetime(out["datetime_parsed"], errors="coerce")
    out = out.dropna(subset=["datetime_parsed"]).sort_values("datetime_parsed")
    if out.empty:
        return "UNKNOWN_15M"
    latest = out.iloc[-1]["datetime_parsed"]
    return "CURRENT_SESSION_15M" if latest.date() == pd.Timestamp(trading_date).date() else "PREVIOUS_SESSION_15M"


def classify_opening_behavior(
    *,
    stock_gap_pct_value: float,
    today_open: float,
    previous_close: float,
    df_1m: pd.DataFrame,
    df_5m: pd.DataFrame,
    trading_date: str,
    settings,
) -> str:
    cfg = _cfg(settings)
    gap_fill_threshold = float(cfg.get("gap_fill_attempt_pct", 0.40))
    pullback_min = float(cfg.get("pullback_min_pct", 0.15))
    reclaim_buffer = float(cfg.get("opening_reclaim_buffer_pct", 0.02))
    day = pd.Timestamp(trading_date).date()
    one = df_1m.copy()
    five = df_5m.copy()
    one["datetime_parsed"] = pd.to_datetime(one["datetime_parsed"], errors="coerce")
    five["datetime_parsed"] = pd.to_datetime(five["datetime_parsed"], errors="coerce")
    one = one[one["datetime_parsed"].dt.date == day].sort_values("datetime_parsed")
    five = five[five["datetime_parsed"].dt.date == day].sort_values("datetime_parsed")
    if one.empty or five.empty:
        return "UNRESOLVED"

    current = float(one.iloc[-1]["close"])
    low = float(one["low"].min())
    high = float(one["high"].max())
    first5 = five.iloc[0]
    vwap = float(first5.get("vwap_session", today_open))
    gap_abs = abs(float(stock_gap_pct_value))
    move_back_to_prior = abs(gap_pct(current, previous_close)) <= max(gap_fill_threshold, gap_abs * 0.5)

    if stock_gap_pct_value > 0:
        pulled_back = gap_pct(today_open, low) >= pullback_min or low <= vwap * (1.0 + reclaim_buffer / 100.0)
        reclaimed = current > today_open or current > vwap * (1.0 + reclaim_buffer / 100.0)
        if current < min(today_open, vwap):
            return "OPENING_REVERSAL"
        if move_back_to_prior:
            return "GAP_FILL_ATTEMPT"
        if pulled_back and reclaimed:
            return "OPENING_PULLBACK"
        if high > today_open and current >= today_open:
            return "OPENING_CONTINUATION"
    elif stock_gap_pct_value < 0:
        bounced = gap_pct(high, today_open) >= pullback_min or high >= vwap * (1.0 - reclaim_buffer / 100.0)
        rejected = current < today_open or current < vwap * (1.0 - reclaim_buffer / 100.0)
        if current > max(today_open, vwap):
            return "OPENING_REVERSAL"
        if move_back_to_prior:
            return "GAP_FILL_ATTEMPT"
        if bounced and rejected:
            return "OPENING_PULLBACK"
        if low < today_open and current <= today_open:
            return "OPENING_CONTINUATION"
    return "UNRESOLVED"


def evaluate_regime(
    *,
    symbol: str,
    market_daily: pd.DataFrame,
    stock_daily: pd.DataFrame,
    stock_1m: pd.DataFrame,
    stock_5m: pd.DataFrame,
    stock_15m: pd.DataFrame,
    trading_date: str,
    settings,
) -> MarketRegime:
    market_prev = previous_trading_close(market_daily, trading_date)
    stock_prev = previous_trading_close(stock_daily, trading_date)
    market_open = trading_day_open(market_daily, trading_date)
    stock_open = trading_day_open(stock_daily, trading_date)
    if market_prev is None or stock_prev is None or market_open is None or stock_open is None:
        raise ValueError(f"{symbol}: insufficient daily data for Strategy C regime")

    market_gap = gap_pct(market_open, market_prev)
    stock_gap = gap_pct(stock_open, stock_prev)
    relative = round(stock_gap - market_gap, 4)
    market_class, market_direction = classify_gap(market_gap, settings)
    stock_class, _ = classify_gap(stock_gap, settings)
    relative_class = classify_relative_strength(relative, settings)
    opening = classify_opening_behavior(
        stock_gap_pct_value=stock_gap,
        today_open=stock_open,
        previous_close=stock_prev,
        df_1m=stock_1m,
        df_5m=stock_5m,
        trading_date=trading_date,
        settings=settings,
    )
    source = context_15m_source(stock_15m, trading_date)
    reasons = [market_class, stock_class, relative_class, opening, source]
    if "STRENGTH" in relative_class and stock_gap >= 0:
        regime = "BULLISH_RELATIVE_STRENGTH"
    elif "WEAKNESS" in relative_class and stock_gap <= 0:
        regime = "BEARISH_RELATIVE_WEAKNESS"
    elif opening == "OPENING_PULLBACK" and stock_gap > 0:
        regime = "BULLISH_PULLBACK_OPPORTUNITY"
    elif opening == "OPENING_PULLBACK" and stock_gap < 0:
        regime = "BEARISH_REJECTION_OPPORTUNITY"
    else:
        regime = market_class
    confidence = 50.0 + min(30.0, abs(relative) * 10.0) + (10.0 if opening != "UNRESOLVED" else 0.0)
    return MarketRegime(
        trading_date=trading_date,
        market_gap_pct=market_gap,
        market_gap_class=market_class,
        market_direction=market_direction,
        stock_gap_pct=stock_gap,
        stock_gap_class=stock_class,
        relative_gap_pct=relative,
        relative_strength_class=relative_class,
        opening_behavior=opening,
        confidence=round(min(confidence, 95.0), 1),
        reasons=reasons,
        regime=regime,
        context_15m_source=source,
    )


def regime_permission(regime: MarketRegime, direction: str, settings, hhmm: str = "") -> StrategyCPermission:
    cfg = _cfg(settings)
    require_pullback = bool(cfg.get("require_pullback_for_medium_large_gap_chase", True))
    in_opening = "09:15" <= str(hhmm or "09:15") < "09:45"
    large_or_medium_up = regime.market_gap_class in {"MEDIUM_GAP_UP", "LARGE_GAP_UP"} or classify_gap(regime.stock_gap_pct, settings)[0] in {"MEDIUM_GAP_UP", "LARGE_GAP_UP"}
    large_or_medium_down = regime.market_gap_class in {"MEDIUM_GAP_DOWN", "LARGE_GAP_DOWN"} or classify_gap(regime.stock_gap_pct, settings)[0] in {"MEDIUM_GAP_DOWN", "LARGE_GAP_DOWN"}
    if require_pullback and in_opening and direction == "BULL" and large_or_medium_up:
        if regime.opening_behavior != "OPENING_PULLBACK":
            return StrategyCPermission(False, "GAP_UP_LONG_CHASE")
        return StrategyCPermission(True, "GAP_UP_PULLBACK_RECLAIM", True)
    if require_pullback and in_opening and direction == "BEAR" and large_or_medium_down:
        if regime.opening_behavior != "OPENING_PULLBACK":
            return StrategyCPermission(False, "GAP_DOWN_SHORT_CHASE")
        return StrategyCPermission(True, "GAP_DOWN_BOUNCE_REJECTION", True)
    if direction == "BULL" and regime.relative_strength_class in {"STRONG_RELATIVE_WEAKNESS", "RELATIVE_WEAKNESS"}:
        return StrategyCPermission(False, "RELATIVE_WEAKNESS_BLOCK")
    if direction == "BEAR" and regime.relative_strength_class in {"STRONG_RELATIVE_STRENGTH", "RELATIVE_STRENGTH"}:
        return StrategyCPermission(False, "RELATIVE_STRENGTH_BLOCK")
    return StrategyCPermission(True, "REGIME_PERMISSION_PASS", False)


def evaluate_strategy_c_setup(symbol: str, df_5m: pd.DataFrame, df_15m: pd.DataFrame, hhmm: str, settings) -> MTFSetupResult:
    return evaluate_mtf_setup(symbol, df_5m, df_15m, hhmm, settings)


def evaluate_strategy_c_trigger(setup, df_1m: pd.DataFrame, max_extension_atr: float, df_5m=None, df_15m=None, settings=None) -> MTFTriggerResult:
    return evaluate_mtf_trigger(setup, df_1m, max_extension_atr, df_5m, df_15m, settings)


def format_regime_log(symbol: str, regime: MarketRegime) -> str:
    return (
        f"[C] REGIME | {symbol} | market={regime.market_gap_class}_{regime.market_gap_pct:+.2f}% | "
        f"stock={regime.stock_gap_class}_{regime.stock_gap_pct:+.2f}% | "
        f"relative={regime.relative_gap_pct:+.2f}% | strength={regime.relative_strength_class} | "
        f"opening={regime.opening_behavior} | regime={regime.regime} | 15m={regime.context_15m_source}"
    )


def format_strategy_c_check(symbol: str, setup: MTFSetupResult, trigger: MTFTriggerResult, permission: StrategyCPermission) -> str:
    trigger_state = "PASS" if trigger.triggered else "FAIL"
    context = getattr(setup, "context_15m", "")
    setup_decision = getattr(setup, "decision", "") or getattr(setup, "setup_5m", "") or getattr(setup, "setup_kind", "")
    return (
        f"[C] MTF CHECK | {symbol} | 15m={context} | "
        f"5m_setup={setup_decision} | 1m_trigger={trigger_state} | "
        f"regime_permission={'PASS' if permission.allowed else 'FAIL'} | "
        f"action={'ENTER' if trigger.triggered and permission.allowed else 'WAIT'} | reason={permission.reason if not permission.allowed else trigger.reason}"
    )


def strategy_c_summary_report(stats, regimes: List[MarketRegime]) -> str:
    by_gap: Dict[str, int] = {}
    by_strength: Dict[str, int] = {}
    for regime in regimes:
        by_gap[regime.market_gap_class] = by_gap.get(regime.market_gap_class, 0) + 1
        by_strength[regime.relative_strength_class] = by_strength.get(regime.relative_strength_class, 0) + 1
    return (
        "Strategy C | "
        f"Regimes={len(regimes)} | Setups={stats.pending_setups} | 1m Triggers={stats.triggers} | "
        f"Entries={stats.trades} | Wins={stats.wins} | Losses={stats.losses} | "
        f"Expired={stats.expired} | Cancelled={stats.cancelled} | Net P&L={stats.net_pnl:.2f} | "
        f"Win Rate={stats.win_rate:.2f}% | Profit Factor={stats.profit_factor:.4f} | "
        f"Expectancy={stats.expectancy:.2f} | By Gap={by_gap} | By Relative Strength={by_strength}"
    )
