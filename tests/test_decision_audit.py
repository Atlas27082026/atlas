import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.decision_audit import DecisionAuditWriter


class DecisionAuditTests(unittest.TestCase):
    def test_decision_audit_writes_required_fields(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "decision_audit.csv"
            writer = DecisionAuditWriter(path)
            result = SimpleNamespace(
                symbol="JSWSTEEL",
                direction="BULL",
                model="TREND_CONTINUATION",
                metrics={
                    "market_state": "BULL",
                    "trade_quality_score": 82.5,
                    "macro_ok": True,
                    "rvol_ok": True,
                    "entry_trigger_ok": True,
                },
            )

            writer.append(result, "OK", "ENTRY_ACCEPTED", "OK")

            text = path.read_text(encoding="utf-8")
            self.assertIn("symbol,direction,model,market_state,quality_score", text)
            self.assertIn("JSWSTEEL,BULL,TREND_CONTINUATION,BULL,82.5,True,True,True,OK,ENTRY_ACCEPTED,OK", text)


if __name__ == "__main__":
    unittest.main()
