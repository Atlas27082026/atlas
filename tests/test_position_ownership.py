import tempfile
import unittest
from pathlib import Path

from core.position_ownership import (
    BrokerPositionSnapshot,
    ManagedPositionRecord,
    ManagedPositionStore,
    reconcile_position_ownership,
)


class PositionOwnershipTests(unittest.TestCase):
    def test_manual_position_is_external_and_not_managed(self):
        broker = [BrokerPositionSnapshot(security_id="100", trading_symbol="MANUAL", quantity=1, pnl=-4000)]
        report = reconcile_position_ownership(broker, [], account_pnl=-4000)
        self.assertEqual(report.managed_open_count, 0)
        self.assertEqual(report.external_open_count, 1)
        self.assertEqual(report.managed_pnl, 0.0)
        self.assertEqual(report.external_pnl, -4000.0)

    def test_managed_position_matches_security_id(self):
        broker = [BrokerPositionSnapshot(security_id="838057", trading_symbol="X", quantity=2, pnl=125)]
        managed = [ManagedPositionRecord(trade_id="T1", security_id="838057", trading_symbol="OLD", quantity=2)]
        report = reconcile_position_ownership(broker, managed, account_pnl=125)
        self.assertEqual(report.managed_open_count, 1)
        self.assertEqual(report.external_open_count, 0)
        self.assertEqual(report.managed_pnl, 125.0)

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = ManagedPositionStore(Path(td) / "managed.json")
            rows = [ManagedPositionRecord(trade_id="T1", security_id="1", trading_symbol="ABC")]
            store.save(rows)
            loaded = store.load()
            self.assertEqual(loaded[0].trade_id, "T1")
            self.assertTrue(loaded[0].is_open)


if __name__ == "__main__":
    unittest.main()
