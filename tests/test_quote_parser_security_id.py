import unittest
from execution.quote_parser import parse_quote_response

class SecurityIdQuoteParserTests(unittest.TestCase):
    def test_nested_integer_security_id(self):
        payload = {
            'status': 'success',
            'data': {
                'NSE_FNO': {
                    856453: {
                        'last_price': 101.5,
                        'volume': 12000,
                        'oi': 34000,
                        'depth': {
                            'buy': [{'price': 100.5, 'quantity': 700}],
                            'sell': [{'price': 102.0, 'quantity': 350}],
                        },
                    }
                }
            }
        }
        q = parse_quote_response(payload, '856453')
        self.assertEqual(q.ltp, 101.5)
        self.assertEqual(q.bid, 100.5)
        self.assertEqual(q.ask, 102.0)
        self.assertEqual(q.bid_qty, 700.0)
        self.assertEqual(q.ask_qty, 350.0)
        self.assertEqual(q.volume, 12000.0)
        self.assertEqual(q.open_interest, 34000.0)

if __name__ == '__main__':
    unittest.main()
