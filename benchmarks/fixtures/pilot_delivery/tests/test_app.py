from __future__ import annotations

import unittest

from app import release_name


class ReleaseTests(unittest.TestCase):
    def test_release_name(self) -> None:
        self.assertEqual(release_name(), "2.0")


if __name__ == "__main__":
    unittest.main()
