"""工具集合：供 deep agent 在推理中调用。"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from langchain.tools import tool

from app.config import get_settings
from app.sandbox import get_current_session_sandbox, _normalize_package_specs


def _read_env_text(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _read_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


FIELD_LINEAGE_ENDPOINT = _read_env_text(
    "FIELD_LINEAGE_ENDPOINT",
    "http://123.207.206.62:39000/api/graph/field-lineage-analysis",
)
FIELD_LINEAGE_STOP_MESSAGE = "目标字段相关血缘已完成查询"
DEFAULT_FIELD_LINEAGE_MAX_ROUNDS = 20
FIELD_LINEAGE_TIMEOUT_SECONDS = _read_env_float("FIELD_LINEAGE_TIMEOUT_SECONDS", 20.0)


def _post_json(url: str, payload: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    """发送 JSON POST 请求并返回解析后的 JSON。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            raw_body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "error": f"HTTP {exc.code}",
            "details": detail,
        }
    except error.URLError as exc:
        return {
            "ok": False,
            "error": "NetworkError",
            "details": str(exc.reason),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "RequestError",
            "details": str(exc),
        }

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": "InvalidJSON",
            "details": f"HTTP {status_code}, body={raw_body}, parse_error={exc}",
        }

    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": "InvalidResponseType",
            "details": f"HTTP {status_code}, expected object JSON but got {type(parsed).__name__}",
        }

    parsed["ok"] = True
    parsed["_status_code"] = status_code
    return parsed


