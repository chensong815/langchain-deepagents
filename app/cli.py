"""终端交互模块：处理用户输入、命令分发与流式输出。"""

from __future__ import annotations

from app.agent import get_agent, stream_chat_sync
from app.config import get_settings
from app.sandbox import SessionSandbox
from app.session_memory import SessionMemoryWriter
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


def run_cli() -> None:
    """启动终端会话循环，支持普通提问与内置命令。"""
    settings = get_settings()
    # 启动时预热 agent，尽早暴露配置错误。
    get_agent()

    thread_id = settings.default_thread_id
    memory_writer = SessionMemoryWriter(
        project_root=settings.project_root,
        thread_id=thread_id,
        model_name=settings.model_name,
    )
    sandbox = SessionSandbox(
        project_root=settings.project_root,
        session_id=memory_writer.session_id,
        sandbox_root_rel_path=settings.sandbox_root_rel_path,
        cleanup_on_exit=settings.sandbox_cleanup_on_exit,
    )

    print("Deep Agent Skills 终端交互模式")
    print(f"模型: {settings.model_name} | 会话ID: {thread_id}")
    print(f"会话历史文件: {memory_writer.memory_path}")
    print(f"会话沙盒目录: {sandbox.sandbox_path}")
    print("输入内容开始对话，输入 /skills 查看技能，输入 /exit 退出。")
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

            if user_input in {"/skills", "skills"}:
                _print_skills()
                continue

            print("assistant> ", end="", flush=True)
            has_output = False
            assistant_chunks: list[str] = []
            try:
                for chunk in stream_chat_sync(user_input, thread_id=thread_id, sandbox=sandbox):
                    if not chunk:
                        continue
                    has_output = True
                    assistant_chunks.append(chunk)
                    print(chunk, end="", flush=True)
            except Exception as exc:
                print(f"\n调用失败: {exc}")
                memory_writer.append_turn(user_text=user_input, assistant_text=f"[ERROR] 调用失败: {exc}")
                continue

            assistant_text = "".join(assistant_chunks).strip()
            if not has_output:
                print("(agent 没有返回文本)", end="")
                assistant_text = "(agent 没有返回文本)"
            memory_writer.append_turn(user_text=user_input, assistant_text=assistant_text)
            print()
    finally:
        if settings.sandbox_cleanup_on_exit:
            sandbox.cleanup()
