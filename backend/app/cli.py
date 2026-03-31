"""终端交互模块：处理用户输入、命令分发与流式输出。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent import get_agent, iter_chat_events_sync
from app.config import get_settings
from app.context_retrieval import retrieve_relevant_context
from app.prompts import load_prompt
from app.sandbox import SessionSandbox
from app.session_context import render_session_context, session_context_virtual_path
from app.session_memory import (
    SessionMemoryWriter,
    SessionTurn,
    build_session_id,
    list_session_records,
    load_session_record,
    load_session_turns,
)
from app.session_store import (
    _extract_confirmed_slots,
    _extract_open_loops_from_assistant,
    _extract_pending_slots,
    _merge_artifacts,
    _merge_state_items,
    _normalize_string_map,
    _normalize_text,
    _record_recent_tool,
    _scan_paths,
    _should_track_goal_as_open_loop,
)
from app.skill_catalog import list_skills
from langchain_openai import ChatOpenAI


AUTO_COMPRESS_KEEP_RECENT_TURNS = 4
AUTO_COMPRESS_MIN_TURNS = 6
AUTO_COMPRESS_MIN_NEW_MESSAGES = 4
AUTO_COMPRESS_CONTEXT_TOKEN_THRESHOLD = 2400


def _print_skills() -> None:
    """读取并打印当前可用技能列表。"""
    settings = get_settings()
    skills = list_skills(settings.project_root, settings.skill_sources)
    if not skills:
        print("未发现可用技能。")
        return

    print("已加载技能：")
    for item in skills:
        print(f"- {item['name']}: {item['description']}")


@dataclass
class ActiveSession:
    """CLI 当前活跃会话的运行时上下文。"""

    thread_id: str
    memory_writer: SessionMemoryWriter
    turns: list[SessionTurn]
    memory_sources: tuple[str, ...]
    sandbox: SessionSandbox
    context_path: Path
    summary: str = ""
    summary_message_count: int = 0
    active_skill: str | None = None
    recent_tools: list[str] | None = None
    current_goal: str | None = None
    confirmed_slots: dict[str, str] | None = None
    pending_slots: list[str] | None = None
    artifacts: list[dict[str, str]] | None = None
    open_loops: list[str] | None = None
    retrieved_context: list[dict[str, Any]] | None = None


def _context_path(session_id: str) -> Path:
    settings = get_settings()
    path = settings.project_root / settings.session_context_dir_rel_path / f"{session_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_cli_context(session: ActiveSession) -> None:
    messages: list[dict[str, str]] = []
    for turn in session.turns:
        messages.append({"role": "user", "content": turn.user_text})
        messages.append({"role": "assistant", "content": turn.assistant_text})
    payload = {
        "summary": session.summary,
        "summary_message_count": session.summary_message_count,
        "working_memory": {
            "active_skill": session.active_skill,
            "recent_tools": list(session.recent_tools or []),
            "current_goal": session.current_goal,
            "confirmed_slots": dict(session.confirmed_slots or {}),
            "pending_slots": list(session.pending_slots or []),
            "artifacts": list(session.artifacts or []),
            "open_loops": list(session.open_loops or []),
        },
        "retrieved_context": list(session.retrieved_context or []),
        "messages": messages,
    }
    session.context_path.write_text(render_session_context(payload), encoding="utf-8")


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


def _build_model(model_name: str) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=model_name,
        temperature=settings.temperature,
    )


def _invoke_prompt(model_name: str, prompt_name: str, replacements: dict[str, str]) -> str:
    prompt = load_prompt(prompt_name)
    for key, value in replacements.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)
    response = _build_model(model_name).invoke(prompt)
    return _extract_model_text(getattr(response, "content", ""))


def _render_transcript_from_turns(turns: list[SessionTurn]) -> str:
    lines: list[str] = []
    for turn in turns:
        if turn.user_text.strip():
            lines.append(f"user: {turn.user_text}")
        if turn.assistant_text.strip():
            lines.append(f"assistant: {turn.assistant_text}")
    return "\n\n".join(lines)


def _compression_cutoff_turn(turns: list[SessionTurn], *, keep_recent_turns: int = AUTO_COMPRESS_KEEP_RECENT_TURNS) -> int:
    if len(turns) <= keep_recent_turns:
        return 0
    return len(turns) - keep_recent_turns


def _estimate_tokens(text: str) -> int:
    normalized = _normalize_text(text)
    if not normalized:
        return 0
    return max(1, round(len(normalized) / 4))


def _cli_session_payload(session: ActiveSession) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    for turn in session.turns:
        messages.append({"role": "user", "content": turn.user_text})
        messages.append({"role": "assistant", "content": turn.assistant_text})
    return {
        "id": session.memory_writer.session_id,
        "summary": session.summary,
        "summary_message_count": session.summary_message_count * 2,
        "working_memory": {
            "active_skill": session.active_skill,
            "recent_tools": list(session.recent_tools or []),
            "current_goal": session.current_goal,
            "confirmed_slots": dict(session.confirmed_slots or {}),
            "pending_slots": list(session.pending_slots or []),
            "artifacts": list(session.artifacts or []),
            "open_loops": list(session.open_loops or []),
        },
        "messages": messages,
    }


def _prepare_cli_turn(session: ActiveSession, user_message: str) -> None:
    session.current_goal = _normalize_text(user_message, max_chars=220) or None
    confirmed = {
        **dict(session.confirmed_slots or {}),
        **_extract_confirmed_slots(user_message),
    }
    session.confirmed_slots = _normalize_string_map(confirmed, max_items=16)
    pending = _extract_pending_slots(user_message)
    session.pending_slots = _merge_state_items(list(session.pending_slots or []), pending, max_items=8)
    new_loops = pending or ([session.current_goal] if _should_track_goal_as_open_loop(session.current_goal) else [])
    session.open_loops = _merge_state_items(list(session.open_loops or []), new_loops, max_items=8)
    session.retrieved_context = retrieve_relevant_context(_cli_session_payload(session), user_message, limit=4)
    _write_cli_context(session)


def _finalize_cli_turn(session: ActiveSession, user_message: str, assistant_text: str) -> None:
    confirmed = {
        **dict(session.confirmed_slots or {}),
        **_extract_confirmed_slots(user_message),
        **_extract_confirmed_slots(assistant_text),
    }
    session.confirmed_slots = _normalize_string_map(confirmed, max_items=16)
    assistant_open_loops = _extract_open_loops_from_assistant(assistant_text)
    if assistant_text.startswith("[ERROR]"):
        fallback_loops = _extract_pending_slots(user_message)
        session.open_loops = _merge_state_items(
            list(session.open_loops or []),
            assistant_open_loops or fallback_loops,
            resolution_text=assistant_text,
            max_items=8,
        )
    else:
        session.open_loops = _merge_state_items(
            list(session.open_loops or []),
            assistant_open_loops,
            resolution_text=assistant_text,
            max_items=8,
        )
    session.pending_slots = _merge_state_items(
        list(session.pending_slots or []),
        assistant_open_loops,
        resolution_text=assistant_text,
        max_items=8,
    )
    additions = [{"path": path, "description": "mentioned in assistant response"} for path in sorted(_scan_paths(assistant_text))]
    session.artifacts = _merge_artifacts(list(session.artifacts or []), additions)
    _write_cli_context(session)


def _auto_compress_cli_session_if_needed(session: ActiveSession) -> None:
    cutoff = _compression_cutoff_turn(session.turns)
    pending_turns = cutoff - session.summary_message_count
    context_tokens = _estimate_tokens(session.summary) + sum(
        _estimate_tokens(turn.user_text) + _estimate_tokens(turn.assistant_text) for turn in session.turns
    )
    if cutoff <= session.summary_message_count:
        return
    if len(session.turns) < AUTO_COMPRESS_MIN_TURNS and context_tokens < AUTO_COMPRESS_CONTEXT_TOKEN_THRESHOLD:
        return
    if pending_turns < AUTO_COMPRESS_MIN_NEW_MESSAGES and context_tokens < AUTO_COMPRESS_CONTEXT_TOKEN_THRESHOLD:
        return
    transcript = _render_transcript_from_turns(session.turns[session.summary_message_count:cutoff])
    if not transcript.strip():
        return
    summary = _invoke_prompt(
        session.memory_writer.model_name,
        "conversation_compress.md",
        {
            "conversation": transcript,
            "existing_summary": session.summary,
        },
    )
    session.summary = summary
    session.summary_message_count = cutoff
    _write_cli_context(session)


def _build_session_memory_sources(session_id: str) -> tuple[str, ...]:
    settings = get_settings()
    context_virtual = session_context_virtual_path(session_id, settings.session_context_dir_rel_path)
    return tuple(dict.fromkeys((*settings.memory_sources, context_virtual)))


def _print_session_banner(settings_model_name: str, session: ActiveSession) -> None:
    print(f"模型: {settings_model_name} | Thread ID: {session.thread_id}")
    print(f"会话ID: {session.memory_writer.session_id}")
    print(f"会话历史文件: {session.memory_writer.memory_path}")
    print(f"会话沙盒目录: {session.sandbox.sandbox_path}")


def _create_active_session() -> ActiveSession:
    settings = get_settings()
    session_id = build_session_id()
    thread_id = f"{settings.default_thread_id}:{session_id}" if settings.default_thread_id else session_id
    memory_writer = SessionMemoryWriter(
        project_root=settings.project_root,
        thread_id=thread_id,
        model_name=settings.model_name,
        session_id=session_id,
        memory_dir_rel_path=settings.session_memory_dir_rel_path,
    )
    context_path = _context_path(memory_writer.session_id)
    memory_sources = _build_session_memory_sources(memory_writer.session_id)
    get_agent(memory_sources, allowed_skill_names=None)
    sandbox = SessionSandbox(
        project_root=settings.project_root,
        session_id=memory_writer.session_id,
        sandbox_root_rel_path=settings.sandbox_root_rel_path,
    )
    active = ActiveSession(
        thread_id=thread_id,
        memory_writer=memory_writer,
        turns=[],
        memory_sources=memory_sources,
        sandbox=sandbox,
        context_path=context_path,
        recent_tools=[],
        confirmed_slots={},
        pending_slots=[],
        artifacts=[],
        open_loops=[],
        retrieved_context=[],
    )
    _write_cli_context(active)
    return active


def _resume_active_session(target: str) -> ActiveSession:
    settings = get_settings()
    normalized_target = target.strip()
    if normalized_target == "latest":
        sessions = list_session_records(
            settings.project_root,
            memory_dir_rel_path=settings.session_memory_dir_rel_path,
            limit=1,
        )
        if not sessions:
            raise FileNotFoundError("没有可恢复的历史会话。")
        record = sessions[0]
    else:
        record = load_session_record(
            settings.project_root,
            normalized_target,
            memory_dir_rel_path=settings.session_memory_dir_rel_path,
        )
        if record is None:
            raise FileNotFoundError(f"未找到会话: {normalized_target}")

    memory_writer = SessionMemoryWriter.resume(
        project_root=settings.project_root,
        session_id=record.session_id,
        model_name=settings.model_name,
        memory_dir_rel_path=settings.session_memory_dir_rel_path,
    )
    turns = load_session_turns(
        settings.project_root,
        record.session_id,
        memory_dir_rel_path=settings.session_memory_dir_rel_path,
    )
    context_path = _context_path(memory_writer.session_id)
    memory_sources = _build_session_memory_sources(memory_writer.session_id)
    get_agent(memory_sources, allowed_skill_names=None)
    sandbox = SessionSandbox(
        project_root=settings.project_root,
        session_id=memory_writer.session_id,
        sandbox_root_rel_path=settings.sandbox_root_rel_path,
    )
    active = ActiveSession(
        thread_id=memory_writer.thread_id,
        memory_writer=memory_writer,
        turns=turns,
        memory_sources=memory_sources,
        sandbox=sandbox,
        context_path=context_path,
        recent_tools=[],
        confirmed_slots={},
        pending_slots=[],
        artifacts=[],
        open_loops=[],
        retrieved_context=[],
    )
    _write_cli_context(active)
    return active


def _print_sessions() -> None:
    settings = get_settings()
    sessions = list_session_records(settings.project_root, memory_dir_rel_path=settings.session_memory_dir_rel_path)
    if not sessions:
        print("未发现历史会话。")
        return

    print("最近会话：")
    for record in sessions:
        print(
            f"- {record.session_id} | turns={record.turn_count} | started_at={record.started_at} | "
            f"last={record.last_timestamp} | thread_id={record.thread_id}"
        )


def _print_sessions_with_index(limit: int = 10) -> list:
    settings = get_settings()
    sessions = list_session_records(
        settings.project_root,
        memory_dir_rel_path=settings.session_memory_dir_rel_path,
        limit=limit,
    )
    if not sessions:
        print("未发现可恢复的历史会话。")
        return []

    print("可恢复会话：")
    for index, record in enumerate(sessions, start=1):
        print(
            f"{index}. {record.session_id} | turns={record.turn_count} | "
            f"started_at={record.started_at} | last={record.last_timestamp}"
        )
    print("输入序号恢复会话，输入 n 新建会话，直接回车默认恢复最新会话。")
    return sessions


def _choose_startup_resume_target(limit: int = 10) -> str | None:
    sessions = _print_sessions_with_index(limit=limit)
    if not sessions:
        return None

    while True:
        try:
            raw = input("select> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not raw:
            return sessions[0].session_id
        if raw.lower() in {"n", "new"}:
            return None
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(sessions):
                return sessions[index - 1].session_id
        print("请输入有效序号、`n`，或直接回车。")


def _close_active_session(session: ActiveSession) -> None:
    session.memory_writer.delete_if_empty()
    if session.context_path.exists():
        session.context_path.unlink()


def run_cli(
    *,
    resume_target: str | None = None,
    pick_session_on_start: bool = False,
    list_sessions_only: bool = False,
) -> None:
    """启动终端会话循环，支持普通提问与内置命令。"""
    settings = get_settings()
    if list_sessions_only:
        _print_sessions()
        return

    startup_resume_target = resume_target
    if pick_session_on_start and startup_resume_target is None:
        startup_resume_target = _choose_startup_resume_target()

    if startup_resume_target is None:
        active_session = _create_active_session()
    else:
        active_session = _resume_active_session(startup_resume_target)

    print("Deep Agent Skills 终端交互模式")
    _print_session_banner(settings.model_name, active_session)
    print("输入内容开始对话，输入 /skills、/sessions、/resume <session_id|latest>，输入 /exit 退出。")
    _print_skills()
    try:
        while True:
            try:
                user_input = input("\nuser> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n会话结束。")
                break

            if not user_input:
                continue

            if user_input in {"/exit", "exit", "quit", "/quit"}:
                print("会话结束。")
                break

            if user_input in {"/session", "session"}:
                _print_session_banner(settings.model_name, active_session)
                continue

            if user_input in {"/skills", "skills"}:
                _print_skills()
                continue

            if user_input in {"/sessions", "sessions"}:
                _print_sessions()
                continue

            if user_input.startswith("/resume"):
                _, _, target = user_input.partition(" ")
                target = target.strip()
                if not target:
                    print("用法: /resume <session_id|latest>")
                    continue
                try:
                    next_session = _resume_active_session(target)
                except Exception as exc:
                    print(f"恢复会话失败: {exc}")
                    continue
                _close_active_session(active_session)
                active_session = next_session
                print("已切换到历史会话：")
                _print_session_banner(settings.model_name, active_session)
                continue

            _auto_compress_cli_session_if_needed(active_session)
            _prepare_cli_turn(active_session, user_input)
            print("assistant> ", end="", flush=True)
            has_output = False
            assistant_chunks: list[str] = []
            try:
                for event in iter_chat_events_sync(
                    user_input,
                    thread_id=active_session.thread_id,
                    sandbox=active_session.sandbox,
                    memory_sources=active_session.memory_sources,
                    preferred_skill_name=active_session.active_skill,
                ):
                    if event["type"] == "skill":
                        selected_skill = str(event.get("skill") or "").strip()
                        active_session.active_skill = selected_skill or active_session.active_skill
                        _write_cli_context(active_session)
                        continue
                    if event["type"] == "tool_start":
                        tool_name = str(event.get("tool") or "").strip()
                        if tool_name:
                            working_memory = {"recent_tools": list(active_session.recent_tools or [])}
                            _record_recent_tool(working_memory, tool_name)
                            active_session.recent_tools = list(working_memory["recent_tools"])
                            _write_cli_context(active_session)
                        continue
                    if event["type"] != "token":
                        continue
                    chunk = event["text"]
                    if not chunk:
                        continue
                    has_output = True
                    assistant_chunks.append(chunk)
                    print(chunk, end="", flush=True)
            except Exception as exc:
                print(f"\n调用失败: {exc}")
                active_session.memory_writer.append_turn(user_text=user_input, assistant_text=f"[ERROR] 调用失败: {exc}")
                active_session.turns.append(
                    SessionTurn(
                        turn=len(active_session.turns) + 1,
                        timestamp="",
                        user_text=user_input,
                        assistant_text=f"[ERROR] 调用失败: {exc}",
                    )
                )
                _finalize_cli_turn(active_session, user_input, f"[ERROR] 调用失败: {exc}")
                continue

            assistant_text = "".join(assistant_chunks).strip()
            if not has_output:
                print("(agent 没有返回文本)", end="")
                assistant_text = "(agent 没有返回文本)"
            active_session.memory_writer.append_turn(user_text=user_input, assistant_text=assistant_text)
            active_session.turns.append(
                SessionTurn(
                    turn=len(active_session.turns) + 1,
                    timestamp="",
                    user_text=user_input,
                    assistant_text=assistant_text,
                )
            )
            _finalize_cli_turn(active_session, user_input, assistant_text)
            print()
    finally:
        _close_active_session(active_session)
