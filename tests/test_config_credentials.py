import os
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from config import CredentialsConfig, ExecutionConfig


class ConfigCredentialTests(unittest.TestCase):
    def test_dotenv_path_is_project_root(self):
        self.assertEqual(config.DOTENV_PATH, Path(config.__file__).resolve().parent / ".env")

    def test_environment_values_are_used_at_instantiation(self):
        env = {
            "CLIENT_CODE": "env-client",
            "PIN": "env-pin",
            "TOTP_SECRET": "env-secret",
            "DHAN_ACCESS_TOKEN": "env-token",
        }
        with patch.dict(os.environ, env, clear=True):
            creds = CredentialsConfig()

        self.assertEqual(creds.client_code, "env-client")
        self.assertEqual(creds.pin, "env-pin")
        self.assertEqual(creds.totp_secret, "env-secret")
        self.assertEqual(creds.access_token, "env-token")

    def test_missing_required_credentials_fail_fast(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CLIENT_CODE"):
                CredentialsConfig()

    def test_run4_execution_defaults_are_observational_and_quiet(self):
        cfg = ExecutionConfig()

        self.assertFalse(cfg.enable_strategy_b)
        self.assertFalse(cfg.enable_strategy_c)
        self.assertFalse(cfg.native_quote_diagnostics)
        self.assertEqual(cfg.strategy_b_setup_max_minutes, 10)
        self.assertEqual(cfg.paper_research_max_daily_trades, 50)
        self.assertEqual(cfg.paper_research_max_open_positions, 5)


if __name__ == "__main__":
    unittest.main()
