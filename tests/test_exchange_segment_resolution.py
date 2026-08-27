import unittest
import pandas as pd

from execution.contract_resolver import InstrumentMasterResolver


class ExchangeSegmentResolutionTests(unittest.TestCase):
    def test_prefers_nse_when_same_underlying_exists_on_bse(self):
        rows = [
            {
                "SEM_EXM_EXCH_ID": "BSE", "SEM_SEGMENT": "D",
                "SEM_SMST_SECURITY_ID": "900001", "SEM_INSTRUMENT_NAME": "OPTSTK",
                "SEM_TRADING_SYMBOL": "ABC-BSE-AUG2099-100-CE", "SEM_CUSTOM_SYMBOL": "ABC BSE 100 CALL",
                "SEM_LOT_UNITS": 100, "SEM_EXPIRY_DATE": "2099-08-25",
                "SEM_STRIKE_PRICE": 100, "SEM_OPTION_TYPE": "CE", "SM_SYMBOL_NAME": "ABC",
            },
            {
                "SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "D",
                "SEM_SMST_SECURITY_ID": "100001", "SEM_INSTRUMENT_NAME": "OPTSTK",
                "SEM_TRADING_SYMBOL": "ABC-NSE-AUG2099-100-CE", "SEM_CUSTOM_SYMBOL": "ABC NSE 100 CALL",
                "SEM_LOT_UNITS": 100, "SEM_EXPIRY_DATE": "2099-08-25",
                "SEM_STRIKE_PRICE": 100, "SEM_OPTION_TYPE": "CE", "SM_SYMBOL_NAME": "ABC",
            },
        ]
        r = InstrumentMasterResolver(pd.DataFrame(rows))
        out = r.resolve("ABC", "BULL", 101, preferred_exchange="NSE")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].security_id, "100001")
        self.assertEqual(out[0].exchange_id, "NSE")
        self.assertEqual(out[0].exchange_segment, "NSE_FNO")

    def test_bse_derivative_maps_to_bse_fno(self):
        frame = pd.DataFrame([{
            "SEM_EXM_EXCH_ID": "BSE", "SEM_SEGMENT": "D",
            "SEM_SMST_SECURITY_ID": "900001", "SEM_INSTRUMENT_NAME": "OPTSTK",
            "SEM_TRADING_SYMBOL": "ABC-BSE-AUG2099-100-CE", "SEM_CUSTOM_SYMBOL": "ABC 100 CALL",
            "SEM_LOT_UNITS": 100, "SEM_EXPIRY_DATE": "2099-08-25",
            "SEM_STRIKE_PRICE": 100, "SEM_OPTION_TYPE": "CE", "SM_SYMBOL_NAME": "ABC",
        }])
        r = InstrumentMasterResolver(frame)
        c = r.resolve("ABC", "BULL", 101, preferred_exchange="BSE")[0]
        self.assertEqual(c.exchange_segment, "BSE_FNO")


if __name__ == "__main__":
    unittest.main()
