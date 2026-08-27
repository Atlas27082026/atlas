import unittest

from config import AppConfig
from execution.models import ContractCandidate, LiquidityAssessment, QuoteSnapshot
from execution.selector import ContractSelector


class ContractSelectorTests(unittest.TestCase):
    def test_build_candidate_reuses_supplied_liquidity_assessment(self):
        selector = ContractSelector(AppConfig())
        selector.scorer.assess = lambda *_: (_ for _ in ()).throw(AssertionError("duplicate scoring"))
        contract = ContractCandidate(
            underlying="TEST",
            trading_symbol="TEST-100-CE",
            option_type="CE",
            strike=100.0,
            expiry="2099-01-01",
        )
        quote = QuoteSnapshot(symbol="123", ltp=100.0, ask=100.0)
        assessment = LiquidityAssessment(
            accepted=True,
            health_score=80.0,
            confidence="A",
            grade="A",
            spread_pct=None,
            available_weight=10.0,
        )

        candidate = selector.build_candidate(contract, quote, lot_size=10, capital=30000.0, assessment=assessment)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.quantity, 290)


if __name__ == "__main__":
    unittest.main()
