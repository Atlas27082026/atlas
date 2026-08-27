import os
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from config import CredentialsConfig


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


if __name__ == "__main__":
    unittest.main()
