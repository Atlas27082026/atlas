import unittest
import pandas as pd
from execution.contract_resolver import InstrumentMasterResolver


class PreferredExchangeStrictTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([
            # Deliberately misleading BSE row indexed under the underlying.
            dict(SEM_EXM_EXCH_ID='BSE', SEM_SEGMENT='D', SEM_SMST_SECURITY_ID='8001',
                 SEM_INSTRUMENT_NAME='OPTSTK', SEM_TRADING_SYMBOL='ONGC-Aug2026-230-PE',
                 SEM_LOT_UNITS=100, SEM_CUSTOM_SYMBOL='ONGC 27 AUG 230 PUT',
                 SEM_EXPIRY_DATE='2026-08-27', SEM_STRIKE_PRICE=230, SEM_OPTION_TYPE='PE',
                 SM_SYMBOL_NAME='ONGC'),
            # NSE rows with an inconvenient SM_SYMBOL_NAME: resolver must recover
            # them using exact master trading/custom-symbol prefixes.
            dict(SEM_EXM_EXCH_ID='NSE', SEM_SEGMENT='D', SEM_SMST_SECURITY_ID='2001',
                 SEM_INSTRUMENT_NAME='OPTSTK', SEM_TRADING_SYMBOL='ONGC-Sep2026-230-PE',
                 SEM_LOT_UNITS=100, SEM_CUSTOM_SYMBOL='ONGC 29 SEP 230 PUT',
                 SEM_EXPIRY_DATE='2026-09-29', SEM_STRIKE_PRICE=230, SEM_OPTION_TYPE='PE',
                 SM_SYMBOL_NAME='ONGCOPT'),
            dict(SEM_EXM_EXCH_ID='NSE', SEM_SEGMENT='D', SEM_SMST_SECURITY_ID='2002',
                 SEM_INSTRUMENT_NAME='OPTSTK', SEM_TRADING_SYMBOL='ONGC-Sep2026-235-PE',
                 SEM_LOT_UNITS=100, SEM_CUSTOM_SYMBOL='ONGC 29 SEP 235 PUT',
                 SEM_EXPIRY_DATE='2026-09-29', SEM_STRIKE_PRICE=235, SEM_OPTION_TYPE='PE',
                 SM_SYMBOL_NAME='ONGCOPT'),
        ])

    def test_preferred_nse_never_silently_uses_bse(self):
        r = InstrumentMasterResolver(self.frame())
        contracts = r.resolve('ONGC', 'BEAR', 232.0, preferred_exchange='NSE')
        self.assertTrue(contracts)
        self.assertTrue(all(c.exchange_id == 'NSE' for c in contracts))
        self.assertTrue(all(c.exchange_segment == 'NSE_FNO' for c in contracts))
        self.assertEqual({c.security_id for c in contracts}, {'2001','2002'})

    def test_missing_preferred_exchange_fails_closed(self):
        f = self.frame()
        f = f[f['SEM_EXM_EXCH_ID'] == 'BSE'].copy()
        r = InstrumentMasterResolver(f)
        contracts = r.resolve('ONGC', 'BEAR', 232.0, preferred_exchange='NSE')
        self.assertEqual(contracts, [])

if __name__ == '__main__':
    unittest.main()
