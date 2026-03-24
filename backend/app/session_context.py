"""会话运行时上下文文件：摘要、结构化工作记忆、检索结果与少量最近轮次。"""

from __future__ import annotations

from typing import Any


MAX_RECENT_TURNS = 4
MAX_USER_CHARS = 500
MAX_ASSISTANT_CHARS = 900
MAX_ITEM_CHARS = 220


def session_context_virtual_path(session_id: str, context_dir_rel_path: str) -> str:
    return f"/{context_dir_rel_path.strip('/')}/{session_id}.md"


def _clip_text(text: str, max_chars: int) -> str:
    content = " ".join((text or "").strip().split())
    if not content:
        return "(empty)"
    if len(content) <= max_chars:
        return content
    return content[: max_chars - 3].rstrip() + "..."


def _group_recent_turns(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    index = 0
    while index < len(messages):
        current = messages[index]
        if current.get("role") != "user":
            index += 1
            continue
        user_text = str(current.get("content") or "")
        assistant_text = ""
        if index + 1 < len(messages) and messages[index + 1].get("role") == "assistant":
            assistant_text = str(messages[index + 1].get("content") or "")
            index += 2
        else:
            index += 1
        turns.append((user_text, assistant_text))
    return turns[-MAX_RECENT_TURNS:]


def _string_list(raw: Any, *, max_items: int = 8) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:max_items]


def _string_map(raw: Any, *, max_items: int = 12) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if not key_text or not value_text:
            continue
        normalized[key_text] = value_text
        if len(normalized) >= max_items:
            break
    return normalized


def _artifact_list(raw: Any, *, max_items: int = 8) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    artifacts: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        description = str(item.get("description") or "").strip()
        if not path:
            continue
        artifacts.append({"path": path, "description": description})
        if len(artifacts) >= max_items:
            break
    return artifacts


def render_session_context(session: dict[str, Any]) -> str:
    summary = str(session.get("summary") or "").strip()
    working_memory = session.get("working_memory", {}) if isinstance(session.get("working_memory"), dict) else {}
    active_skill = str(working_memory.get("active_skill") or "").strip() or "(none)"
    recent_tools = _string_list(working_memory.get("recent_tools", []))
    current_goal = str(working_memory.get("current_goal") or "").strip()
    confirmed_slots = _string_map(working_memory.get("confirmed_slots", {}))
    pending_slots = _string_list(working_memory.get("pending_slots", []))
    open_loops = _string_list(working_memory.get("open_loops", []))
    artifacts = _artifact_list(working_memory.get("artifacts", []))
    retrieved_context = session.get("retrieved_context", []) if isinstance(session.get("retrieved_context"), list) else []
    recent_turns = _group_recent_turns(session.get("messages", []))

    lines = [
        "# Session Runtime Context",
        "",
        "## Summary",
        summary or "(empty)",
        "",
        "## Working Memory",
        f"- active_skill: `{active_skill}`",
        f"- recent_tools: `{', '.join(recent_tools) if recent_tools else '(none)'}`",
        f"- current_goal: {_clip_text(current_goal, MAX_ITEM_CHARS) if current_goal else '(none)'}",
        "",
        "### Confirmed Slots",
    ]

    if not confirmed_slots:
        lines.append("(empty)")
    else:
        for key, value in confirmed_slots.items():
            lines.append(f"- {key}: {_clip_text(value, MAX_ITEM_CHARS)}")

    lines.extend(["", "### Pending Slots"])

    if not pending_slots:
        lines.append("(empty)")
    else:
        for item in pending_slots:
            lines.append(f"- {_clip_text(item, MAX_ITEM_CHARS)}")

    lines.extend(["", "### Open Loops"])

    if not open_loops:
        lines.append("(empty)")
    else:
        for item in open_loops:
            lines.append(f"- {_clip_text(item, MAX_ITEM_CHARS)}")

    lines.extend(["", "### Artifacts"])

    if not artifacts:
        lines.append("(empty)")
    else:
        for item in artifacts:
            description = item["description"] if item["description"] else "(no description)"
            lines.append(f"- `{item['path']}`: {_clip_text(description, MAX_ITEM_CHARS)}")

    lines.extend(["", "## Retrieved Context"])

    if not retrieved_context:
        lines.append("(empty)")
    else:
        for index, item in enumerate(retrieved_context, start=1):
            source = str(item.get("source") or "").strip() or "(unknown)"
            title = str(item.get("title") or "").strip() or f"Snippet {index}"
            snippet = _clip_text(str(item.get("snippet") or ""), MAX_ASSISTANT_CHARS)
            lines.extend(
                [
                    f"### Retrieved {index}: {title}",
                    f"- Source: `{source}`",
                    f"- Snippet: {snippet}",
                    "",
                ]
            )

    lines.append("## Recent Turns")

    if not recent_turns:
        lines.append("(empty)")
    else:
        for index, (user_text, assistant_text) in enumerate(recent_turns, start=1):
            lines.extend(
                [
                    f"### Recent Turn {index}",
                    f"- User: {_clip_text(user_text, MAX_USER_CHARS)}",
                    f"- Assistant: {_clip_text(assistant_text, MAX_ASSISTANT_CHARS)}",
                    "",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"
