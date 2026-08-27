from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import pandas as pd

from strategy.market_state import MarketStateResult


@dataclass(frozen=True)
class OpeningProtectionResult:
    in_opening_window: bool
    observe_mode: bool
    passed: bool
    exceptional: bool
    reason: str
    reasons: List[str]


def evaluate_opening_protection(
    hhmm: str,
    direction: str,
    row5: pd.Series,
    row15: pd.Series,
    market_state: MarketStateResult,
    quality_score: float,
    settings: Any,
) -> OpeningProtectionResult:
    cfg = settings.strategy.get("opening_protection", {})
    enabled = bool(cfg.get("enabled", True))
    observe_mode = bool(cfg.get("observe_mode", True))
    start = str(cfg.get("start", "09:15"))
    end = str(cfg.get("end", "09:30"))
    in_window = enabled and start <= hhmm < end

    if not in_window:
        return OpeningProtectionResult(False, observe_mode, True, False, "OUTSIDE_OPENING_WINDOW", [])

    min_quality = float(cfg.get("min_quality_score", 75.0))
    min_rvol = float(cfg.get("min_rvol", 1.3))
    min_adx = float(cfg.get("min_adx_15m", 25.0))
    exceptional_quality = float(cfg.get("exceptional_quality_score", 90.0))
    exceptional_adx = float(cfg.get("exceptional_adx_15m", 30.0))

    direction = str(direction or "").upper()
    rvol = float(row5["rvol"])
    adx15 = float(row15["adx_15m"])
    aligned_states = {"BULL", "STRONG_BULL"} if direction == "BULL" else {"BEAR", "STRONG_BEAR"}
    strong_state = "STRONG_BULL" if direction == "BULL" else "STRONG_BEAR"

    reasons: List[str] = []
    quality_ok = quality_score >= min_quality
    rvol_ok = rvol >= min_rvol
    adx_ok = adx15 >= min_adx
    context_ok = market_state.state in aligned_states
    exceptional = (
        market_state.state == strong_state
        and quality_score >= exceptional_quality
        and adx15 >= exceptional_adx
    )

    if quality_ok:
        reasons.append("OPENING_QUALITY_OK")
    if rvol_ok:
        reasons.append("OPENING_RVOL_OK")
    if adx_ok:
        reasons.append("OPENING_ADX_OK")
    if context_ok:
        reasons.append("OPENING_CONTEXT_OK")
    if exceptional:
        reasons.append("EXCEPTIONAL_OPENING_TREND")

    passed = exceptional or all((quality_ok, rvol_ok, adx_ok, context_ok))
    reason = "OPENING_CONFIRMED" if passed else "OPENING_WEAK_CONFIRMATION"
    return OpeningProtectionResult(in_window, observe_mode, passed, exceptional, reason, reasons)
