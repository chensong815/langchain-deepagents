"""Web 会话状态存储。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.context_retrieval import retrieve_relevant_context
from app.serialization import make_json_safe
from app.sandbox import resolve_session_sandbox_path
from app.session_context import render_session_context, session_context_virtual_path
from app.session_memory import (
    SessionMemoryWriter,
    SessionTurn,
    build_session_id,
    build_thread_id,
    list_session_records,
    load_session_record,
    load_session_turns,
)
from app.skill_catalog import list_skills


DEFAULT_TOOL_SWITCHES = {
    "weather": True,
    "knowledge_base": True,
    "python_packages": True,
    "python_code": True,
    "field_lineage_step": True,
    "field_lineage_auto": True,
}

DEFAULT_WORKING_MEMORY = {
    "active_skill": None,
    "recent_tools": [],
    "current_goal": None,
    "confirmed_slots": {},
    "pending_slots": [],
    "artifacts": [],
    "open_loops": [],
}

DEFAULT_TURN_STATE = {
    "status": "idle",
    "phase": None,
    "turn_id": None,
    "user_message_id": None,
    "requested_text": "",
    "selected_skill": None,
    "active_tool": None,
    "tool_count": 0,
    "stop_requested": False,
    "started_at": None,
    "updated_at": None,
    "completed_at": None,
}

MAX_RECENT_TOOLS = 8
MAX_PENDING_SLOTS = 8
MAX_OPEN_LOOPS = 8
MAX_ARTIFACTS = 8
MAX_CONFIRMED_SLOTS = 16
MAX_RETRIEVED_CONTEXT = 4
MAX_GOAL_CHARS = 220
KEY_VALUE_LINE_PATTERN = re.compile(r"^\s*[-*]?\s*([^:\n：]{1,40})\s*[:：]\s*(.{1,200})\s*$", re.MULTILINE)
PATH_PATTERN = re.compile(
    r"(?P<path>(?:/[\w.\-@]+)+|(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.\-]+(?:\.[A-Za-z0-9_.\-]+)?)"
)
ASCII_STATE_TOKEN_PATTERN = re.compile(r"[a-z0-9_./:-]{2,}", re.IGNORECASE)
STATE_PREFIX_PATTERN = re.compile(r"^(?:后续还需|后续需要|还需要|仍需|需要|待|待补|待处理|请|帮我)\s*")
RESOLUTION_MARKERS = ("已完成", "已处理", "已解决", "已补", "已更新", "已添加", "已修复", "完成了", "处理好了")
LOW_SIGNAL_GOALS = {"继续", "继续吧", "继续处理", "go on", "continue", "ok", "好的", "收到"}
SESSION_SUMMARY_KEYS = (
    "id",
    "title",
    "thread_id",
    "model_name",
    "created_at",
    "updated_at",
    "summary",
    "summary_message_count",
    "debug",
    "stats",
)


_SESSION_LOCKS: dict[str, threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _estimate_tokens(text: str) -> int:
    content = (text or "").strip()
    if not content:
        return 0
    return max(1, round(len(content) / 4))


def _default_title(session_id: str) -> str:
    return f"Session {session_id[-8:]}"


def _session_state_dir() -> Path:
    settings = get_settings()
    path = settings.project_root / settings.session_state_dir_rel_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_state_path(session_id: str) -> Path:
    return _session_state_dir() / f"{session_id}.json"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _get_session_lock(session_id: str) -> threading.RLock:
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = threading.RLock()
            _SESSION_LOCKS[session_id] = lock
        return lock


@contextmanager
def _session_guard(session_id: str):
    lock = _get_session_lock(session_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _safe_title(seed: str, fallback: str) -> str:
    title = " ".join(seed.strip().split())
    if not title:
        return fallback
    if len(title) > 48:
        return f"{title[:45].rstrip()}..."
    return title


def _default_skills() -> list[str]:
    settings = get_settings()
    return [item["name"] for item in list_skills(settings.project_root, settings.skill_sources)]


def _default_working_memory() -> dict[str, Any]:
    return deepcopy(DEFAULT_WORKING_MEMORY)


def _default_turn_state() -> dict[str, Any]:
    return deepcopy(DEFAULT_TURN_STATE)


def _normalize_text(value: Any, *, max_chars: int | None = None) -> str:
    text = " ".join(str(value or "").strip().split())
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _state_match_key(value: Any) -> str:
    compact = _normalize_text(value).replace(" ", "")
    compact = STATE_PREFIX_PATTERN.sub("", compact)
    return compact


def _normalize_string_list(raw: Any, *, max_items: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _normalize_text(item, max_chars=220)
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
        if len(values) >= max_items:
            break
    return values


def _normalize_string_map(raw: Any, *, max_items: int) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        key_text = _normalize_text(key, max_chars=64)
        value_text = _normalize_text(value, max_chars=220)
        if not key_text or not value_text:
            continue
        normalized[key_text] = value_text
        if len(normalized) >= max_items:
            break
    return normalized


def _normalize_artifacts(raw: Any, *, max_items: int) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    artifacts: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = _normalize_text(item.get("path"), max_chars=240)
        description = _normalize_text(item.get("description"), max_chars=220)
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        artifacts.append({"path": path, "description": description})
        if len(artifacts) >= max_items:
            break
    return artifacts


def _normalize_retrieved_context(raw: Any, *, max_items: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = _normalize_text(item.get("source"), max_chars=240)
        title = _normalize_text(item.get("title"), max_chars=120)
        snippet = _normalize_text(item.get("snippet"), max_chars=420)
        kind = _normalize_text(item.get("kind"), max_chars=32) or "snippet"
        if not source or not snippet:
            continue
        key = (source, title)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "kind": kind,
                "source": source,
                "title": title or source,
                "snippet": snippet,
                "score": float(item.get("score") or 0.0),
            }
        )
        if len(normalized) >= max_items:
            break
    return normalized


def _message_payload(role: str, content: str) -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "role": role,
        "content": content,
        "state": "completed",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def _raw_payload(kind: str, payload: Any) -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "kind": kind,
        "payload": make_json_safe(payload),
        "created_at": _now_iso(),
    }


def _should_drop_turn_raw_message(kind: str) -> bool:
    return kind in {"system", "user", "assistant", "skill", "tool_start", "tool_end", "error"} or kind.startswith(
        "debug_"
    )


def _session_context_dir() -> Path:
    settings = get_settings()
    path = settings.project_root / settings.session_context_dir_rel_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_context_path(session_id: str) -> Path:
    return _session_context_dir() / f"{session_id}.md"


def _session_raw_log_dir() -> Path:
    settings = get_settings()
    path = settings.project_root / settings.session_log_dir_rel_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_raw_log_path(session_id: str) -> Path:
    return _session_raw_log_dir() / f"{session_id}.ndjson"


def _append_raw_log(session_id: str, kind: str, payload: Any) -> None:
    entry = _raw_payload(kind, payload)
    with _session_raw_log_path(session_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_raw_log(session_id: str) -> list[dict[str, Any]]:
    path = _session_raw_log_path(session_id)
    if not path.exists():
        return []

    messages: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            messages.append(parsed)
    return messages


def _clear_raw_log(session_id: str) -> None:
    path = _session_raw_log_path(session_id)
    if path.exists():
        path.unlink()


def _extract_confirmed_slots(text: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    for match in KEY_VALUE_LINE_PATTERN.finditer(text or ""):
        key = _normalize_text(match.group(1), max_chars=64)
        value = _normalize_text(match.group(2), max_chars=220)
        if not key or not value:
            continue
        slots[key] = value
        if len(slots) >= MAX_CONFIRMED_SLOTS:
            break
    return slots


def _extract_pending_slots(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    candidates: list[str] = []
    for chunk in re.split(r"[\n。！？!?]+", normalized):
        item = _normalize_text(chunk, max_chars=220)
        if not item:
            continue
        if any(token in item for token in ("?", "？", "请", "帮我", "需要", "希望", "想要", "目标")):
            candidates.append(item)
    return _normalize_string_list(candidates, max_items=MAX_PENDING_SLOTS)


def _should_track_goal_as_open_loop(goal: str | None) -> bool:
    normalized = _state_match_key(goal).lower()
    if not normalized:
        return False
    if normalized in LOW_SIGNAL_GOALS:
        return False
    return len(normalized) >= 4


def _extract_open_loops_from_assistant(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    candidates: list[str] = []
    for chunk in re.split(r"[\n。！？!?]+", normalized):
        item = _normalize_text(chunk, max_chars=220)
        if not item:
            continue
        lowered = item.lower()
        if any(token in item for token in ("未完成", "待处理", "待补", "后续", "下一步", "仍需", "TODO")) or "todo" in lowered:
            candidates.append(item)
    return _normalize_string_list(candidates, max_items=MAX_OPEN_LOOPS)


def _state_items_match(left: str, right: str) -> bool:
    left_key = _state_match_key(left).lower()
    right_key = _state_match_key(right).lower()
    if not left_key or not right_key:
        return False
    if left_key in right_key or right_key in left_key:
        return True

    if len(left_key) >= 4 and left_key[: min(len(left_key), 8)] in right_key:
        return True
    if len(right_key) >= 4 and right_key[: min(len(right_key), 8)] in left_key:
        return True

    left_tokens = set(ASCII_STATE_TOKEN_PATTERN.findall(left_key))
    right_tokens = set(ASCII_STATE_TOKEN_PATTERN.findall(right_key))
    return bool(left_tokens and right_tokens and len(left_tokens & right_tokens) >= 2)


def _is_explicitly_resolved(item: str, assistant_text: str) -> bool:
    normalized_assistant = _normalize_text(assistant_text)
    if not normalized_assistant or not any(marker in normalized_assistant for marker in RESOLUTION_MARKERS):
        return False
    for chunk in re.split(r"[\n。！？!?]+", normalized_assistant):
        if any(marker in chunk for marker in RESOLUTION_MARKERS) and _state_items_match(item, chunk):
            return True
    return False


def _merge_state_items(
    existing: list[str],
    additions: list[str],
    *,
    resolution_text: str | None = None,
    max_items: int,
) -> list[str]:
    merged = list(_normalize_string_list(existing, max_items=max_items))
    if resolution_text:
        merged = [item for item in merged if not _is_explicitly_resolved(item, resolution_text)]
    merged.extend(_normalize_string_list(additions, max_items=max_items))
    return _normalize_string_list(merged, max_items=max_items)


def _scan_paths(value: Any, *, results: set[str] | None = None) -> set[str]:
    if results is None:
        results = set()
    if isinstance(value, dict):
        for item in value.values():
            _scan_paths(item, results=results)
        return results
    if isinstance(value, list):
        for item in value:
            _scan_paths(item, results=results)
        return results
    if not isinstance(value, str):
        return results
    for match in PATH_PATTERN.finditer(value):
        path = _normalize_text(match.group("path"), max_chars=240)
        if "/" not in path:
            continue
        results.add(path)
    return results


def _merge_artifacts(existing: list[dict[str, str]], additions: list[dict[str, str]]) -> list[dict[str, str]]:
    merged = list(existing)
    seen_paths = {str(item.get("path") or "").strip() for item in merged if str(item.get("path") or "").strip()}
    for item in additions:
        path = str(item.get("path") or "").strip()
        if not path or path in seen_paths:
            continue
        merged.append({"path": path, "description": _normalize_text(item.get("description"), max_chars=220)})
        seen_paths.add(path)
    return _normalize_artifacts(merged[-MAX_ARTIFACTS:], max_items=MAX_ARTIFACTS)


def _collect_recent_tool_artifacts(raw_messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    additions: list[dict[str, str]] = []
    for entry in raw_messages:
        if entry.get("kind") != "tool_end":
            continue
        payload = entry.get("payload")
        tool_name = _normalize_text(payload.get("tool") if isinstance(payload, dict) else "", max_chars=64)
        for path in sorted(_scan_paths(payload)):
            additions.append({"path": path, "description": f"tool output from {tool_name or 'tool'}"})
            if len(additions) >= MAX_ARTIFACTS:
                return additions
    return additions


def _normalize_working_memory(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    normalized = _default_working_memory()
    active_skill = _normalize_text(raw.get("active_skill"), max_chars=64)
    normalized["active_skill"] = active_skill or None
    normalized["recent_tools"] = _normalize_string_list(raw.get("recent_tools", []), max_items=MAX_RECENT_TOOLS)
    current_goal = _normalize_text(raw.get("current_goal"), max_chars=MAX_GOAL_CHARS)
    normalized["current_goal"] = current_goal or None
    normalized["confirmed_slots"] = _normalize_string_map(raw.get("confirmed_slots", {}), max_items=MAX_CONFIRMED_SLOTS)
    normalized["pending_slots"] = _normalize_string_list(raw.get("pending_slots", []), max_items=MAX_PENDING_SLOTS)
    normalized["artifacts"] = _normalize_artifacts(raw.get("artifacts", []), max_items=MAX_ARTIFACTS)
    normalized["open_loops"] = _normalize_string_list(raw.get("open_loops", []), max_items=MAX_OPEN_LOOPS)
    return normalized


def _normalize_turn_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    normalized = _default_turn_state()
    status = _normalize_text(raw.get("status"), max_chars=32).lower()
    normalized["status"] = status or "idle"
    phase = _normalize_text(raw.get("phase"), max_chars=32).lower()
    normalized["phase"] = phase or None
    normalized["turn_id"] = _normalize_text(raw.get("turn_id"), max_chars=64) or None
    normalized["user_message_id"] = _normalize_text(raw.get("user_message_id"), max_chars=64) or None
    normalized["requested_text"] = _normalize_text(raw.get("requested_text"), max_chars=2000)
    normalized["selected_skill"] = _normalize_text(raw.get("selected_skill"), max_chars=64) or None
    normalized["active_tool"] = _normalize_text(raw.get("active_tool"), max_chars=64) or None
    normalized["tool_count"] = max(0, int(raw.get("tool_count") or 0))
    normalized["stop_requested"] = bool(raw.get("stop_requested"))
    normalized["started_at"] = _normalize_text(raw.get("started_at"), max_chars=64) or None
    normalized["updated_at"] = _normalize_text(raw.get("updated_at"), max_chars=64) or None
    normalized["completed_at"] = _normalize_text(raw.get("completed_at"), max_chars=64) or None
    return normalized


def _record_recent_tool(working_memory: dict[str, Any], tool_name: str) -> None:
    normalized_tool = _normalize_text(tool_name, max_chars=64)
    if not normalized_tool:
        return
    recent_tools = [
        _normalize_text(item, max_chars=64)
        for item in working_memory.get("recent_tools", [])
        if _normalize_text(item, max_chars=64) and _normalize_text(item, max_chars=64) != normalized_tool
    ]
    recent_tools.append(normalized_tool)
    working_memory["recent_tools"] = recent_tools[-MAX_RECENT_TOOLS:]


def _write_session_context(session: dict[str, Any]) -> None:
    context_path = _session_context_path(session["id"])
    _atomic_write_text(context_path, render_session_context(session))


def session_runtime_memory_sources(session_id: str) -> tuple[str, ...]:
    settings = get_settings()
    context_virtual_path = session_context_virtual_path(session_id, settings.session_context_dir_rel_path)
    return tuple(dict.fromkeys((*settings.memory_sources, context_virtual_path)))


def _recompute_stats(session: dict[str, Any]) -> None:
    input_tokens = sum(_estimate_tokens(message["content"]) for message in session["messages"] if message["role"] == "user")
    output_tokens = sum(
        _estimate_tokens(message["content"]) for message in session["messages"] if message["role"] == "assistant"
    )
    system_prompt = str(session.get("system_prompt", "")).strip()
    context_body = system_prompt + "\n" + render_session_context(session)
    session["stats"] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_tokens": _estimate_tokens(context_body),
    }


def _normalize_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session = deepcopy(payload)
    session.setdefault("title", _default_title(session["id"]))
    session.setdefault("summary", "")
    session.setdefault("summary_message_count", 0)
    session.setdefault("messages", [])
    normalized_messages: list[dict[str, Any]] = []
    for message in session["messages"]:
        if not isinstance(message, dict):
            continue
        normalized_message = dict(message)
        normalized_message["state"] = _normalize_text(message.get("state"), max_chars=32).lower() or "completed"
        normalized_messages.append(normalized_message)
    session["messages"] = normalized_messages
    session.setdefault("raw_messages", [])
    session["summary_message_count"] = max(0, min(int(session.get("summary_message_count") or 0), len(session["messages"])))
    session["retrieved_context"] = _normalize_retrieved_context(
        session.get("retrieved_context", []),
        max_items=MAX_RETRIEVED_CONTEXT,
    )
    session.setdefault("tool_switches", deepcopy(DEFAULT_TOOL_SWITCHES))
    session.setdefault("skills_enabled", _default_skills())
    session["working_memory"] = _normalize_working_memory(session.get("working_memory"))
    session["turn_state"] = _normalize_turn_state(session.get("turn_state"))
    session.setdefault("debug", False)
    session.setdefault("system_prompt", get_settings().system_prompt)
    session.setdefault("stats", {"input_tokens": 0, "output_tokens": 0, "context_tokens": 0})
    session["updated_at"] = session.get("updated_at") or _now_iso()
    _recompute_stats(session)
    return session


def _hydrate_session(session: dict[str, Any]) -> dict[str, Any]:
    hydrated = deepcopy(session)
    hydrated["raw_messages"] = _load_raw_log(hydrated["id"])
    return hydrated


def _save_session(session: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_session_payload(session)
    normalized["updated_at"] = _now_iso()
    persisted = deepcopy(normalized)
    persisted["raw_messages"] = []
    _atomic_write_text(
        _session_state_path(normalized["id"]),
        json.dumps(make_json_safe(persisted), ensure_ascii=False, indent=2),
    )
    _write_session_context(normalized)
    return _hydrate_session(normalized)


def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
    summary = {key: deepcopy(session.get(key)) for key in SESSION_SUMMARY_KEYS}
    summary["message_count"] = len(session.get("messages", []))
    return make_json_safe(summary)


def _create_memory_writer(session_id: str, thread_id: str, model_name: str) -> SessionMemoryWriter:
    settings = get_settings()
    return SessionMemoryWriter(
        project_root=settings.project_root,
        thread_id=thread_id,
        model_name=model_name,
        session_id=session_id,
        memory_dir_rel_path=settings.session_memory_dir_rel_path,
    )


def _as_turns(messages: list[dict[str, Any]]) -> list[SessionTurn]:
    turns: list[SessionTurn] = []
    index = 0
    turn_number = 0
    while index < len(messages):
        user_message = messages[index]
        if user_message["role"] != "user":
            index += 1
            continue
        assistant_text = ""
        if index + 1 < len(messages) and messages[index + 1]["role"] == "assistant":
            assistant_text = messages[index + 1]["content"]
            index += 2
        else:
            index += 1
        turn_number += 1
        turns.append(
            SessionTurn(
                turn=turn_number,
                timestamp=user_message["created_at"],
                user_text=user_message["content"],
                assistant_text=assistant_text,
            )
        )
    return turns


def generate_title_from_message(message: str, current_title: str | None = None) -> str | None:
    normalized = message.strip()
    if not normalized:
        return None
    if current_title and not current_title.startswith("Session "):
        return None
    first_line = normalized.splitlines()[0]
    return _safe_title(first_line, current_title or "新会话")


@dataclass
class SessionStore:
    def list_sessions(self) -> list[dict[str, Any]]:
        settings = get_settings()
        sessions: dict[str, dict[str, Any]] = {}
        for path in sorted(_session_state_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            sessions[payload["id"]] = _hydrate_session(_normalize_session_payload(payload))

        for record in list_session_records(settings.project_root, memory_dir_rel_path=settings.session_memory_dir_rel_path, limit=100):
            if record.session_id in sessions:
                continue
            sessions[record.session_id] = self._import_legacy_session(record.session_id, persist=False)

        ordered_sessions = sorted(sessions.values(), key=lambda item: item["updated_at"], reverse=True)
        return [_session_summary(session) for session in ordered_sessions]

    def create_session(self, *, model_name: str | None = None) -> dict[str, Any]:
        settings = get_settings()
        session_id = build_session_id()
        resolved_model = (model_name or settings.model_name).strip() or settings.model_name
        thread_id = build_thread_id(session_id, settings.default_thread_id)
        writer = _create_memory_writer(session_id, thread_id, resolved_model)
        session = {
            "id": session_id,
            "title": _default_title(session_id),
            "thread_id": thread_id,
            "model_name": resolved_model,
            "created_at": writer.started_at,
            "updated_at": writer.started_at,
            "summary": "",
            "summary_message_count": 0,
            "messages": [],
            "raw_messages": [],
            "retrieved_context": [],
            "tool_switches": deepcopy(DEFAULT_TOOL_SWITCHES),
            "skills_enabled": _default_skills(),
            "working_memory": _default_working_memory(),
            "turn_state": _default_turn_state(),
            "debug": False,
            "system_prompt": settings.system_prompt,
        }
        return _save_session(session)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with _session_guard(session_id):
            path = _session_state_path(session_id)
            if path.exists():
                return _hydrate_session(_normalize_session_payload(json.loads(path.read_text(encoding="utf-8"))))
            return self._import_legacy_session(session_id, persist=True)

    def _reset_runtime_state(self, session: dict[str, Any]) -> None:
        session["summary"] = ""
        session["summary_message_count"] = 0
        session["retrieved_context"] = []
        session["working_memory"] = _default_working_memory()
        session["turn_state"] = _default_turn_state()

    def prepare_for_agent_turn(self, session_id: str, user_message: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            working_memory = session["working_memory"]
            current_goal = _normalize_text(user_message, max_chars=MAX_GOAL_CHARS)
            working_memory["current_goal"] = current_goal or None

            confirmed_slots = {
                **working_memory.get("confirmed_slots", {}),
                **_extract_confirmed_slots(user_message),
            }
            working_memory["confirmed_slots"] = _normalize_string_map(confirmed_slots, max_items=MAX_CONFIRMED_SLOTS)

            pending_slots = _extract_pending_slots(user_message)
            working_memory["pending_slots"] = _merge_state_items(
                working_memory.get("pending_slots", []),
                pending_slots,
                max_items=MAX_PENDING_SLOTS,
            )
            new_loops = pending_slots or ([current_goal] if _should_track_goal_as_open_loop(current_goal) else [])
            working_memory["open_loops"] = _merge_state_items(
                working_memory.get("open_loops", []),
                new_loops,
                max_items=MAX_OPEN_LOOPS,
            )

            session["retrieved_context"] = retrieve_relevant_context(
                session,
                user_message,
                limit=MAX_RETRIEVED_CONTEXT,
            )
            return _save_session(session)

    def finalize_agent_turn(self, session_id: str, *, user_message: str, assistant_text: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            working_memory = session["working_memory"]

            confirmed_slots = {
                **working_memory.get("confirmed_slots", {}),
                **_extract_confirmed_slots(user_message),
                **_extract_confirmed_slots(assistant_text),
            }
            working_memory["confirmed_slots"] = _normalize_string_map(confirmed_slots, max_items=MAX_CONFIRMED_SLOTS)

            assistant_open_loops = _extract_open_loops_from_assistant(assistant_text)
            if assistant_text.startswith("[ERROR]"):
                fallback_loops = _extract_pending_slots(user_message)
                working_memory["open_loops"] = _merge_state_items(
                    working_memory.get("open_loops", []),
                    assistant_open_loops or fallback_loops,
                    resolution_text=assistant_text,
                    max_items=MAX_OPEN_LOOPS,
                )
            else:
                working_memory["open_loops"] = _merge_state_items(
                    working_memory.get("open_loops", []),
                    assistant_open_loops,
                    resolution_text=assistant_text,
                    max_items=MAX_OPEN_LOOPS,
                )
            working_memory["pending_slots"] = _merge_state_items(
                working_memory.get("pending_slots", []),
                assistant_open_loops,
                resolution_text=assistant_text,
                max_items=MAX_PENDING_SLOTS,
            )

            additions = [{"path": path, "description": "mentioned in assistant response"} for path in sorted(_scan_paths(assistant_text))]
            additions.extend(_collect_recent_tool_artifacts(session.get("raw_messages", [])))
            working_memory["artifacts"] = _merge_artifacts(working_memory.get("artifacts", []), additions)
            return _save_session(session)

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        model_name: str | None = None,
        debug: bool | None = None,
        tool_switches: dict[str, bool] | None = None,
        skills_enabled: list[str] | None = None,
    ) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            if title is not None:
                session["title"] = _safe_title(title, session["title"])
            if model_name is not None and model_name.strip():
                session["model_name"] = model_name.strip()
            if debug is not None:
                session["debug"] = bool(debug)
            if tool_switches is not None:
                session["tool_switches"] = {**session["tool_switches"], **tool_switches}
            if skills_enabled is not None:
                available = set(_default_skills())
                session["skills_enabled"] = [item for item in skills_enabled if item in available]
                active_skill = session["working_memory"].get("active_skill")
                if active_skill and active_skill not in session["skills_enabled"]:
                    session["working_memory"]["active_skill"] = None
            return _save_session(session)

    def delete_session(self, session_id: str) -> None:
        with _session_guard(session_id):
            settings = get_settings()
            state_path = _session_state_path(session_id)
            if state_path.exists():
                state_path.unlink()
            context_path = _session_context_path(session_id)
            if context_path.exists():
                context_path.unlink()
            raw_log_path = _session_raw_log_path(session_id)
            if raw_log_path.exists():
                raw_log_path.unlink()
            memory_path = settings.project_root / settings.session_memory_dir_rel_path / f"session_{session_id}.md"
            if memory_path.exists():
                memory_path.unlink()
            sandbox_path = resolve_session_sandbox_path(
                settings.project_root,
                settings.sandbox_root_rel_path,
                session_id,
            )
            shutil.rmtree(sandbox_path, ignore_errors=True)

    def append_message(self, session_id: str, *, role: str, content: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            message = _message_payload(role, content)
            session["messages"].append(message)
            session = _save_session(session)
            return session, message

    def update_message_state(self, session_id: str, message_id: str, state: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            normalized_state = _normalize_text(state, max_chars=32).lower() or "completed"
            for message in session["messages"]:
                if message["id"] != message_id:
                    continue
                message["state"] = normalized_state
                message["updated_at"] = _now_iso()
                break
            else:
                raise FileNotFoundError(f"未找到消息: {message_id}")
            return _save_session(session)

    def append_raw_message(self, session_id: str, *, kind: str, payload: Any) -> dict[str, Any]:
        with _session_guard(session_id):
            _append_raw_log(session_id, kind, payload)
            return {"ok": True}

    def start_turn(self, session_id: str, *, turn_id: str, user_message_id: str, requested_text: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            started_at = _now_iso()
            session["turn_state"] = {
                "status": "streaming",
                "phase": "routing",
                "turn_id": turn_id,
                "user_message_id": user_message_id,
                "requested_text": requested_text,
                "selected_skill": None,
                "active_tool": None,
                "tool_count": 0,
                "stop_requested": False,
                "started_at": started_at,
                "updated_at": started_at,
                "completed_at": None,
            }
            return _save_session(session)

    def update_turn_state(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        selected_skill: str | None = None,
        active_tool: str | None = None,
        increment_tool_count: bool = False,
        stop_requested: bool | None = None,
    ) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            turn_state = _normalize_turn_state(session.get("turn_state"))
            active_turn_id = str(turn_state.get("turn_id") or "")
            if turn_id is not None and active_turn_id and active_turn_id != turn_id:
                return session
            if status is not None:
                turn_state["status"] = _normalize_text(status, max_chars=32).lower() or turn_state["status"]
            if phase is not None:
                turn_state["phase"] = _normalize_text(phase, max_chars=32).lower() or None
            if selected_skill is not None:
                turn_state["selected_skill"] = _normalize_text(selected_skill, max_chars=64) or None
            if active_tool is not None:
                turn_state["active_tool"] = _normalize_text(active_tool, max_chars=64) or None
            if increment_tool_count:
                turn_state["tool_count"] = int(turn_state.get("tool_count") or 0) + 1
            if stop_requested is not None:
                turn_state["stop_requested"] = bool(stop_requested)
            turn_state["updated_at"] = _now_iso()
            if turn_state["status"] in {"completed", "interrupted", "error"}:
                turn_state["completed_at"] = turn_state["updated_at"]
                turn_state["active_tool"] = None
            session["turn_state"] = turn_state
            return _save_session(session)

    def request_turn_stop(self, session_id: str) -> dict[str, Any]:
        return self.update_turn_state(
            session_id,
            status="cancelling",
            phase="responding",
            stop_requested=True,
        )

    def should_stop_turn(self, session_id: str, turn_id: str | None = None) -> bool:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            turn_state = _normalize_turn_state(session.get("turn_state"))
            active_turn_id = str(turn_state.get("turn_id") or "")
            if turn_id is not None and active_turn_id and active_turn_id != turn_id:
                return False
            return bool(turn_state.get("stop_requested"))

    def finish_turn(self, session_id: str, *, turn_id: str | None = None, status: str) -> dict[str, Any]:
        return self.update_turn_state(
            session_id,
            turn_id=turn_id,
            status=status,
            phase="",
            active_tool="",
        )

    def replace_message(self, session_id: str, message_id: str, content: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            for message in session["messages"]:
                if message["id"] != message_id:
                    continue
                message["content"] = content
                message["updated_at"] = _now_iso()
                break
            else:
                raise FileNotFoundError(f"未找到消息: {message_id}")
            self._reset_runtime_state(session)
            _clear_raw_log(session_id)
            return _save_session(session)

    def truncate_after_message(self, session_id: str, message_id: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            keep_index = None
            for index, message in enumerate(session["messages"]):
                if message["id"] == message_id:
                    keep_index = index
                    break
            if keep_index is None:
                raise FileNotFoundError(f"未找到消息: {message_id}")

            session["messages"] = session["messages"][: keep_index + 1]
            session["raw_messages"] = []
            self._reset_runtime_state(session)
            _clear_raw_log(session_id)
            session["thread_id"] = build_thread_id(session_id, get_settings().default_thread_id)
            self._rewrite_memory_file(session)
            return _save_session(session)

    def truncate_from_message(self, session_id: str, message_id: str) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            target_index = None
            for index, message in enumerate(session["messages"]):
                if message["id"] == message_id:
                    target_index = index
                    break
            if target_index is None:
                raise FileNotFoundError(f"未找到消息: {message_id}")

            session["messages"] = session["messages"][:target_index]
            session["raw_messages"] = []
            self._reset_runtime_state(session)
            _clear_raw_log(session_id)
            session["thread_id"] = build_thread_id(session_id, get_settings().default_thread_id)
            self._rewrite_memory_file(session)
            return _save_session(session)

    def set_summary(self, session_id: str, summary: str, *, summary_message_count: int | None = None) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            session["summary"] = summary.strip()
            if summary_message_count is not None:
                session["summary_message_count"] = max(0, min(int(summary_message_count), len(session["messages"])))
            saved = _save_session(session)
            _append_raw_log(
                session_id,
                "summary",
                {
                    "content": saved["summary"],
                    "summary_message_count": saved.get("summary_message_count", 0),
                },
            )
            return self.get_session(session_id)

    def set_active_skill(self, session_id: str, skill_name: str | None) -> dict[str, Any]:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            normalized_skill = _normalize_text(skill_name, max_chars=64)
            session["working_memory"]["active_skill"] = normalized_skill or None
            return _save_session(session)

    def record_tool_usage(self, session_id: str, tool_name: str | None) -> dict[str, Any]:
        with _session_guard(session_id):
            normalized = _normalize_text(tool_name, max_chars=64)
            if not normalized:
                return self.get_session(session_id)
            session = self.get_session(session_id)
            _record_recent_tool(session["working_memory"], normalized)
            return _save_session(session)

    def persist_turn_to_memory(self, session_id: str) -> None:
        with _session_guard(session_id):
            session = self.get_session(session_id)
            self._rewrite_memory_file(session)

    def _rewrite_memory_file(self, session: dict[str, Any]) -> None:
        settings = get_settings()
        writer = SessionMemoryWriter.resume(
            project_root=settings.project_root,
            session_id=session["id"],
            model_name=session["model_name"],
            memory_dir_rel_path=settings.session_memory_dir_rel_path,
        )
        writer.rewrite_turns(
            _as_turns(session["messages"]),
            thread_id=session["thread_id"],
            started_at=session["created_at"],
        )

    def _import_legacy_session(self, session_id: str, *, persist: bool) -> dict[str, Any]:
        with _session_guard(session_id):
            settings = get_settings()
            record = load_session_record(settings.project_root, session_id, memory_dir_rel_path=settings.session_memory_dir_rel_path)
            if record is None:
                raise FileNotFoundError(f"未找到会话: {session_id}")
            turns = load_session_turns(
                settings.project_root,
                session_id,
                memory_dir_rel_path=settings.session_memory_dir_rel_path,
            )
            messages: list[dict[str, Any]] = []
            for turn in turns:
                messages.append(
                    {
                        "id": uuid4().hex,
                        "role": "user",
                        "content": turn.user_text,
                        "created_at": turn.timestamp,
                        "updated_at": turn.timestamp,
                    }
                )
                messages.append(
                    {
                        "id": uuid4().hex,
                        "role": "assistant",
                        "content": turn.assistant_text,
                        "created_at": turn.timestamp,
                        "updated_at": turn.timestamp,
                    }
                )
            title_seed = turns[0].user_text if turns else record.session_id
            session = {
                "id": record.session_id,
                "title": _safe_title(title_seed, _default_title(record.session_id)),
                "thread_id": record.thread_id,
                "model_name": record.model_name,
                "created_at": record.started_at,
                "updated_at": record.last_timestamp,
                "summary": "",
                "summary_message_count": 0,
                "messages": messages,
                "raw_messages": [],
                "retrieved_context": [],
                "tool_switches": deepcopy(DEFAULT_TOOL_SWITCHES),
                "skills_enabled": _default_skills(),
                "working_memory": _default_working_memory(),
                "turn_state": _default_turn_state(),
                "debug": False,
                "system_prompt": settings.system_prompt,
            }
            normalized = _normalize_session_payload(session)
            if persist:
                return _save_session(normalized)
            return normalized


session_store = SessionStore()
