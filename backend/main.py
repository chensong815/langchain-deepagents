"""终端程序入口：支持 CLI 与 API 服务。"""

from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deep Agent Skills Backend")
    parser.add_argument(
        "mode",
        nargs="?",
        default="cli",
        choices=("cli", "serve"),
        help="运行模式：cli 或 serve",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="启动时直接恢复指定会话，支持 latest",
    )
    parser.add_argument(
        "--pick-session",
        action="store_true",
        help="启动时列出最近会话，并交互式选择恢复哪个 session",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="仅列出最近历史会话并退出",
    )
    parser.add_argument("--host", help="API 服务监听地址")
    parser.add_argument("--port", type=int, help="API 服务端口")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.mode == "serve":
        import uvicorn

        from app.config import get_settings
        from app.server import create_app

        settings = get_settings()
        uvicorn.run(
            create_app(),
            host=args.host or settings.api_host,
            port=args.port or settings.api_port,
        )
        return

    from app.cli import run_cli

    run_cli(
        resume_target=args.resume,
        pick_session_on_start=args.pick_session,
        list_sessions_only=args.sessions,
    )


if __name__ == "__main__":
    # 统一入口，便于后续替换为其他运行模式。
    main()
