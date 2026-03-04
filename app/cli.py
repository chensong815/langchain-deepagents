"""终端交互模块：处理用户输入、命令分发与流式输出。"""

from __future__ import annotations

from app.agent import get_agent, stream_chat_sync
from app.config import get_settings
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

    print("Deep Agent Skills 终端交互模式")
    print(f"模型: {settings.model_name} | 会话ID: {thread_id}")
    print("输入内容开始对话，输入 /skills 查看技能，输入 /exit 退出。")
    _print_skills()

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
        try:
            for chunk in stream_chat_sync(user_input, thread_id=thread_id):
                if not chunk:
                    continue
                has_output = True
                print(chunk, end="", flush=True)
        except Exception as exc:
            print(f"\n调用失败: {exc}")
            continue

        if not has_output:
            print("(agent 没有返回文本)", end="")
        print()
