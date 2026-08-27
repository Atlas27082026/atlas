from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from strategy.settings import StrategySettings


DEFAULT_WEIGHTS = {
    "ema_position": 15,
    "ema_slope": 15,
    "momentum": 20,
    "vwap": 15,
    "supertrend": 15,
    "adx": 10,
    "structure": 10,
}


@dataclass(frozen=True)
class MarketStateResult:
    state: str
    bull_confidence: float
    bear_confidence: float
    reasons: List[str]


def _weights(settings: StrategySettings) -> Dict[str, float]:
    configured = settings.strategy.get("market_state", {}).get("weights", {})
    return {
        key: float(configured.get(key, value))
        for key, value in DEFAULT_WEIGHTS.items()
    }


def _ema_slope(df: pd.DataFrame, idx: int, column: str, lookback: int) -> float:
    if idx < lookback:
        return 0.0
    current = float(df.iloc[idx][column])
    prior = float(df.iloc[idx - lookback][column])
    if pd.isna(current) or pd.isna(prior) or prior == 0:
        return 0.0
    return ((current - prior) / abs(prior)) * 100.0


def _structure(df: pd.DataFrame, idx: int, lookback: int) -> str:
    if idx < lookback:
        return "UNKNOWN"
    row = df.iloc[idx]
    prior = df.iloc[idx - lookback:idx]
    higher_high = float(row["high"]) > float(prior["high"].max())
    higher_low = float(row["low"]) >= float(prior["low"].min())
    lower_low = float(row["low"]) < float(prior["low"].min())
    lower_high = float(row["high"]) <= float(prior["high"].max())
    if higher_high and higher_low:
        return "BULL"
    if lower_low and lower_high:
        return "BEAR"
    return "MIXED"


def evaluate_market_state(
    df_5m: pd.DataFrame,
    idx_5m: int,
    df_15m: pd.DataFrame,
    idx_15m: int,
    settings: StrategySettings,
) -> MarketStateResult:
    cfg = settings.strategy.get("market_state", {})
    weights = _weights(settings)
    slope_lookback = int(cfg.get("ema_slope_lookback", 3))
    structure_lookback = int(cfg.get("structure_lookback", 4))
    strong_confidence = float(cfg.get("strong_confidence", 70.0))
    directional_confidence = float(cfg.get("directional_confidence", 55.0))
    sideways_band = float(cfg.get("sideways_confidence_diff", 12.0))
    strong_adx = float(cfg.get("strong_adx", 25.0))

    row5 = df_5m.iloc[idx_5m]
    row15 = df_15m.iloc[idx_15m]
    close5 = float(row5["close"])
    close15 = float(row15["close"])
    ema5 = float(row5["ema_5m"])
    ema15 = float(row15["ema_15m"])
    rsi5 = float(row5["rsi_5m"])
    rsi15 = float(row15["rsi_15m"])
    roc5 = float(row5["roc_5m"])
    roc15 = float(row15["roc_15m"])
    adx15 = float(row15["adx_15m"])
    vwap_session = float(row5["vwap_session"])
    vwap_weekly = float(row5["vwap_weekly"])
    st_direction = str(row5["st_direction"])

    bull = 0.0
    bear = 0.0
    reasons: List[str] = []

    if close15 > ema15 and close5 > ema5:
        bull += weights["ema_position"]
        reasons.append("PRICE_ABOVE_EMA")
    elif close15 < ema15 and close5 < ema5:
        bear += weights["ema_position"]
        reasons.append("PRICE_BELOW_EMA")

    slope15 = _ema_slope(df_15m, idx_15m, "ema_15m", slope_lookback)
    if slope15 > 0:
        bull += weights["ema_slope"]
        reasons.append("EMA_SLOPE_UP")
    elif slope15 < 0:
        bear += weights["ema_slope"]
        reasons.append("EMA_SLOPE_DOWN")

    if rsi15 >= 55 and rsi5 >= 55 and roc15 > 0 and roc5 > 0:
        bull += weights["momentum"]
        reasons.append("BULL_MOMENTUM")
    elif rsi15 <= 45 and rsi5 <= 45 and roc15 < 0 and roc5 < 0:
        bear += weights["momentum"]
        reasons.append("BEAR_MOMENTUM")

    if close5 > vwap_session and close5 > vwap_weekly:
        bull += weights["vwap"]
        reasons.append("ABOVE_VWAP")
    elif close5 < vwap_session and close5 < vwap_weekly:
        bear += weights["vwap"]
        reasons.append("BELOW_VWAP")

    if st_direction == "BULL":
        bull += weights["supertrend"]
        reasons.append("SUPERTREND_BULL")
    elif st_direction == "BEAR":
        bear += weights["supertrend"]
        reasons.append("SUPERTREND_BEAR")

    if adx15 >= strong_adx:
        if bull > bear:
            bull += weights["adx"]
            reasons.append("ADX_SUPPORTS_BULL")
        elif bear > bull:
            bear += weights["adx"]
            reasons.append("ADX_SUPPORTS_BEAR")

    structure = _structure(df_5m, idx_5m, structure_lookback)
    if structure == "BULL":
        bull += weights["structure"]
        reasons.append("BULL_STRUCTURE")
    elif structure == "BEAR":
        bear += weights["structure"]
        reasons.append("BEAR_STRUCTURE")

    total = max(sum(weights.values()), 1.0)
    bull_confidence = round((bull / total) * 100.0, 1)
    bear_confidence = round((bear / total) * 100.0, 1)
    diff = abs(bull_confidence - bear_confidence)

    if diff <= sideways_band or max(bull_confidence, bear_confidence) < directional_confidence:
        state = "SIDEWAYS"
    elif bull_confidence > bear_confidence:
        state = "STRONG_BULL" if bull_confidence >= strong_confidence else "BULL"
    else:
        state = "STRONG_BEAR" if bear_confidence >= strong_confidence else "BEAR"

    return MarketStateResult(state, bull_confidence, bear_confidence, reasons)
