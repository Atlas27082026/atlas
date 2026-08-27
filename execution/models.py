from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass(frozen=True)
class ContractCandidate:
    """A real exchange-listed option contract from the instrument master.

    `trading_symbol` is the exchange/master identifier and is never reconstructed.
    `broker_symbol` is an adapter-specific lookup alias (TradeHull convenience APIs
    accept a different human-readable format on some releases).
    """

    underlying: str
    trading_symbol: str
    option_type: str
    strike: float
    expiry: str
    exchange: str = "NFO"
    source: str = "UNKNOWN"
    moneyness: str = "UNKNOWN"
    security_id: str = ""
    lot_size: int = 0
    custom_symbol: str = ""
    broker_symbol: str = ""
    exchange_id: str = "NSE"
    exchange_segment: str = "NSE_FNO"
    segment_code: str = "D"


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    ltp: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    source: str = "QUOTE"
    raw_shape: str = ""


@dataclass(frozen=True)
class LiquidityAssessment:
    accepted: bool
    health_score: float
    confidence: str
    grade: str
    spread_pct: Optional[float]
    available_weight: float
    reasons: list = field(default_factory=list)
    components: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionCandidate:
    contract: ContractCandidate
    quote: QuoteSnapshot
    liquidity: LiquidityAssessment
    capital_allocated: float
    quantity: int
    lot_size: int
    entry_limit: float
    target_price: float
    stop_price: float
    size_fraction: float
