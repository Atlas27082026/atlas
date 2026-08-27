import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    security_id: str = ""
    trading_symbol: str = ""
    quantity: float = 0.0
    pnl: Optional[float] = None
    average_price: Optional[float] = None
    product_type: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class ManagedPositionRecord:
    trade_id: str
    security_id: str = ""
    trading_symbol: str = ""
    underlying: str = ""
    quantity: float = 0.0
    side: str = "BUY"
    entry_price: Optional[float] = None
    broker_order_id: str = ""
    status: str = "OPEN"
    opened_at: str = ""
    closed_at: str = ""

    def __post_init__(self):
        if not self.opened_at:
            self.opened_at = datetime.now().isoformat(timespec="seconds")

    @property
    def is_open(self) -> bool:
        return str(self.status).upper() in {"OPEN", "PARTIAL", "PENDING"}


class ManagedPositionStore:
    """Persistent registry of positions explicitly created/adopted by this engine.

    Broker positions are never auto-adopted. A position becomes managed only when
    the engine writes a ManagedPositionRecord (future paper/live order manager) or
    the operator explicitly adopts it in a later workflow.
    """

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> List[ManagedPositionRecord]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("positions", payload if isinstance(payload, list) else [])
            return [ManagedPositionRecord(**row) for row in rows if isinstance(row, dict)]
        except Exception:
            return []

    def save(self, positions: Iterable[ManagedPositionRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"positions": [asdict(p) for p in positions]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def open_records(self) -> List[ManagedPositionRecord]:
        return [p for p in self.load() if p.is_open]


@dataclass(frozen=True)
class PositionOwnershipReport:
    managed_positions: List[BrokerPositionSnapshot]
    external_positions: List[BrokerPositionSnapshot]
    orphaned_managed: List[ManagedPositionRecord]
    managed_pnl: Optional[float]
    external_pnl: Optional[float]
    account_pnl: Optional[float]

    @property
    def managed_open_count(self) -> int:
        return len(self.managed_positions)

    @property
    def external_open_count(self) -> int:
        return len(self.external_positions)


def _norm_symbol(value: str) -> str:
    return " ".join(str(value or "").upper().split())


def reconcile_position_ownership(
    broker_positions: Iterable[BrokerPositionSnapshot],
    managed_records: Iterable[ManagedPositionRecord],
    account_pnl: Optional[float] = None,
) -> PositionOwnershipReport:
    positions = [p for p in broker_positions if abs(float(p.quantity or 0)) > 0]
    records = [r for r in managed_records if r.is_open]

    by_security = {str(r.security_id).strip(): r for r in records if str(r.security_id).strip()}
    by_symbol = {_norm_symbol(r.trading_symbol): r for r in records if _norm_symbol(r.trading_symbol)}

    managed: List[BrokerPositionSnapshot] = []
    external: List[BrokerPositionSnapshot] = []
    matched_trade_ids = set()

    for pos in positions:
        rec = None
        sid = str(pos.security_id).strip()
        if sid:
            rec = by_security.get(sid)
        if rec is None and pos.trading_symbol:
            rec = by_symbol.get(_norm_symbol(pos.trading_symbol))
        if rec is None:
            external.append(pos)
        else:
            managed.append(pos)
            matched_trade_ids.add(rec.trade_id)

    orphaned = [r for r in records if r.trade_id not in matched_trade_ids]

    managed_pnls = [p.pnl for p in managed if p.pnl is not None]
    managed_pnl: Optional[float]
    if not managed:
        managed_pnl = 0.0
    elif len(managed_pnls) == len(managed):
        managed_pnl = float(sum(managed_pnls))
    else:
        managed_pnl = None

    external_pnls = [p.pnl for p in external if p.pnl is not None]
    if not external:
        external_pnl: Optional[float] = 0.0
    elif len(external_pnls) == len(external):
        external_pnl = float(sum(external_pnls))
    elif account_pnl is not None and managed_pnl is not None:
        external_pnl = float(account_pnl - managed_pnl)
    else:
        external_pnl = None

    return PositionOwnershipReport(
        managed_positions=managed,
        external_positions=external,
        orphaned_managed=orphaned,
        managed_pnl=managed_pnl,
        external_pnl=external_pnl,
        account_pnl=account_pnl,
    )
