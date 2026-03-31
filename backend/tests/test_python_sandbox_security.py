from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sandbox import SessionSandbox


class PythonSandboxSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.sandbox = SessionSandbox(
            project_root=self.project_root,
            session_id="test-python-security",
            sandbox_root_rel_path=".sandbox",
            cleanup_on_exit=False,
        )

    def tearDown(self) -> None:
        self.sandbox.cleanup()
        self.temp_dir.cleanup()

    def test_allows_writing_inside_session_sandbox(self) -> None:
        result = self.sandbox.run_python_code(
            "from pathlib import Path\nPath('artifact.txt').write_text('ok', encoding='utf-8')\nprint('done')\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertTrue(result["ok"])
        self.assertEqual((self.sandbox.workspace_path / "artifact.txt").read_text(encoding="utf-8"), "ok")

    def test_blocks_deleting_file_outside_session_sandbox(self) -> None:
        victim = self.project_root / "victim.txt"
        victim.write_text("important", encoding="utf-8")

        result = self.sandbox.run_python_code(
            f"import os\nos.remove({str(victim)!r})\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("blocked os.remove outside session sandbox", str(result["stderr"]))
        self.assertTrue(victim.exists())

    def test_blocks_deleting_via_dir_fd_outside_session_sandbox(self) -> None:
        outside_dir = self.project_root / "outside"
        outside_dir.mkdir()
        victim = outside_dir / "victim.txt"
        victim.write_text("important", encoding="utf-8")

        result = self.sandbox.run_python_code(
            f"import os\nfd = os.open({str(outside_dir)!r}, os.O_RDONLY)\n"
            "try:\n"
            "    os.remove('victim.txt', dir_fd=fd)\n"
            "finally:\n"
            "    os.close(fd)\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("dir_fd", str(result["stderr"]))
        self.assertTrue(victim.exists())

    def test_blocks_process_creation_even_when_static_scan_is_evaded(self) -> None:
        result = self.sandbox.run_python_code(
            "module_name = 'subprocess'\nmod = __import__(module_name)\nmod.run(['echo', 'hi'], check=False)\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("blocked process creation", str(result["stderr"]))

    def test_blocks_network_access_even_when_static_scan_is_evaded(self) -> None:
        result = self.sandbox.run_python_code(
            "module_name = 'socket'\nmod = __import__(module_name)\nmod.socket()\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("blocked network access", str(result["stderr"]))

    def test_blocks_external_file_reads(self) -> None:
        secret = self.project_root / "secret.txt"
        secret.write_text("top-secret", encoding="utf-8")

        result = self.sandbox.run_python_code(
            f"from pathlib import Path\nprint(Path({str(secret)!r}).read_text(encoding='utf-8'))\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("blocked read outside session sandbox", str(result["stderr"]))

    def test_blocks_external_file_reads_via_dir_fd(self) -> None:
        outside_dir = self.project_root / "outside-read"
        outside_dir.mkdir()
        secret = outside_dir / "secret.txt"
        secret.write_text("top-secret", encoding="utf-8")

        result = self.sandbox.run_python_code(
            f"import os\nfd = os.open({str(outside_dir)!r}, os.O_RDONLY)\n"
            "try:\n"
            "    inner = os.open('secret.txt', os.O_RDONLY, dir_fd=fd)\n"
            "    try:\n"
            "        print(os.read(inner, 100).decode())\n"
            "    finally:\n"
            "        os.close(inner)\n"
            "finally:\n"
            "    os.close(fd)\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("blocked read", str(result["stderr"]))

    def test_does_not_inherit_parent_secret_environment(self) -> None:
        secret_key = "PYTHON_SANDBOX_TEST_SECRET"
        previous = os.environ.get(secret_key)
        os.environ[secret_key] = "should-not-leak"
        try:
            result = self.sandbox.run_python_code(
                f"import os\nprint(os.environ.get({secret_key!r}))\n",
                timeout_seconds=5,
                output_char_limit=4000,
            )
        finally:
            if previous is None:
                os.environ.pop(secret_key, None)
            else:
                os.environ[secret_key] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(str(result["stdout"]).strip(), "None")

    def test_blocks_os_fork(self) -> None:
        result = self.sandbox.run_python_code(
            "import os\npid = os.fork()\nif pid == 0:\n    os._exit(0)\nos.waitpid(pid, 0)\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertIn("blocked process creation via os.fork", str(result["stderr"]))

    def test_allows_standard_library_imports(self) -> None:
        result = self.sandbox.run_python_code(
            "import json\nprint(json.dumps({'ok': True}, sort_keys=True))\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(str(result["stdout"]).strip(), '{"ok": true}')

    def test_blocks_ctypes_imports_during_static_scan(self) -> None:
        result = self.sandbox.run_python_code(
            "import ctypes\nprint('never runs')\n",
            timeout_seconds=5,
            output_char_limit=4000,
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("blocked"))
        self.assertEqual(result.get("error"), "SecurityPolicyViolation")
        self.assertIn("importing blocked module 'ctypes'", str(result["stderr"]))


if __name__ == "__main__":
    unittest.main()
