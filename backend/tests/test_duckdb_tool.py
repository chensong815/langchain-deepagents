from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools import DuckDBRunner, run_duckdb_sql


class _FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]], columns: list[str]) -> None:
        self._rows = rows
        self.description = [(column, None, None, None, None, None, None) for column in columns]

    def execute(self, sql: str) -> None:
        self.sql = sql

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, rows: list[tuple[object, ...]], columns: list[str]) -> None:
        self._cursor = _FakeCursor(rows, columns)

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        return None


class DuckDBToolTests(unittest.TestCase):
    def test_runner_rejects_mutating_sql(self) -> None:
        runner = DuckDBRunner(db_path=":memory:", sql_limit_rows=10)

        ok, digest, preview, error = runner.execute("DELETE FROM metrics", meta={})

        self.assertFalse(ok)
        self.assertEqual(digest, {})
        self.assertIsNone(preview)
        self.assertIn("只读查询", error or "")

    def test_runner_builds_digest_and_preview(self) -> None:
        rows = [
            ("2026-01-01", 10),
            ("2026-02-01", 20),
        ]
        columns = ["order_date", "amount"]
        fake_connection = _FakeConnection(rows, columns)
        fake_duckdb = SimpleNamespace(connect=lambda **_: fake_connection)

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_sandbox = SimpleNamespace(workspace_path=Path(temp_dir))
            with patch("app.tools._import_duckdb", return_value=fake_duckdb):
                with patch("app.tools.get_current_session_sandbox", return_value=fake_sandbox):
                    runner = DuckDBRunner(db_path=":memory:", sql_limit_rows=1)
                    ok, digest, preview, error = runner.execute("SELECT * FROM metrics", meta={})

                    self.assertTrue(Path(digest["result_file_path"]).exists())
                    self.assertEqual(
                        Path(digest["result_file_path"]).read_text(encoding="utf-8"),
                        "order_date,amount\n2026-01-01,10\n2026-02-01,20\n",
                    )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(digest["rows"], 2)
        self.assertEqual(digest["preview_rows"], 1)
        self.assertEqual(digest["period"], ["2026-01-01", "2026-02-01"])
        self.assertAlmostEqual(digest["keyvals"]["mean"], 15.0)
        self.assertEqual(digest["preview_records"], [{"order_date": "2026-01-01", "amount": 10}])
        self.assertEqual(digest["summary_text"], "返回结果：共2行，2个字段，预览如下（仅展示前1行）：")
        self.assertIn("| order_date | amount |", digest["preview_markdown"])
        self.assertIn("order_date,amount", preview or "")
        self.assertIn("等2行数据", preview or "")

    def test_tool_returns_json_payload(self) -> None:
        rows = [(1, "alice")]
        columns = ["id", "name"]
        fake_connection = _FakeConnection(rows, columns)
        fake_duckdb = SimpleNamespace(connect=lambda **_: fake_connection)

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_sandbox = SimpleNamespace(workspace_path=Path(temp_dir))
            with patch("app.tools._import_duckdb", return_value=fake_duckdb):
                with patch("app.tools.get_current_session_sandbox", return_value=fake_sandbox):
                    raw = run_duckdb_sql.invoke(
                        {
                            "db_path": ":memory:",
                            "sql": "SELECT * FROM users",
                            "max_rows": 10,
                        }
                    )

                    payload = json.loads(raw)
                    self.assertTrue(Path(payload["file_path"]).exists())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["digest"]["rows"], 1)
        self.assertEqual(payload["summary_text"], "返回结果：共1行，2个字段，预览如下：")
        self.assertEqual(payload["file_path"], payload["result_file_path"])
        self.assertEqual(payload["preview_records"], [{"id": 1, "name": "alice"}])
        self.assertIn("| id | name |", payload["preview_markdown"])
        self.assertIn("id,name", payload["preview"])

    def test_tool_uses_env_db_path_when_input_missing(self) -> None:
        rows = [(1,)]
        columns = ["id"]
        fake_connection = _FakeConnection(rows, columns)
        fake_duckdb = SimpleNamespace(connect=lambda **_: fake_connection)

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_sandbox = SimpleNamespace(workspace_path=Path(temp_dir))
            with patch.dict(os.environ, {"DB_PATH": ":memory:"}, clear=False):
                with patch("app.tools._import_duckdb", return_value=fake_duckdb):
                    with patch("app.tools.get_current_session_sandbox", return_value=fake_sandbox):
                        raw = run_duckdb_sql.invoke(
                            {
                                "sql": "SELECT 1",
                                "max_rows": 10,
                            }
                        )

                        payload = json.loads(raw)
                        self.assertTrue(Path(payload["file_path"]).exists())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["resolved_db_path"], ":memory:")

    def test_runner_allows_database_path_outside_session_workspace(self) -> None:
        rows = [(1,)]
        columns = ["id"]
        fake_connection = _FakeConnection(rows, columns)
        fake_duckdb = SimpleNamespace(connect=lambda **_: fake_connection)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_sandbox = SimpleNamespace(workspace_path=temp_root / "workspace")
            fake_sandbox.workspace_path.mkdir(parents=True, exist_ok=True)
            outside_db = temp_root / "outside.duckdb"
            outside_db.write_text("not-a-real-db", encoding="utf-8")

            with patch("app.tools._import_duckdb", return_value=fake_duckdb):
                with patch("app.tools.get_current_session_sandbox", return_value=fake_sandbox):
                    runner = DuckDBRunner(db_path=str(outside_db), sql_limit_rows=10)
                    ok, digest, preview, error = runner.execute("SELECT 1", meta={})

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(digest["db_path"], str(outside_db.resolve()))
        self.assertTrue(str(digest["result_file_path"]).startswith(str(fake_sandbox.workspace_path.resolve())))
        self.assertIn("共1行数据", preview or "")


if __name__ == "__main__":
    unittest.main()
