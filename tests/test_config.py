import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alina_bot.config import ConfigError, load_settings  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.missing_env = Path(tempfile.gettempdir()) / "zerocoder-no-such.env"

    def test_required_variables(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                load_settings(env_file=self.missing_env)

    def test_valid_credentials(self) -> None:
        env = {
            "VK_TOKEN": "test-token",
            "VK_GROUP_ID": "12345678",
            "GIGACHAT_CREDENTIALS": "test-credentials",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=self.missing_env)
        self.assertEqual(settings.vk_group_id, 12345678)
        self.assertEqual(settings.authorization_key, "test-credentials")
        self.assertEqual(settings.gigachat_model, "GigaChat-2")

    def test_club_prefix_is_accepted(self) -> None:
        env = {
            "VK_TOKEN": "test-token",
            "VK_GROUP_ID": "club12345678",
            "GIGACHAT_CREDENTIALS": "test-credentials",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=self.missing_env)
        self.assertEqual(settings.vk_group_id, 12345678)

    def test_client_id_and_secret_build_authorization_key(self) -> None:
        env = {
            "VK_TOKEN": "test-token",
            "VK_GROUP_ID": "1",
            "GIGACHAT_CLIENT_ID": "client",
            "GIGACHAT_CLIENT_SECRET": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=self.missing_env)
        self.assertTrue(settings.authorization_key)
        self.assertNotIn(":", settings.authorization_key)

    def test_invalid_sales_form_url_is_rejected(self) -> None:
        env = {
            "VK_TOKEN": "test-token",
            "VK_GROUP_ID": "1",
            "GIGACHAT_CREDENTIALS": "test-credentials",
            "ZEROCODER_SALES_FORM_URL": "zerocoder.ru/form",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                load_settings(env_file=self.missing_env)

    def test_sales_form_url_default(self) -> None:
        env = {
            "VK_TOKEN": "test-token",
            "VK_GROUP_ID": "1",
            "GIGACHAT_CREDENTIALS": "test-credentials",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=self.missing_env)
        self.assertEqual(settings.sales_form_url, "https://zerocoder.ru/#popup:call")

    def test_legacy_vk_group_token_is_supported(self) -> None:
        env = {
            "VK_GROUP_TOKEN": "legacy-token",
            "VK_GROUP_ID": "1",
            "GIGACHAT_CREDENTIALS": "test-credentials",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=self.missing_env)
        self.assertEqual(settings.vk_group_token, "legacy-token")


if __name__ == "__main__":
    unittest.main()
