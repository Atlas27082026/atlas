import unittest

from execution.quote_parser import parse_quote_response


class DhanSecurityQuoteTests(unittest.TestCase):
    def test_nested_nse_fno_security_id_quote(self):
        payload = {
            "status": "success",
            "data": {
                "NSE_FNO": {
                    "838057": {
                        "last_price": 12.5,
                        "volume": 25000,
                        "oi": 54000,
                        "depth": {
                            "buy": [{"price": 12.4, "quantity": 800}],
                            "sell": [{"price": 12.6, "quantity": 1200}],
                        },
                    }
                }
            },
        }
        q = parse_quote_response(payload, "838057")
        self.assertEqual(q.ltp, 12.5)
        self.assertEqual(q.bid, 12.4)
        self.assertEqual(q.ask, 12.6)
        self.assertEqual(q.bid_qty, 800)
        self.assertEqual(q.ask_qty, 1200)
        self.assertEqual(q.volume, 25000)
        self.assertEqual(q.open_interest, 54000)


if __name__ == "__main__":
    unittest.main()
