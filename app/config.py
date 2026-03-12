"""环境配置加载模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parse_posix_paths(raw_value: str | None, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """将逗号分隔的路径字符串转换为规范化的 POSIX 路径元组。"""
    if not raw_value:
        return fallback

    normalized: list[str] = []
    for chunk in raw_value.split(","):
        value = chunk.strip()
        if not value:
            continue
        if not value.startswith("/"):
            value = f"/{value}"
        normalized.append(value.rstrip("/"))

    return tuple(normalized or fallback)


def _parse_bool(raw_value: str | None, fallback: bool) -> bool:
    if raw_value is None:
        return fallback
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return fallback


@dataclass(frozen=True)
class Settings:
    """应用运行时配置。"""

    deepseek_api_key: str
    deepseek_base_url: str
    model_name: str
    temperature: float
    default_thread_id: str
    system_prompt: str
    intent_router_enabled: bool
    intent_router_threshold: float
    intent_router_model: str
    skill_sources: tuple[str, ...]
    memory_sources: tuple[str, ...]
    sandbox_root_rel_path: str
    sandbox_command_timeout_seconds: float
    sandbox_install_timeout_seconds: float
    sandbox_output_char_limit: int
    sandbox_cleanup_on_exit: bool
    project_root: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取 `.env` 并返回缓存后的配置对象。"""
    load_dotenv()

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY in environment.")

    return Settings(
        deepseek_api_key=api_key,
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        model_name=os.getenv("MODEL_NAME", "deepseek-chat").strip(),
        temperature=float(os.getenv("MODEL_TEMPERATURE", "0.3")),
        default_thread_id=os.getenv("DEFAULT_THREAD_ID", "default").strip(),
        system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            "You are an engineering copilot. Be concise, factual, and action-oriented.",
        ).strip(),
        intent_router_enabled=_parse_bool(os.getenv("INTENT_ROUTER_ENABLED"), True),
        intent_router_threshold=max(0.0, min(float(os.getenv("INTENT_ROUTER_THRESHOLD", "0.72")), 1.0)),
        intent_router_model=os.getenv("INTENT_ROUTER_MODEL", "").strip(),
        skill_sources=_parse_posix_paths(os.getenv("SKILL_SOURCES"), ("/skills/base",)),
        memory_sources=_parse_posix_paths(os.getenv("MEMORY_SOURCES"), ("/memory/AGENTS.md",)),
        sandbox_root_rel_path=os.getenv("SANDBOX_ROOT_REL_PATH", ".sandbox").strip() or ".sandbox",
        sandbox_command_timeout_seconds=max(1.0, float(os.getenv("SANDBOX_COMMAND_TIMEOUT_SECONDS", "60"))),
        sandbox_install_timeout_seconds=max(1.0, float(os.getenv("SANDBOX_INSTALL_TIMEOUT_SECONDS", "180"))),
        sandbox_output_char_limit=max(1000, int(os.getenv("SANDBOX_OUTPUT_CHAR_LIMIT", "12000"))),
        sandbox_cleanup_on_exit=_parse_bool(os.getenv("SANDBOX_CLEANUP_ON_EXIT"), True),
        project_root=PROJECT_ROOT,
    )
