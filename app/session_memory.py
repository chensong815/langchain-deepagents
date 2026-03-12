"""会话落盘模块：按 session 生成独立记忆文件并按轮追加内容。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def build_session_id() -> str:
    """生成会话级唯一 ID，同时作为 session_id / thread_id 的基础。"""
    pid = os.getpid()
    suffix = uuid4().hex[:8]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"pid-{pid}-{stamp}-{suffix}"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _to_block(text: str) -> str:
    content = (text or "").strip()
    if not content:
        content = "(empty)"
    # 避免用户文本中包含 markdown fence 破坏结构。
    content = content.replace("```", "'''")
    return f"```text\n{content}\n```"


SESSION_FILE_PATTERN = re.compile(r"^session_(?P<session_id>.+)\.md$")
SESSION_HEADER_PATTERN = re.compile(r"^## Session (?P<session_id>.+)$", re.MULTILINE)
TURN_HEADER_PATTERN = re.compile(r"^### Turn (?P<turn>\d+)$", re.MULTILINE)
METADATA_PATTERN = re.compile(r"^- (?P<key>[a-z_]+): `(?P<value>.*)`$", re.MULTILINE)
TURN_TIMESTAMP_PATTERN = re.compile(r"^### Turn \d+\n- timestamp: `(?P<timestamp>[^`]+)`$", re.MULTILINE)


@dataclass(frozen=True)
class SessionRecord:
    """历史会话摘要，用于列出和恢复会话。"""

    session_id: str
    thread_id: str
    model_name: str
    started_at: str
    memory_path: Path
    turn_count: int
    last_timestamp: str

    @property
    def memory_virtual_path(self) -> str:
        return "/" + self.memory_path.relative_to(self.memory_path.parents[1]).as_posix()


def _parse_session_record(memory_path: Path) -> SessionRecord | None:
    try:
        content = memory_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    header_match = SESSION_HEADER_PATTERN.search(content)
    if header_match is None:
        return None

    metadata = {match.group("key"): match.group("value") for match in METADATA_PATTERN.finditer(content)}
    thread_id = metadata.get("thread_id", "").strip()
    model_name = metadata.get("model", "").strip()
    started_at = metadata.get("started_at", "").strip()
    if not thread_id or not model_name or not started_at:
        return None

    turn_matches = list(TURN_HEADER_PATTERN.finditer(content))
    timestamp_matches = list(TURN_TIMESTAMP_PATTERN.finditer(content))
    turn_count = int(turn_matches[-1].group("turn")) if turn_matches else 0
    last_timestamp = timestamp_matches[-1].group("timestamp") if timestamp_matches else started_at
    session_id = header_match.group("session_id").strip()

    return SessionRecord(
        session_id=session_id,
        thread_id=thread_id,
        model_name=model_name,
        started_at=started_at,
        memory_path=memory_path,
        turn_count=turn_count,
        last_timestamp=last_timestamp,
    )


def _memory_dir(project_root: Path, memory_dir_rel_path: str) -> Path:
    return project_root / memory_dir_rel_path


def load_session_record(
    project_root: Path,
    session_id: str,
    memory_dir_rel_path: str = "memory",
) -> SessionRecord | None:
    """按 session_id 读取历史会话摘要。"""
    memory_path = _memory_dir(project_root, memory_dir_rel_path) / f"session_{session_id}.md"
    return _parse_session_record(memory_path)


def list_session_records(
    project_root: Path,
    memory_dir_rel_path: str = "memory",
    limit: int = 20,
    include_empty: bool = False,
) -> list[SessionRecord]:
    """列出最近会话，默认按文件修改时间倒序返回。"""
    memory_dir = _memory_dir(project_root, memory_dir_rel_path)
    if not memory_dir.exists():
        return []

    records: list[SessionRecord] = []
    for memory_path in sorted(memory_dir.glob("session_*.md"), key=lambda path: path.stat().st_mtime, reverse=True):
        record = _parse_session_record(memory_path)
        if record is None:
            continue
        if not include_empty and record.turn_count == 0:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return records


@dataclass
class SessionMemoryWriter:
    """将每轮 user/assistant 内容追加到 markdown，按 session 区分。"""

    project_root: Path
    thread_id: str
    model_name: str
    session_id: str | None = None
    memory_dir_rel_path: str = "memory"
    resume_existing: bool = False
    pid: int = field(init=False)
    started_at: str = field(init=False)
    memory_path: Path = field(init=False)
    turn_index: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.pid = os.getpid()
        self.started_at = _now_iso()
        if self.session_id is None:
            self.session_id = build_session_id()
        memory_dir = _memory_dir(self.project_root, self.memory_dir_rel_path)
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_path = memory_dir / f"session_{self.session_id}.md"
        if self.resume_existing and self.memory_path.exists():
            record = _parse_session_record(self.memory_path)
            if record is None:
                raise ValueError(f"无法解析历史会话文件: {self.memory_path}")
            self.thread_id = record.thread_id
            self.started_at = record.started_at
            self.turn_index = record.turn_count
            return

        if self.memory_path.exists():
            raise FileExistsError(f"会话文件已存在，拒绝覆盖: {self.memory_path}")

        self._append_session_header()

    @property
    def memory_virtual_path(self) -> str:
        """返回供 deepagents backend 使用的项目内虚拟路径。"""
        return "/" + self.memory_path.relative_to(self.project_root).as_posix()

    @classmethod
    def resume(
        cls,
        *,
        project_root: Path,
        session_id: str,
        model_name: str,
        memory_dir_rel_path: str = "memory",
    ) -> SessionMemoryWriter:
        """恢复已有会话文件，并继续从已有 turn 之后追加。"""
        record = load_session_record(project_root, session_id, memory_dir_rel_path=memory_dir_rel_path)
        if record is None:
            raise FileNotFoundError(f"未找到会话: {session_id}")
        return cls(
            project_root=project_root,
            thread_id=record.thread_id,
            model_name=model_name,
            session_id=record.session_id,
            memory_dir_rel_path=memory_dir_rel_path,
            resume_existing=True,
        )

    def _append_text(self, text: str) -> None:
        with self.memory_path.open("a", encoding="utf-8") as fh:
            fh.write(text)

    def _append_session_header(self) -> None:
        # 每个会话独立文件，始终写入文件头。
        self._append_text("# Conversation Memory\n\n")
        self._append_text(
            f"## Session {self.session_id}\n"
            f"- pid: `{self.pid}`\n"
            f"- thread_id: `{self.thread_id}`\n"
            f"- model: `{self.model_name}`\n"
            f"- started_at: `{self.started_at}`\n\n"
        )

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        self.turn_index += 1
        self._append_text(
            f"### Turn {self.turn_index}\n"
            f"- timestamp: `{_now_iso()}`\n"
            f"- pid: `{self.pid}`\n"
            f"- thread_id: `{self.thread_id}`\n\n"
            f"**User**\n"
            f"{_to_block(user_text)}\n\n"
            f"**Assistant**\n"
            f"{_to_block(assistant_text)}\n\n"
        )

    def delete_if_empty(self) -> bool:
        """若会话尚未产生 turn，则删除空白 session 文件。"""
        if self.turn_index != 0 or not self.memory_path.exists():
            return False
        self.memory_path.unlink()
        return True
