"""终端交互模块：处理用户输入、命令分发与流式输出。"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent import get_agent, stream_chat_sync
from app.config import get_settings
from app.sandbox import SessionSandbox
from app.session_memory import SessionMemoryWriter, build_session_id, list_session_records, load_session_record
from app.skill_catalog import list_skills


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
    memory_sources: tuple[str, ...]
    sandbox: SessionSandbox


def _build_session_memory_sources(memory_writer: SessionMemoryWriter) -> tuple[str, ...]:
    settings = get_settings()
    return tuple(dict.fromkeys((*settings.memory_sources, memory_writer.memory_virtual_path)))


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
    )
    memory_sources = _build_session_memory_sources(memory_writer)
    get_agent(memory_sources)
    sandbox = SessionSandbox(
        project_root=settings.project_root,
        session_id=memory_writer.session_id,
        sandbox_root_rel_path=settings.sandbox_root_rel_path,
        cleanup_on_exit=settings.sandbox_cleanup_on_exit,
    )
    return ActiveSession(
        thread_id=thread_id,
        memory_writer=memory_writer,
        memory_sources=memory_sources,
        sandbox=sandbox,
    )


def _resume_active_session(target: str) -> ActiveSession:
    settings = get_settings()
    normalized_target = target.strip()
    if normalized_target == "latest":
        sessions = list_session_records(settings.project_root, limit=1)
        if not sessions:
            raise FileNotFoundError("没有可恢复的历史会话。")
        record = sessions[0]
    else:
        record = load_session_record(settings.project_root, normalized_target)
        if record is None:
            raise FileNotFoundError(f"未找到会话: {normalized_target}")

    memory_writer = SessionMemoryWriter.resume(
        project_root=settings.project_root,
        session_id=record.session_id,
        model_name=settings.model_name,
    )
    memory_sources = _build_session_memory_sources(memory_writer)
    get_agent(memory_sources)
    sandbox = SessionSandbox(
        project_root=settings.project_root,
        session_id=memory_writer.session_id,
        sandbox_root_rel_path=settings.sandbox_root_rel_path,
        cleanup_on_exit=settings.sandbox_cleanup_on_exit,
    )
    return ActiveSession(
        thread_id=memory_writer.thread_id,
        memory_writer=memory_writer,
        memory_sources=memory_sources,
        sandbox=sandbox,
    )


def _print_sessions() -> None:
    settings = get_settings()
    sessions = list_session_records(settings.project_root)
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
    sessions = list_session_records(settings.project_root, limit=limit)
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
    settings = get_settings()
    session.memory_writer.delete_if_empty()
    if settings.sandbox_cleanup_on_exit:
        session.sandbox.cleanup()


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

            print("assistant> ", end="", flush=True)
            has_output = False
            assistant_chunks: list[str] = []
            try:
                for chunk in stream_chat_sync(
                    user_input,
                    thread_id=active_session.thread_id,
                    sandbox=active_session.sandbox,
                    memory_sources=active_session.memory_sources,
                ):
                    if not chunk:
                        continue
                    has_output = True
                    assistant_chunks.append(chunk)
                    print(chunk, end="", flush=True)
            except Exception as exc:
                print(f"\n调用失败: {exc}")
                active_session.memory_writer.append_turn(user_text=user_input, assistant_text=f"[ERROR] 调用失败: {exc}")
                continue

            assistant_text = "".join(assistant_chunks).strip()
            if not has_output:
                print("(agent 没有返回文本)", end="")
                assistant_text = "(agent 没有返回文本)"
            active_session.memory_writer.append_turn(user_text=user_input, assistant_text=assistant_text)
            print()
    finally:
        _close_active_session(active_session)
