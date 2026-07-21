"""Tests that environment configuration overrides file timeout values."""

import unittest

from src.config.loader import load_timeout


class ConfigurationPrecedenceTests(unittest.TestCase):
    def test_environment_overrides_file_timeout(self) -> None:
        file_configuration = {"timeout": "60"}
        environment = {"APP_TIMEOUT": "12"}
        self.assertEqual(load_timeout(file_configuration, environment), 12.0)

    def test_file_timeout_is_used_without_environment_value(self) -> None:
        self.assertEqual(load_timeout({"timeout": "45"}, {}), 45.0)
