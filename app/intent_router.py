"""LLM 语义意图路由：根据用户问题动态选择 skill。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.skill_catalog import SkillCard, list_skills


@dataclass(frozen=True)
class SkillRouteDecision:
    """路由决策结果。"""

    selected_skill: str | None
    confidence: float
    reason: str
    normalized_query: str


def _extract_text(content: Any) -> str:
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


def _parse_json_from_text(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        chunk = text[first : last + 1]
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _skill_catalog_prompt(skills: list[SkillCard]) -> str:
    lines = []
    for item in skills:
        lines.append(f"- name: {item['name']}\n  description: {item['description']}")
    return "\n".join(lines)


def _build_router_prompt(user_message: str, skills: list[SkillCard]) -> str:
    return (
        "你是一个 Skill 路由器，只负责判断用户问题最适合哪个 skill。\n"
        "要求：\n"
        "1) 仅可从候选 skill 中选择一个；若不确定则返回 null。\n"
        "2) 不要臆造参数，不要执行工具，不要回答业务内容。\n"
        "3) 仅输出 JSON 对象，不要输出其他文字。\n"
        "JSON 格式：\n"
        '{\n'
        '  "selected_skill": string | null,\n'
        '  "confidence": number,\n'
        '  "reason": string,\n'
        '  "normalized_query": string\n'
        '}\n'
        f"候选 skills:\n{_skill_catalog_prompt(skills)}\n\n"
        f"用户问题：{user_message}"
    )


@lru_cache(maxsize=1)
def _get_router_model() -> ChatOpenAI:
    settings = get_settings()
    model_name = settings.intent_router_model or settings.model_name
    return ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=model_name,
        temperature=0.0,
    )


def _get_loaded_skills() -> list[SkillCard]:
    settings = get_settings()
    return list_skills(settings.project_root, settings.skill_sources)


def _normalize_decision(parsed: dict[str, Any], user_message: str) -> SkillRouteDecision | None:
    skill = parsed.get("selected_skill")
    selected_skill = str(skill).strip() if isinstance(skill, str) else None
    if selected_skill == "":
        selected_skill = None

    confidence_raw = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    reason = str(parsed.get("reason", "")).strip()
    normalized_query = str(parsed.get("normalized_query", "")).strip() or user_message

    return SkillRouteDecision(
        selected_skill=selected_skill,
        confidence=confidence,
        reason=reason,
        normalized_query=normalized_query,
    )


def route_with_skill_intent(user_message: str) -> tuple[str | None, str | None]:
    """
    返回 (增强后的消息, 立即返回文本)。
    - 未命中 skill：返回 (None, None)
    - 命中且置信度足够：返回 (augmented_message, None)
    """
    settings = get_settings()
    if not settings.intent_router_enabled:
        return None, None

    skills = _get_loaded_skills()
    if not skills:
        return None, None

    prompt = _build_router_prompt(user_message, skills)
    try:
        response = _get_router_model().invoke(prompt)
    except Exception:
        return None, None

    parsed = _parse_json_from_text(_extract_text(getattr(response, "content", "")))
    if not parsed:
        return None, None

    decision = _normalize_decision(parsed, user_message)
    if decision is None or decision.selected_skill is None:
        return None, None

    available_names = {item["name"] for item in skills}
    if decision.selected_skill not in available_names:
        return None, None

    if decision.confidence < settings.intent_router_threshold:
        return None, None

    hint_payload = {
        "selected_skill": decision.selected_skill,
        "confidence": decision.confidence,
        "reason": decision.reason,
    }
    augmented = (
        "[SKILL_ROUTER_HINT]\n"
        f"{json.dumps(hint_payload, ensure_ascii=False)}\n"
        "[/SKILL_ROUTER_HINT]\n"
        "优先依据该 skill 的 SKILL.md 执行；若信息不足先追问用户，不要臆造参数。\n"
        f"用户原始问题：{decision.normalized_query}"
    )
    return augmented, None