def _normalize_insert_rank(value: Any, default: int = 1) -> int:
    """将 insert_rank 归一化为整数，非法值回退到 default。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _print_lineage_log(tag: str, text: str) -> None:
    """同步输出血缘工具执行日志，便于在终端观察实时进度。"""
    print(f"[lineage:{tag}] {text}", flush=True)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(key in text for key in keywords)


def _decide_drill_down(
    *,
    logic_summary: str,
    next_name: str,
    next_rank: str,
    current_name: str,
    current_rank: str,
) -> tuple[bool, str]:
    """
    基于单条 logic_summary 决策该分支是否继续下钻。
    规则优先级：
    1) 命中“常量/无上游”类信号 -> 不下钻
    2) 命中“存在上游来源”类信号 -> 下钻
    3) 同表不同 insert_rank 视为多次插入分支 -> 下钻
    4) 其他不明确场景 -> 默认不下钻
    """
    text = logic_summary.strip().lower()
    if not next_name:
        return False, "缺少上游实体 name，无法继续下钻。"

    stop_keywords = (
        "硬编码常量",
        "常量",
        "直接赋值生成",
        "字面量",
        "固定值",
        "无上游",
        "未从其他上游字段",
        "无目标字段相关血缘",
        "没有上游",
        "not from any upstream",
    )
    drill_keywords = (
        "来源于上游表",
        "直接来源于上游表",
        "来自上游表",
        "上游表为",
        "直接上游表",
        "from upstream",
        "upstream table",
        "join",
    )

    if _contains_any(text, stop_keywords):
        return False, "logic_summary 显示该分支为常量赋值或无可追溯上游。"

    if _contains_any(text, drill_keywords):
        return True, "logic_summary 显示存在上游表来源，可继续下钻。"

    if next_name == current_name and next_rank != current_rank:
        return True, "同表不同 insert_rank，视为另一条插入分支，继续下钻。"

    return False, "logic_summary 未提供明确可继续下钻的上游线索。"


@tool
def get_weather(location: str) -> str:
    """查询天气信息（示例桩工具）。"""
    return f"Weather lookup result for {location}: sunny, 22C."


@tool
def search_knowledge_base(query: str) -> str:
    """检索内部知识库（示例桩工具）。"""
    return f"Knowledge search result for '{query}': no indexed documents yet."


@tool
def ensure_python_packages(packages: str) -> str:
    """
    在当前会话 sandbox 的 .venv 中安装 Python 依赖。

    packages 支持两种格式：
    1) JSON 数组字符串，例如 ["pandas", "requests==2.32.3"]
    2) 每行一个 requirement 的纯文本
    """
    settings = get_settings()
    sandbox = get_current_session_sandbox()
    try:
        package_specs = _normalize_package_specs(packages)
    except ValueError as exc:
        return json.dumps(
            {
                "ok": False,
                "operation": "pip_install",
                "error": "InvalidPackageSpec",
                "details": str(exc),
            },
            ensure_ascii=False,
        )

    result = sandbox.ensure_packages(
        package_specs=package_specs,
        timeout_seconds=settings.sandbox_install_timeout_seconds,
        output_char_limit=settings.sandbox_output_char_limit,
    )
    return json.dumps(result, ensure_ascii=False)


@tool
def run_python_code(code: str) -> str:
    """
    在当前会话 sandbox 的独立 .venv 中执行 Python 代码。

    代码运行目录固定为 sandbox/workspace。需要输出结果时请显式使用 print(...)。
    如有第三方依赖，先调用 ensure_python_packages。
    """
    settings = get_settings()
    sandbox = get_current_session_sandbox()
    result = sandbox.run_python_code(
        code=code,
        timeout_seconds=settings.sandbox_command_timeout_seconds,
        output_char_limit=settings.sandbox_output_char_limit,
    )
    return json.dumps(result, ensure_ascii=False)


@tool
def query_field_lineage_step(name: str, target_col: str, insert_rank: int = 1) -> str:
    """调用字段血缘接口，返回单轮结果（message 字段使用 logic_summary）。"""
    rank = _normalize_insert_rank(insert_rank, default=1)
    _print_lineage_log("call", f"name={name}, target_col={target_col}, insert_rank={rank}")
    payload = {
        "name": name,
        "target_col": target_col,
        "insert_rank": str(rank),
    }
    response = _post_json(FIELD_LINEAGE_ENDPOINT, payload, timeout=FIELD_LINEAGE_TIMEOUT_SECONDS)
    if not response.get("ok"):
        _print_lineage_log(
            "error",
            f"name={name}, target_col={target_col}, insert_rank={rank}, error={response.get('error')}, details={response.get('details')}",
        )
        return json.dumps(response, ensure_ascii=False)

    business_entities = response.get("business_entities")
    full_lineage_paths = response.get("full_lineage_paths")
    count = response.get("count")

    if not isinstance(business_entities, list):
        business_entities = []
    if not isinstance(full_lineage_paths, list):
        full_lineage_paths = []
    if not isinstance(count, int):
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = len(business_entities)

    logic_summaries: list[str] = []
    for entity in business_entities:
        if not isinstance(entity, dict):
            continue
        summary = str(entity.get("logic_summary", "")).strip()
        if summary:
            logic_summaries.append(summary)
            _print_lineage_log("logic_summary", summary)

    if not logic_summaries:
        _print_lineage_log("logic_summary", "(empty)")

    message = "\n".join(logic_summaries)
    should_continue = bool(count > 0 and business_entities)

    result = {
        "ok": True,
        "name": str(response.get("name", name)),
        "target_col": str(response.get("target_col", target_col)),
        "insert_rank": str(response.get("insert_rank", str(rank))),
        "business_entities": business_entities,
        "count": count,
        "full_lineage_paths": full_lineage_paths,
        "logic_summaries": logic_summaries,
        "message": message,
        "should_continue": should_continue,
        "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
    }
    return json.dumps(result, ensure_ascii=False)


@tool
def query_field_lineage_until_stop(
    name: str,
    target_col: str,
    insert_rank: int = 1,
    max_rounds: int = DEFAULT_FIELD_LINEAGE_MAX_ROUNDS,
) -> str:
    """按返回的 business_entities 迭代查询，直到 count=0 或达到最大轮次。"""
    rounds = max(1, min(max_rounds, 100))
    messages: list[str] = []
    round_records: list[dict[str, Any]] = []
    visited: set[tuple[str, str, str]] = set()
    visited_table_set: set[str] = set()
    visited_tables: list[str] = []
    related_table_set: set[str] = set()
    related_tables: list[str] = []
    initial_rank = _normalize_insert_rank(insert_rank, default=1)
    pending: list[dict[str, str]] = [{"name": name, "target_col": target_col, "insert_rank": str(initial_rank)}]
    round_count = 0

    while pending and round_count < rounds:
        current = pending.pop(0)
        visit_key = (current["name"], current["target_col"], current["insert_rank"])
        if visit_key in visited:
            continue
        visited.add(visit_key)
        round_count += 1
        if current["name"] not in visited_table_set:
            visited_table_set.add(current["name"])
            visited_tables.append(current["name"])
        if current["name"] not in related_table_set:
            related_table_set.add(current["name"])
            related_tables.append(current["name"])
        _print_lineage_log(
            "round",
            f"round={round_count}, name={current['name']}, target_col={current['target_col']}, insert_rank={current['insert_rank']}",
        )

        raw = query_field_lineage_step.invoke(
            {
                "name": current["name"],
                "target_col": current["target_col"],
                "insert_rank": _normalize_insert_rank(current["insert_rank"], default=1),
            }
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps(
                {
                    "ok": False,
                    "error": "ToolParseError",
                    "details": raw,
                },
                ensure_ascii=False,
            )

        if not parsed.get("ok"):
            return json.dumps(parsed, ensure_ascii=False)

        logic_summaries = parsed.get("logic_summaries")
        collected_summaries: list[str] = []
        if isinstance(logic_summaries, list):
            for item in logic_summaries:
                text = str(item).strip()
                if text:
                    messages.append(text)
                    collected_summaries.append(text)

        current_count = parsed.get("count")
        try:
            current_count = int(current_count)
        except (TypeError, ValueError):
            current_count = 0

        business_entities = parsed.get("business_entities")
        entity_decisions: list[dict[str, Any]] = []
        enqueued = 0
        if isinstance(business_entities, list):
            for entity in business_entities:
                if not isinstance(entity, dict):
                    continue
                next_name = str(entity.get("name", "")).strip()
                next_rank = str(entity.get("insert_rank", "")).strip() or "1"
                logic_summary = str(entity.get("logic_summary", "")).strip()
                should_drill_down, reason = _decide_drill_down(
                    logic_summary=logic_summary,
                    next_name=next_name,
                    next_rank=next_rank,
                    current_name=current["name"],
                    current_rank=current["insert_rank"],
                )

                decision = {
                    "name": next_name,
                    "insert_rank": next_rank,
                    "logic_summary": logic_summary,
                    "should_drill_down": should_drill_down,
                    "reason": reason,
                }
                entity_decisions.append(decision)
                if next_name and next_name not in related_table_set:
                    related_table_set.add(next_name)
                    related_tables.append(next_name)
                _print_lineage_log(
                    "decision",
                    (
                        f"name={next_name or '(empty)'}, insert_rank={next_rank}, "
                        f"should_drill_down={should_drill_down}, reason={reason}"
                    ),
                )

                if not next_name:
                    continue
                if should_drill_down:
                    pending.append(
                        {
                            "name": next_name,
                            "target_col": current["target_col"],
                            "insert_rank": next_rank,
                        }
                    )
                    enqueued += 1

        round_records.append(
            {
                "round": round_count,
                "query": {
                    "name": current["name"],
                    "target_col": current["target_col"],
                    "insert_rank": current["insert_rank"],
                },
                "count": current_count,
                "logic_summaries": collected_summaries,
                "entity_decisions": entity_decisions,
                "enqueued_next_queries": enqueued,
            }
        )

    stopped = not pending
    remaining_queries = len(pending)
    _print_lineage_log(
        "summary",
        (
            f"rounds={round_count}, logic_summary_count={len(messages)}, "
            f"stopped={stopped}, remaining_queries={remaining_queries}, "
            f"visited_tables={visited_tables}, related_tables={related_tables}"
        ),
    )

    return json.dumps(
        {
            "ok": True,
            "name": name,
            "target_col": target_col,
            "insert_rank": str(initial_rank),
            "messages": messages,
            "logic_summary_count": len(messages),
            "rounds": round_count,
            "stopped": stopped,
            "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
            "details": "" if stopped else "Reached max_rounds before lineage traversal completed.",
            "remaining_queries": remaining_queries,
            "visited_tables": visited_tables,
            "related_tables": related_tables,
            "round_records": round_records,
            "agent_summary_required": True,
            "agent_summary_hint": "请基于 messages、round_records、related_tables、rounds、stopped 输出最终血缘追踪总结。",
        },
        ensure_ascii=False,
    )
