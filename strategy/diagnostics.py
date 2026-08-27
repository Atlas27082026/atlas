from pathlib import Path
from typing import Iterable

from core.journal import CsvJournal
from strategy.signal_engine import SignalResult


FIELDS = [
    "timestamp", "symbol", "candle_time", "direction", "decision", "score_pct", "model", "blockers",
    "close_5m", "ema_5m", "rsi_5m", "roc_5m", "rvol", "rvol_required",
    "vwap_session", "vwap_weekly", "supertrend", "st_direction",
    "close_15m", "ema_15m", "rsi_15m", "roc_15m", "adx_15m",
    "bull_direction_ok", "bear_direction_ok", "macro_ok", "rvol_ok", "entry_trigger_ok",
    "vwap_bull", "vwap_bear", "breakout_bull", "breakout_bear",
    "trend_continuation_bull", "trend_continuation_bear",
    "market_state", "market_bull_confidence", "market_bear_confidence", "market_state_reasons",
    "trade_quality_score", "trade_quality_reasons", "trade_quality_trend", "trade_quality_momentum",
    "trade_quality_structure", "trade_quality_volume", "trade_quality_market_context",
]


class DiagnosticsWriter:
    def __init__(self, path: Path):
        self.journal = CsvJournal(path, FIELDS)

    def append(self, result: SignalResult) -> None:
        row = {
            "symbol": result.symbol,
            "candle_time": result.candle_time,
            "direction": result.direction or "",
            "decision": result.decision,
            "score_pct": result.score_pct,
            "model": result.model or "",
            "blockers": "|".join(result.blockers),
        }
        row.update(result.metrics)
        self.journal.append(row)
