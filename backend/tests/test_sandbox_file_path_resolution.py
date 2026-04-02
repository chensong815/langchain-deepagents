from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from app.server import _read_csv_preview, _resolve_safe_sandbox_file_path


class SandboxFilePathResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.sample_file = (
            self.root / ".sandbox" / "session_test-svg-path" / "workspace" / "chart.svg"
        )
        self.sample_csv = (
            self.root / ".sandbox" / "session_test-svg-path" / "workspace" / "result.csv"
        )
        self.sample_file.parent.mkdir(parents=True, exist_ok=True)
        self.sample_file.write_text("<svg/>", encoding="utf-8")
        self.sample_csv.write_text("id,name\n1,alice\n2,bob\n", encoding="utf-8")

    def tearDown(self) -> None:
        if self.sample_file.exists():
            self.sample_file.unlink()
        if self.sample_csv.exists():
            self.sample_csv.unlink()
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

    def test_reads_csv_preview_with_truncation_flag(self) -> None:
        payload = _read_csv_preview(self.sample_csv.resolve(), limit=1)

        self.assertEqual(payload["columns"], ["id", "name"])
        self.assertEqual(payload["rows"], [["1", "alice"]])
        self.assertEqual(payload["column_count"], 2)
        self.assertEqual(payload["displayed_rows"], 1)
        self.assertTrue(payload["truncated"])


if __name__ == "__main__":
    unittest.main()
