from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from app.server import _resolve_safe_sandbox_file_path


class SandboxFilePathResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.sample_file = (
            self.root / ".sandbox" / "session_test-svg-path" / "workspace" / "chart.svg"
        )
        self.sample_file.parent.mkdir(parents=True, exist_ok=True)
        self.sample_file.write_text("<svg/>", encoding="utf-8")

    def tearDown(self) -> None:
        if self.sample_file.exists():
            self.sample_file.unlink()
        for path in [self.sample_file.parent, self.sample_file.parent.parent, self.sample_file.parent.parent.parent]:
            try:
                path.rmdir()
            except OSError:
                pass

    def test_accepts_project_relative_sandbox_path(self) -> None:
        resolved = _resolve_safe_sandbox_file_path(".sandbox/session_test-svg-path/workspace/chart.svg")
        self.assertEqual(resolved, self.sample_file.resolve())

    def test_accepts_legacy_root_prefixed_sandbox_path(self) -> None:
        resolved = _resolve_safe_sandbox_file_path("/.sandbox/session_test-svg-path/workspace/chart.svg")
        self.assertEqual(resolved, self.sample_file.resolve())

    def test_rejects_non_sandbox_absolute_path(self) -> None:
        with self.assertRaises(HTTPException):
            _resolve_safe_sandbox_file_path("/tmp/not-allowed.svg")


if __name__ == "__main__":
    unittest.main()
