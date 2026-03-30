from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROBE_PATH = Path(__file__).resolve().parents[2] / "skills" / "data-digest" / "scripts" / "probe.py"
SPEC = importlib.util.spec_from_file_location("data_digest_probe", PROBE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DataDigestProbeTests(unittest.TestCase):
    def test_probe_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.csv"
            path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "CSV")
            self.assertEqual(report["row_count"], 2)
            self.assertEqual(report["columns"], ["a", "b"])
            self.assertEqual(report["sub_reader"], "readers/dataframe/SKILL.md")

    def test_probe_csv_with_multiline_field_counts_logical_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "multiline.csv"
            path.write_text('id,note\n1,"hello\nworld"\n2,"plain"\n', encoding="utf-8")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "CSV")
            self.assertEqual(report["line_count"], 4)
            self.assertEqual(report["row_count"], 2)
            self.assertFalse(report["row_count_estimated"])
            self.assertEqual(report["columns"], ["id", "note"])

    def test_probe_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.jsonl"
            path.write_text('{"x":1,"y":"a"}\n{"x":2,"y":"b"}\n', encoding="utf-8")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSONL")
            self.assertEqual(report["row_count"], 2)
            self.assertEqual(report["columns"], ["x", "y"])
            self.assertFalse(report["columns_sampled"])
            self.assertEqual(report["sub_reader"], "readers/json/SKILL.md")

    def test_probe_small_json_array_unions_all_object_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.json"
            path.write_text(
                json.dumps([{"alpha": 1}, {"beta": 2}, {"alpha": 3, "gamma": 4}], ensure_ascii=False),
                encoding="utf-8",
            )

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSON")
            self.assertEqual(report["json_type"], "array")
            self.assertEqual(report["columns"], ["alpha", "beta", "gamma"])
            self.assertFalse(report["columns_sampled"])

    def test_probe_small_jsonl_unions_keys_beyond_preview_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schema-drift.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"alpha": 1}',
                        '{"alpha": 2}',
                        '{"alpha": 3}',
                        '{"late_key": 4}',
                        '{"alpha": 5, "late_key": 6}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSONL")
            self.assertEqual(report["columns"], ["alpha", "late_key"])
            self.assertFalse(report["columns_sampled"])

    def test_probe_large_jsonl_marks_columns_as_sampled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large.jsonl"
            filler = "x" * (150 * 1024)
            with path.open("w", encoding="utf-8") as handle:
                for index in range(40):
                    key = "early_key"
                    if index >= 6:
                        key = "late_key"
                    handle.write(json.dumps({key: index, "blob": filler}, ensure_ascii=False))
                    handle.write("\n")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSONL")
            self.assertTrue(report["columns_sampled"])
            self.assertEqual(report["columns"], ["blob", "early_key"])
            self.assertIn("bounded sample of records", " ".join(report["warnings"]))

    def test_probe_gzip_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.csv.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("a,b\n1,2\n")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "CSV")
            self.assertEqual(report["row_count"], 1)
            self.assertEqual(report["columns"], ["a", "b"])
            self.assertEqual(report["sub_reader"], "readers/dataframe/SKILL.md")

    def test_probe_gzip_csv_with_large_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "wide.csv.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("blob\n")
                handle.write("a" * (6 * 1024 * 1024))
                handle.write("\n")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "CSV")
            self.assertEqual(report["row_count"], 1)
            self.assertEqual(report["columns"], ["blob"])
            self.assertEqual(report["sub_reader"], "readers/dataframe/SKILL.md")
            self.assertIsNone(report.get("error"))
            self.assertEqual(report["size_category"], "large")
            self.assertEqual(report["strategy_recommendation"], "Chunked summary")

    def test_probe_gzip_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write('{"a": 1, "b": 2}')

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSON")
            self.assertEqual(report["json_type"], "object")
            self.assertEqual(report["sub_reader"], "readers/json/SKILL.md")

    def test_probe_text_pattern_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.txt"
            path.write_text(
                "2026-01-01 00:00:00 INFO start\n2026-01-01 00:01:00 ERROR boom\n",
                encoding="utf-8",
            )

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "Text")
            self.assertEqual(report["text_pattern"], "log")
            self.assertEqual(report["sub_reader"], "readers/txt/SKILL.md")

    def test_large_gzip_text_uses_bounded_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large.log.gz"
            line = "2026-01-01 00:00:00 INFO " + ("x" * 180) + "\n"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                for _ in range(60000):
                    handle.write(line)

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "Text")
            self.assertTrue(report["line_count_estimated"])
            self.assertIn("bounded decompressed sample", " ".join(report["warnings"]))
            self.assertIn(report["size_category"], {"large", "huge"})

    def test_large_multiline_json_object_uses_line_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "object.json"
            payload = {f"key_{index}": index for index in range(3000)}
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSON")
            self.assertEqual(report["json_type"], "object")
            self.assertEqual(report["size_category"], "large")

    def test_large_minified_json_does_not_fall_back_to_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "minified.json"
            payload = {"blob": "a" * (6 * 1024 * 1024)}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSON")
            self.assertEqual(report["json_type"], "object")
            self.assertEqual(report["size_category"], "large")
            self.assertEqual(report["strategy_recommendation"], "Chunked summary")

    def test_large_gzip_json_uses_decompressed_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large.json.gz"
            payload = {"blob": "a" * (6 * 1024 * 1024)}
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False))

            report = MODULE.probe_file(str(path))

            self.assertEqual(report["file_type"], "JSON")
            self.assertEqual(report["json_type"], "object")
            self.assertEqual(report["size_category"], "large")
            self.assertEqual(report["strategy_recommendation"], "Chunked summary")


if __name__ == "__main__":
    unittest.main()
