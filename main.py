"""终端程序入口：启动 CLI 对话循环。"""

from app.cli import run_cli


if __name__ == "__main__":
    # 统一入口，便于后续替换为其他运行模式。
    run_cli()
