from dataclasses import dataclass
from typing import Dict

import pandas as pd

from strategy.settings import StrategySettings


@dataclass(frozen=True)
class ModelDecision:
    passed: bool
    details: Dict[str, object]


def evaluate_vwap_pullback(row: pd.Series, direction: str, settings: StrategySettings) -> ModelDecision:
    cfg = settings.strategy["vwap_pullback"]
    if not bool(cfg["enabled"]):
        return ModelDecision(False, {"enabled": False})

    vwap = float(row["vwap_session"])
    weekly = float(row["vwap_weekly"])
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    open_ = float(row["open"])
    span = max(high - low, 1e-9)

    if direction == "BULL":
        touched = low <= vwap * (1 + float(cfg["touch_upper_pct"])) and low >= vwap * (1 - float(cfg["touch_lower_pct"]))
        weekly_ok = close > weekly if bool(cfg["require_weekly_alignment"]) else True
        body_ok = close > open_
        upper_wick_ratio = (high - max(open_, close)) / span
        wick_ok = upper_wick_ratio <= float(cfg["bullish_max_upper_wick_ratio"])
        close_ok = close > vwap
        passed = touched and weekly_ok and body_ok and wick_ok and close_ok
        return ModelDecision(passed, {
            "touch": touched, "weekly": weekly_ok, "body": body_ok,
            "wick": wick_ok, "close_vs_vwap": close_ok,
            "upper_wick_ratio": round(upper_wick_ratio, 4),
        })

    touched = high >= vwap * (1 - float(cfg["touch_upper_pct"])) and high <= vwap * (1 + float(cfg["touch_lower_pct"]))
    weekly_ok = close < weekly if bool(cfg["require_weekly_alignment"]) else True
    body_ok = close < open_
    lower_wick_ratio = (min(open_, close) - low) / span
    wick_ok = lower_wick_ratio <= float(cfg["bearish_max_lower_wick_ratio"])
    close_ok = close < vwap
    passed = touched and weekly_ok and body_ok and wick_ok and close_ok
    return ModelDecision(passed, {
        "touch": touched, "weekly": weekly_ok, "body": body_ok,
        "wick": wick_ok, "close_vs_vwap": close_ok,
        "lower_wick_ratio": round(lower_wick_ratio, 4),
    })
