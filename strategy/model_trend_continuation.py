from dataclasses import dataclass
from typing import Dict

import pandas as pd

from strategy.settings import StrategySettings


@dataclass(frozen=True)
class ModelDecision:
    passed: bool
    details: Dict[str, object]


def evaluate_trend_continuation(
    df: pd.DataFrame,
    idx: int,
    row15: pd.Series,
    direction: str,
    settings: StrategySettings,
) -> ModelDecision:
    """Conservative early-entry model for already-established trends.

    This model is intentionally stricter than a generic trend filter.  It is meant
    to rescue high-quality 80% setups that fail only ENTRY_TRIGGER without chasing
    an exhausted move.  It requires strong 15m trend strength, a directional 5m
    candle, minimum candle-body quality, recent directional progress, and a cap on
    ATR extension from the 5m EMA.
    """
    cfg = settings.strategy.get("trend_continuation", {})
    if not bool(cfg.get("enabled", False)):
        return ModelDecision(False, {"enabled": False})
    if idx < int(cfg.get("lookback_bars", 3)):
        return ModelDecision(False, {"enabled": True, "enough_history": False})

    row = df.iloc[idx]
    lookback = int(cfg.get("lookback_bars", 3))
    prior = df.iloc[idx - lookback:idx]

    close = float(row["close"])
    open_ = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    ema = float(row["ema_5m"])
    atr = float(row.get("atr", 0.0) or 0.0)
    rsi = float(row["rsi_5m"])
    roc = float(row["roc_5m"])
    adx15 = float(row15["adx_15m"])
    span = max(high - low, 1e-9)
    body = abs(close - open_)
    body_atr = body / atr if atr > 0 else 0.0
    extension_atr = abs(close - ema) / atr if atr > 0 else 999.0

    adx_ok = adx15 >= float(cfg.get("min_adx_15m", 25.0))
    body_ok = body_atr >= float(cfg.get("min_body_atr", 0.20))
    extension_ok = extension_atr <= float(cfg.get("max_ema_extension_atr", 1.75))
    close_location = ((close - low) / span) if direction == "BULL" else ((high - close) / span)
    close_location_ok = close_location >= float(cfg.get("min_close_location", 0.60))

    if direction == "BULL":
        direction_ok = close > open_ and close > ema and roc > 0
        rsi_ok = float(cfg.get("rsi_bull_min", 55.0)) <= rsi <= float(cfg.get("rsi_bull_max", 74.0))
        progress = close > float(prior["close"].iloc[-1]) and low >= float(prior["low"].min())
        structure = close >= float(prior["close"].max())
    else:
        direction_ok = close < open_ and close < ema and roc < 0
        rsi_ok = float(cfg.get("rsi_bear_min", 26.0)) <= rsi <= float(cfg.get("rsi_bear_max", 45.0))
        progress = close < float(prior["close"].iloc[-1]) and high <= float(prior["high"].max())
        structure = close <= float(prior["close"].min())

    passed = all((adx_ok, direction_ok, rsi_ok, body_ok, extension_ok, close_location_ok, progress, structure))
    return ModelDecision(passed, {
        "enabled": True,
        "enough_history": True,
        "adx": adx_ok,
        "direction": direction_ok,
        "rsi_band": rsi_ok,
        "body_atr": body_ok,
        "extension": extension_ok,
        "close_location": close_location_ok,
        "progress": progress,
        "structure": structure,
        "body_atr_value": round(body_atr, 3),
        "extension_atr_value": round(extension_atr, 3),
        "close_location_value": round(close_location, 3),
        "adx_15m_value": round(adx15, 2),
        "rsi_5m_value": round(rsi, 2),
    })
