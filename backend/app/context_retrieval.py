"""轻量上下文检索：从历史会话与长期记忆中召回相关片段。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.session_memory import SessionTurn, list_session_records, parse_session_turns


MAX_SNIPPET_CHARS = 420
SESSION_SCAN_LIMIT = 12
RECENT_TURNS_TO_SKIP = 4
QUERY_TOKEN_LIMIT = 24
ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9_./:-]{2,}", re.IGNORECASE)
CJK_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class RetrievalSnippet:
    kind: str
    source: str
    title: str
    snippet: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "title": self.title,
            "snippet": self.snippet,
            "score": round(self.score, 4),
        }


def _normalize_space(text: str) -> str:
    return " ".join((text or "").strip().split())


def _clip_text(text: str, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    normalized = _normalize_space(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _cjk_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for block in CJK_TOKEN_PATTERN.findall(text):
        if len(block) <= 4:
            terms.add(block)
            continue
        for index in range(len(block) - 1):
            terms.add(block[index : index + 2])
        for index in range(len(block) - 2):
            terms.add(block[index : index + 3])
    return terms


def _tokenize(text: str) -> set[str]:
    normalized = _normalize_space(text).lower()
    if not normalized:
        return set()
    ascii_terms = set(ASCII_TOKEN_PATTERN.findall(normalized))
    cjk_terms = _cjk_terms(normalized)
    combined = [term for term in (*ascii_terms, *cjk_terms) if term]
    if len(combined) > QUERY_TOKEN_LIMIT * 6:
        combined = combined[: QUERY_TOKEN_LIMIT * 6]
    return set(combined)


def _score_text(query: str, candidate: str) -> float:
    query_text = _normalize_space(query).lower()
    candidate_text = _normalize_space(candidate).lower()
    if not query_text or not candidate_text:
        return 0.0

    query_tokens = _tokenize(query_text)
    candidate_tokens = _tokenize(candidate_text)
    if not query_tokens or not candidate_tokens:
        return 0.0

    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0

    coverage = len(overlap) / max(1, min(len(query_tokens), QUERY_TOKEN_LIMIT))
    density = len(overlap) / max(1, len(candidate_tokens) ** 0.5)
    substring_bonus = 0.25 if query_text in candidate_text else 0.0
    return coverage * 0.75 + density * 0.25 + substring_bonus


def _turns_from_messages(messages: list[dict[str, Any]]) -> list[tuple[SessionTurn, int]]:
    turns: list[tuple[SessionTurn, int]] = []
    index = 0
    turn_number = 0
    while index < len(messages):
        message_index = index
        current = messages[index]
        if current.get("role") != "user":
            index += 1
            continue
        assistant_text = ""
        if index + 1 < len(messages) and messages[index + 1].get("role") == "assistant":
            assistant_text = str(messages[index + 1].get("content") or "")
            index += 2
        else:
            index += 1
        turn_number += 1
        turns.append(
            (
                SessionTurn(
                    turn=turn_number,
                    timestamp=str(current.get("created_at") or ""),
                    user_text=str(current.get("content") or ""),
                    assistant_text=assistant_text,
                ),
                message_index,
            )
        )
    return turns


def _memory_virtual_path(path: Path) -> str:
    settings = get_settings()
    relative = path.resolve().relative_to(settings.project_root.resolve())
    return f"/{relative.as_posix()}"


def _resolve_memory_file(virtual_path: str) -> Path | None:
    settings = get_settings()
    cleaned = virtual_path.strip()
    if not cleaned.startswith("/"):
        return None
    candidate = (settings.project_root / cleaned.lstrip("/")).resolve()
    try:
        candidate.relative_to(settings.project_root.resolve())
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _chunk_markdown(content: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    current_title = "Document"
    buffer: list[str] = []

    def flush() -> None:
        body = _normalize_space("\n".join(buffer))
        if body:
            chunks.append((current_title, body))
        buffer.clear()

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if line.startswith("#"):
            flush()
            current_title = line.lstrip("#").strip() or "Document"
            continue
        if not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()
    return chunks


def _file_signature(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _session_dir_signature(session_dir: Path) -> tuple[tuple[str, int, int], ...]:
    if not session_dir.exists():
        return ()
    entries: list[tuple[str, int, int]] = []
    for path in sorted(session_dir.glob("session_*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


@lru_cache(maxsize=32)
def _cached_session_records(
    project_root: str,
    memory_dir_rel_path: str,
    dir_signature: tuple[tuple[str, int, int], ...],
) -> tuple[Any, ...]:
    del dir_signature
    return tuple(
        list_session_records(
            Path(project_root),
            memory_dir_rel_path=memory_dir_rel_path,
            limit=SESSION_SCAN_LIMIT,
        )
    )


@lru_cache(maxsize=256)
def _cached_session_turns(path_str: str, mtime_ns: int, size: int) -> tuple[SessionTurn, ...]:
    del mtime_ns, size
    return tuple(parse_session_turns(Path(path_str)))


@lru_cache(maxsize=128)
def _cached_markdown_chunks(path_str: str, mtime_ns: int, size: int) -> tuple[tuple[str, str], ...]:
    del mtime_ns, size
    try:
        content = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(_chunk_markdown(content))


def retrieve_relevant_context(session: dict[str, Any], query: str, *, limit: int = 4) -> list[dict[str, Any]]:
    normalized_query = _normalize_space(query)
    if not normalized_query:
        return []

    settings = get_settings()
    candidates: list[RetrievalSnippet] = []
    session_dir = settings.project_root / settings.session_memory_dir_rel_path
    session_records = _cached_session_records(
        str(settings.project_root),
        settings.session_memory_dir_rel_path,
        _session_dir_signature(session_dir),
    )

    current_session_id = str(session.get("id") or "").strip()
    summary_message_count = max(0, int(session.get("summary_message_count") or 0))
    current_turns = _turns_from_messages(session.get("messages", []))
    visible_turns = current_turns[:-RECENT_TURNS_TO_SKIP] if len(current_turns) > RECENT_TURNS_TO_SKIP else []
    for turn, message_index in visible_turns:
        if message_index < summary_message_count:
            continue
        turn_text = f"{turn.user_text}\n{turn.assistant_text}"
        score = _score_text(normalized_query, turn_text)
        if score <= 0:
            continue
        candidates.append(
            RetrievalSnippet(
                kind="session_turn",
                source=f"/sessions/session_{current_session_id}.md#turn-{turn.turn}",
                title=f"Current Session Turn {turn.turn}",
                snippet=_clip_text(turn_text),
                score=score + min(0.15, turn.turn * 0.01),
            )
        )

    for record in session_records:
        if record.session_id == current_session_id:
            continue
        memory_path = session_dir / f"session_{record.session_id}.md"
        signature = _file_signature(memory_path)
        if signature is None:
            continue
        for turn in _cached_session_turns(*signature):
            turn_text = f"{turn.user_text}\n{turn.assistant_text}"
            score = _score_text(normalized_query, turn_text)
            if score <= 0:
                continue
            candidates.append(
                RetrievalSnippet(
                    kind="session_turn",
                    source=f"/sessions/session_{record.session_id}.md#turn-{turn.turn}",
                    title=f"Session {record.session_id[-8:]} Turn {turn.turn}",
                    snippet=_clip_text(turn_text),
                    score=score + 0.05,
                )
            )

    for virtual_path in settings.memory_sources:
        if "session_context" in virtual_path:
            continue
        memory_file = _resolve_memory_file(virtual_path)
        if memory_file is None or memory_file.suffix.lower() != ".md":
            continue
        signature = _file_signature(memory_file)
        if signature is None:
            continue
        for title, chunk in _cached_markdown_chunks(*signature):
            score = _score_text(normalized_query, chunk)
            if score <= 0:
                continue
            candidates.append(
                RetrievalSnippet(
                    kind="memory_file",
                    source=_memory_virtual_path(memory_file),
                    title=title,
                    snippet=_clip_text(chunk),
                    score=score,
                )
            )

    candidates.sort(key=lambda item: item.score, reverse=True)
    results: list[RetrievalSnippet] = []
    seen_keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.source, candidate.title)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        results.append(candidate)
        if len(results) >= limit:
            break
    return [item.as_dict() for item in results]
