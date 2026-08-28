import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.version import build_run_identity, startup_banner


class VersionIdentityTests(unittest.TestCase):
    def test_run_identity_uses_single_version_file(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "VERSION").write_text("4.4.0\n", encoding="utf-8")
            config = SimpleNamespace(
                risk=SimpleNamespace(dry_run=True),
                execution=SimpleNamespace(enable_strategy_b=True),
            )

            identity = build_run_identity(config, base, trading_date="2026-08-31")

            self.assertEqual(identity.version, "4.4.0")
            self.assertEqual(identity.run, "RUN 4")
            self.assertEqual(identity.mode, "PAPER")
            self.assertEqual(identity.strategy_a, "IMMEDIATE_5M_BASELINE")
            self.assertEqual(identity.strategy_b, "MTF_15M_5M_1M")
            self.assertEqual(identity.run_id, "run_4_20260831")

    def test_startup_banner_contains_run_context(self):
        config = SimpleNamespace(
            risk=SimpleNamespace(dry_run=True),
            execution=SimpleNamespace(enable_strategy_b=True),
        )
        identity = build_run_identity(config, Path("/missing"), trading_date="2026-08-31")

        text = startup_banner(identity)

        self.assertIn("ATLAS TRADING ENGINE", text)
        self.assertIn("Run          : RUN 4", text)
        self.assertIn("Mode         : PAPER", text)
        self.assertIn("Strategy B   : MTF_15M_5M_1M", text)
        self.assertIn("Trading Date : 2026-08-31", text)


if __name__ == "__main__":
    unittest.main()
