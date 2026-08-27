import tempfile
import unittest
from pathlib import Path

from strategy.settings import load_strategy_settings


class StrategySettingsTests(unittest.TestCase):
    def test_time_bucket_rvol(self):
        settings = load_strategy_settings(Path(__file__).resolve().parents[1] / "strategy.yaml")
        self.assertEqual(settings.rvol_minimum("09:45"), 1.20)
        self.assertEqual(settings.rvol_minimum("12:00"), 1.05)
        self.assertEqual(settings.rvol_minimum("14:00"), 1.10)


if __name__ == "__main__":
    unittest.main()
