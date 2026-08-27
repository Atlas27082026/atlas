import unittest
from data.dhan_native_client import NativeDhanClient


class FakeNativeDhan:
    def quote_data(self, securities):
        return {"status": "success", "data": {"NSE_FNO": {str(securities["NSE_FNO"][0]): {"last_price": 12.5}}}}


class Holder:
    def __init__(self):
        self.some_private_client = FakeNativeDhan()
        self.access_token = "must-not-be-traversed"


class NativeDhanClientTests(unittest.TestCase):
    def test_discovers_capability_not_attribute_name(self):
        native = NativeDhanClient("123", tradehull_root=Holder())
        self.assertTrue(native.status.available)
        self.assertEqual(native.status.source, "TRADEHULL_AUTHENTICATED_NATIVE_DHAN")
        response = native.quote_data({"NSE_FNO": [856453]})
        self.assertEqual(response["data"]["NSE_FNO"]["856453"]["last_price"], 12.5)

    def test_unavailable_fails_closed(self):
        native = NativeDhanClient("123", tradehull_root=object())
        self.assertFalse(native.status.available)
        with self.assertRaises(RuntimeError):
            native.quote_data({"NSE_FNO": [1]})


if __name__ == "__main__":
    unittest.main()
