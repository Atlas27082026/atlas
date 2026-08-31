import unittest
import sys
import types
from types import SimpleNamespace

sys.modules.setdefault("talib", types.SimpleNamespace())

from execution.models import ContractCandidate, ExecutionCandidate, LiquidityAssessment
from execution.quote_parser import parse_quote_response
from main import (
    _select_best_contract,
    get_cached_security_quote_payload,
    get_paper_mtm_quote_payloads,
)


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


class _Broker:
    def __init__(self, payload=None):
        self.quote_calls = []
        self.payload = payload

    def get_quote_data_by_security_ids(self, security_ids, exchange_segment):
        self.quote_calls.append((tuple(str(s) for s in security_ids), exchange_segment))
        if self.payload is not None:
            return self.payload
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


def _position(security_id, exchange_segment="NSE_FNO"):
    return SimpleNamespace(security_id=str(security_id), exchange_segment=exchange_segment)


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

    def test_unusable_payload_is_not_cached(self):
        broker = _Broker(payload={"status": "success", "data": {"NSE_FNO": {}}})
        quote_cache = {}

        first = get_cached_security_quote_payload(
            logger=_Logger(),
            broker=broker,
            security_ids=["1001", "1002"],
            exchange_segment="NSE_FNO",
            quote_cache=quote_cache,
            symbol="[A] Paper MTM",
            cache_label="paper quote cache",
        )
        second = get_cached_security_quote_payload(
            logger=_Logger(),
            broker=broker,
            security_ids=["1001", "1002"],
            exchange_segment="NSE_FNO",
            quote_cache=quote_cache,
            symbol="[B] Paper MTM",
            cache_label="paper quote cache",
        )

        self.assertEqual(first, {"status": "success", "data": {"NSE_FNO": {}}})
        self.assertEqual(second, {"status": "success", "data": {"NSE_FNO": {}}})
        self.assertEqual(len(broker.quote_calls), 2)
        self.assertEqual(quote_cache, {})

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

    def test_contract_selection_payload_reuses_existing_cache_key(self):
        broker = _Broker()
        scorer = _Scorer()
        selector = _Selector()
        quote_cache = {}
        contracts = [_contract("1001"), _contract("1002")]

        _select(contracts, broker, scorer, selector, quote_cache, 10000.0)
        payload = get_cached_security_quote_payload(
            logger=_Logger(),
            broker=broker,
            security_ids=["1001", "1002"],
            exchange_segment="NSE_FNO",
            quote_cache=quote_cache,
            symbol="[A] Paper MTM",
            cache_label="paper quote cache",
        )

        self.assertEqual(len(broker.quote_calls), 1)
        self.assertEqual(payload, next(iter(quote_cache.values())))

    def test_paper_mtm_overlapping_strategy_ids_make_one_segment_call(self):
        broker = _Broker()

        payloads = get_paper_mtm_quote_payloads(
            logger=_Logger(),
            broker=broker,
            position_groups=(
                [_position("1001"), _position("1002")],
                [_position("1002"), _position("1003")],
                [_position("1001"), _position("1004")],
            ),
        )

        self.assertEqual(broker.quote_calls, [(("1001", "1002", "1003", "1004"), "NSE_FNO")])
        shared_payload = payloads["NSE_FNO"]
        for security_id in ("1001", "1002", "1003", "1004"):
            quote = parse_quote_response(shared_payload, security_id)
            mark = quote.bid if quote.bid is not None and quote.bid > 0 else quote.ltp
            self.assertEqual(mark, 99.5)

    def test_paper_mtm_two_segments_make_two_quote_calls(self):
        broker = _Broker()

        payloads = get_paper_mtm_quote_payloads(
            logger=_Logger(),
            broker=broker,
            position_groups=(
                [_position("1001", "NSE_FNO")],
                [_position("2001", "BSE_FNO")],
                [_position("1002", "NSE_FNO"), _position("2001", "BSE_FNO")],
            ),
        )

        self.assertEqual(
            broker.quote_calls,
            [(("1001", "1002"), "NSE_FNO"), (("2001",), "BSE_FNO")],
        )
        self.assertEqual(set(payloads), {"NSE_FNO", "BSE_FNO"})

    def test_paper_mtm_unusable_payload_is_not_shared(self):
        broker = _Broker(payload={"status": "success", "data": {"NSE_FNO": {}}})

        payloads = get_paper_mtm_quote_payloads(
            logger=_Logger(),
            broker=broker,
            position_groups=(
                [_position("1001")],
                [_position("1002")],
                [_position("1001")],
            ),
        )

        self.assertEqual(broker.quote_calls, [(("1001", "1002"), "NSE_FNO")])
        self.assertEqual(payloads, {})


if __name__ == "__main__":
    unittest.main()
