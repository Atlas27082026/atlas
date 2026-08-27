from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from config import AppConfig
from execution.liquidity import LiquidityScorer
from execution.models import ContractCandidate, ExecutionCandidate, LiquidityAssessment, QuoteSnapshot


class ContractSelector:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scorer = LiquidityScorer(config)

    def build_candidate(
        self,
        contract: ContractCandidate,
        quote: QuoteSnapshot,
        lot_size: int,
        capital: float,
        assessment: Optional[LiquidityAssessment] = None,
    ) -> Optional[ExecutionCandidate]:
        if assessment is None:
            assessment = self.scorer.assess(quote, lot_size)
        if not assessment.accepted:
            return None
        reference = quote.ask if quote.ask is not None and quote.ask > 0 else quote.ltp
        if reference is None or reference <= 0 or lot_size <= 0:
            return None
        entry = round(reference * (1.0 + self.config.execution.limit_price_buffer_pct), 2)
        lots = int(capital / (entry * lot_size))
        if lots <= 0:
            return None
        size_fraction = 0.5 if assessment.grade == "B" else 1.0
        lots = max(1, int(lots * size_fraction))
        qty = lots * lot_size
        return ExecutionCandidate(
            contract=contract,
            quote=quote,
            liquidity=assessment,
            capital_allocated=capital * size_fraction,
            quantity=qty,
            lot_size=lot_size,
            entry_limit=entry,
            target_price=round(entry * self.config.execution.target_multiplier, 2),
            stop_price=round(entry * self.config.execution.stop_multiplier, 2),
            size_fraction=size_fraction,
        )

    @staticmethod
    def choose_best(candidates: Iterable[ExecutionCandidate]) -> Optional[ExecutionCandidate]:
        candidates = list(candidates)
        if not candidates:
            return None
        # Health dominates; confidence and tighter spread break ties.
        confidence_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
        return max(candidates, key=lambda c: (
            c.liquidity.health_score,
            confidence_rank.get(c.liquidity.confidence, 0),
            -(c.liquidity.spread_pct if c.liquidity.spread_pct is not None else 999.0),
        ))
