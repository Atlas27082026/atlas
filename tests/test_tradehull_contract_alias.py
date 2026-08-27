import unittest
from unittest.mock import patch

from execution.models import ContractCandidate


class TradeHullAliasTests(unittest.TestCase):
    def test_human_quote_alias_is_derived_from_real_contract(self):
        # Importing TradeHullBroker requires the external package on the user's environment.
        try:
            from data.tradehull_client import TradeHullBroker
        except ModuleNotFoundError:
            self.skipTest("Dhan_Tradehull not installed in build environment")

        obj = object.__new__(TradeHullBroker)
        c = ContractCandidate(
            underlying="MAXHEALTH",
            trading_symbol="MAXHEALTH-AUG2026-1010-CE",
            option_type="CE",
            strike=1010,
            expiry="2026-08-25",
            security_id="777001",
            lot_size=525,
        )
        self.assertEqual(obj.quote_symbol(c), "MAXHEALTH 25 AUG 1010 CALL")


if __name__ == "__main__":
    unittest.main()
