"""Agent 构建与消息调用模块（同步、异步与流式输出）。"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.intent_router import route_with_skill_intent
from app.reloading_memory import ReloadingMemoryMiddleware
from app.reloading_skills import ReloadingSkillsMiddleware
from app.sandbox import SessionSandbox, use_session_sandbox
from app.serialization import make_json_safe
from app.skill_catalog import list_skills
from app.tools import (
    get_weather,
    query_field_lineage_step,
    query_field_lineage_until_stop,
    run_python_code,
    search_knowledge_base,
)


TOOL_REGISTRY = {
    "weather": get_weather,
    "knowledge_base": search_knowledge_base,
    "python_code": run_python_code,
    "field_lineage_step": query_field_lineage_step,
    "field_lineage_auto": query_field_lineage_until_stop,
}

_ROUTER_HINT_PATTERN = re.compile(r"\[SKILL_ROUTER_HINT\]\s*(\{.*?\})\s*\[/SKILL_ROUTER_HINT\]", re.DOTALL)


def estimate_text_tokens(text: str) -> int:
    content = (text or "").strip()
    if not content:
        return 0
    return max(1, round(len(content) / 4))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_text(message: BaseMessage, *, strip: bool = True) -> str:
    """从 LangChain 消息对象中抽取可显示文本。"""
    content = message.content
    if isinstance(content, str):
        return content.strip() if strip else content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        merged = "".join(text_parts)
        return merged.strip() if strip else merged
    return ""


def _extract_text_from_generation_batches(generation_batches: Any) -> str:
    text_parts: list[str] = []
    if not isinstance(generation_batches, list):
        return ""

    for batch in generation_batches:
        if not isinstance(batch, list):
            continue
        for generation in batch:
            message = getattr(generation, "message", None)
            if isinstance(message, BaseMessage):
                text = _extract_text(message, strip=False)
                if text:
                    text_parts.append(text)
                    continue
            raw_text = getattr(generation, "text", None)
            if isinstance(raw_text, str) and raw_text:
                text_parts.append(raw_text)

    return "".join(text_parts)


def _message_role_name(message: BaseMessage) -> str:
    message_type = getattr(message, "type", "") or ""
    return {
        "human": "user",
        "ai": "assistant",
        "system": "system",
        "tool": "tool",
    }.get(message_type, message_type or "message")


def _summarize_debug_message(message: BaseMessage) -> dict[str, Any]:
    tool_call_id = getattr(message, "tool_call_id", None)
    content_text = _extract_text(message, strip=False)
    payload: dict[str, Any] = {
        "role": _message_role_name(message),
        "name": getattr(message, "name", None),
        "id": getattr(message, "id", None),
        "tool_call_id": tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None,
        "content_text": content_text,
        "content": make_json_safe(message.content),
        "tool_calls": make_json_safe(getattr(message, "tool_calls", [])),
        "additional_kwargs": make_json_safe(getattr(message, "additional_kwargs", {})),
        "response_metadata": make_json_safe(getattr(message, "response_metadata", {})),
        "status": make_json_safe(getattr(message, "status", None)),
    }
    return payload


def _summarize_debug_message_batches(messages: list[list[BaseMessage]]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    for batch in messages:
        if not isinstance(batch, list):
            continue
        batches.append([_summarize_debug_message(message) for message in batch if isinstance(message, BaseMessage)])
    return batches


def _render_debug_message_batches(message_batches: list[list[dict[str, Any]]]) -> str:
    rendered_batches: list[str] = []
    for batch_index, batch in enumerate(message_batches, start=1):
        rendered_messages: list[str] = []
        for message in batch:
            role = str(message.get("role") or "message")
            name = str(message.get("name") or "").strip()
            header = f"{role}({name})" if name else role
            content_text = message.get("content_text")
            content = content_text if isinstance(content_text, str) and content_text else message.get("content")
            if isinstance(content, (dict, list)):
                try:
                    content = json.dumps(content, ensure_ascii=False, indent=2)
                except TypeError:
                    content = str(content)
            rendered_messages.append(f"[{header}]\n{content or '(empty)'}")
        if rendered_messages:
            rendered_batches.append(f"Batch {batch_index}\n" + "\n\n".join(rendered_messages))
    return "\n\n".join(rendered_batches)


def _summarize_generation_batches(generation_batches: Any) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    if not isinstance(generation_batches, list):
        return summarized

    for batch in generation_batches:
        if not isinstance(batch, list):
            continue
        for generation in batch:
            item: dict[str, Any] = {}
            message = getattr(generation, "message", None)
            if isinstance(message, BaseMessage):
                item["message"] = _summarize_debug_message(message)
            text = getattr(generation, "text", None)
            if isinstance(text, str) and text:
                item["text"] = text
            generation_info = getattr(generation, "generation_info", None)
            if generation_info:
                item["generation_info"] = make_json_safe(generation_info)
            if item:
                summarized.append(item)
    return summarized


def _read_field(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_runnable_name(serialized: Any) -> str | None:
    if not isinstance(serialized, dict):
        return None

    direct_name = serialized.get("name")
    if isinstance(direct_name, str) and direct_name.strip():
        return direct_name.strip()

    raw_id = serialized.get("id")
    if isinstance(raw_id, list):
        for item in reversed(raw_id):
            if isinstance(item, str) and item.strip():
                return item.strip()
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()

    kwargs = serialized.get("kwargs")
    if isinstance(kwargs, dict):
        candidate = kwargs.get("name")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    return None


def _extract_langgraph_node(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None

    for key in ("langgraph_node", "graph_node", "node_name", "langgraph_step"):
        candidate = metadata.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    return None


def _extract_token_usage(llm_output: Any, generations: Any) -> Any:
    if isinstance(llm_output, dict):
        for key in ("token_usage", "usage", "usage_metadata"):
            if llm_output.get(key) is not None:
                return make_json_safe(llm_output.get(key))

    if isinstance(generations, list):
        for batch in generations:
            if not isinstance(batch, list):
                continue
            for generation in batch:
                message = getattr(generation, "message", None)
                response_metadata = getattr(message, "response_metadata", None) if isinstance(message, BaseMessage) else None
                if isinstance(response_metadata, dict):
                    for key in ("token_usage", "usage", "usage_metadata"):
                        if response_metadata.get(key) is not None:
                            return make_json_safe(response_metadata.get(key))
                usage_metadata = getattr(message, "usage_metadata", None) if isinstance(message, BaseMessage) else None
                if usage_metadata is not None:
                    return make_json_safe(usage_metadata)

    return None


def _maybe_parse_json_payload(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not (text.startswith("{") or text.startswith("[")):
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return _maybe_parse_json_payload(value[0])

    return None


def _collect_artifact_paths(value: Any, *, limit: int = 16) -> list[dict[str, str]]:
    artifact_keys = {
        "path",
        "file_path",
        "script_path",
        "sandbox_path",
        "workspace_path",
        "cwd",
        "artifact_path",
    }
    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def visit(candidate: Any, label: str | None = None) -> None:
        if len(results) >= limit:
            return
        if isinstance(candidate, dict):
            for key, item in candidate.items():
                if isinstance(item, str) and key in artifact_keys and item.strip():
                    normalized_label = label or key
                    normalized_value = item.strip()
                    dedupe_key = (normalized_label, normalized_value)
                    if dedupe_key not in seen:
                        seen.add(dedupe_key)
                        results.append({"label": normalized_label, "path": normalized_value})
                else:
                    visit(item, str(key))
            return
        if isinstance(candidate, list):
            for item in candidate:
                visit(item, label)

    visit(value)
    return results


def _summarize_tool_message(message: ToolMessage) -> dict[str, Any]:
    parsed_output = _maybe_parse_json_payload(message.content)
    base_content = parsed_output if parsed_output is not None else make_json_safe(message.content)
    return {
        **_summarize_debug_message(message),
        "parsed_output": make_json_safe(parsed_output) if parsed_output is not None else None,
        "artifact_paths": _collect_artifact_paths(base_content),
    }


def _extract_tool_calls(message: BaseMessage) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []

    raw_tool_calls = getattr(message, "tool_calls", None)
    if isinstance(raw_tool_calls, list):
        for call in raw_tool_calls:
            extracted.append(
                {
                    "kind": "tool_call",
                    "id": _read_field(call, "id"),
                    "name": _read_field(call, "name"),
                    "args": _read_field(call, "args"),
                }
            )

    raw_tool_call_chunks = getattr(message, "tool_call_chunks", None)
    if isinstance(raw_tool_call_chunks, list):
        for chunk in raw_tool_call_chunks:
            extracted.append(
                {
                    "kind": "tool_call_chunk",
                    "id": _read_field(chunk, "id"),
                    "name": _read_field(chunk, "name"),
                    "args": _read_field(chunk, "args"),
                    "index": _read_field(chunk, "index"),
                }
            )

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                extracted.append(
                    {
                        "kind": "raw_tool_call",
                        "id": raw.get("id"),
                        "name": function.get("name"),
                        "args": function.get("arguments"),
                    }
                )

    return extracted


def _serialize_skill_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": card.get("name"),
        "description": card.get("description"),
        "path": card.get("path"),
        "source": card.get("source"),
        "allowed_tools": card.get("allowed_tools", []),
        "triggers": card.get("triggers", []),
        "required_slots": card.get("required_slots", []),
        "output_contract": card.get("output_contract", ""),
        "validation_errors": card.get("validation_errors", []),
    }


def _build_skill_debug_snapshot(
    *,
    allowed_skill_names: tuple[str, ...] | None,
    selected_skill: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    cards = list_skills(settings.project_root, settings.skill_sources)
    if allowed_skill_names is not None:
        allowed = set(allowed_skill_names)
        cards = [item for item in cards if item["name"] in allowed]

    payload: dict[str, Any] = {
        "enabled_skills": [_serialize_skill_card(card) for card in cards],
        "selected_skill": selected_skill,
    }

    if not selected_skill:
        return payload

    selected_card = next((card for card in cards if card["name"] == selected_skill), None)
    if selected_card is None:
        payload["selected_skill_file"] = None
        return payload

    try:
        skill_path = settings.project_root / str(selected_card["path"]).lstrip("/")
        skill_content = skill_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        payload["selected_skill_file"] = {
            **_serialize_skill_card(selected_card),
            "error": str(exc),
        }
        return payload

    payload["selected_skill_file"] = {
        **_serialize_skill_card(selected_card),
        "content": skill_content,
    }
    return payload


class DebugTraceHandler(BaseCallbackHandler):
    """采集 LangChain chat model 的真实输入输出。"""

    raise_error = False

    def __init__(self, *, enabled: bool) -> None:
        super().__init__()
        self.enabled = enabled
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._model_runs: dict[str, dict[str, Any]] = {}

    def _append_event(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._events.append(event)

    def drain(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock:
            drained = list(self._events)
            self._events.clear()
        return drained

    def resolve_active_model_run(self, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        preferred_node = _extract_langgraph_node(metadata)
        with self._lock:
            candidates = [
                {
                    "run_id": run_id,
                    "parent_run_id": state.get("parent_run_id"),
                    "model_name": state.get("model_name"),
                    "started_at": state.get("started_at"),
                    "langgraph_node": state.get("langgraph_node"),
                    "runnable_name": state.get("runnable_name"),
                    "started_perf": state.get("started_perf"),
                }
                for run_id, state in self._model_runs.items()
            ]

        if not candidates:
            return None

        if preferred_node:
            node_matches = [
                candidate for candidate in candidates if str(candidate.get("langgraph_node") or "").strip() == preferred_node
            ]
            if node_matches:
                candidates = node_matches

        candidates.sort(
            key=lambda candidate: (
                float(candidate.get("started_perf") or 0.0),
                str(candidate.get("started_at") or ""),
            ),
            reverse=True,
        )
        selected = candidates[0]
        selected.pop("started_perf", None)
        return selected

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if not self.enabled:
            return None

        invocation_params = kwargs.get("invocation_params")
        model_name = None
        if isinstance(invocation_params, dict):
            candidate = invocation_params.get("model")
            if isinstance(candidate, str) and candidate:
                model_name = candidate
        if model_name is None and isinstance(metadata, dict):
            candidate = metadata.get("ls_model_name")
            if isinstance(candidate, str) and candidate:
                model_name = candidate
        message_batches = _summarize_debug_message_batches(messages)
        payload = {
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "started_at": _utcnow_iso(),
            "model_name": model_name,
            "langgraph_node": _extract_langgraph_node(metadata),
            "runnable_name": _extract_runnable_name(serialized),
            "serialized": make_json_safe(serialized),
            "invocation_params": make_json_safe(invocation_params),
            "messages": make_json_safe(messages),
            "message_batches": message_batches,
            "input_text": _render_debug_message_batches(message_batches),
            "tags": make_json_safe(tags or []),
            "metadata": make_json_safe(metadata or {}),
        }

        with self._lock:
            self._model_runs[str(run_id)] = {
                "tokens": [],
                "run_id": str(run_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "model_name": model_name,
                "started_at": payload["started_at"],
                "started_perf": time.perf_counter(),
                "langgraph_node": payload["langgraph_node"],
                "runnable_name": payload["runnable_name"],
            }
            self._events.append({"type": "debug", "kind": "debug_model_input", "payload": payload})
        return None

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if not self.enabled:
            return None

        run_key = str(run_id)
        invocation_params = kwargs.get("invocation_params")
        model_name = None
        if isinstance(invocation_params, dict):
            candidate = invocation_params.get("model")
            if isinstance(candidate, str) and candidate:
                model_name = candidate
        if model_name is None and isinstance(metadata, dict):
            candidate = metadata.get("ls_model_name")
            if isinstance(candidate, str) and candidate:
                model_name = candidate

        with self._lock:
            if run_key in self._model_runs:
                return None
            self._model_runs[run_key] = {
                "tokens": [],
                "run_id": run_key,
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "model_name": model_name,
                "started_at": _utcnow_iso(),
                "started_perf": time.perf_counter(),
                "langgraph_node": _extract_langgraph_node(metadata),
                "runnable_name": _extract_runnable_name(serialized),
            }

        self._append_event(
            {
                "type": "debug",
                "kind": "debug_model_input",
                "payload": {
                    "run_id": run_key,
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "started_at": _utcnow_iso(),
                    "model_name": model_name,
                    "langgraph_node": _extract_langgraph_node(metadata),
                    "runnable_name": _extract_runnable_name(serialized),
                    "serialized": make_json_safe(serialized),
                    "invocation_params": make_json_safe(invocation_params),
                    "prompts": make_json_safe(prompts),
                    "input_text": "\n\n".join(prompt for prompt in prompts if isinstance(prompt, str)),
                    "tags": make_json_safe(tags or []),
                    "metadata": make_json_safe(metadata or {}),
                },
            }
        )
        return None

    def on_llm_new_token(self, token: str, run_id: Any = None, **kwargs: Any) -> Any:
        if not self.enabled or not token:
            return None

        with self._lock:
            state = self._model_runs.setdefault(str(run_id), {"tokens": []})
            state.setdefault("tokens", []).append(token)
        return None

    def on_llm_end(self, response: Any, run_id: Any = None, parent_run_id: Any = None, **kwargs: Any) -> Any:
        if not self.enabled:
            return None

        run_key = str(run_id)
        with self._lock:
            state = self._model_runs.pop(run_key, {"tokens": []})

        generations = getattr(response, "generations", [])
        streamed_text = "".join(state.get("tokens", []))
        output_text = streamed_text or _extract_text_from_generation_batches(generations)
        llm_output = make_json_safe(getattr(response, "llm_output", {}))
        duration_ms = None
        started_perf = state.get("started_perf")
        if isinstance(started_perf, (int, float)):
            duration_ms = round((time.perf_counter() - started_perf) * 1000, 1)

        self._append_event(
            {
                "type": "debug",
                "kind": "debug_model_output",
                "payload": {
                    "run_id": run_key,
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "model_name": state.get("model_name"),
                    "started_at": state.get("started_at"),
                    "finished_at": _utcnow_iso(),
                    "duration_ms": duration_ms,
                    "langgraph_node": state.get("langgraph_node"),
                    "runnable_name": state.get("runnable_name"),
                    "token_usage": _extract_token_usage(llm_output, generations),
                    "output_text": output_text,
                    "output_messages": _summarize_generation_batches(generations),
                    "generations": make_json_safe(generations),
                    "llm_output": llm_output,
                },
            }
        )
        return None

    def on_llm_error(self, error: BaseException, run_id: Any = None, parent_run_id: Any = None, **kwargs: Any) -> Any:
        if not self.enabled:
            return None
        with self._lock:
            state = self._model_runs.pop(str(run_id), {})
        duration_ms = None
        started_perf = state.get("started_perf") if isinstance(state, dict) else None
        if isinstance(started_perf, (int, float)):
            duration_ms = round((time.perf_counter() - started_perf) * 1000, 1)
        self._append_event(
            {
                "type": "debug",
                "kind": "debug_model_error",
                "payload": {
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "model_name": state.get("model_name") if isinstance(state, dict) else None,
                    "started_at": state.get("started_at") if isinstance(state, dict) else None,
                    "finished_at": _utcnow_iso(),
                    "duration_ms": duration_ms,
                    "langgraph_node": state.get("langgraph_node") if isinstance(state, dict) else None,
                    "runnable_name": state.get("runnable_name") if isinstance(state, dict) else None,
                    "error": str(error),
                },
            }
        )
        return None


def _normalize_tool_args(args: Any) -> Any:
    if not isinstance(args, str):
        return args

    text = args.strip()
    if not text:
        return args

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return args


def _try_parse_tool_args(args: Any) -> tuple[bool, Any]:
    if not isinstance(args, str):
        return True, args

    text = args.strip()
    if not text:
        return False, None

    try:
        return True, json.loads(text)
    except json.JSONDecodeError:
        return False, None


def _tool_call_event_key(call_id: Any = None, index: Any = None) -> str | None:
    if call_id:
        return f"id:{call_id}"
    if index is not None:
        return f"idx:{index}"
    return None


def _extract_completed_tool_calls_from_chunks(
    message: BaseMessage,
    pending_chunks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    chunk_items: list[dict[str, Any]] = []

    raw_tool_call_chunks = getattr(message, "tool_call_chunks", None)
    if isinstance(raw_tool_call_chunks, list):
        for raw_chunk in raw_tool_call_chunks:
            chunk_items.append(
                {
                    "id": _read_field(raw_chunk, "id"),
                    "index": _read_field(raw_chunk, "index"),
                    "name": _read_field(raw_chunk, "name"),
                    "args": _read_field(raw_chunk, "args"),
                }
            )

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                chunk_items.append(
                    {
                        "id": raw.get("id"),
                        "index": raw.get("index"),
                        "name": function.get("name"),
                        "args": function.get("arguments"),
                    }
                )

    if not chunk_items:
        return []

    completed: list[dict[str, Any]] = []

    for raw_chunk in chunk_items:
        call_id = raw_chunk.get("id")
        index = raw_chunk.get("index")
        id_key = _tool_call_event_key(call_id)
        index_key = _tool_call_event_key(index=index)
        event_key = id_key or index_key
        if event_key is None:
            continue

        state = None
        if id_key and id_key in pending_chunks:
            state = pending_chunks[id_key]
        elif index_key and index_key in pending_chunks:
            state = pending_chunks[index_key]
        if state is None:
            state = {"id": str(call_id) if call_id else None, "index": index, "name": "", "args_text": ""}

        if call_id:
            state["id"] = str(call_id)
        if state.get("index") is None and index is not None:
            state["index"] = index

        name = raw_chunk.get("name")
        if isinstance(name, str) and name:
            existing_name = str(state.get("name") or "")
            state["name"] = name if not existing_name else existing_name + name

        args = raw_chunk.get("args")
        if isinstance(args, str) and args:
            state["args_text"] = f"{state.get('args_text', '')}{args}"
        elif args is not None and not (isinstance(args, str) and args == ""):
            state["args_value"] = args

        if id_key:
            pending_chunks[id_key] = state
            event_key = id_key
        if index_key:
            pending_chunks[index_key] = state
            if not id_key:
                event_key = index_key

        parsed_args = state.get("args_value")
        is_complete = parsed_args is not None
        if not is_complete:
            is_complete, parsed_args = _try_parse_tool_args(state.get("args_text", ""))

        if not state.get("name") or not is_complete:
            continue

        completed.append(
            {
                "event_key": event_key,
                "id": state.get("id"),
                "name": state.get("name"),
                "args": parsed_args,
            }
        )

    return completed


def _extract_complete_tool_calls(message: BaseMessage) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []

    raw_tool_calls = getattr(message, "tool_calls", None)
    if isinstance(raw_tool_calls, list):
        for call in raw_tool_calls:
            extracted.append(
                {
                    "id": _read_field(call, "id"),
                    "name": _read_field(call, "name"),
                    "args": _normalize_tool_args(_read_field(call, "args")),
                }
            )

    if extracted:
        return extracted

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                extracted.append(
                    {
                        "id": raw.get("id"),
                        "name": function.get("name"),
                        "args": _normalize_tool_args(function.get("arguments")),
                    }
                )

    return extracted


def _debug_print_tool_calls(message: BaseMessage, metadata: dict[str, Any] | None = None) -> None:
    tool_calls = _extract_tool_calls(message)
    if not tool_calls:
        return

    lineage_tools = {"query_field_lineage_step", "query_field_lineage_until_stop"}
    if not any(item.get("name") in lineage_tools for item in tool_calls):
        return

    lineage_calls = [item for item in tool_calls if item.get("name") in lineage_tools]
    payload = json.dumps(lineage_calls, ensure_ascii=False, default=str)
    node = (metadata or {}).get("langgraph_node", "unknown")
    print(f"\n[debug:lineage_tool_call:node={node}] {payload}", flush=True)


def _resolve_memory_sources(memory_sources: tuple[str, ...] | None) -> tuple[str, ...]:
    settings = get_settings()
    if memory_sources is None:
        return settings.memory_sources
    return memory_sources


def _resolve_model_name(model_name: str | None) -> str:
    settings = get_settings()
    return (model_name or settings.model_name).strip() or settings.model_name


def _resolve_system_prompt(system_prompt: str | None) -> str:
    settings = get_settings()
    return (system_prompt or settings.system_prompt).strip() or settings.system_prompt


def _resolve_tool_ids(enabled_tool_ids: tuple[str, ...] | None) -> tuple[str, ...]:
    if enabled_tool_ids is None:
        return tuple(TOOL_REGISTRY.keys())
    return tuple(tool_id for tool_id in enabled_tool_ids if tool_id in TOOL_REGISTRY)


def _resolve_skill_sources(skill_sources: tuple[str, ...] | None) -> tuple[str, ...]:
    settings = get_settings()
    if skill_sources is None:
        return settings.skill_sources
    return skill_sources


def _resolve_allowed_skill_names(allowed_skill_names: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if allowed_skill_names is None:
        return None
    normalized = tuple(dict.fromkeys(name.strip() for name in allowed_skill_names if name and name.strip()))
    return normalized


def _extract_router_hint_payload(text: str) -> dict[str, Any] | None:
    match = _ROUTER_HINT_PATTERN.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


@lru_cache(maxsize=32)
def get_agent(
    memory_sources: tuple[str, ...] | None = None,
    *,
    model_name: str | None = None,
    system_prompt: str | None = None,
    enabled_tool_ids: tuple[str, ...] | None = None,
    skill_sources: tuple[str, ...] | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
):
    """构建并缓存 deep agent，避免每轮对话重复初始化。"""
    settings = get_settings()
    resolved_memory_sources = _resolve_memory_sources(memory_sources)
    resolved_model_name = _resolve_model_name(model_name)
    resolved_system_prompt = _resolve_system_prompt(system_prompt)
    resolved_tool_ids = _resolve_tool_ids(enabled_tool_ids)
    resolved_skill_sources = _resolve_skill_sources(skill_sources)
    resolved_allowed_skill_names = _resolve_allowed_skill_names(allowed_skill_names)

    model = ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=resolved_model_name,
        temperature=settings.temperature,
    )
    backend = FilesystemBackend(root_dir=settings.project_root, virtual_mode=True)

    loaded_skills = list_skills(settings.project_root, resolved_skill_sources)
    if resolved_allowed_skill_names is not None:
        allowed = set(resolved_allowed_skill_names)
        loaded_skills = [item for item in loaded_skills if item["name"] in allowed]
    if loaded_skills:
        print("[startup:skills] 已注入的 skill 元数据：")
        for item in loaded_skills:
            print(
                f"- name={item['name']} | source={item['source']} | path={item['path']} | description={item['description']}"
            )
    else:
        print("[startup:skills] 未发现可注入的 skill 元数据。")

    return create_deep_agent(
        model=model,
        tools=[TOOL_REGISTRY[tool_id] for tool_id in resolved_tool_ids],
        system_prompt=resolved_system_prompt,
        middleware=[
            ReloadingMemoryMiddleware(backend=backend, sources=list(resolved_memory_sources)),
            ReloadingSkillsMiddleware(
                backend=backend,
                sources=list(resolved_skill_sources),
                allowed_skill_names=resolved_allowed_skill_names,
            ),
        ],
        backend=backend,
        checkpointer=InMemorySaver(),
        name="deepagent-skills-backend",
    )


def _build_agent_payload(
    message: str,
    *,
    allowed_skill_names: tuple[str, ...] | None,
    model_name: str | None,
    preferred_skill_name: str | None,
) -> tuple[str, str | None, dict[str, Any]]:
    route_result = route_with_skill_intent(
        message,
        model_name=_resolve_model_name(model_name),
        allowed_skill_names=allowed_skill_names,
        preferred_skill_name=preferred_skill_name,
    )
    routed_message = route_result.augmented_message
    return routed_message or message, route_result.immediate, route_result.trace


async def chat_once(
    message: str,
    thread_id: str,
    sandbox: SessionSandbox | None = None,
    memory_sources: tuple[str, ...] | None = None,
    model_name: str | None = None,
    system_prompt: str | None = None,
    enabled_tool_ids: tuple[str, ...] | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
    preferred_skill_name: str | None = None,
) -> str:
    """异步单次调用：返回该轮对话的最终文本。"""
    resolved_message, immediate, _ = _build_agent_payload(
        message,
        allowed_skill_names=allowed_skill_names,
        model_name=model_name,
        preferred_skill_name=preferred_skill_name,
    )
    if immediate is not None:
        return immediate

    agent = get_agent(
        memory_sources,
        model_name=model_name,
        system_prompt=system_prompt,
        enabled_tool_ids=enabled_tool_ids,
        allowed_skill_names=allowed_skill_names,
    )
    payload = {"messages": [{"role": "user", "content": resolved_message}]}
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    with use_session_sandbox(sandbox):
        result = await agent.ainvoke(payload, config=config)

    messages = result.get("messages", [])
    for candidate in reversed(messages):
        if isinstance(candidate, AIMessage):
            text = _extract_text(candidate)
            if text:
                return text

    if messages:
        last = messages[-1]
        if isinstance(last, BaseMessage):
            fallback_text = _extract_text(last)
            if fallback_text:
                return fallback_text

    return ""


def chat_once_sync(
    message: str,
    thread_id: str,
    sandbox: SessionSandbox | None = None,
    memory_sources: tuple[str, ...] | None = None,
    model_name: str | None = None,
    system_prompt: str | None = None,
    enabled_tool_ids: tuple[str, ...] | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
    preferred_skill_name: str | None = None,
) -> str:
    """同步单次调用：返回该轮对话的最终文本。"""
    resolved_message, immediate, _ = _build_agent_payload(
        message,
        allowed_skill_names=allowed_skill_names,
        model_name=model_name,
        preferred_skill_name=preferred_skill_name,
    )
    if immediate is not None:
        return immediate

    agent = get_agent(
        memory_sources,
        model_name=model_name,
        system_prompt=system_prompt,
        enabled_tool_ids=enabled_tool_ids,
        allowed_skill_names=allowed_skill_names,
    )
    payload = {"messages": [{"role": "user", "content": resolved_message}]}
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    with use_session_sandbox(sandbox):
        result = agent.invoke(payload, config=config)

    messages = result.get("messages", [])
    for candidate in reversed(messages):
        if isinstance(candidate, AIMessage):
            text = _extract_text(candidate)
            if text:
                return text

    if messages:
        last = messages[-1]
        if isinstance(last, BaseMessage):
            fallback_text = _extract_text(last)
            if fallback_text:
                return fallback_text

    return ""


def stream_chat_sync(
    message: str,
    thread_id: str,
    sandbox: SessionSandbox | None = None,
    memory_sources: tuple[str, ...] | None = None,
    model_name: str | None = None,
    system_prompt: str | None = None,
    enabled_tool_ids: tuple[str, ...] | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
    preferred_skill_name: str | None = None,
) -> Iterator[str]:
    """同步流式调用：逐块产出模型文本，供终端实时打印。"""
    resolved_message, immediate, _ = _build_agent_payload(
        message,
        allowed_skill_names=allowed_skill_names,
        model_name=model_name,
        preferred_skill_name=preferred_skill_name,
    )
    if immediate is not None:
        yield immediate
        return

    agent = get_agent(
        memory_sources,
        model_name=model_name,
        system_prompt=system_prompt,
        enabled_tool_ids=enabled_tool_ids,
        allowed_skill_names=allowed_skill_names,
    )
    payload = {"messages": [{"role": "user", "content": resolved_message}]}
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    seen_chunk_in_round = False
    with use_session_sandbox(sandbox):
        for emitted, metadata in agent.stream(payload, config=config, stream_mode="messages"):
            if isinstance(emitted, (AIMessageChunk, AIMessage)):
                _debug_print_tool_calls(emitted, metadata)

            if isinstance(emitted, AIMessageChunk):
                text = _extract_text(emitted, strip=False)
                if text:
                    seen_chunk_in_round = True
                    yield text
                continue

            if isinstance(emitted, AIMessage):
                text = _extract_text(emitted, strip=False)
                if text and not seen_chunk_in_round:
                    yield text
                seen_chunk_in_round = False


def iter_chat_events_sync(
    message: str,
    thread_id: str,
    sandbox: SessionSandbox | None = None,
    memory_sources: tuple[str, ...] | None = None,
    model_name: str | None = None,
    system_prompt: str | None = None,
    enabled_tool_ids: tuple[str, ...] | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
    preferred_skill_name: str | None = None,
    debug: bool = False,
) -> Iterator[dict[str, Any]]:
    """同步流式调用：产出 token/tool 级事件，供异步 SSE 包装复用。"""
    resolved_message, immediate, route_trace = _build_agent_payload(
        message,
        allowed_skill_names=allowed_skill_names,
        model_name=model_name,
        preferred_skill_name=preferred_skill_name,
    )
    router_hint = _extract_router_hint_payload(resolved_message)
    if immediate is not None:
        yield {"type": "token", "text": immediate}
        return
    if debug:
        selected_skill = route_trace.get("selected_skill") if isinstance(route_trace, dict) else None
        yield {
            "type": "debug",
            "kind": "debug_skill_router",
            "payload": {
                **make_json_safe(route_trace),
                **_build_skill_debug_snapshot(
                    allowed_skill_names=allowed_skill_names,
                    selected_skill=str(selected_skill) if isinstance(selected_skill, str) else None,
                ),
            },
        }
        yield {
            "type": "debug",
            "kind": "debug_agent_input",
            "payload": {
                "thread_id": thread_id,
                "original_message": message,
                "resolved_message": resolved_message,
                "model_name": _resolve_model_name(model_name),
                "system_prompt": _resolve_system_prompt(system_prompt),
                "memory_sources": list(_resolve_memory_sources(memory_sources)),
                "enabled_tool_ids": list(_resolve_tool_ids(enabled_tool_ids)),
                "allowed_skill_names": list(allowed_skill_names or ()),
                "preferred_skill_name": preferred_skill_name,
            },
        }
    if router_hint is not None:
        yield {
            "type": "skill",
            "skill": router_hint.get("selected_skill"),
            "confidence": router_hint.get("confidence"),
            "reason": router_hint.get("reason"),
        }

    agent = get_agent(
        memory_sources,
        model_name=model_name,
        system_prompt=system_prompt,
        enabled_tool_ids=enabled_tool_ids,
        allowed_skill_names=allowed_skill_names,
    )
    payload = {"messages": [{"role": "user", "content": resolved_message}]}
    debug_handler = DebugTraceHandler(enabled=debug)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if debug:
        config["callbacks"] = [debug_handler]
    emitted_tool_ids: set[str] = set()
    emitted_tool_event_keys: set[str] = set()
    tool_names_by_call_id: dict[str, str] = {}
    pending_tool_call_chunks: dict[str, dict[str, Any]] = {}
    pending_tool_invocations: list[dict[str, Any]] = []
    seen_chunk_in_round = False

    with use_session_sandbox(sandbox):
        for emitted, metadata in agent.stream(payload, config=config, stream_mode="messages"):
            for debug_event in debug_handler.drain():
                yield debug_event
            if isinstance(emitted, (AIMessageChunk, AIMessage)):
                _debug_print_tool_calls(emitted, metadata)
            if isinstance(emitted, AIMessageChunk):
                for tool_call in _extract_completed_tool_calls_from_chunks(emitted, pending_tool_call_chunks):
                    event_key = str(tool_call.get("event_key") or "")
                    if event_key in emitted_tool_event_keys:
                        continue
                    emitted_tool_event_keys.add(event_key)
                    tool_call_id = str(tool_call.get("id") or tool_call.get("event_key") or uuid4().hex)
                    tool_name = str(tool_call.get("name") or "").strip()
                    if tool_call_id in emitted_tool_ids:
                        continue
                    emitted_tool_ids.add(tool_call_id)
                    if tool_name:
                        tool_names_by_call_id[tool_call_id] = tool_name
                    started_at = _utcnow_iso()
                    node_name = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                    source_model_run = debug_handler.resolve_active_model_run(metadata if isinstance(metadata, dict) else None)
                    event = {
                        "type": "tool_start",
                        "tool": tool_name or None,
                        "tool_call_id": tool_call_id,
                        "input": make_json_safe(tool_call.get("args")),
                        "started_at": started_at,
                        "langgraph_node": node_name,
                        "stream_metadata": make_json_safe(metadata or {}),
                        "source_message": _summarize_debug_message(emitted),
                        "source_run_id": source_model_run.get("run_id") if isinstance(source_model_run, dict) else None,
                        "source_parent_run_id": (
                            source_model_run.get("parent_run_id") if isinstance(source_model_run, dict) else None
                        ),
                        "source_model_name": source_model_run.get("model_name") if isinstance(source_model_run, dict) else None,
                        "source_runnable_name": (
                            source_model_run.get("runnable_name") if isinstance(source_model_run, dict) else None
                        ),
                    }
                    pending_tool_invocations.append(
                        {
                            "tool_call_id": tool_call_id,
                            "tool": tool_name or None,
                            "started_at": started_at,
                            "started_perf": time.perf_counter(),
                            "langgraph_node": node_name,
                            "stream_metadata": event["stream_metadata"],
                            "input": event["input"],
                            "source_message": event["source_message"],
                            "source_run_id": event["source_run_id"],
                            "source_parent_run_id": event["source_parent_run_id"],
                            "source_model_name": event["source_model_name"],
                            "source_runnable_name": event["source_runnable_name"],
                        }
                    )
                    yield event

            if isinstance(emitted, AIMessage) and not isinstance(emitted, AIMessageChunk):
                for tool_call in _extract_complete_tool_calls(emitted):
                    event_key = _tool_call_event_key(tool_call.get("id"))
                    if event_key and event_key in emitted_tool_event_keys:
                        continue
                    normalized_args = tool_call.get("args")
                    if normalized_args in ("", None):
                        continue
                    if isinstance(normalized_args, dict) and not normalized_args:
                        continue
                    if event_key:
                        emitted_tool_event_keys.add(event_key)
                    tool_call_id = str(tool_call.get("id") or tool_call.get("event_key") or uuid4().hex)
                    tool_name = str(tool_call.get("name") or "").strip()
                    if tool_call_id in emitted_tool_ids:
                        continue
                    emitted_tool_ids.add(tool_call_id)
                    if tool_name:
                        tool_names_by_call_id[tool_call_id] = tool_name
                    started_at = _utcnow_iso()
                    node_name = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                    source_model_run = debug_handler.resolve_active_model_run(metadata if isinstance(metadata, dict) else None)
                    event = {
                        "type": "tool_start",
                        "tool": tool_name or None,
                        "tool_call_id": tool_call_id,
                        "input": make_json_safe(normalized_args),
                        "started_at": started_at,
                        "langgraph_node": node_name,
                        "stream_metadata": make_json_safe(metadata or {}),
                        "source_message": _summarize_debug_message(emitted),
                        "source_run_id": source_model_run.get("run_id") if isinstance(source_model_run, dict) else None,
                        "source_parent_run_id": (
                            source_model_run.get("parent_run_id") if isinstance(source_model_run, dict) else None
                        ),
                        "source_model_name": source_model_run.get("model_name") if isinstance(source_model_run, dict) else None,
                        "source_runnable_name": (
                            source_model_run.get("runnable_name") if isinstance(source_model_run, dict) else None
                        ),
                    }
                    pending_tool_invocations.append(
                        {
                            "tool_call_id": tool_call_id,
                            "tool": tool_name or None,
                            "started_at": started_at,
                            "started_perf": time.perf_counter(),
                            "langgraph_node": node_name,
                            "stream_metadata": event["stream_metadata"],
                            "input": event["input"],
                            "source_message": event["source_message"],
                            "source_run_id": event["source_run_id"],
                            "source_parent_run_id": event["source_parent_run_id"],
                            "source_model_name": event["source_model_name"],
                            "source_runnable_name": event["source_runnable_name"],
                        }
                    )
                    yield event

            if isinstance(emitted, ToolMessage):
                tool_call_id = getattr(emitted, "tool_call_id", None)
                tool_name = (
                    getattr(emitted, "name", None)
                    or (tool_names_by_call_id.get(str(tool_call_id)) if tool_call_id else None)
                    or (metadata.get("langgraph_node") if isinstance(metadata, dict) else None)
                )
                matched_invocation = None
                for index in range(len(pending_tool_invocations) - 1, -1, -1):
                    candidate = pending_tool_invocations[index]
                    if tool_call_id and candidate.get("tool_call_id") == tool_call_id:
                        matched_invocation = pending_tool_invocations.pop(index)
                        break
                    if not tool_call_id and candidate.get("tool") == tool_name:
                        matched_invocation = pending_tool_invocations.pop(index)
                        break

                resolved_tool_call_id = str(tool_call_id) if tool_call_id else None
                if resolved_tool_call_id is None and matched_invocation:
                    candidate_tool_call_id = matched_invocation.get("tool_call_id")
                    if candidate_tool_call_id:
                        resolved_tool_call_id = str(candidate_tool_call_id)
                finished_at = _utcnow_iso()
                duration_ms = None
                if matched_invocation and isinstance(matched_invocation.get("started_perf"), (int, float)):
                    duration_ms = round((time.perf_counter() - matched_invocation["started_perf"]) * 1000, 1)
                output_value = make_json_safe(emitted.content)
                output_json = _maybe_parse_json_payload(output_value)
                yield {
                    "type": "tool_end",
                    "tool": tool_name,
                    "tool_call_id": resolved_tool_call_id,
                    "started_at": matched_invocation.get("started_at") if matched_invocation else None,
                    "finished_at": finished_at,
                    "duration_ms": duration_ms,
                    "langgraph_node": (
                        metadata.get("langgraph_node") if isinstance(metadata, dict) and metadata.get("langgraph_node") else None
                    )
                    or (matched_invocation.get("langgraph_node") if matched_invocation else None),
                    "stream_metadata": make_json_safe(metadata or {}),
                    "input": matched_invocation.get("input") if matched_invocation else None,
                    "source_message": matched_invocation.get("source_message") if matched_invocation else None,
                    "source_run_id": matched_invocation.get("source_run_id") if matched_invocation else None,
                    "source_parent_run_id": matched_invocation.get("source_parent_run_id") if matched_invocation else None,
                    "source_model_name": matched_invocation.get("source_model_name") if matched_invocation else None,
                    "source_runnable_name": matched_invocation.get("source_runnable_name") if matched_invocation else None,
                    "tool_message": _summarize_tool_message(emitted),
                    "output": output_value,
                    "output_json": make_json_safe(output_json) if output_json is not None else None,
                    "artifacts": _collect_artifact_paths(output_json if output_json is not None else output_value),
                }
                pending_tool_call_chunks = {
                    key: value for key, value in pending_tool_call_chunks.items() if not key.startswith("idx:")
                }
                emitted_tool_event_keys = {key for key in emitted_tool_event_keys if not key.startswith("idx:")}
                continue

            if isinstance(emitted, AIMessageChunk):
                text = _extract_text(emitted, strip=False)
                if text:
                    seen_chunk_in_round = True
                    yield {"type": "token", "text": text}
                continue

            if isinstance(emitted, AIMessage):
                text = _extract_text(emitted, strip=False)
                if text and not seen_chunk_in_round:
                    yield {"type": "token", "text": text}
                seen_chunk_in_round = False

    for debug_event in debug_handler.drain():
        yield debug_event


async def stream_chat_events(
    message: str,
    thread_id: str,
    sandbox: SessionSandbox | None = None,
    memory_sources: tuple[str, ...] | None = None,
    model_name: str | None = None,
    system_prompt: str | None = None,
    enabled_tool_ids: tuple[str, ...] | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
    preferred_skill_name: str | None = None,
    debug: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """异步流式调用：产出 token/tool 级事件，供 SSE 使用。"""
    queue: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _worker() -> None:
        try:
            for event in iter_chat_events_sync(
                message,
                thread_id=thread_id,
                sandbox=sandbox,
                memory_sources=memory_sources,
                model_name=model_name,
                system_prompt=system_prompt,
                enabled_tool_ids=enabled_tool_ids,
                allowed_skill_names=allowed_skill_names,
                preferred_skill_name=preferred_skill_name,
                debug=debug,
            ):
                asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()
        except BaseException as exc:  # noqa: BLE001
            asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, BaseException):
            raise item
        yield item
