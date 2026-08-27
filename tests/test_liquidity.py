import unittest
from config import AppConfig
from execution.liquidity import LiquidityScorer
from execution.models import QuoteSnapshot


class LiquidityTests(unittest.TestCase):
    def test_missing_volume_oi_not_scored_as_zero(self):
        scorer = LiquidityScorer(AppConfig())
        q = QuoteSnapshot("X", ltp=100, bid=99, ask=101, bid_qty=1000, ask_qty=1000)
        a = scorer.assess(q, lot_size=100)
        self.assertGreater(a.health_score, 70)
        self.assertEqual(a.confidence, "C")

    def test_catastrophic_spread_rejected(self):
        scorer = LiquidityScorer(AppConfig())
        q = QuoteSnapshot("X", ltp=100, bid=80, ask=120, bid_qty=1000, ask_qty=1000)
        a = scorer.assess(q, lot_size=100)
        self.assertFalse(a.accepted)
        self.assertIn("CATASTROPHIC_SPREAD", a.reasons)
