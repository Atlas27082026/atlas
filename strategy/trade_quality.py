from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from strategy.market_state import MarketStateResult


DEFAULT_WEIGHTS = {
    "trend": 30,
    "momentum": 25,
    "structure": 20,
    "volume": 15,
    "market_context": 10,
}


@dataclass(frozen=True)
class TradeQualityScore:
    score: float
    components: Dict[str, float]
    reasons: List[str]


def _weights(settings: Any) -> Dict[str, float]:
    configured = settings.strategy.get("trade_quality", {}).get("weights", {})
    return {
        key: float(configured.get(key, value))
        for key, value in DEFAULT_WEIGHTS.items()
    }


def evaluate_trade_quality(
    row5: pd.Series,
    row15: pd.Series,
    direction: str,
    model: str,
    entry_trigger: bool,
    market_state: MarketStateResult,
    settings: Any,
    rvol_required: float,
) -> TradeQualityScore:
    weights = _weights(settings)
    direction = str(direction or "").upper()
    total = max(sum(weights.values()), 1.0)
    components: Dict[str, float] = {}
    reasons: List[str] = []

    close5 = float(row5["close"])
    close15 = float(row15["close"])
    ema5 = float(row5["ema_5m"])
    ema15 = float(row15["ema_15m"])
    rsi5 = float(row5["rsi_5m"])
    rsi15 = float(row15["rsi_15m"])
    roc5 = float(row5["roc_5m"])
    roc15 = float(row15["roc_15m"])
    adx15 = float(row15["adx_15m"])
    rvol = float(row5["rvol"])
    vwap_weekly = float(row5["vwap_weekly"])
    st_direction = str(row5["st_direction"])

    if direction == "BULL":
        trend_ok = close15 > ema15 and close5 > ema5 and st_direction == "BULL" and close5 > vwap_weekly
        momentum_ok = rsi15 > 50 and rsi5 >= 55 and roc15 > 0 and roc5 > 0
        context_ok = market_state.state in {"BULL", "STRONG_BULL"}
    elif direction == "BEAR":
        trend_ok = close15 < ema15 and close5 < ema5 and st_direction == "BEAR" and close5 < vwap_weekly
        momentum_ok = rsi15 < 50 and rsi5 <= 45 and roc15 < 0 and roc5 < 0
        context_ok = market_state.state in {"BEAR", "STRONG_BEAR"}
    else:
        trend_ok = momentum_ok = context_ok = False

    trend_factor = 1.0 if trend_ok else 0.5 if adx15 >= 22 else 0.0
    components["trend"] = weights["trend"] * trend_factor
    if trend_ok:
        reasons.append("TREND_ALIGNED")

    momentum_factor = 1.0 if momentum_ok else 0.0
    components["momentum"] = weights["momentum"] * momentum_factor
    if momentum_ok:
        reasons.append("MOMENTUM_ALIGNED")

    structure_factor = 1.0 if entry_trigger and model else 0.0
    components["structure"] = weights["structure"] * structure_factor
    if structure_factor:
        reasons.append(f"MODEL_{model}")

    volume_factor = min(max(rvol / max(float(rvol_required), 1e-9), 0.0), 1.0)
    components["volume"] = weights["volume"] * volume_factor
    if volume_factor >= 1.0:
        reasons.append("RVOL_CONFIRMED")

    context_factor = 1.0 if context_ok else 0.5 if market_state.state == "SIDEWAYS" else 0.0
    components["market_context"] = weights["market_context"] * context_factor
    if context_ok:
        reasons.append("MARKET_STATE_ALIGNED")
    elif market_state.state == "SIDEWAYS":
        reasons.append("MARKET_STATE_SIDEWAYS")
    else:
        reasons.append("MARKET_STATE_AGAINST")

    score = round((sum(components.values()) / total) * 100.0, 1)
    return TradeQualityScore(score=score, components=components, reasons=reasons)
