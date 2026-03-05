"""工具集合：供 deep agent 在推理中调用。"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from langchain.tools import tool


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


@tool
def get_weather(location: str) -> str:
    """查询天气信息（示例桩工具）。"""
    return f"Weather lookup result for {location}: sunny, 22C."


@tool
def search_knowledge_base(query: str) -> str:
    """检索内部知识库（示例桩工具）。"""
    return f"Knowledge search result for '{query}': no indexed documents yet."


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
    visited: set[tuple[str, str, str]] = set()
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
        if isinstance(logic_summaries, list):
            for item in logic_summaries:
                text = str(item).strip()
                if text:
                    messages.append(text)

        if not parsed.get("should_continue", False):
            continue

        business_entities = parsed.get("business_entities")
        if isinstance(business_entities, list):
            for entity in business_entities:
                if not isinstance(entity, dict):
                    continue
                next_name = str(entity.get("name", "")).strip()
                if not next_name:
                    continue
                next_rank = str(entity.get("insert_rank", "")).strip() or "1"
                pending.append(
                    {
                        "name": next_name,
                        "target_col": current["target_col"],
                        "insert_rank": next_rank,
                    }
                )

    stopped = not pending

    return json.dumps(
        {
            "ok": True,
            "name": name,
            "target_col": target_col,
            "insert_rank": str(initial_rank),
            "messages": messages,
            "rounds": round_count,
            "stopped": stopped,
            "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
            "details": "" if stopped else "Reached max_rounds before lineage traversal completed.",
            "remaining_queries": len(pending),
        },
        ensure_ascii=False,
    )
