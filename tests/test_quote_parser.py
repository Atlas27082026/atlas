import unittest
from execution.quote_parser import parse_quote_response


class QuoteParserTests(unittest.TestCase):
    def test_dhan_depth_shape(self):
        payload = {"ABC": {"last_price": 100.0, "volume": 50000, "oi": 22000,
                            "depth": {"buy": [{"price": 99, "quantity": 500}], "sell": [{"price": 101, "quantity": 600}]}}}
        q = parse_quote_response(payload, "ABC")
        self.assertEqual(q.ltp, 100.0)
        self.assertEqual(q.bid, 99.0)
        self.assertEqual(q.ask, 101.0)
        self.assertEqual(q.bid_qty, 500.0)
        self.assertEqual(q.ask_qty, 600.0)
        self.assertEqual(q.volume, 50000.0)
        self.assertEqual(q.open_interest, 22000.0)

    def test_scalar_ltp(self):
        q = parse_quote_response({"ABC": 12.5}, "ABC")
        self.assertEqual(q.ltp, 12.5)
