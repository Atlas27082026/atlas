from pathlib import Path

from core.journal import CsvJournal


FIELDS = [
    "timestamp", "symbol", "direction", "model", "market_state", "quality_score",
    "macro", "rvol", "entry_trigger", "risk", "decision", "reason",
]


class DecisionAuditWriter:
    def __init__(self, path: Path):
        self.journal = CsvJournal(path, FIELDS)

    def append(self, result, risk: str, decision: str, reason: str) -> None:
        metrics = result.metrics or {}
        self.journal.append({
            "symbol": result.symbol,
            "direction": result.direction or "",
            "model": result.model or "",
            "market_state": metrics.get("market_state", ""),
            "quality_score": metrics.get("trade_quality_score", ""),
            "macro": metrics.get("macro_ok", ""),
            "rvol": metrics.get("rvol_ok", ""),
            "entry_trigger": metrics.get("entry_trigger_ok", ""),
            "risk": risk,
            "decision": decision,
            "reason": reason,
        })
