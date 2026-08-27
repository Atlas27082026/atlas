import tempfile
import unittest
from pathlib import Path

import pandas as pd

from execution.contract_resolver import InstrumentMasterResolver


class ResolverTests(unittest.TestCase):
    def test_nearby_ce_resolution_generic_schema(self):
        rows = []
        for strike in [90, 95, 100, 105, 110]:
            rows.append({
                "trading_symbol": f"ABC 30 AUG {strike} CALL",
                "underlying": "ABC",
                "expiry_date": "2099-08-30",
                "strike_price": strike,
                "option_type": "CALL",
            })
        r = InstrumentMasterResolver(pd.DataFrame(rows))
        out = r.resolve("ABC", "BULL", 102, max_each_side=2)
        self.assertTrue(any(x.moneyness == "ATM" for x in out))
        self.assertGreaterEqual(len(out), 3)

    def test_dhan_sem_schema_is_auto_detected(self):
        rows = []
        for strike in [1400, 1420, 1440]:
            rows.append({
                "SEM_EXM_EXCH_ID": "NSE",
                "SEM_SEGMENT": "D",
                "SEM_SMST_SECURITY_ID": str(100000 + strike),
                "SEM_INSTRUMENT_NAME": "OPTSTK",
                "SEM_TRADING_SYMBOL": f"ICICIBANK 25 AUG {strike} CALL",
                "SEM_LOT_UNITS": 700,
                "SEM_CUSTOM_SYMBOL": f"ICICIBANK AUG {strike} CE",
                "SEM_EXPIRY_DATE": "2099-08-25",
                "SEM_STRIKE_PRICE": strike,
                "SEM_OPTION_TYPE": "CE",
                "SM_SYMBOL_NAME": "ICICIBANK",
            })
        r = InstrumentMasterResolver(pd.DataFrame(rows))
        mapping = r.schema_mapping()
        self.assertEqual(mapping["trading_symbol"], "SEM_TRADING_SYMBOL")
        self.assertEqual(mapping["underlying"], "SM_SYMBOL_NAME")
        out = r.resolve("ICICIBANK", "BULL", 1417, max_each_side=2)
        self.assertEqual(len(out), 3)
        self.assertTrue(any(x.strike == 1420 and x.moneyness == "ATM" for x in out))

    def test_put_moneyness(self):
        rows = []
        for strike in [225, 230, 235, 240]:
            rows.append({
                "SEM_TRADING_SYMBOL": f"ONGC 25 AUG {strike} PUT",
                "SEM_EXPIRY_DATE": "2099-08-25",
                "SEM_STRIKE_PRICE": strike,
                "SEM_OPTION_TYPE": "PE",
                "SM_SYMBOL_NAME": "ONGC",
            })
        r = InstrumentMasterResolver(pd.DataFrame(rows))
        out = r.resolve("ONGC", "BEAR", 232, max_each_side=2)
        atm = min(out, key=lambda x: abs(x.strike - 230))
        self.assertEqual(atm.strike, 230)
        self.assertEqual(atm.moneyness, "ATM")
        self.assertEqual(next(x for x in out if x.strike == 235).moneyness, "ITM")
        self.assertEqual(next(x for x in out if x.strike == 225).moneyness, "OTM")

    def test_cache_roundtrip(self):
        frame = pd.DataFrame([{
            "SEM_TRADING_SYMBOL": "ABC 30 AUG 100 CALL",
            "SEM_EXPIRY_DATE": "2099-08-30",
            "SEM_STRIKE_PRICE": 100,
            "SEM_OPTION_TYPE": "CE",
            "SM_SYMBOL_NAME": "ABC",
        }])
        resolver = InstrumentMasterResolver(frame)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "master.pkl"
            resolver.write_cache(path, frame)
            loaded = InstrumentMasterResolver.from_cache(path)
            self.assertTrue(loaded.stats.cache_used)
            self.assertEqual(len(loaded.resolve("ABC", "BULL", 101)), 1)


if __name__ == "__main__":
    unittest.main()

class ResolverIdentityTests(unittest.TestCase):
    def test_exact_master_symbol_security_id_and_lot_are_preserved(self):
        frame = pd.DataFrame([{
            "SEM_TRADING_SYMBOL": "MAXHEALTH-AUG2099-1010-CE",
            "SEM_CUSTOM_SYMBOL": "MAXHEALTH AUG 1010 CE",
            "SEM_SMST_SECURITY_ID": "777001",
            "SEM_LOT_UNITS": 525,
            "SEM_EXPIRY_DATE": "2099-08-25",
            "SEM_STRIKE_PRICE": 1010,
            "SEM_OPTION_TYPE": "CE",
            "SM_SYMBOL_NAME": "MAXHEALTH",
        }])
        r = InstrumentMasterResolver(frame)
        c = r.resolve("MAXHEALTH", "BULL", 1008)[0]
        self.assertEqual(c.trading_symbol, "MAXHEALTH-AUG2099-1010-CE")
        self.assertEqual(c.security_id, "777001")
        self.assertEqual(c.lot_size, 525)
        self.assertEqual(c.strike, 1010)

    def test_resolver_never_invents_intermediate_strikes(self):
        frame = pd.DataFrame([
            {"SEM_TRADING_SYMBOL": "TRENT-AUG2099-2900-CE", "SEM_EXPIRY_DATE": "2099-08-25", "SEM_STRIKE_PRICE": 2900, "SEM_OPTION_TYPE": "CE", "SM_SYMBOL_NAME": "TRENT"},
            {"SEM_TRADING_SYMBOL": "TRENT-AUG2099-2950-CE", "SEM_EXPIRY_DATE": "2099-08-25", "SEM_STRIKE_PRICE": 2950, "SEM_OPTION_TYPE": "CE", "SM_SYMBOL_NAME": "TRENT"},
            {"SEM_TRADING_SYMBOL": "TRENT-AUG2099-3000-CE", "SEM_EXPIRY_DATE": "2099-08-25", "SEM_STRIKE_PRICE": 3000, "SEM_OPTION_TYPE": "CE", "SM_SYMBOL_NAME": "TRENT"},
        ])
        r = InstrumentMasterResolver(frame)
        strikes = {c.strike for c in r.resolve("TRENT", "BULL", 2933.35, max_each_side=4)}
        self.assertEqual(strikes, {2900.0, 2950.0, 3000.0})
