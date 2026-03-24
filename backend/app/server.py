"""FastAPI 服务入口。"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.agent import stream_chat_events
from app.config import get_settings
from app.prompts import load_prompt
from app.sandbox import SessionSandbox
from app.session_store import generate_title_from_message, session_runtime_memory_sources, session_store
from app.skill_catalog import list_skills, normalize_skill_frontmatter, split_skill_document, validate_skill_frontmatter


AUTO_COMPRESS_KEEP_RECENT_TURNS = 4
AUTO_COMPRESS_MIN_TURNS = 6
AUTO_COMPRESS_MIN_NEW_MESSAGES = 4
AUTO_COMPRESS_CONTEXT_TOKEN_THRESHOLD = 2400


def _extract_model_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts).strip()
    return ""


def _json_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _turn_state_event(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "turn_state",
        "turn": session.get("turn_state", {}),
    }


def _memory_sources_for_session(session_id: str) -> tuple[str, ...]:
    return session_runtime_memory_sources(session_id)


def _render_transcript(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"{item['role']}: {item['content']}" for item in messages if str(item.get("content") or "").strip())


def _compression_cutoff(messages: list[dict[str, Any]], *, keep_recent_turns: int = AUTO_COMPRESS_KEEP_RECENT_TURNS) -> int:
    user_indexes = [index for index, item in enumerate(messages) if item.get("role") == "user"]
    if len(user_indexes) <= keep_recent_turns:
        return 0
    return user_indexes[-keep_recent_turns]


async def _auto_compress_session_if_needed(session_id: str) -> dict[str, Any]:
    session = session_store.get_session(session_id)
    messages = session.get("messages", [])
    cutoff = _compression_cutoff(messages)
    summary_message_count = int(session.get("summary_message_count") or 0)
    pending_messages = cutoff - summary_message_count
    turn_count = sum(1 for item in messages if item.get("role") == "user")
    context_tokens = int(session.get("stats", {}).get("context_tokens") or 0)

    if cutoff <= summary_message_count:
        return session
    if turn_count < AUTO_COMPRESS_MIN_TURNS and context_tokens < AUTO_COMPRESS_CONTEXT_TOKEN_THRESHOLD:
        return session
    if pending_messages < AUTO_COMPRESS_MIN_NEW_MESSAGES and context_tokens < AUTO_COMPRESS_CONTEXT_TOKEN_THRESHOLD:
        return session

    transcript = _render_transcript(messages[summary_message_count:cutoff])
    if not transcript.strip():
        return session

    summary = await _invoke_prompt(
        session["model_name"],
        "conversation_compress.md",
        {
            "conversation": transcript,
            "existing_summary": session.get("summary", ""),
        },
    )
    return session_store.set_summary(session_id, summary, summary_message_count=cutoff)


def _resolve_enabled_tool_ids(tool_switches: dict[str, bool]) -> tuple[str, ...]:
    return tuple(tool_id for tool_id, enabled in tool_switches.items() if enabled)


def _build_debug_session_snapshot(
    session: dict[str, Any],
    *,
    request_message: str | None = None,
    memory_sources: tuple[str, ...] = (),
) -> dict[str, Any]:
    snapshot = {
        "session_id": session["id"],
        "thread_id": session["thread_id"],
        "model_name": session["model_name"],
        "debug": bool(session.get("debug")),
        "request_message": request_message,
        "system_prompt": session.get("system_prompt", ""),
        "summary": session.get("summary", ""),
        "summary_message_count": session.get("summary_message_count", 0),
        "working_memory": session.get("working_memory", {}),
        "retrieved_context": session.get("retrieved_context", []),
        "messages": session.get("messages", []),
        "tool_switches": session.get("tool_switches", {}),
        "enabled_tool_ids": list(_resolve_enabled_tool_ids(session.get("tool_switches", {}))),
        "skills_enabled": session.get("skills_enabled", []),
        "memory_sources": list(memory_sources),
        "stats": session.get("stats", {}),
        "raw_message_count": len(session.get("raw_messages", [])),
    }
    return snapshot


def _resolve_safe_path(base_dir: Path, relative_path: str) -> Path:
    cleaned = relative_path.strip().lstrip("/")
    candidate = (base_dir / cleaned).resolve()
    if not candidate.is_relative_to(base_dir.resolve()):
        raise HTTPException(status_code=400, detail="非法路径")
    return candidate


def _resolve_safe_sandbox_file_path(raw_path: str) -> Path:
    settings = get_settings()
    sandbox_root = (settings.project_root / settings.sandbox_root_rel_path).resolve()
    path_text = raw_path.strip()
    if not path_text:
        raise HTTPException(status_code=400, detail="缺少文件路径")

    candidate_path = Path(path_text)
    if candidate_path.is_absolute():
        candidate = candidate_path.resolve()
    else:
        candidate = (settings.project_root / path_text.lstrip("/")).resolve()

    if not candidate.is_relative_to(sandbox_root):
        raise HTTPException(status_code=400, detail="不允许访问沙盒外文件")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


def _managed_skills_root() -> Path:
    settings = get_settings()
    root = settings.project_root / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slugify_skill_name(value: str) -> str:
    normalized = re.sub(r"[^\w]+", "-", value.strip().lower(), flags=re.UNICODE).replace("_", "-")
    slug = re.sub(r"-{2,}", "-", normalized).strip("-")
    return slug or "custom-skill"


def _skill_root_dir_from_path(path: str) -> str:
    cleaned = path.strip().lstrip("/")
    candidate = Path(cleaned)
    if not candidate.parts:
        raise HTTPException(status_code=400, detail="缺少技能路径")
    return candidate.parts[0]


def _default_skill_body(name: str) -> str:
    return (
        f"# {name}\n\n"
        "## 适用场景\n"
        "- 描述这个技能最适合被调用的任务。\n\n"
        "## 工作流程\n"
        "1. 先确认用户目标与输入边界。\n"
        "2. 按步骤执行核心操作。\n"
        "3. 输出结果时给出关键结论与下一步建议。\n\n"
        "## 输出要求\n"
        "- 结果保持简洁、具体、可执行。\n"
    )


def _build_skill_content(
    *,
    slug: str,
    name: str,
    description: str,
    existing_content: str | None = None,
) -> str:
    expected_path = f"/skills/{slug}/SKILL.md"
    metadata, body = split_skill_document(existing_content or "")
    metadata["name"] = slug.strip()
    metadata["description"] = description.strip()
    metadata["path"] = expected_path
    if "allowed-tools" not in metadata and "allowed_tools" not in metadata:
        metadata["allowed-tools"] = []
    normalized = normalize_skill_frontmatter(
        metadata,
        expected_path=expected_path,
        expected_slug=slug.strip(),
    )
    errors = validate_skill_frontmatter(
        normalized,
        expected_path=expected_path,
        expected_slug=slug.strip(),
    )
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    normalized.pop("validation_errors", None)
    frontmatter = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False).strip()
    content_body = body or _default_skill_body(name)
    return f"---\n{frontmatter}\n---\n\n{content_body.rstrip()}\n"


def _write_custom_skill(
    *,
    slug: str,
    name: str,
    description: str,
    existing_content: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    skills_root = _managed_skills_root()
    skill_dir = _resolve_safe_path(skills_root, slug)
    skill_file = skill_dir / "SKILL.md"
    if skill_file.exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"技能已存在: {slug}")

    content = _build_skill_content(
        slug=slug,
        name=name,
        description=description,
        existing_content=existing_content,
    )
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content, encoding="utf-8")
    relative_path = f"{slug}/SKILL.md"
    return {"path": relative_path, "content": content}


def _build_model(model_name: str) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=model_name,
        temperature=settings.temperature,
    )


async def _invoke_prompt(model_name: str, prompt_name: str, replacements: dict[str, str]) -> str:
    prompt = load_prompt(prompt_name)
    for key, value in replacements.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)
    response = await _build_model(model_name).ainvoke(prompt)
    return _extract_model_text(getattr(response, "content", ""))


class SessionCreateRequest(BaseModel):
    model_name: str | None = None


class SessionUpdateRequest(BaseModel):
    title: str | None = None
    model_name: str | None = None
    debug: bool | None = None
    tool_switches: dict[str, bool] | None = None
    skills_enabled: list[str] | None = None


class MessageStreamRequest(BaseModel):
    message: str = Field(min_length=1)
    model_name: str | None = None
    debug: bool | None = None
    tool_switches: dict[str, bool] | None = None
    skills_enabled: list[str] | None = None


class MessageUpdateRequest(BaseModel):
    content: str = Field(min_length=1)


class FileWriteRequest(BaseModel):
    path: str
    content: str


class OptimizeMemoryRequest(BaseModel):
    path: str | None = None
    content: str = Field(min_length=1)


class SkillCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    slug: str | None = None
    content: str | None = None


class SkillUploadRequest(BaseModel):
    filename: str = Field(min_length=1)
    content: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    slug: str | None = None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SERAPH-Claw Backend", version="0.1.0")

    builtin_model_candidates = (
        "doubao-seed-2.0-pro",
        "doubao-seed-2.0-lite",
        "doubao-seed-2.0-code",
        "doubao-seed-code",
        "minimax-m2.5",
        "glm-4.7",
        "deepseek-v3.2",
        "kimi-k2.5",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(FileNotFoundError)
    async def handle_file_not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "backend_root": str(settings.project_root),
            "sessions": len(session_store.list_sessions()),
        }

    @app.get("/api/sandbox/file")
    async def get_sandbox_file(path: str = Query(...)) -> FileResponse:
        target = _resolve_safe_sandbox_file_path(path)
        media_type, _ = mimetypes.guess_type(target.name)
        return FileResponse(target, media_type=media_type or "application/octet-stream")

    @app.get("/api/options")
    async def options() -> dict[str, Any]:
        skills = list_skills(settings.project_root, settings.skill_sources)
        model_candidates = list(
            dict.fromkeys(
                [
                    settings.model_name,
                    *builtin_model_candidates,
                ]
            )
        )
        return {
            "models": model_candidates,
            "skills": skills,
            "tool_switches": [
                {"id": "weather", "label": "Weather"},
                {"id": "knowledge_base", "label": "Knowledge Base"},
                {"id": "python_packages", "label": "Pip Install"},
                {"id": "python_code", "label": "Python Sandbox"},
                {"id": "field_lineage_step", "label": "Field Lineage Step"},
                {"id": "field_lineage_auto", "label": "Field Lineage Auto"},
            ],
            "default_model": settings.model_name,
            "system_prompt": settings.system_prompt,
        }

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return session_store.list_sessions()

    @app.post("/api/sessions")
    async def create_session(request: SessionCreateRequest) -> dict[str, Any]:
        return session_store.create_session(model_name=request.model_name)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return session_store.get_session(session_id)

    @app.patch("/api/sessions/{session_id}")
    async def update_session(session_id: str, request: SessionUpdateRequest) -> dict[str, Any]:
        return session_store.update_session(
            session_id,
            title=request.title,
            model_name=request.model_name,
            debug=request.debug,
            tool_switches=request.tool_switches,
            skills_enabled=request.skills_enabled,
        )

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, bool]:
        session_store.delete_session(session_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel_session_turn(session_id: str) -> dict[str, Any]:
        session = session_store.request_turn_stop(session_id)
        session_store.append_raw_message(
            session_id,
            kind="turn_state",
            payload={"action": "stop_requested", "turn": session.get("turn_state", {})},
        )
        return {"ok": True, "session": session}

    @app.patch("/api/sessions/{session_id}/messages/{message_id}")
    async def update_message(session_id: str, message_id: str, request: MessageUpdateRequest) -> dict[str, Any]:
        return session_store.replace_message(session_id, message_id, request.content)

    @app.post("/api/sessions/{session_id}/messages/{message_id}/truncate")
    async def truncate_after_message(session_id: str, message_id: str) -> dict[str, Any]:
        return session_store.truncate_after_message(session_id, message_id)

    @app.post("/api/sessions/{session_id}/messages/{message_id}/retry-base")
    async def truncate_from_message(session_id: str, message_id: str) -> dict[str, Any]:
        return session_store.truncate_from_message(session_id, message_id)

    @app.post("/api/sessions/{session_id}/compress")
    async def compress_session(session_id: str) -> dict[str, Any]:
        session = session_store.get_session(session_id)
        messages = session["messages"]
        summary_message_count = int(session.get("summary_message_count") or 0)
        cutoff = _compression_cutoff(messages)
        if cutoff <= summary_message_count:
            cutoff = len(messages)
        transcript = _render_transcript(messages[summary_message_count:cutoff])
        if not transcript.strip():
            return {"summary": session.get("summary", ""), "session": session}
        summary = await _invoke_prompt(
            session["model_name"],
            "conversation_compress.md",
            {
                "conversation": transcript,
                "existing_summary": session.get("summary", ""),
            },
        )
        updated = session_store.set_summary(session_id, summary, summary_message_count=cutoff)
        return {"summary": summary, "session": updated}

    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(session_id: str, request: MessageStreamRequest) -> StreamingResponse:
        session = session_store.get_session(session_id)
        if request.tool_switches is not None or request.skills_enabled is not None or request.model_name or request.debug is not None:
            session = session_store.update_session(
                session_id,
                model_name=request.model_name,
                debug=request.debug,
                tool_switches=request.tool_switches,
                skills_enabled=request.skills_enabled,
            )

        async def event_stream():
            active_session = session_store.get_session(session_id)
            maybe_title = generate_title_from_message(request.message, active_session.get("title"))
            if maybe_title is not None:
                active_session = session_store.update_session(session_id, title=maybe_title)
                yield _json_event({"type": "title", "title": maybe_title})

            active_session, user_message = session_store.append_message(session_id, role="user", content=request.message)
            turn_id = user_message["id"]
            active_session = session_store.start_turn(
                session_id,
                turn_id=turn_id,
                user_message_id=user_message["id"],
                requested_text=request.message,
            )
            session_store.append_raw_message(session_id, kind="turn_state", payload=active_session.get("turn_state", {}))
            yield _json_event(_turn_state_event(active_session))
            active_session = await _auto_compress_session_if_needed(session_id)
            active_session = session_store.prepare_for_agent_turn(session_id, request.message)
            system_prompt = active_session["system_prompt"]
            memory_sources = _memory_sources_for_session(session_id)
            preferred_skill_name = (
                str(active_session.get("working_memory", {}).get("active_skill") or "").strip() or None
            )
            session_store.append_raw_message(
                session_id,
                kind="system",
                payload={"role": "system", "content": system_prompt},
            )
            session_store.append_raw_message(
                session_id,
                kind="user",
                payload={"role": "user", "content": request.message},
            )
            debug_enabled = bool(active_session.get("debug"))
            if debug_enabled:
                debug_context_payload = _build_debug_session_snapshot(
                    active_session,
                    request_message=request.message,
                    memory_sources=memory_sources,
                )
                session_store.append_raw_message(session_id, kind="debug_context", payload=debug_context_payload)
                yield _json_event({"type": "debug", "kind": "debug_context", "payload": debug_context_payload})

            sandbox = SessionSandbox(
                project_root=settings.project_root,
                session_id=session_id,
                sandbox_root_rel_path=settings.sandbox_root_rel_path,
                cleanup_on_exit=settings.sandbox_cleanup_on_exit,
            )
            assistant_chunks: list[str] = []
            try:
                async for event in stream_chat_events(
                    request.message,
                    thread_id=active_session["thread_id"],
                    sandbox=sandbox,
                    memory_sources=memory_sources,
                    model_name=active_session["model_name"],
                    system_prompt=system_prompt,
                    enabled_tool_ids=_resolve_enabled_tool_ids(active_session["tool_switches"]),
                    allowed_skill_names=tuple(active_session["skills_enabled"]),
                    preferred_skill_name=preferred_skill_name,
                    debug=debug_enabled,
                ):
                    if session_store.should_stop_turn(session_id, turn_id):
                        interrupted_text = "".join(assistant_chunks).strip() or "已停止本轮生成。"
                        session_store.append_raw_message(
                            session_id,
                            kind="turn_state",
                            payload={"action": "interrupted", "turn_id": turn_id},
                        )
                        session_store.append_raw_message(
                            session_id,
                            kind="assistant",
                            payload={"role": "assistant", "content": interrupted_text, "state": "interrupted"},
                        )
                        _, interrupted_message = session_store.append_message(session_id, role="assistant", content=interrupted_text)
                        session_store.update_message_state(
                            session_id,
                            message_id=interrupted_message["id"],
                            state="interrupted",
                        )
                        session_store.persist_turn_to_memory(session_id)
                        updated_session = session_store.finalize_agent_turn(
                            session_id,
                            user_message=request.message,
                            assistant_text=interrupted_text,
                        )
                        updated_session = session_store.finish_turn(session_id, turn_id=turn_id, status="interrupted")
                        session_store.append_raw_message(
                            session_id,
                            kind="turn_state",
                            payload=updated_session.get("turn_state", {}),
                        )
                        yield _json_event(_turn_state_event(updated_session))
                        yield _json_event(
                            {
                                "type": "done",
                                "message_id": user_message["id"],
                                "session": updated_session,
                            }
                        )
                        return
                    if event["type"] == "token":
                        if not assistant_chunks:
                            active_session = session_store.update_turn_state(
                                session_id,
                                turn_id=turn_id,
                                status="streaming",
                                phase="responding",
                            )
                            yield _json_event(_turn_state_event(active_session))
                        assistant_chunks.append(event["text"])
                    elif event["type"] == "skill":
                        selected_skill = str(event.get("skill") or "").strip()
                        if selected_skill:
                            session_store.set_active_skill(session_id, selected_skill)
                            active_session = session_store.update_turn_state(
                                session_id,
                                turn_id=turn_id,
                                status="streaming",
                                phase="routing",
                                selected_skill=selected_skill,
                            )
                            session_store.append_raw_message(session_id, kind="turn_state", payload=active_session.get("turn_state", {}))
                            yield _json_event(_turn_state_event(active_session))
                        session_store.append_raw_message(session_id, kind=event["type"], payload=event)
                    elif event["type"] == "tool_start":
                        session_store.record_tool_usage(session_id, str(event.get("tool") or "").strip() or None)
                        active_session = session_store.update_turn_state(
                            session_id,
                            turn_id=turn_id,
                            status="streaming",
                            phase="tool",
                            active_tool=str(event.get("tool") or "").strip() or None,
                            increment_tool_count=True,
                        )
                        session_store.append_raw_message(session_id, kind="turn_state", payload=active_session.get("turn_state", {}))
                        yield _json_event(_turn_state_event(active_session))
                        session_store.append_raw_message(session_id, kind=event["type"], payload=event)
                    elif event["type"] == "tool_end":
                        active_session = session_store.update_turn_state(
                            session_id,
                            turn_id=turn_id,
                            status="streaming",
                            phase="responding",
                            active_tool="",
                        )
                        session_store.append_raw_message(session_id, kind="turn_state", payload=active_session.get("turn_state", {}))
                        yield _json_event(_turn_state_event(active_session))
                        session_store.append_raw_message(session_id, kind=event["type"], payload=event)
                    elif event["type"] == "debug":
                        session_store.append_raw_message(session_id, kind=event["kind"], payload=event["payload"])
                    yield _json_event(event)
            except Exception as exc:  # noqa: BLE001
                error_text = str(exc)
                session_store.append_raw_message(session_id, kind="error", payload={"message": error_text})
                _, error_message = session_store.append_message(session_id, role="assistant", content=f"[ERROR] {error_text}")
                session_store.update_message_state(session_id, message_id=error_message["id"], state="error")
                session_store.finalize_agent_turn(session_id, user_message=request.message, assistant_text=f"[ERROR] {error_text}")
                errored_session = session_store.finish_turn(session_id, turn_id=turn_id, status="error")
                session_store.persist_turn_to_memory(session_id)
                session_store.append_raw_message(
                    session_id,
                    kind="turn_state",
                    payload=errored_session.get("turn_state", {}),
                )
                yield _json_event(_turn_state_event(errored_session))
                yield _json_event({"type": "error", "message": error_text})
            else:
                assistant_text = "".join(assistant_chunks).strip() or "(agent 没有返回文本)"
                session_store.append_raw_message(
                    session_id,
                    kind="assistant",
                    payload={"role": "assistant", "content": assistant_text, "state": "completed"},
                )
                _, assistant_message = session_store.append_message(session_id, role="assistant", content=assistant_text)
                session_store.update_message_state(session_id, message_id=assistant_message["id"], state="completed")
                session_store.persist_turn_to_memory(session_id)
                session_store.finalize_agent_turn(
                    session_id,
                    user_message=request.message,
                    assistant_text=assistant_text,
                )
                updated_session = session_store.finish_turn(session_id, turn_id=turn_id, status="completed")
                session_store.append_raw_message(
                    session_id,
                    kind="turn_state",
                    payload=updated_session.get("turn_state", {}),
                )
                yield _json_event(_turn_state_event(updated_session))
                if debug_enabled:
                    debug_result_payload = {
                        "assistant_text": assistant_text,
                        "session_snapshot": _build_debug_session_snapshot(
                            updated_session,
                            request_message=request.message,
                            memory_sources=memory_sources,
                        ),
                    }
                    session_store.append_raw_message(session_id, kind="debug_turn_result", payload=debug_result_payload)
                    yield _json_event({"type": "debug", "kind": "debug_turn_result", "payload": debug_result_payload})
                yield _json_event(
                    {
                        "type": "done",
                        "message_id": user_message["id"],
                        "session": updated_session,
                    }
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/memory/files")
    async def list_memory_files() -> list[dict[str, Any]]:
        memory_root = settings.project_root / "memory"
        files = []
        for path in sorted(memory_root.rglob("*.md")):
            stat = path.stat()
            files.append(
                {
                    "path": path.relative_to(memory_root).as_posix(),
                    "name": path.name,
                    "updated_at": stat.st_mtime,
                    "size": stat.st_size,
                }
            )
        return files

    @app.get("/api/memory/file")
    async def get_memory_file(path: str = Query(...)) -> dict[str, Any]:
        memory_root = settings.project_root / "memory"
        target = _resolve_safe_path(memory_root, path)
        return {"path": path, "content": target.read_text(encoding="utf-8")}

    @app.put("/api/memory/file")
    async def save_memory_file(request: FileWriteRequest) -> dict[str, Any]:
        memory_root = settings.project_root / "memory"
        target = _resolve_safe_path(memory_root, request.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(request.content, encoding="utf-8")
        return {"ok": True}

    @app.post("/api/memory/optimize")
    async def optimize_memory(request: OptimizeMemoryRequest) -> dict[str, Any]:
        suggestion = await _invoke_prompt(
            settings.model_name,
            "memory_optimize.md",
            {
                "file_path": request.path or "(unspecified)",
                "content": request.content,
            },
        )
        return {"suggestion": suggestion}

    @app.post("/api/skills/optimize")
    async def optimize_skill(request: OptimizeMemoryRequest) -> dict[str, Any]:
        suggestion = await _invoke_prompt(
            settings.model_name,
            "skill_optimize.md",
            {
                "file_path": request.path or "(unspecified)",
                "content": request.content,
            },
        )
        return {"suggestion": suggestion}

    @app.get("/api/prompts/file")
    async def get_prompt_file(path: str = Query(...)) -> dict[str, Any]:
        prompts_root = settings.project_root / settings.prompts_dir_rel_path
        target = _resolve_safe_path(prompts_root, path)
        return {"path": path, "content": target.read_text(encoding="utf-8")}

    @app.put("/api/prompts/file")
    async def save_prompt_file(request: FileWriteRequest) -> dict[str, Any]:
        prompts_root = settings.project_root / settings.prompts_dir_rel_path
        target = _resolve_safe_path(prompts_root, request.path)
        target.write_text(request.content, encoding="utf-8")
        return {"ok": True}

    @app.get("/api/skills")
    async def list_skill_cards() -> list[dict[str, Any]]:
        return list_skills(settings.project_root, settings.skill_sources)

    @app.post("/api/skills")
    async def create_skill(request: SkillCreateRequest) -> dict[str, Any]:
        slug = _slugify_skill_name(request.slug or request.name)
        created = _write_custom_skill(
            slug=slug,
            name=request.name.strip(),
            description=request.description.strip(),
            existing_content=request.content,
        )
        return {"ok": True, **created}

    @app.post("/api/skills/upload")
    async def upload_skill(request: SkillUploadRequest) -> dict[str, Any]:
        metadata, _ = split_skill_document(request.content)
        fallback_name = Path(request.filename).stem or "custom-skill"
        name = (
            (request.name or "").strip()
            or str(metadata.get("name", "")).strip()
            or fallback_name
        )
        description = (
            (request.description or "").strip()
            or str(metadata.get("description", "")).strip()
            or f"{name} 自定义技能"
        )
        slug = _slugify_skill_name(request.slug or name)
        created = _write_custom_skill(
            slug=slug,
            name=name,
            description=description,
            existing_content=request.content,
        )
        return {"ok": True, **created}

    @app.get("/api/skills/file")
    async def get_skill_file(path: str = Query(...)) -> dict[str, Any]:
        skills_root = settings.project_root / "skills"
        target = _resolve_safe_path(skills_root, path)
        return {"path": path, "content": target.read_text(encoding="utf-8")}

    @app.get("/api/skills/files")
    async def list_skill_files(path: str = Query(...)) -> list[dict[str, Any]]:
        skills_root = settings.project_root / "skills"
        skill_root_dir = _resolve_safe_path(skills_root, _skill_root_dir_from_path(path))
        if not skill_root_dir.exists() or not skill_root_dir.is_dir():
            raise HTTPException(status_code=404, detail="技能不存在")

        files: list[dict[str, Any]] = []
        for item in sorted(skill_root_dir.rglob("*")):
            if not item.is_file():
                continue
            relative_path = item.relative_to(skill_root_dir).as_posix()
            files.append(
                {
                    "path": item.relative_to(skills_root).as_posix(),
                    "relative_path": relative_path,
                    "name": item.name,
                    "depth": max(0, len(Path(relative_path).parts) - 1),
                }
            )
        return files

    @app.put("/api/skills/file")
    async def save_skill_file(request: FileWriteRequest) -> dict[str, Any]:
        skills_root = settings.project_root / "skills"
        target = _resolve_safe_path(skills_root, request.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content_to_write = request.content
        if target.name == "SKILL.md":
            relative_path = "/" + target.relative_to(settings.project_root).as_posix()
            slug = target.parent.name
            metadata, body = split_skill_document(request.content)
            normalized = normalize_skill_frontmatter(
                metadata,
                expected_path=relative_path,
                expected_slug=slug,
            )
            errors = validate_skill_frontmatter(
                normalized,
                expected_path=relative_path,
                expected_slug=slug,
            )
            if errors:
                raise HTTPException(status_code=400, detail="; ".join(errors))
            normalized.pop("validation_errors", None)
            frontmatter = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False).strip()
            content_to_write = f"---\n{frontmatter}\n---\n\n{body.rstrip()}\n"
        target.write_text(content_to_write, encoding="utf-8")
        return {"ok": True}

    @app.delete("/api/skills/file")
    async def delete_skill_file(path: str = Query(...)) -> dict[str, Any]:
        skills_root = settings.project_root / "skills"
        target = _resolve_safe_path(skills_root, path)
        if target.name != "SKILL.md":
            raise HTTPException(status_code=400, detail="只能删除技能主文件")
        if not target.exists():
            raise HTTPException(status_code=404, detail="技能不存在")

        skill_dir = target.parent
        shutil.rmtree(skill_dir)
        return {"ok": True}

    return app
