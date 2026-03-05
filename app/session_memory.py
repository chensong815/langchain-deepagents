"""会话落盘模块：按轮次将对话历史写入 memory/memory.md。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _to_block(text: str) -> str:
    content = (text or "").strip()
    if not content:
        content = "(empty)"
    # 避免用户文本中包含 markdown fence 破坏结构。
    content = content.replace("```", "'''")
    return f"```text\n{content}\n```"


@dataclass
class SessionMemoryWriter:
    """将每轮 user/assistant 内容追加到 markdown，按 session 区分。"""

    project_root: Path
    thread_id: str
    model_name: str
    memory_dir_rel_path: str = "memory"
    pid: int = field(init=False)
    session_id: str = field(init=False)
    started_at: str = field(init=False)
    memory_path: Path = field(init=False)
    turn_index: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.pid = os.getpid()
        self.started_at = _now_iso()
        suffix = uuid4().hex[:8]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_id = f"pid-{self.pid}-{stamp}-{suffix}"
        memory_dir = self.project_root / self.memory_dir_rel_path
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_path = memory_dir / f"session_{self.session_id}.md"
        self._append_session_header()

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
