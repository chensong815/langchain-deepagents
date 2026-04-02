"""会话级 Python 执行工作区。"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from deepagents.backends import FilesystemBackend


_CURRENT_SESSION_SANDBOX: ContextVar["SessionSandbox | None"] = ContextVar("current_session_sandbox", default=None)
_BLOCKED_MODULE_ROOTS = (
    "asyncio.subprocess",
    "ctypes",
    "multiprocessing",
    "socket",
    "subprocess",
)
_SANDBOX_ENV_ALLOWLIST = (
    "PATH",
    "PYTHONPATH",
    "HOME",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONIOENCODING",
)


def _is_blocked_module_name(name: str) -> bool:
    normalized = (name or "").strip()
    return any(normalized == root or normalized.startswith(f"{root}.") for root in _BLOCKED_MODULE_ROOTS)


class _SecurityPolicyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.issues: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_blocked_module_name(alias.name):
                self.issues.append(
                    f"line {node.lineno}: importing blocked module '{alias.name}' is not allowed"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = node.module or ""
        if _is_blocked_module_name(module_name):
            self.issues.append(
                f"line {node.lineno}: importing from blocked module '{module_name}' is not allowed"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target_module = self._extract_dynamic_import_target(node)
        if target_module and _is_blocked_module_name(target_module):
            self.issues.append(
                f"line {node.lineno}: dynamically importing blocked module '{target_module}' is not allowed"
            )
        self.generic_visit(node)

    @staticmethod
    def _extract_dynamic_import_target(node: ast.Call) -> str | None:
        if not node.args:
            return None
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            return None
        if isinstance(node.func, ast.Name) and node.func.id in {"__import__", "import_module"}:
            return first_arg.value
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib" and node.func.attr == "import_module":
                return first_arg.value
        return None


def _collect_security_policy_issues(code: str) -> list[str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        # Keep syntax errors in the normal execution path so users still get the
        # standard traceback with accurate source lines.
        return []
    visitor = _SecurityPolicyVisitor()
    visitor.visit(tree)
    return visitor.issues


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
        configured_path = Path(configured).expanduser()
        if configured_path.is_absolute():
            if not configured_path.exists():
                raise RuntimeError(f"SANDBOX_PYTHON_BIN does not exist: {configured}")
            return configured_path
        discovered = shutil.which(configured)
        if discovered is None:
            raise RuntimeError(f"SANDBOX_PYTHON_BIN is not executable: {configured}")
        return Path(discovered)

    # Prefer the interpreter running the backend so the sandbox inherits the
    # same virtualenv and already-installed packages.
    candidates: list[Path] = [Path(sys.executable)]
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

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return Path(sys.executable)


@dataclass
class SessionSandbox:
    """为单个会话提供独立工作目录。"""

    project_root: Path
    session_id: str
    sandbox_root_rel_path: str
    cleanup_on_exit: bool = False
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

    @property
    def python_bin(self) -> Path:
        return _resolve_python_bin_path()

    def run_python_code(self, code: str, timeout_seconds: float, output_char_limit: int) -> dict[str, object]:
        self._run_index += 1
        script_path = self.runs_path / f"run_{self._run_index}_{uuid4().hex[:8]}.py"
        script_path.write_text(code or "", encoding="utf-8")
        policy_issues = _collect_security_policy_issues(code or "")
        if policy_issues:
            return self._blocked_result(
                script_path=script_path,
                details="; ".join(policy_issues),
                operation="run_python_code",
            )
        launcher_path = self.runs_path / f"launcher_{self._run_index}_{uuid4().hex[:8]}.py"
        launcher_path.write_text(self._build_launcher_script(script_path), encoding="utf-8")
        home_path = self.sandbox_path / "home"
        cache_path = self.sandbox_path / ".cache"
        temp_path = self.workspace_path / ".tmp"
        home_path.mkdir(parents=True, exist_ok=True)
        cache_path.mkdir(parents=True, exist_ok=True)
        temp_path.mkdir(parents=True, exist_ok=True)
        result = self._run_subprocess(
            [str(self.python_bin), str(launcher_path)],
            cwd=self.workspace_path,
            timeout_seconds=timeout_seconds,
            output_char_limit=output_char_limit,
            operation="run_python_code",
            env_update={
                "PYTHONPATH": str(self.workspace_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "HOME": str(home_path),
                "TMPDIR": str(temp_path),
                "XDG_CACHE_HOME": str(cache_path),
            },
        )
        result["script_path"] = str(script_path)
        result["launcher_path"] = str(launcher_path)
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
        env = {
            key: value
            for key in _SANDBOX_ENV_ALLOWLIST
            if (value := os.environ.get(key))
        }
        if env_update:
            env.update(env_update)
        env.setdefault("PYTHONIOENCODING", "utf-8")
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

    def _blocked_result(self, *, script_path: Path, details: str, operation: str) -> dict[str, object]:
        return {
            "ok": False,
            "blocked": True,
            "error": "SecurityPolicyViolation",
            "details": details,
            "operation": operation,
            "timed_out": False,
            "timeout_seconds": None,
            "command": [str(self.python_bin), str(script_path)],
            "cwd": str(self.workspace_path),
            "sandbox_path": str(self.sandbox_path),
            "python": str(self.python_bin),
            "stdout": "",
            "stderr": details,
            "returncode": None,
            "script_path": str(script_path),
        }

    def _build_launcher_script(self, script_path: Path) -> str:
        sandbox_root = str(self.sandbox_path.resolve())
        user_script = str(script_path.resolve())
        return textwrap.dedent(
            f"""
            from __future__ import annotations

            import builtins
            import io
            import os
            import runpy
            import sys
            import sysconfig
            from pathlib import Path

            try:
                import fcntl as _fcntl
            except ImportError:
                _fcntl = None

            _SANDBOX_ROOT = Path({sandbox_root!r}).resolve()
            _USER_SCRIPT = {user_script!r}
            _ORIGINAL_BUILTIN_OPEN = builtins.open
            _ORIGINAL_IO_OPEN = io.open
            _ORIGINAL_OS_ACCESS = os.access
            _ORIGINAL_OS_CHDIR = os.chdir
            _ORIGINAL_OS_LISTDIR = os.listdir
            _ORIGINAL_OS_LSTAT = os.lstat
            _ORIGINAL_OS_MKDIR = os.mkdir
            _ORIGINAL_OS_OPEN = os.open
            _ORIGINAL_OS_READLINK = os.readlink
            _ORIGINAL_OS_REMOVE = os.remove
            _ORIGINAL_OS_RENAME = os.rename
            _ORIGINAL_OS_REPLACE = os.replace
            _ORIGINAL_OS_RMDIR = os.rmdir
            _ORIGINAL_OS_SCANDIR = os.scandir
            _ORIGINAL_OS_STAT = os.stat
            _ORIGINAL_OS_SYMLINK = os.symlink
            _ORIGINAL_OS_TRUNCATE = os.truncate
            _ORIGINAL_OS_UNLINK = os.unlink
            _TRUSTED_IMPORT_ROOTS = []
            _IMPORT_STACK_PREFIXES = (
                "importlib",
                "_frozen_importlib",
                "zipimport",
                "pkgutil",
                "runpy",
                "site",
            )

            for _entry in list(sys.path):
                if not _entry:
                    continue
                try:
                    _TRUSTED_IMPORT_ROOTS.append(Path(_entry).expanduser().resolve(strict=False))
                except Exception:
                    pass
            for _entry in sysconfig.get_paths().values():
                if not _entry:
                    continue
                try:
                    _TRUSTED_IMPORT_ROOTS.append(Path(_entry).expanduser().resolve(strict=False))
                except Exception:
                    pass


            def _absolute_path(path_text):
                return Path(os.path.abspath(os.path.expanduser(os.fsdecode(path_text))))


            def _normalize_dir_fd(dir_fd):
                if not isinstance(dir_fd, int) or dir_fd < 0:
                    return None
                proc_fd_path = f"/proc/self/fd/{{dir_fd}}"
                try:
                    target = _ORIGINAL_OS_READLINK(proc_fd_path)
                except Exception:
                    target = None
                if target:
                    return _absolute_path(target)
                if _fcntl is not None:
                    try:
                        getpath_op = getattr(_fcntl, "F_GETPATH", 50)
                        raw = _fcntl.fcntl(dir_fd, getpath_op, b"\\0" * 1024)
                        target = raw.split(b"\\0", 1)[0].decode()
                        if target:
                            return _absolute_path(target)
                    except Exception:
                        return None
                return None


            def _normalize_path(value, *, dir_fd=None):
                if value is None:
                    return None
                if isinstance(value, int):
                    return _normalize_dir_fd(value)
                try:
                    path_text = os.fsdecode(value)
                except TypeError:
                    return None
                candidate = Path(os.path.expanduser(path_text))
                if not candidate.is_absolute():
                    dir_base = _normalize_dir_fd(dir_fd) if dir_fd is not None and dir_fd != -1 else None
                    candidate = (dir_base or Path.cwd()) / candidate
                return _absolute_path(candidate)


            def _is_allowed_path(path: Path) -> bool:
                return path == _SANDBOX_ROOT or path.is_relative_to(_SANDBOX_ROOT)


            def _is_trusted_import_path(path: Path) -> bool:
                return any(path == root or path.is_relative_to(root) for root in _TRUSTED_IMPORT_ROOTS)


            def _is_import_stack() -> bool:
                frame = sys._getframe(2)
                while frame is not None:
                    module_name = str(frame.f_globals.get("__name__", "") or "")
                    if module_name.startswith(_IMPORT_STACK_PREFIXES):
                        return True
                    frame = frame.f_back
                return False


            def _ensure_allowed_path(path_value, action: str, *, dir_fd=None, allow_trusted_import_read=False) -> None:
                if dir_fd is not None and dir_fd != -1 and _normalize_dir_fd(dir_fd) is None:
                    raise PermissionError(f"Python sandbox blocked {{action}} with unresolved dir_fd={{dir_fd}}")
                path = _normalize_path(path_value, dir_fd=dir_fd)
                if path is None:
                    return
                if allow_trusted_import_read and _is_import_stack() and _is_trusted_import_path(path):
                    return
                if not _is_allowed_path(path):
                    raise PermissionError(f"Python sandbox blocked {{action}} outside session sandbox: {{path}}")


            def _ensure_allowed_pair(
                source_value,
                target_value,
                action: str,
                *,
                source_dir_fd=None,
                target_dir_fd=None,
            ) -> None:
                _ensure_allowed_path(source_value, action, dir_fd=source_dir_fd)
                _ensure_allowed_path(target_value, action, dir_fd=target_dir_fd)


            def _is_write_open(mode, flags) -> bool:
                if isinstance(mode, str) and any(token in mode for token in ("w", "a", "x", "+")):
                    return True
                if isinstance(flags, int):
                    if flags & getattr(os, "O_CREAT", 0):
                        return True
                    if flags & getattr(os, "O_TRUNC", 0):
                        return True
                    if flags & getattr(os, "O_APPEND", 0):
                        return True
                    return (flags & os.O_ACCMODE) != os.O_RDONLY
                return False


            def _guard_open_path(path, mode, flags, *, dir_fd=None) -> None:
                if _is_write_open(mode, flags):
                    _ensure_allowed_path(path, "write", dir_fd=dir_fd)
                else:
                    _ensure_allowed_path(path, "read", dir_fd=dir_fd, allow_trusted_import_read=True)


            def _guard_directory_read(path, action: str, *, dir_fd=None) -> None:
                _ensure_allowed_path(path, action, dir_fd=dir_fd, allow_trusted_import_read=True)


            def _guard_stat_read(path, action: str, *, dir_fd=None) -> None:
                _ensure_allowed_path(path, action, dir_fd=dir_fd, allow_trusted_import_read=True)


            def _sandbox_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
                _guard_open_path(file, mode, 0)
                return _ORIGINAL_BUILTIN_OPEN(file, mode, buffering, encoding, errors, newline, closefd, opener)


            def _sandbox_io_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
                _guard_open_path(file, mode, 0)
                return _ORIGINAL_IO_OPEN(file, mode, buffering, encoding, errors, newline, closefd, opener)


            def _sandbox_os_open(path, flags, mode=0o777, *, dir_fd=None):
                _guard_open_path(path, None, flags, dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_OPEN(path, flags, mode, dir_fd=dir_fd)
                return _ORIGINAL_OS_OPEN(path, flags, mode)


            def _sandbox_remove(path, *, dir_fd=None):
                _ensure_allowed_path(path, "os.remove", dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_REMOVE(path, dir_fd=dir_fd)
                return _ORIGINAL_OS_REMOVE(path)


            def _sandbox_unlink(path, *, dir_fd=None):
                _ensure_allowed_path(path, "os.unlink", dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_UNLINK(path, dir_fd=dir_fd)
                return _ORIGINAL_OS_UNLINK(path)


            def _sandbox_mkdir(path, mode=0o777, *, dir_fd=None):
                _ensure_allowed_path(path, "os.mkdir", dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_MKDIR(path, mode, dir_fd=dir_fd)
                return _ORIGINAL_OS_MKDIR(path, mode)


            def _sandbox_rmdir(path, *, dir_fd=None):
                _ensure_allowed_path(path, "os.rmdir", dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_RMDIR(path, dir_fd=dir_fd)
                return _ORIGINAL_OS_RMDIR(path)


            def _sandbox_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
                _ensure_allowed_pair(src, dst, "os.rename", source_dir_fd=src_dir_fd, target_dir_fd=dst_dir_fd)
                kwargs = {{}}
                if src_dir_fd is not None:
                    kwargs["src_dir_fd"] = src_dir_fd
                if dst_dir_fd is not None:
                    kwargs["dst_dir_fd"] = dst_dir_fd
                return _ORIGINAL_OS_RENAME(src, dst, **kwargs)


            def _sandbox_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
                _ensure_allowed_pair(src, dst, "os.replace", source_dir_fd=src_dir_fd, target_dir_fd=dst_dir_fd)
                kwargs = {{}}
                if src_dir_fd is not None:
                    kwargs["src_dir_fd"] = src_dir_fd
                if dst_dir_fd is not None:
                    kwargs["dst_dir_fd"] = dst_dir_fd
                return _ORIGINAL_OS_REPLACE(src, dst, **kwargs)


            def _sandbox_symlink(src, dst, target_is_directory=False, *, dir_fd=None):
                _ensure_allowed_path(src, "os.symlink source")
                _ensure_allowed_path(dst, "os.symlink destination", dir_fd=dir_fd)
                raise PermissionError("Python sandbox blocked symbolic link creation")


            def _sandbox_stat(path, *, dir_fd=None, follow_symlinks=True):
                _guard_stat_read(path, "os.stat", dir_fd=dir_fd)
                kwargs = {{"follow_symlinks": follow_symlinks}}
                if dir_fd is not None:
                    kwargs["dir_fd"] = dir_fd
                return _ORIGINAL_OS_STAT(path, **kwargs)


            def _sandbox_lstat(path, *, dir_fd=None):
                _guard_stat_read(path, "os.lstat", dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_LSTAT(path, dir_fd=dir_fd)
                return _ORIGINAL_OS_LSTAT(path)


            def _sandbox_access(path, mode, *, dir_fd=None, effective_ids=False, follow_symlinks=True):
                _guard_stat_read(path, "os.access", dir_fd=dir_fd)
                kwargs = {{
                    "effective_ids": effective_ids,
                    "follow_symlinks": follow_symlinks,
                }}
                if dir_fd is not None:
                    kwargs["dir_fd"] = dir_fd
                return _ORIGINAL_OS_ACCESS(path, mode, **kwargs)


            def _sandbox_readlink(path, *, dir_fd=None):
                _guard_stat_read(path, "os.readlink", dir_fd=dir_fd)
                if dir_fd is not None:
                    return _ORIGINAL_OS_READLINK(path, dir_fd=dir_fd)
                return _ORIGINAL_OS_READLINK(path)


            def _sandbox_listdir(path="."):
                _guard_directory_read(path, "os.listdir")
                return _ORIGINAL_OS_LISTDIR(path)


            def _sandbox_scandir(path="."):
                _guard_directory_read(path, "os.scandir")
                return _ORIGINAL_OS_SCANDIR(path)


            def _sandbox_chdir(path):
                _ensure_allowed_path(path, "os.chdir")
                return _ORIGINAL_OS_CHDIR(path)


            def _sandbox_truncate(path, length):
                _ensure_allowed_path(path, "os.truncate")
                return _ORIGINAL_OS_TRUNCATE(path, length)


            def _audit_hook(event, args):
                if event == "open":
                    path = args[0] if len(args) > 0 else None
                    mode = args[1] if len(args) > 1 else None
                    flags = args[2] if len(args) > 2 else 0
                    _guard_open_path(path, mode, flags)
                    return

                if event in {{"os.remove", "os.rmdir"}}:
                    target = args[0] if args else None
                    dir_fd = args[1] if len(args) > 1 and isinstance(args[1], int) else None
                    _ensure_allowed_path(target, event, dir_fd=dir_fd)
                    return

                if event == "os.mkdir":
                    target = args[0] if args else None
                    dir_fd = args[2] if len(args) > 2 and isinstance(args[2], int) and args[2] != -1 else None
                    _ensure_allowed_path(target, event, dir_fd=dir_fd)
                    return

                if event == "os.truncate":
                    target = args[0] if args else None
                    _ensure_allowed_path(target, event)
                    return

                if event == "os.chmod":
                    target = args[0] if args else None
                    dir_fd = args[2] if len(args) > 2 and isinstance(args[2], int) and args[2] != -1 else None
                    _ensure_allowed_path(target, event, dir_fd=dir_fd)
                    return

                if event == "os.chown":
                    target = args[0] if args else None
                    dir_fd = args[3] if len(args) > 3 and isinstance(args[3], int) and args[3] != -1 else None
                    _ensure_allowed_path(target, event, dir_fd=dir_fd)
                    return

                if event == "os.utime":
                    target = args[0] if args else None
                    dir_fd = args[3] if len(args) > 3 and isinstance(args[3], int) and args[3] != -1 else None
                    _ensure_allowed_path(target, event, dir_fd=dir_fd)
                    return

                if event in {{"os.rename", "os.replace", "os.link"}}:
                    source = args[0] if len(args) > 0 else None
                    target = args[1] if len(args) > 1 else None
                    source_dir_fd = args[2] if len(args) > 2 and isinstance(args[2], int) and args[2] != -1 else None
                    target_dir_fd = args[3] if len(args) > 3 and isinstance(args[3], int) and args[3] != -1 else None
                    _ensure_allowed_pair(
                        source,
                        target,
                        event,
                        source_dir_fd=source_dir_fd,
                        target_dir_fd=target_dir_fd,
                    )
                    return

                if event == "os.symlink":
                    source = args[0] if len(args) > 0 else None
                    target = args[1] if len(args) > 1 else None
                    dir_fd = args[2] if len(args) > 2 and isinstance(args[2], int) and args[2] != -1 else None
                    _ensure_allowed_path(source, "os.symlink source")
                    _ensure_allowed_path(target, event, dir_fd=dir_fd)
                    raise PermissionError("Python sandbox blocked symbolic link creation")

                if event.startswith("subprocess.") or event in {{"os.system", "os.posix_spawn", "os.fork", "os.forkpty"}} or event.startswith("os.spawn"):
                    raise PermissionError(f"Python sandbox blocked process creation via {{event}}")

                if event.startswith("socket."):
                    raise PermissionError(f"Python sandbox blocked network access via {{event}}")

                if event.startswith("ctypes."):
                    raise PermissionError(f"Python sandbox blocked dynamic library access via {{event}}")


            builtins.open = _sandbox_open
            io.open = _sandbox_io_open
            os.open = _sandbox_os_open
            os.remove = _sandbox_remove
            os.unlink = _sandbox_unlink
            os.mkdir = _sandbox_mkdir
            os.rmdir = _sandbox_rmdir
            os.rename = _sandbox_rename
            os.replace = _sandbox_replace
            os.symlink = _sandbox_symlink
            os.stat = _sandbox_stat
            os.lstat = _sandbox_lstat
            os.access = _sandbox_access
            os.readlink = _sandbox_readlink
            os.listdir = _sandbox_listdir
            os.scandir = _sandbox_scandir
            os.chdir = _sandbox_chdir
            os.truncate = _sandbox_truncate
            sys.addaudithook(_audit_hook)
            runpy.run_path(_USER_SCRIPT, run_name="__main__")
            """
        ).lstrip()


def get_current_session_sandbox() -> SessionSandbox:
    sandbox = _CURRENT_SESSION_SANDBOX.get()
    if sandbox is None:
        raise RuntimeError("No active session sandbox is bound to the current agent call.")
    return sandbox


def resolve_agent_workspace_path(project_root: Path, sandbox_root_rel_path: str) -> Path:
    del project_root, sandbox_root_rel_path
    sandbox = get_current_session_sandbox()
    sandbox.workspace_path.mkdir(parents=True, exist_ok=True)
    return sandbox.workspace_path


def create_session_workspace_backend(project_root: Path, sandbox_root_rel_path: str) -> "FilesystemBackend":
    from deepagents.backends import FilesystemBackend

    return FilesystemBackend(
        root_dir=resolve_agent_workspace_path(project_root, sandbox_root_rel_path),
        virtual_mode=True,
    )


def resolve_path_in_session_workspace(raw_path: str, *, sandbox: SessionSandbox | None = None) -> Path:
    active_sandbox = sandbox or get_current_session_sandbox()
    workspace_root = active_sandbox.workspace_path.resolve()
    expanded = Path(raw_path).expanduser()
    candidate = expanded if expanded.is_absolute() else workspace_root / expanded
    resolved = candidate.resolve()
    if resolved != workspace_root and not resolved.is_relative_to(workspace_root):
        raise ValueError(f"路径必须位于当前会话 workspace 内：{raw_path}")
    return resolved


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
