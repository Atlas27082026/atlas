import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.reset_paper_run import reset_paper_run


class ResetPaperRunTests(unittest.TestCase):
    def test_refuses_without_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "--confirm"):
                reset_paper_run(base_dir=Path(td), dry_run=True, backup=False, confirm=False)

    def test_refuses_live_mode(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "dry_run=False"):
                reset_paper_run(base_dir=Path(td), dry_run=False, backup=False, confirm=True)

    def test_resets_paper_state_and_backs_up(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state = base / "state"
            state.mkdir()
            (state / "daily_state.json").write_text(
                json.dumps({"trading_date": "2026-08-28", "session_start_balance": 150000.0, "daily_trade_count": 4}),
                encoding="utf-8",
            )
            (state / "daily_state_strategy_c.json").write_text(
                json.dumps({"trading_date": "2026-08-28", "session_start_balance": 150000.0, "daily_trade_count": 7}),
                encoding="utf-8",
            )
            (state / "paper_positions.json").write_text(json.dumps({"positions": [{"trade_id": "old"}]}), encoding="utf-8")
            (state / "paper_positions_strategy_c.json").write_text(json.dumps({"positions": [{"trade_id": "old-c"}]}), encoding="utf-8")
            (state / "pending_setups_strategy_c.json").write_text(json.dumps({"setups": [{"setup_id": "old-c"}]}), encoding="utf-8")
            now = datetime(2026, 8, 31, 8, 0, 0)

            actions = reset_paper_run(base_dir=base, dry_run=True, backup=True, confirm=True, now=now)

            daily = json.loads((state / "daily_state.json").read_text(encoding="utf-8"))
            daily_c = json.loads((state / "daily_state_strategy_c.json").read_text(encoding="utf-8"))
            paper = json.loads((state / "paper_positions.json").read_text(encoding="utf-8"))
            paper_c = json.loads((state / "paper_positions_strategy_c.json").read_text(encoding="utf-8"))
            pending_c = json.loads((state / "pending_setups_strategy_c.json").read_text(encoding="utf-8"))
            backup = base / "runtime_data" / "paper_reset_backups" / "20260831_080000" / "paper_positions.json"
            backup_c = base / "runtime_data" / "paper_reset_backups" / "20260831_080000" / "paper_positions_strategy_c.json"
            backup_pending_c = base / "runtime_data" / "paper_reset_backups" / "20260831_080000" / "pending_setups_strategy_c.json"
            self.assertEqual(daily["trading_date"], "2026-08-31")
            self.assertEqual(daily["daily_trade_count"], 0)
            self.assertEqual(daily_c["trading_date"], "2026-08-31")
            self.assertEqual(daily_c["daily_trade_count"], 0)
            self.assertEqual(paper["positions"], [])
            self.assertEqual(paper_c["positions"], [])
            self.assertEqual(pending_c["setups"], [])
            self.assertTrue(backup.exists())
            self.assertTrue(backup_c.exists())
            self.assertTrue(backup_pending_c.exists())
            self.assertIn("RESET state/pending_setups_strategy_c.json", actions)

    def test_missing_strategy_c_pending_file_does_not_error(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "state").mkdir()

            actions = reset_paper_run(
                base_dir=base,
                dry_run=True,
                backup=True,
                confirm=True,
                now=datetime(2026, 8, 31, 8, 0, 0),
            )

            self.assertIn("RESET state/pending_setups_strategy_c.json", actions)


if __name__ == "__main__":
    unittest.main()
