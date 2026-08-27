import json
from dataclasses import dataclass, asdict, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DailyState:
    trading_date: str
    session_start_balance: float = 0.0
    daily_trade_count: int = 0
    consecutive_losses: int = 0
    traded_underlyings: List[str] = field(default_factory=list)
    last_processed_candle: Dict[str, str] = field(default_factory=dict)
    last_order_ids: List[str] = field(default_factory=list)


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load_or_create(self, session_start_balance: float) -> DailyState:
        today = date.today().isoformat()
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if payload.get("trading_date") == today:
                    return DailyState(**payload)
            except Exception:
                pass

        state = DailyState(
            trading_date=today,
            session_start_balance=float(session_start_balance),
        )
        self.save(state)
        return state

    def save(self, state: DailyState) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self, session_start_balance: float) -> DailyState:
        state = DailyState(
            trading_date=date.today().isoformat(),
            session_start_balance=float(session_start_balance),
        )
        self.save(state)
        return state
