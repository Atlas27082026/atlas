from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from strategy.indicators import latest_completed_candle
from strategy.model_breakout import evaluate_breakout
from strategy.model_vwap import evaluate_vwap_pullback
from strategy.model_trend_continuation import evaluate_trend_continuation
from strategy.market_state import evaluate_market_state
from strategy.trade_quality import evaluate_trade_quality
from strategy.settings import StrategySettings


@dataclass(frozen=True)
class SignalResult:
    symbol: str
    candle_time: str
    direction: Optional[str]
    decision: str
    score_pct: float
    model: Optional[str]
    blockers: List[str]
    metrics: Dict[str, object]


class SignalEngine:
    def __init__(self, settings: StrategySettings):
        self.settings = settings

    def evaluate(self, symbol: str, df_5m: pd.DataFrame, df_15m: pd.DataFrame, hhmm: str) -> SignalResult:
        c5 = latest_completed_candle(df_5m, 5)
        c15 = latest_completed_candle(df_15m, 15)
        row5 = c5.row
        row15 = c15.row

        required = [
            row5.get("ema_5m"), row5.get("rsi_5m"), row5.get("roc_5m"),
            row5.get("vwap_session"), row5.get("vwap_weekly"), row5.get("rvol"),
            row5.get("supertrend"), row15.get("ema_15m"), row15.get("rsi_15m"),
            row15.get("roc_15m"), row15.get("adx_15m"),
        ]
        if any(pd.isna(x) for x in required):
            return SignalResult(symbol, str(c5.timestamp), None, "NOT_READY", 0.0, None, ["INDICATORS_NOT_READY"], {})

        cfg = self.settings.strategy
        macro_cfg = cfg["macro"]
        dir_cfg = cfg["direction"]
        close5 = float(row5["close"])
        rvol_req = self.settings.rvol_minimum(hhmm)

        bull_checks = {
            "MACRO_BULL": (
                float(row15["close"]) > float(row15["ema_15m"])
                and float(row15["adx_15m"]) > float(macro_cfg["adx_min"])
                and float(row15["roc_15m"]) > 0
                and float(row15["rsi_15m"]) > float(macro_cfg["rsi_bull_min"])
            ),
            "ST_BULL": str(row5["st_direction"]) == "BULL",
            "ABOVE_WVWAP": close5 > float(row5["vwap_weekly"]),
            "RVOL": float(row5["rvol"]) >= rvol_req,
        }
        bear_checks = {
            "MACRO_BEAR": (
                float(row15["close"]) < float(row15["ema_15m"])
                and float(row15["adx_15m"]) > float(macro_cfg["adx_min"])
                and float(row15["roc_15m"]) < 0
                and float(row15["rsi_15m"]) < float(macro_cfg["rsi_bear_max"])
            ),
            "ST_BEAR": str(row5["st_direction"]) == "BEAR",
            "BELOW_WVWAP": close5 < float(row5["vwap_weekly"]),
            "RVOL": float(row5["rvol"]) >= rvol_req,
        }

        # 5m direction sanity is included inside entry trigger quality rather than being a separate hard gate.
        bull_direction_ok = (
            close5 > float(row5["ema_5m"])
            and float(row5["roc_5m"]) > 0
            and float(row5["rsi_5m"]) >= float(dir_cfg["rsi_bull_min_5m"])
        )
        bear_direction_ok = (
            close5 < float(row5["ema_5m"])
            and float(row5["roc_5m"]) < 0
            and float(row5["rsi_5m"]) <= float(dir_cfg["rsi_bear_max_5m"])
        )

        vwap_bull = evaluate_vwap_pullback(row5, "BULL", self.settings)
        vwap_bear = evaluate_vwap_pullback(row5, "BEAR", self.settings)
        bo_bull = evaluate_breakout(df_5m, c5.index, "BULL", self.settings)
        bo_bear = evaluate_breakout(df_5m, c5.index, "BEAR", self.settings)
        tc_bull = evaluate_trend_continuation(df_5m, c5.index, row15, "BULL", self.settings)
        tc_bear = evaluate_trend_continuation(df_5m, c5.index, row15, "BEAR", self.settings)
        market_state = evaluate_market_state(df_5m, c5.index, df_15m, c15.index, self.settings)

        bull_model = (
            "VWAP_PULLBACK" if vwap_bull.passed else
            "BREAKOUT_CONTINUATION" if bo_bull.passed else
            "TREND_CONTINUATION" if tc_bull.passed else None
        )
        bear_model = (
            "VWAP_PULLBACK" if vwap_bear.passed else
            "BREAKOUT_CONTINUATION" if bo_bear.passed else
            "TREND_CONTINUATION" if tc_bear.passed else None
        )
        bull_checks["ENTRY_TRIGGER"] = bool(bull_model and bull_direction_ok)
        bear_checks["ENTRY_TRIGGER"] = bool(bear_model and bear_direction_ok)

        weights = cfg["scoring"]["weights"]
        # Map configured weights to current five decision families.
        def score(checks: Dict[str, bool], direction: str) -> float:
            macro_key = f"MACRO_{direction}"
            st_key = f"ST_{direction}"
            weekly_key = "ABOVE_WVWAP" if direction == "BULL" else "BELOW_WVWAP"
            points = 0.0
            points += float(weights["macro"]) if checks[macro_key] else 0.0
            points += float(weights["supertrend"]) if checks[st_key] else 0.0
            points += float(weights["weekly_vwap"]) if checks[weekly_key] else 0.0
            points += float(weights["rvol"]) if checks["RVOL"] else 0.0
            points += float(weights["entry_trigger"]) if checks["ENTRY_TRIGGER"] else 0.0
            return points

        bull_score = score(bull_checks, "BULL")
        bear_score = score(bear_checks, "BEAR")
        bull_pass = all(bull_checks.values())
        bear_pass = all(bear_checks.values())

        if bull_pass and not bear_pass:
            direction, decision, model, blockers, final_score = "BULL", "SIGNAL", bull_model, [], bull_score
        elif bear_pass and not bull_pass:
            direction, decision, model, blockers, final_score = "BEAR", "SIGNAL", bear_model, [], bear_score
        else:
            if bull_score >= bear_score:
                direction, final_score = "BULL", bull_score
                blockers = [k for k, v in bull_checks.items() if not v]
            else:
                direction, final_score = "BEAR", bear_score
                blockers = [k for k, v in bear_checks.items() if not v]
            near = float(cfg["scoring"]["near_signal_pct"])
            decision = "NEAR" if final_score >= near else "NO_SIGNAL"
            model = bull_model if direction == "BULL" else bear_model

        entry_trigger = bull_checks["ENTRY_TRIGGER"] if direction == "BULL" else bear_checks["ENTRY_TRIGGER"]
        quality = evaluate_trade_quality(
            row5, row15, direction, model or "", entry_trigger, market_state, self.settings, rvol_req
        )

        metrics = {
            "close_5m": round(close5, 4),
            "ema_5m": round(float(row5["ema_5m"]), 4),
            "rsi_5m": round(float(row5["rsi_5m"]), 2),
            "roc_5m": round(float(row5["roc_5m"]), 3),
            "rvol": round(float(row5["rvol"]), 3),
            "rvol_required": round(float(rvol_req), 3),
            "vwap_session": round(float(row5["vwap_session"]), 4),
            "vwap_weekly": round(float(row5["vwap_weekly"]), 4),
            "supertrend": round(float(row5["supertrend"]), 4),
            "st_direction": str(row5["st_direction"]),
            "close_15m": round(float(row15["close"]), 4),
            "ema_15m": round(float(row15["ema_15m"]), 4),
            "rsi_15m": round(float(row15["rsi_15m"]), 2),
            "roc_15m": round(float(row15["roc_15m"]), 3),
            "adx_15m": round(float(row15["adx_15m"]), 2),
            "bull_direction_ok": bull_direction_ok,
            "bear_direction_ok": bear_direction_ok,
            "vwap_bull": vwap_bull.passed,
            "vwap_bear": vwap_bear.passed,
            "breakout_bull": bo_bull.passed,
            "breakout_bear": bo_bear.passed,
            "trend_continuation_bull": tc_bull.passed,
            "trend_continuation_bear": tc_bear.passed,
            "market_state": market_state.state,
            "market_bull_confidence": market_state.bull_confidence,
            "market_bear_confidence": market_state.bear_confidence,
            "market_state_reasons": "|".join(market_state.reasons),
            "trade_quality_score": quality.score,
            "trade_quality_reasons": "|".join(quality.reasons),
            "trade_quality_trend": round(quality.components.get("trend", 0.0), 3),
            "trade_quality_momentum": round(quality.components.get("momentum", 0.0), 3),
            "trade_quality_structure": round(quality.components.get("structure", 0.0), 3),
            "trade_quality_volume": round(quality.components.get("volume", 0.0), 3),
            "trade_quality_market_context": round(quality.components.get("market_context", 0.0), 3),
            "entry_trigger_bull_details": {
                "direction_ok": bull_direction_ok,
                "vwap": vwap_bull.details,
                "breakout": bo_bull.details,
                "trend_continuation": tc_bull.details,
            },
            "entry_trigger_bear_details": {
                "direction_ok": bear_direction_ok,
                "vwap": vwap_bear.details,
                "breakout": bo_bear.details,
                "trend_continuation": tc_bear.details,
            },
        }
        return SignalResult(symbol, str(c5.timestamp), direction, decision, final_score, model, blockers, metrics)
