from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class PaperPosition:
    trade_id: str
    underlying: str
    direction: str
    model: str
    contract_symbol: str
    security_id: str
    exchange_segment: str
    strike: float
    option_type: str
    expiry: str
    lot_size: int
    entry_price: float
    initial_quantity: int
    open_quantity: int
    stop_price: float
    target_price: float
    opened_at: str
    status: str = "OPEN"
    target1_hit: bool = False
    peak_price: float = 0.0
    realized_pnl: float = 0.0
    last_price: float = 0.0
    last_marked_at: str = ""
    closed_at: str = ""
    exit_reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in {"OPEN", "PARTIAL"} and self.open_quantity > 0

    @property
    def unrealized_pnl(self) -> float:
        if not self.is_open or self.last_price <= 0:
            return 0.0
        return (self.last_price - self.entry_price) * self.open_quantity

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl


@dataclass(frozen=True)
class PaperEvent:
    event: str
    trade_id: str
    underlying: str
    contract_symbol: str
    quantity: int
    price: float
    pnl: float
    reason: str
    timestamp: str


class PaperPositionStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> List[PaperPosition]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("positions", []) if isinstance(payload, dict) else payload
            return [PaperPosition(**row) for row in rows if isinstance(row, dict)]
        except Exception:
            return []

    def save(self, positions: Iterable[PaperPosition]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"positions": [asdict(p) for p in positions]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def open_positions(self) -> List[PaperPosition]:
        return [p for p in self.load() if p.is_open]

    def has_open_underlying(self, underlying: str) -> bool:
        key = str(underlying).upper().strip()
        return any(p.underlying.upper().strip() == key for p in self.open_positions())

    def add_from_candidate(self, underlying: str, direction: str, model: str, candidate) -> PaperPosition:
        positions = self.load()
        if any(p.is_open and p.underlying.upper() == underlying.upper() for p in positions):
            raise ValueError(f"paper position already open for {underlying}")
        now = datetime.now().isoformat(timespec="seconds")
        p = PaperPosition(
            trade_id=f"PAPER-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            underlying=underlying,
            direction=direction,
            model=model,
            contract_symbol=candidate.contract.trading_symbol,
            security_id=str(candidate.contract.security_id),
            exchange_segment=str(candidate.contract.exchange_segment or "NSE_FNO"),
            strike=float(candidate.contract.strike),
            option_type=str(candidate.contract.option_type),
            expiry=str(candidate.contract.expiry),
            lot_size=int(candidate.lot_size),
            entry_price=float(candidate.entry_limit),
            initial_quantity=int(candidate.quantity),
            open_quantity=int(candidate.quantity),
            stop_price=float(candidate.stop_price),
            target_price=float(candidate.target_price),
            opened_at=now,
            peak_price=float(candidate.entry_limit),
            last_price=float(candidate.entry_limit),
            last_marked_at=now,
        )
        positions.append(p)
        self.save(positions)
        return p

    def replace(self, updated: PaperPosition) -> None:
        positions = self.load()
        out = []
        found = False
        for p in positions:
            if p.trade_id == updated.trade_id:
                out.append(updated)
                found = True
            else:
                out.append(p)
        if not found:
            out.append(updated)
        self.save(out)

    def strategy_pnl(self) -> float:
        return float(sum(p.total_pnl for p in self.load()))

    def closed_trades_today(self) -> int:
        today = datetime.now().date().isoformat()
        return sum(1 for p in self.load() if p.opened_at.startswith(today))


def manage_paper_position(
    p: PaperPosition,
    current_price: float,
    now_hhmm: str,
    force_exit_time: str,
    partial_exit_fraction: float,
    trailing_pct: float,
) -> Tuple[PaperPosition, List[PaperEvent]]:
    events: List[PaperEvent] = []
    if not p.is_open or current_price <= 0:
        return p, events

    now = datetime.now().isoformat(timespec="seconds")
    p.last_price = float(current_price)
    p.last_marked_at = now
    p.peak_price = max(float(p.peak_price or 0), float(current_price))

    def close_qty(qty: int, price: float, event: str, reason: str) -> None:
        nonlocal p
        qty = max(0, min(int(qty), int(p.open_quantity)))
        if qty <= 0:
            return
        pnl = (float(price) - p.entry_price) * qty
        p.realized_pnl += pnl
        p.open_quantity -= qty
        events.append(PaperEvent(event, p.trade_id, p.underlying, p.contract_symbol, qty, float(price), pnl, reason, now))
        if p.open_quantity <= 0:
            p.open_quantity = 0
            p.status = "CLOSED"
            p.closed_at = now
            p.exit_reason = reason
        else:
            p.status = "PARTIAL"

    # Time exit has highest priority at/after the configured square-off time.
    if now_hhmm >= force_exit_time:
        close_qty(p.open_quantity, current_price, "EXIT", "FORCE_EXIT")
        return p, events

    # Stop is always honored before profit-management decisions.
    if current_price <= p.stop_price:
        close_qty(p.open_quantity, current_price, "EXIT", "STOP")
        return p, events

    # First target: realize a configurable fraction, then protect remainder at BE.
    if not p.target1_hit and current_price >= p.target_price:
        raw_qty = int(round(p.initial_quantity * partial_exit_fraction))
        if p.lot_size > 0:
            lots = max(1, raw_qty // p.lot_size)
            raw_qty = lots * p.lot_size
        raw_qty = min(raw_qty, p.open_quantity)
        # Never partial-exit the whole position; leave a runner when possible.
        if raw_qty >= p.open_quantity and p.open_quantity > p.lot_size:
            raw_qty = p.open_quantity - p.lot_size
        close_qty(raw_qty, current_price, "PARTIAL_EXIT", "TARGET1")
        p.target1_hit = True
        if p.is_open:
            p.stop_price = max(p.stop_price, p.entry_price)
            p.peak_price = max(p.peak_price, current_price)
        return p, events

    # After target1, trail the remaining runner from its highest observed premium.
    if p.target1_hit and p.is_open:
        trail = round(p.peak_price * (1.0 - trailing_pct), 2)
        p.stop_price = max(p.stop_price, p.entry_price, trail)

    return p, events
