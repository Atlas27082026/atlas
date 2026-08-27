from __future__ import annotations

from typing import Optional

from config import AppConfig
from execution.models import LiquidityAssessment, QuoteSnapshot


class LiquidityScorer:
    """Normalized scoring that never treats missing data as zero.

    Components are scored only when the broker actually supplied them.
    Health = earned points / available points * 100.
    Confidence is reported separately from health.
    """

    WEIGHTS = {
        "spread": 40.0,
        "depth": 20.0,
        "volume": 20.0,
        "oi": 10.0,
        "premium": 10.0,
    }

    def __init__(self, config: AppConfig):
        self.config = config

    @staticmethod
    def _spread_pct(q: QuoteSnapshot) -> Optional[float]:
        if q.bid is None or q.ask is None or q.bid <= 0 or q.ask <= 0:
            return None
        mid = (q.bid + q.ask) / 2.0
        if mid <= 0:
            return None
        return ((q.ask - q.bid) / mid) * 100.0

    @staticmethod
    def _spread_component(spread: float) -> float:
        if spread <= 2: return 1.00
        if spread <= 4: return 0.90
        if spread <= 6: return 0.75
        if spread <= 8: return 0.55
        if spread <= 12: return 0.25
        return 0.0

    @staticmethod
    def _depth_component(q: QuoteSnapshot, lot_size: int) -> Optional[float]:
        if q.bid_qty is None or q.ask_qty is None or lot_size <= 0:
            return None
        min_lots = min(q.bid_qty, q.ask_qty) / lot_size
        if min_lots >= 10: return 1.0
        if min_lots >= 5: return 0.85
        if min_lots >= 2: return 0.65
        if min_lots >= 1: return 0.45
        return 0.0

    @staticmethod
    def _volume_component(volume: float) -> float:
        if volume >= 100000: return 1.0
        if volume >= 50000: return 0.85
        if volume >= 20000: return 0.70
        if volume >= 5000: return 0.50
        if volume > 0: return 0.25
        return 0.0

    @staticmethod
    def _oi_component(oi: float) -> float:
        if oi >= 100000: return 1.0
        if oi >= 50000: return 0.85
        if oi >= 20000: return 0.70
        if oi >= 5000: return 0.50
        if oi > 0: return 0.25
        return 0.0

    def assess(self, q: QuoteSnapshot, lot_size: int) -> LiquidityAssessment:
        ex = self.config.execution
        reasons = []
        components = {}
        earned = 0.0
        available = 0.0

        premium = q.ask if q.ask is not None and q.ask > 0 else q.ltp
        if premium is None or premium <= 0:
            return LiquidityAssessment(False, 0.0, "D", "D", None, 0.0, ["NO_VALID_PREMIUM"], {})

        # Premium is always available if we reached this point.
        available += self.WEIGHTS["premium"]
        premium_ok = ex.premium_min <= premium <= ex.premium_max
        premium_factor = 1.0 if premium_ok else 0.0
        earned += self.WEIGHTS["premium"] * premium_factor
        components["premium"] = premium_factor
        if not premium_ok:
            reasons.append(f"PREMIUM_OUTSIDE_{ex.premium_min:.0f}_{ex.premium_max:.0f}")

        spread = self._spread_pct(q)
        if spread is not None:
            available += self.WEIGHTS["spread"]
            factor = self._spread_component(spread)
            earned += self.WEIGHTS["spread"] * factor
            components["spread"] = factor
            if spread >= ex.catastrophic_spread_pct:
                reasons.append("CATASTROPHIC_SPREAD")

        depth_factor = self._depth_component(q, lot_size)
        if depth_factor is not None:
            available += self.WEIGHTS["depth"]
            earned += self.WEIGHTS["depth"] * depth_factor
            components["depth"] = depth_factor

        if q.volume is not None:
            available += self.WEIGHTS["volume"]
            factor = self._volume_component(q.volume)
            earned += self.WEIGHTS["volume"] * factor
            components["volume"] = factor

        if q.open_interest is not None:
            available += self.WEIGHTS["oi"]
            factor = self._oi_component(q.open_interest)
            earned += self.WEIGHTS["oi"] * factor
            components["oi"] = factor

        health = 0.0 if available <= 0 else (earned / available) * 100.0
        supplied = {
            "spread": spread is not None,
            "depth": depth_factor is not None,
            "volume": q.volume is not None,
            "oi": q.open_interest is not None,
        }
        count = sum(supplied.values())
        confidence = "A" if count == 4 else "B" if count == 3 else "C" if count == 2 else "D"
        grade = "A+" if health >= 85 else "A" if health >= 75 else "B" if health >= 65 else "C" if health >= 50 else "D"

        threshold = ex.min_health_score_dry_run if self.config.risk.dry_run else ex.min_health_score_live
        accepted = health >= threshold and premium_ok and "CATASTROPHIC_SPREAD" not in reasons
        if not self.config.risk.dry_run and confidence == "D":
            accepted = False
            reasons.append("LOW_DATA_CONFIDENCE")
        if health < threshold:
            reasons.append(f"HEALTH_{health:.0f}_BELOW_{threshold:.0f}")

        return LiquidityAssessment(
            accepted=accepted,
            health_score=round(health, 1),
            confidence=confidence,
            grade=grade,
            spread_pct=None if spread is None else round(spread, 2),
            available_weight=available,
            reasons=reasons,
            components=components,
        )
