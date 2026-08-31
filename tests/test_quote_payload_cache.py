import unittest
import sys
import types
from types import SimpleNamespace

sys.modules.setdefault("talib", types.SimpleNamespace())

from execution.models import ContractCandidate, ExecutionCandidate, LiquidityAssessment
from main import _select_best_contract


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


class _Broker:
    def __init__(self):
        self.quote_calls = []

    def get_quote_data_by_security_ids(self, security_ids, exchange_segment):
        self.quote_calls.append((tuple(str(s) for s in security_ids), exchange_segment))
        return {
            "status": "success",
            "data": {
                exchange_segment: {
                    str(security_id): {
                        "last_price": 100.0,
                        "depth": {
                            "buy": [{"price": 99.5, "quantity": 1000}],
                            "sell": [{"price": 100.5, "quantity": 1000}],
                        },
                    }
                    for security_id in security_ids
                }
            },
        }

    def quote_symbol(self, contract):
        return contract.trading_symbol

    def lot_size_for_contract(self, contract):
        return int(contract.lot_size)


class _Resolver:
    def __init__(self, contracts):
        self.contracts = contracts

    def resolve(self, *_args, **_kwargs):
        return list(self.contracts)


class _Risk:
    def strategy_capital_base(self, state):
        return state.capital

    def capital_for_trade(self, capital):
        return capital


class _Scorer:
    def __init__(self):
        self.calls = 0

    def assess(self, _quote, _lot_size):
        self.calls += 1
        return LiquidityAssessment(
            accepted=True,
            health_score=80.0,
            confidence="A",
            grade="A",
            spread_pct=1.0,
            available_weight=1.0,
        )


class _Selector:
    def __init__(self):
        self.build_capitals = []
        self.choose_calls = 0

    def build_candidate(self, contract, quote, lot_size, capital, assessment):
        self.build_capitals.append(capital)
        return ExecutionCandidate(
            contract=contract,
            quote=quote,
            liquidity=assessment,
            capital_allocated=capital,
            quantity=int(capital),
            lot_size=lot_size,
            entry_limit=100.5,
            target_price=120.6,
            stop_price=90.45,
            size_fraction=1.0,
        )

    def choose_best(self, candidates):
        self.choose_calls += 1
        return list(candidates)[0]


def _config():
    return SimpleNamespace(
        execution=SimpleNamespace(
            nearby_strikes_each_side=4,
            preferred_option_exchange="NFO",
            native_quote_diagnostics=False,
            native_quote_diagnostics_once=True,
            native_quote_diagnostics_max_chars=500,
        )
    )


def _contract(security_id):
    return ContractCandidate(
        underlying="HCLTECH",
        trading_symbol=f"HCLTECH-{security_id}-CE",
        option_type="CE",
        strike=1500.0,
        expiry="2099-01-01",
        security_id=str(security_id),
        lot_size=700,
        exchange_segment="NSE_FNO",
    )


def _select(contracts, broker, scorer, selector, quote_cache, capital):
    return _select_best_contract(
        logger=_Logger(),
        broker=broker,
        resolver=_Resolver(contracts),
        config=_config(),
        scorer=scorer,
        selector=selector,
        exec_diag=SimpleNamespace(append_assessment=lambda *args, **kwargs: None),
        risk=_Risk(),
        state=SimpleNamespace(capital=capital),
        symbol="HCLTECH",
        direction="BULL",
        model="TEST",
        score_pct=90.0,
        underlying_price=1500.0,
        quote_cache=quote_cache,
    )


class QuotePayloadCacheTests(unittest.TestCase):
    def test_same_security_ids_reuse_one_native_quote_request(self):
        broker = _Broker()
        scorer = _Scorer()
        selector = _Selector()
        quote_cache = {}
        contracts = [_contract("1001"), _contract("1002")]

        first = _select(contracts, broker, scorer, selector, quote_cache, 10000.0)
        second = _select(contracts, broker, scorer, selector, quote_cache, 25000.0)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(len(broker.quote_calls), 1)

    def test_different_security_ids_do_not_reuse_cached_quote(self):
        broker = _Broker()
        scorer = _Scorer()
        selector = _Selector()
        quote_cache = {}

        _select([_contract("1001"), _contract("1002")], broker, scorer, selector, quote_cache, 10000.0)
        _select([_contract("2001"), _contract("2002")], broker, scorer, selector, quote_cache, 10000.0)

        self.assertEqual(len(broker.quote_calls), 2)

    def test_cached_payload_still_runs_strategy_specific_scoring_and_sizing(self):
        broker = _Broker()
        scorer = _Scorer()
        selector = _Selector()
        quote_cache = {}
        contracts = [_contract("1001"), _contract("1002")]

        first = _select(contracts, broker, scorer, selector, quote_cache, 10000.0)
        second = _select(contracts, broker, scorer, selector, quote_cache, 25000.0)

        self.assertEqual(len(broker.quote_calls), 1)
        self.assertEqual(scorer.calls, 4)
        self.assertEqual(selector.choose_calls, 2)
        self.assertEqual(selector.build_capitals, [10000.0, 10000.0, 25000.0, 25000.0])
        self.assertEqual(first.quantity, 10000)
        self.assertEqual(second.quantity, 25000)


if __name__ == "__main__":
    unittest.main()
