from __future__ import annotations

import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "langchain_core.messages" not in sys.modules:
    langchain_core_module = ModuleType("langchain_core")
    messages_module = ModuleType("langchain_core.messages")

    class BaseMessage:  # pragma: no cover - lightweight test stub
        pass

    messages_module.BaseMessage = BaseMessage
    langchain_core_module.messages = messages_module
    sys.modules["langchain_core"] = langchain_core_module
    sys.modules["langchain_core.messages"] = messages_module

from app.sandbox import _resolve_python_bin_path
from app.serialization import make_json_safe


class SerializationTests(unittest.TestCase):
    def test_make_json_safe_converts_bytes_to_text(self) -> None:
        payload = {
            "stdout": b"hello",
            "stderr": bytearray("world", encoding="utf-8"),
            "nested": [memoryview("ok".encode("utf-8"))],
        }

        self.assertEqual(
            make_json_safe(payload),
            {
                "stdout": "hello",
                "stderr": "world",
                "nested": ["ok"],
            },
        )


class SandboxTests(unittest.TestCase):
    def test_resolve_python_bin_prefers_current_interpreter(self) -> None:
        current_python = Path("/tmp/project-venv/bin/python")
        base_python = Path("/tmp/base-python/bin/python")

        with (
            patch("app.sandbox.os.getenv", return_value=""),
            patch("app.sandbox.sys.executable", str(current_python)),
            patch("app.sandbox.sys.prefix", "/tmp/project-venv"),
            patch("app.sandbox.sys.base_prefix", "/tmp/base-python"),
            patch("app.sandbox.sys._base_executable", str(base_python), create=True),
            patch.object(Path, "exists", lambda self: self in {current_python, base_python}),
        ):
            self.assertEqual(_resolve_python_bin_path(), current_python)


if __name__ == "__main__":
    unittest.main()
