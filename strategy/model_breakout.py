from dataclasses import dataclass
from typing import Dict

import pandas as pd

from strategy.settings import StrategySettings


@dataclass(frozen=True)
class ModelDecision:
    passed: bool
    details: Dict[str, object]


def evaluate_breakout(df: pd.DataFrame, idx: int, direction: str, settings: StrategySettings) -> ModelDecision:
    cfg = settings.strategy["breakout"]
    if not bool(cfg["enabled"]):
        return ModelDecision(False, {"enabled": False})

    lookback = int(cfg["lookback_bars"])
    if idx < lookback:
        return ModelDecision(False, {"enough_history": False})

    prior = df.iloc[idx - lookback:idx]
    row = df.iloc[idx]
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())
    midpoint = max((prior_high + prior_low) / 2.0, 1e-9)
    consolidation_range_pct = (prior_high - prior_low) / midpoint
    compact = consolidation_range_pct <= float(cfg["max_consolidation_range_pct"])
    buffer = float(cfg["breakout_buffer_pct"])
    close = float(row["close"])
    weekly = float(row["vwap_weekly"])

    if direction == "BULL":
        breakout = close > prior_high * (1 + buffer)
        weekly_ok = close > weekly if bool(cfg["require_weekly_alignment"]) else True
        passed = compact and breakout and weekly_ok
    else:
        breakout = close < prior_low * (1 - buffer)
        weekly_ok = close < weekly if bool(cfg["require_weekly_alignment"]) else True
        passed = compact and breakout and weekly_ok

    return ModelDecision(passed, {
        "enough_history": True,
        "compact": compact,
        "breakout": breakout,
        "weekly": weekly_ok,
        "range_pct": round(consolidation_range_pct, 5),
        "prior_high": prior_high,
        "prior_low": prior_low,
    })
