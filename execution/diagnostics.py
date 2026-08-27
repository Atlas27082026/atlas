from pathlib import Path

from core.journal import CsvJournal
from execution.models import ExecutionCandidate, LiquidityAssessment, ContractCandidate, QuoteSnapshot


FIELDS = [
    "timestamp", "underlying", "direction", "signal_model", "signal_score",
    "contract_symbol", "broker_symbol", "exchange_id", "exchange_segment", "security_id", "expiry", "strike", "moneyness",
    "resolver_source", "ltp", "bid", "ask", "bid_qty", "ask_qty", "volume", "open_interest",
    "spread_pct", "health_score", "confidence", "grade", "accepted", "reasons", "lot_size",
    "quantity", "entry_limit", "target_price", "stop_price", "size_fraction",
]


class ExecutionDiagnostics:
    def __init__(self, path: Path):
        self.journal = CsvJournal(path, FIELDS)

    def append_assessment(self, underlying, direction, signal_model, signal_score, contract: ContractCandidate,
                          quote: QuoteSnapshot, assessment: LiquidityAssessment, lot_size="", candidate: ExecutionCandidate = None,
                          broker_symbol: str = ""):
        self.journal.append({
            "underlying": underlying,
            "direction": direction,
            "signal_model": signal_model or "",
            "signal_score": signal_score,
            "contract_symbol": contract.trading_symbol,
            "broker_symbol": broker_symbol or contract.broker_symbol,
            "exchange_id": contract.exchange_id,
            "exchange_segment": contract.exchange_segment,
            "security_id": contract.security_id,
            "expiry": contract.expiry,
            "strike": contract.strike,
            "moneyness": contract.moneyness,
            "resolver_source": contract.source,
            "ltp": quote.ltp,
            "bid": quote.bid,
            "ask": quote.ask,
            "bid_qty": quote.bid_qty,
            "ask_qty": quote.ask_qty,
            "volume": quote.volume,
            "open_interest": quote.open_interest,
            "spread_pct": assessment.spread_pct,
            "health_score": assessment.health_score,
            "confidence": assessment.confidence,
            "grade": assessment.grade,
            "accepted": assessment.accepted,
            "reasons": "|".join(assessment.reasons),
            "lot_size": lot_size,
            "quantity": "" if candidate is None else candidate.quantity,
            "entry_limit": "" if candidate is None else candidate.entry_limit,
            "target_price": "" if candidate is None else candidate.target_price,
            "stop_price": "" if candidate is None else candidate.stop_price,
            "size_fraction": "" if candidate is None else candidate.size_fraction,
        })
