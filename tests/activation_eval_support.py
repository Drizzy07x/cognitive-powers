"""Shared scaffolding for the activation-eval tests.

Named without a ``test_`` prefix so unittest discovery does not collect it.

It exists for one reason: the first version of these tests called
``tempfile.mkdtemp`` thirteen times and never removed anything, so a single
suite run left about ninety directories behind and a few runs buried the
operator's temp under a hundred and seventy. Handing out temporary trees
through a base class is what makes the removal impossible to forget, since the
only way to get one is to register its cleanup at the same time.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path


class TempTreeTestCase(unittest.TestCase):
    """A test case whose temporary trees are removed when it finishes."""

    def temp_dir(self, prefix: str = "cp-eval-") -> Path:
        """Return a fresh directory that this test will delete on the way out."""
        path = Path(tempfile.mkdtemp(prefix=prefix))
        # ignore_errors because a Windows test that spawned a process can still
        # be holding a handle when the cleanup runs, and a failure to tidy up
        # must not be reported as a failure of the thing under test.
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def temp_file(self, text: str, *, name: str, prefix: str = "cp-eval-") -> Path:
        """Write one file into a fresh temporary directory and return its path."""
        path = self.temp_dir(prefix) / name
        path.write_text(text, encoding="utf-8")
        return path
