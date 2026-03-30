"""会话级临时 Python 执行工作区。"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


_CURRENT_SESSION_SANDBOX: ContextVar["SessionSandbox | None"] = ContextVar("current_session_sandbox", default=None)
_REGISTERED_SANDBOX_PATHS: set[str] = set()


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n...[truncated {omitted} chars]"


def resolve_session_sandbox_path(project_root: Path, sandbox_root_rel_path: str, session_id: str) -> Path:
    sandbox_root = project_root / sandbox_root_rel_path
    return sandbox_root / f"session_{session_id}"


def _cleanup_sandbox_path(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _resolve_python_bin_path() -> Path:
    configured = os.getenv("SANDBOX_PYTHON_BIN", "").strip()
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            if not configured_path.exists():
                raise RuntimeError(f"SANDBOX_PYTHON_BIN does not exist: {configured}")
            return configured_path.resolve()
        discovered = shutil.which(configured)
        if discovered is None:
            raise RuntimeError(f"SANDBOX_PYTHON_BIN is not executable: {configured}")
        return Path(discovered).resolve()

    candidates: list[Path] = []
    if sys.prefix != sys.base_prefix:
        base_executable = getattr(sys, "_base_executable", None)
        if isinstance(base_executable, str) and base_executable.strip():
            candidates.append(Path(base_executable))

        base_bin = Path(sys.base_prefix) / "bin"
        candidates.extend(
            [
                base_bin / f"python{sys.version_info.major}.{sys.version_info.minor}",
                base_bin / f"python{sys.version_info.major}",
                base_bin / "python3",
                base_bin / "python",
            ]
        )

    candidates.append(Path(sys.executable))

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    return Path(sys.executable).resolve()


@dataclass
class SessionSandbox:
    """为单个会话提供独立的临时工作目录。"""

    project_root: Path
    session_id: str
    sandbox_root_rel_path: str
    cleanup_on_exit: bool = True
    sandbox_path: Path = field(init=False)
    workspace_path: Path = field(init=False)
    runs_path: Path = field(init=False)
    _cleaned_up: bool = field(default=False, init=False)
    _run_index: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        sandbox_root = self.project_root / self.sandbox_root_rel_path
        sandbox_root.mkdir(parents=True, exist_ok=True)
        self.sandbox_path = resolve_session_sandbox_path(
            self.project_root,
            self.sandbox_root_rel_path,
            self.session_id,
        )
        self.workspace_path = self.sandbox_path / "workspace"
        self.runs_path = self.sandbox_path / "runs"
        self.sandbox_path.mkdir(parents=True, exist_ok=True)
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.runs_path.mkdir(parents=True, exist_ok=True)
        if self.cleanup_on_exit:
            sandbox_key = str(self.sandbox_path.resolve())
            if sandbox_key not in _REGISTERED_SANDBOX_PATHS:
                atexit.register(_cleanup_sandbox_path, self.sandbox_path)
                _REGISTERED_SANDBOX_PATHS.add(sandbox_key)

    @property
    def python_bin(self) -> Path:
        return _resolve_python_bin_path()

    def run_python_code(self, code: str, timeout_seconds: float, output_char_limit: int) -> dict[str, object]:
        self._run_index += 1
        script_path = self.runs_path / f"run_{self._run_index}_{uuid4().hex[:8]}.py"
        script_path.write_text(code or "", encoding="utf-8")
        result = self._run_subprocess(
            [str(self.python_bin), str(script_path)],
            cwd=self.workspace_path,
            timeout_seconds=timeout_seconds,
            output_char_limit=output_char_limit,
            operation="run_python_code",
            env_update={"PYTHONPATH": str(self.workspace_path)},
        )
        result["script_path"] = str(script_path)
        return result

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        _cleanup_sandbox_path(self.sandbox_path)
        self._cleaned_up = True

    def _run_subprocess(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        output_char_limit: int,
        operation: str,
        env_update: dict[str, str] | None = None,
    ) -> dict[str, object]:
        env = os.environ.copy()
        if env_update:
            env.update(env_update)
        try:
            completed = subprocess.run(
                args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate_text(exc.stdout or "", output_char_limit)
            stderr = _truncate_text(exc.stderr or "", output_char_limit)
            return {
                "ok": False,
                "operation": operation,
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
                "command": args,
                "cwd": str(cwd),
                "sandbox_path": str(self.sandbox_path),
                "python": str(self.python_bin),
                "stdout": stdout,
                "stderr": stderr,
                "returncode": None,
            }

        return {
            "ok": completed.returncode == 0,
            "operation": operation,
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
            "command": args,
            "cwd": str(cwd),
            "sandbox_path": str(self.sandbox_path),
            "python": str(self.python_bin),
            "stdout": _truncate_text(completed.stdout, output_char_limit),
            "stderr": _truncate_text(completed.stderr, output_char_limit),
            "returncode": completed.returncode,
        }


def get_current_session_sandbox() -> SessionSandbox:
    sandbox = _CURRENT_SESSION_SANDBOX.get()
    if sandbox is None:
        raise RuntimeError("No active session sandbox is bound to the current agent call.")
    return sandbox


@contextmanager
def use_session_sandbox(sandbox: SessionSandbox | None):
    if sandbox is None:
        yield
        return

    token: Token[SessionSandbox | None] = _CURRENT_SESSION_SANDBOX.set(sandbox)
    try:
        yield
    finally:
        try:
            _CURRENT_SESSION_SANDBOX.reset(token)
        except ValueError:
            # 异步流式响应在 GeneratorExit/取消时，可能跨 context 关闭；此时退化为清空当前上下文绑定。
            _CURRENT_SESSION_SANDBOX.set(None)
