"""LLM 语义意图路由：根据用户问题动态选择 skill。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class SkillRouteResult:
    """skill 路由返回值，附带调试追踪信息。"""

    augmented_message: str | None
    immediate: str | None
    trace: dict[str, Any]


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
        block = [f"- name: {item['name']}", f"  description: {item['description']}"]
        triggers = [str(trigger).strip() for trigger in item.get("triggers", []) if str(trigger).strip()]
        if triggers:
            block.append(f"  triggers: {', '.join(triggers[:12])}")
        required_slots = [str(slot).strip() for slot in item.get("required_slots", []) if str(slot).strip()]
        if required_slots:
            block.append(f"  required_slots: {', '.join(required_slots[:8])}")
        lines.append("\n".join(block))
    return "\n".join(lines)


def _serialize_skill_cards(skills: list[SkillCard]) -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "description": item["description"],
            "triggers": item.get("triggers", []),
            "required_slots": item.get("required_slots", []),
            "output_contract": item.get("output_contract", ""),
            "path": item["path"],
            "source": item["source"],
        }
        for item in skills
    ]


def _find_skill_card(skills: list[SkillCard], skill_name: str | None) -> SkillCard | None:
    if not skill_name:
        return None
    return next((item for item in skills if item["name"] == skill_name), None)


def _build_skill_execution_guidance(selected_skill_name: str | None, skills: list[SkillCard]) -> str:
    lines = ["优先依据该 skill 的 SKILL.md 执行。"]
    selected = _find_skill_card(skills, selected_skill_name)
    if selected is None:
        lines.append("先尝试从同一会话上下文补全缺失参数，仍不足时再追问用户，不要臆造参数。")
        return "\n".join(lines)

    required_slots = [str(slot).strip() for slot in selected.get("required_slots", []) if str(slot).strip()]
    if required_slots:
        lines.append(
            f"必需输入槽位: {', '.join(required_slots)}。先尝试从同一会话上下文补全；仍缺失时再追问用户，不要臆造参数。"
        )
    else:
        lines.append("先尝试从同一会话上下文补全缺失参数，仍不足时再追问用户，不要臆造参数。")

    output_contract = str(selected.get("output_contract", "")).strip()
    if output_contract:
        lines.append(f"输出结果需满足: {output_contract}")

    allowed_tools = [str(tool).strip() for tool in selected.get("allowed_tools", []) if str(tool).strip()]
    if allowed_tools:
        lines.append(f"优先使用该 skill 声明的工具: {', '.join(allowed_tools)}。")

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


def _build_router_prompt_with_preference(
    user_message: str,
    skills: list[SkillCard],
    *,
    preferred_skill_name: str | None,
) -> str:
    preferred_text = ""
    if preferred_skill_name:
        preferred_text = (
            f"当前会话正在进行的 skill: {preferred_skill_name}\n"
            "如果用户看起来是在继续上一个任务、补充参数、确认细节或要求继续执行，优先沿用该 skill；"
            "只有在当前问题明显切换任务时才选择其他 skill 或 null。\n"
        )
    return preferred_text + _build_router_prompt(user_message, skills)


def _is_context_only_followup(user_message: str) -> bool:
    """识别仅需基于会话上下文处理的跟进请求，避免误触发 skill 路由。"""
    text = user_message.strip().lower()
    if not text:
        return False

    summary_keywords = (
        "总结",
        "概括",
        "归纳",
        "提炼",
        "简述",
        "复述",
        "重述",
        "润色",
        "改写",
        "翻译",
        "解释",
        "说明",
        "再说一遍",
        "再解释",
        "summarize",
        "summary",
        "recap",
        "rephrase",
        "rewrite",
        "translate",
        "explain",
    )
    context_ref_keywords = (
        "上一轮",
        "上轮",
        "上一条",
        "上条",
        "刚才",
        "之前",
        "前面",
        "上一个回答",
        "上次回答",
        "你的回答",
        "你刚才",
        "last response",
        "previous response",
        "your answer",
    )

    has_summary_intent = any(key in text for key in summary_keywords)
    has_context_ref = any(key in text for key in context_ref_keywords)
    return has_summary_intent and has_context_ref


def _looks_like_followup_message(user_message: str) -> bool:
    text = user_message.strip()
    if not text:
        return False

    followup_prefixes = (
        "继续",
        "然后",
        "就按这个",
        "按这个",
        "用这个",
        "是这个",
        "改成",
        "换成",
        "补充",
        "还有",
        "那就",
        "这个表",
        "这个字段",
        "this",
        "use this",
        "continue",
        "go on",
        "change to",
    )
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in followup_prefixes):
        return True

    if text.count("\n") <= 2 and any(token in text for token in ("{", "}", "[", "]", "${", ".", "_", "=", ":")):
        return True

    if len(text) <= 24 and len(text.split()) <= 4 and any(char.isdigit() for char in text):
        return True

    return False


@lru_cache(maxsize=8)
def _get_router_model(model_name: str) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=model_name,
        temperature=0.0,
    )


def _get_loaded_skills(allowed_skill_names: tuple[str, ...] | None = None) -> list[SkillCard]:
    settings = get_settings()
    loaded = list_skills(settings.project_root, settings.skill_sources)
    if allowed_skill_names is None:
        return loaded
    allowed = set(allowed_skill_names)
    return [item for item in loaded if item["name"] in allowed]


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


def route_with_skill_intent(
    user_message: str,
    *,
    model_name: str | None = None,
    allowed_skill_names: tuple[str, ...] | None = None,
    preferred_skill_name: str | None = None,
) -> SkillRouteResult:
    """
    返回 skill 路由结果，包含增强后的消息与调试 trace。
    """
    settings = get_settings()
    resolved_model_name = (settings.intent_router_model or model_name or settings.model_name).strip()
    trace: dict[str, Any] = {
        "user_message": user_message,
        "model_name": resolved_model_name,
        "allowed_skill_names": list(allowed_skill_names or ()),
        "preferred_skill_name": preferred_skill_name,
        "threshold": settings.intent_router_threshold,
    }
    if not settings.intent_router_enabled:
        return SkillRouteResult(None, None, {**trace, "status": "skipped", "reason": "intent_router_disabled"})
    if _is_context_only_followup(user_message):
        return SkillRouteResult(None, None, {**trace, "status": "skipped", "reason": "context_only_followup"})

    skills = _get_loaded_skills(allowed_skill_names)
    trace["available_skills"] = _serialize_skill_cards(skills)
    if not skills:
        return SkillRouteResult(None, None, {**trace, "status": "skipped", "reason": "no_skills_available"})

    preferred_skill = preferred_skill_name if preferred_skill_name in {item["name"] for item in skills} else None
    prompt = _build_router_prompt_with_preference(
        user_message,
        skills,
        preferred_skill_name=preferred_skill,
    )
    trace["prompt"] = prompt
    try:
        response = _get_router_model(resolved_model_name).invoke(prompt)
    except Exception as exc:
        return SkillRouteResult(None, None, {**trace, "status": "error", "error": str(exc)})

    response_text = _extract_text(getattr(response, "content", ""))
    trace["response_text"] = response_text

    parsed = _parse_json_from_text(response_text)
    trace["parsed_response"] = parsed
    if not parsed:
        if preferred_skill and _looks_like_followup_message(user_message):
            guidance = _build_skill_execution_guidance(preferred_skill, skills)
            return SkillRouteResult(
                (
                    "[SKILL_ROUTER_HINT]\n"
                    f'{json.dumps({"selected_skill": preferred_skill, "confidence": 0.99, "reason": "继续沿用当前会话 active skill"}, ensure_ascii=False)}\n'
                    "[/SKILL_ROUTER_HINT]\n"
                    f"{guidance}\n"
                    f"用户原始问题：{user_message}"
                ),
                None,
                {**trace, "status": "selected", "reason": "sticky_active_skill_fallback", "selected_skill": preferred_skill},
            )
        return SkillRouteResult(None, None, {**trace, "status": "no_match", "reason": "response_not_json"})

    decision = _normalize_decision(parsed, user_message)
    trace["decision"] = asdict(decision) if decision is not None else None
    if decision is None or decision.selected_skill is None:
        if preferred_skill and _looks_like_followup_message(user_message):
            decision = SkillRouteDecision(
                selected_skill=preferred_skill,
                confidence=0.99,
                reason="继续沿用当前会话 active skill",
                normalized_query=user_message,
            )
        else:
            return SkillRouteResult(None, None, {**trace, "status": "no_match", "reason": "selected_skill_empty"})

    available_names = {item["name"] for item in skills}
    if decision.selected_skill not in available_names:
        return SkillRouteResult(
            None,
            None,
            {**trace, "status": "no_match", "reason": "selected_skill_not_available"},
        )

    if decision.confidence < settings.intent_router_threshold:
        if preferred_skill and preferred_skill == decision.selected_skill and _looks_like_followup_message(user_message):
            decision = SkillRouteDecision(
                selected_skill=preferred_skill,
                confidence=max(decision.confidence, 0.99),
                reason=decision.reason or "继续沿用当前会话 active skill",
                normalized_query=decision.normalized_query,
            )
        else:
            return SkillRouteResult(
                None,
                None,
                {**trace, "status": "no_match", "reason": "confidence_below_threshold"},
            )

    hint_payload = {
        "selected_skill": decision.selected_skill,
        "confidence": decision.confidence,
        "reason": decision.reason,
    }
    guidance = _build_skill_execution_guidance(decision.selected_skill, skills)
    augmented = (
        "[SKILL_ROUTER_HINT]\n"
        f"{json.dumps(hint_payload, ensure_ascii=False)}\n"
        "[/SKILL_ROUTER_HINT]\n"
        f"{guidance}\n"
        f"用户原始问题：{decision.normalized_query}"
    )
    return SkillRouteResult(
        augmented,
        None,
        {
            **trace,
            "status": "selected",
            "selected_skill": decision.selected_skill,
            "confidence": decision.confidence,
        },
    )
