from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "dotenv" not in sys.modules:
    dotenv_module = ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: False
    sys.modules["dotenv"] = dotenv_module

if "yaml" not in sys.modules:
    yaml_module = ModuleType("yaml")
    yaml_module.safe_load = lambda *args, **kwargs: {}
    sys.modules["yaml"] = yaml_module

if "langchain_core.messages" not in sys.modules:
    langchain_core_module = ModuleType("langchain_core")
    messages_module = ModuleType("langchain_core.messages")

    class BaseMessage:  # pragma: no cover - lightweight test stub
        pass

    messages_module.BaseMessage = BaseMessage
    langchain_core_module.messages = messages_module
    sys.modules["langchain_core"] = langchain_core_module
    sys.modules["langchain_core.messages"] = messages_module

from app.sandbox import SessionSandbox, resolve_session_sandbox_path
from app.session_store import SessionStore


class SessionSandboxLifecycleTests(unittest.TestCase):
    def test_sandbox_persists_until_explicit_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            sandbox = SessionSandbox(
                project_root=project_root,
                session_id="persistent-session",
                sandbox_root_rel_path=".sandbox",
                cleanup_on_exit=True,
            )
            artifact_path = sandbox.workspace_path / "artifact.txt"
            artifact_path.write_text("keep me", encoding="utf-8")

            resumed = SessionSandbox(
                project_root=project_root,
                session_id="persistent-session",
                sandbox_root_rel_path=".sandbox",
                cleanup_on_exit=True,
            )

            self.assertTrue(artifact_path.exists())
            self.assertEqual((resumed.workspace_path / "artifact.txt").read_text(encoding="utf-8"), "keep me")

            resumed.cleanup()
            self.assertFalse(resumed.sandbox_path.exists())

    def test_delete_session_removes_session_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            session_id = "delete-target"
            settings = SimpleNamespace(
                project_root=project_root,
                session_state_dir_rel_path="data/sessions",
                session_context_dir_rel_path="data/session_context",
                session_log_dir_rel_path="data/session_logs",
                session_memory_dir_rel_path="sessions",
                sandbox_root_rel_path=".sandbox",
            )

            state_path = project_root / settings.session_state_dir_rel_path / f"{session_id}.json"
            context_path = project_root / settings.session_context_dir_rel_path / f"{session_id}.md"
            raw_log_path = project_root / settings.session_log_dir_rel_path / f"{session_id}.ndjson"
            memory_path = project_root / settings.session_memory_dir_rel_path / f"session_{session_id}.md"
            sandbox_path = resolve_session_sandbox_path(project_root, settings.sandbox_root_rel_path, session_id)

            for path in (state_path, context_path, raw_log_path, memory_path, sandbox_path / "workspace" / "artifact.txt"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("present", encoding="utf-8")

            with patch("app.session_store.get_settings", return_value=settings):
                SessionStore().delete_session(session_id)

            self.assertFalse(state_path.exists())
            self.assertFalse(context_path.exists())
            self.assertFalse(raw_log_path.exists())
            self.assertFalse(memory_path.exists())
            self.assertFalse(sandbox_path.exists())


if __name__ == "__main__":
    unittest.main()
