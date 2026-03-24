"""Prompt 文件加载。"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def load_prompt(name: str) -> str:
    settings = get_settings()
    prompt_path = settings.project_root / settings.prompts_dir_rel_path / name
    return prompt_path.read_text(encoding="utf-8").strip()


def prompt_path(name: str) -> Path:
    settings = get_settings()
    return settings.project_root / settings.prompts_dir_rel_path / name
