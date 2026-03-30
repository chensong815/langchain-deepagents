"""工具集合：供 deep agent 在推理中调用。"""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.config import get_settings
from app.sandbox import get_current_session_sandbox


FIELD_LINEAGE_STOP_MESSAGE = "目标字段相关血缘已完成查询"
DEFAULT_FIELD_LINEAGE_MAX_ROUNDS = 20


class LineageFieldInput(BaseModel):
    """单个字段血缘查询入参。"""

    col_name: str = Field(description="字段名")
    col_desc: str = Field(default="", description="字段描述")


class QueryFieldLineageStepInput(BaseModel):
    """字段血缘单步查询工具入参。"""

    table_name: str = Field(description="目标表名，需保留 ${SCHEMA} 这类占位符")
    insert_rank: int = Field(default=1, description="插入顺序")
    fields: list[LineageFieldInput] = Field(min_length=1, description="需要联合分析的字段列表，至少一个")


class QueryFieldLineageUntilStopInput(QueryFieldLineageStepInput):
    """字段血缘自动下钻工具入参。"""

    max_rounds: int = Field(default=DEFAULT_FIELD_LINEAGE_MAX_ROUNDS, description="最大追踪轮次")


def _post_json(url: str, payload: dict[str, Any], timeout: float = 360.0) -> dict[str, Any]:
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


def _normalize_lineage_fields(fields: Any) -> list[dict[str, str]]:
    """将 fields 归一化为 [{"col_name": "...", "col_desc": "..."}]。"""
    if not isinstance(fields, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in fields:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item
        else:
            data = {
                "col_name": getattr(item, "col_name", ""),
                "col_desc": getattr(item, "col_desc", ""),
            }

        col_name = str(data.get("col_name", "")).strip()
        if not col_name:
            continue
        normalized.append(
            {
                "col_name": col_name,
                "col_desc": str(data.get("col_desc", "")).strip(),
            }
        )

    return normalized


def _normalize_from_tables(from_tables: Any) -> list[dict[str, Any]]:
    """将上游表列表归一化为标准结构。"""
    if not isinstance(from_tables, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in from_tables:
        if not isinstance(item, dict):
            continue
        from_table = str(item.get("from_table", "")).strip()
        if not from_table:
            continue
        normalized.append(
            {
                "from_table": from_table,
                "insert_rank": _normalize_insert_rank(item.get("insert_rank"), default=1),
            }
        )
    return normalized


def _normalize_target_entity(target_entity: Any, *, table_name: str, insert_rank: int) -> dict[str, Any]:
    """归一化 target_entity，保证 analysis/table_name/insert_rank 始终可用。"""
    if not isinstance(target_entity, dict):
        target_entity = {}

    normalized = dict(target_entity)
    normalized["analysis"] = str(normalized.get("analysis", "")).strip()
    normalized["table_name"] = str(normalized.get("table_name", table_name)).strip() or table_name
    normalized["insert_rank"] = _normalize_insert_rank(normalized.get("insert_rank"), default=insert_rank)
    return normalized


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fields_visit_key(fields: list[dict[str, str]]) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


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
def run_python_code(code: str) -> str:
    """
    在当前会话 sandbox 的独立工作目录中执行 Python 代码。

    代码运行目录固定为 sandbox/workspace。需要输出结果时请显式使用 print(...)。
    第三方依赖需要由部署容器预先提供。
    """
    settings = get_settings()
    sandbox = get_current_session_sandbox()
    result = sandbox.run_python_code(
        code=code,
        timeout_seconds=settings.sandbox_command_timeout_seconds,
        output_char_limit=settings.sandbox_output_char_limit,
    )
    return json.dumps(result, ensure_ascii=False)


@tool(args_schema=QueryFieldLineageStepInput)
def query_field_lineage_step(table_name: str, fields: list[dict[str, str]], insert_rank: int = 1) -> str:
    """调用字段血缘接口，返回单轮结果（message 字段使用 target_entity.analysis）。"""
    settings = get_settings()
    rank = _normalize_insert_rank(insert_rank, default=1)
    normalized_fields = _normalize_lineage_fields(fields)
    field_names = [item["col_name"] for item in normalized_fields]
    _print_lineage_log("call", f"table_name={table_name}, fields={field_names}, insert_rank={rank}")

    if not normalized_fields:
        return json.dumps(
            {
                "ok": False,
                "error": "InvalidFields",
                "details": "fields 至少需要一个合法字段，格式为 [{'col_name': '...', 'col_desc': '...'}]",
            },
            ensure_ascii=False,
        )

    payload = {
        "table_name": table_name,
        "insert_rank": rank,
        "fields": normalized_fields,
    }
    response = _post_json(
        settings.field_lineage_endpoint,
        payload,
        timeout=settings.field_lineage_timeout_seconds,
    )
    if not response.get("ok"):
        _print_lineage_log(
            "error",
            f"table_name={table_name}, fields={field_names}, insert_rank={rank}, error={response.get('error')}, details={response.get('details')}",
        )
        return json.dumps(response, ensure_ascii=False)

    response_fields = _normalize_lineage_fields(response.get("fields"))
    if not response_fields:
        response_fields = normalized_fields

    from_tables = _normalize_from_tables(response.get("from_tables"))
    field_count = _parse_int(response.get("field_count"), default=len(response_fields))
    from_count = _parse_int(response.get("from_count"), default=len(from_tables))
    resolved_table_name = str(response.get("table_name", table_name)).strip() or table_name
    target_entity = _normalize_target_entity(
        response.get("target_entity"),
        table_name=resolved_table_name,
        insert_rank=rank,
    )
    analysis = str(target_entity.get("analysis", "")).strip()
    if analysis:
        _print_lineage_log("analysis", analysis)
    else:
        _print_lineage_log("analysis", "(empty)")

    result = {
        "ok": True,
        "table_name": resolved_table_name,
        "insert_rank": _normalize_insert_rank(response.get("insert_rank"), default=rank),
        "fields": response_fields,
        "field_count": field_count,
        "from_tables": from_tables,
        "from_count": from_count,
        "target_entity": target_entity,
        "analysis": analysis,
        "message": analysis,
        "should_continue": bool(from_count > 0 and from_tables),
        "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
    }
    return json.dumps(result, ensure_ascii=False)


@tool(args_schema=QueryFieldLineageUntilStopInput)
def query_field_lineage_until_stop(
    table_name: str,
    fields: list[dict[str, str]],
    insert_rank: int = 1,
    max_rounds: int = DEFAULT_FIELD_LINEAGE_MAX_ROUNDS,
) -> str:
    """按返回的 from_tables 迭代查询，直到 from_count=0 或达到最大轮次。"""
    rounds = max(1, min(max_rounds, 100))
    messages: list[str] = []
    round_records: list[dict[str, Any]] = []
    normalized_fields = _normalize_lineage_fields(fields)
    if not normalized_fields:
        return json.dumps(
            {
                "ok": False,
                "error": "InvalidFields",
                "details": "fields 至少需要一个合法字段，格式为 [{'col_name': '...', 'col_desc': '...'}]",
            },
            ensure_ascii=False,
        )

    fields_key = _fields_visit_key(normalized_fields)
    visited: set[tuple[str, str, int]] = set()
    visited_table_set: set[str] = set()
    visited_tables: list[str] = []
    related_table_set: set[str] = set()
    related_tables: list[str] = []
    initial_rank = _normalize_insert_rank(insert_rank, default=1)
    pending: list[dict[str, Any]] = [
        {
            "table_name": table_name,
            "fields": normalized_fields,
            "insert_rank": initial_rank,
        }
    ]
    round_count = 0

    while pending and round_count < rounds:
        current = pending.pop(0)
        visit_key = (current["table_name"], fields_key, current["insert_rank"])
        if visit_key in visited:
            continue
        visited.add(visit_key)
        round_count += 1
        if current["table_name"] not in visited_table_set:
            visited_table_set.add(current["table_name"])
            visited_tables.append(current["table_name"])
        if current["table_name"] not in related_table_set:
            related_table_set.add(current["table_name"])
            related_tables.append(current["table_name"])
        _print_lineage_log(
            "round",
            (
                f"round={round_count}, table_name={current['table_name']}, "
                f"fields={[item['col_name'] for item in current['fields']]}, insert_rank={current['insert_rank']}"
            ),
        )

        raw = query_field_lineage_step.invoke(
            {
                "table_name": current["table_name"],
                "fields": current["fields"],
                "insert_rank": current["insert_rank"],
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

        analysis = str(parsed.get("analysis", "")).strip()
        collected_messages: list[str] = []
        if analysis:
            messages.append(analysis)
            collected_messages.append(analysis)

        current_count = _parse_int(parsed.get("from_count"), default=0)

        from_tables = parsed.get("from_tables")
        from_table_decisions: list[dict[str, Any]] = []
        enqueued = 0
        if isinstance(from_tables, list):
            for item in from_tables:
                if not isinstance(item, dict):
                    continue
                next_name = str(item.get("from_table", "")).strip()
                next_rank = _normalize_insert_rank(item.get("insert_rank"), default=1)
                should_drill_down = bool(next_name)
                reason = "from_tables 返回上游表，继续下钻。" if should_drill_down else "缺少上游表名，无法继续下钻。"

                decision = {
                    "from_table": next_name,
                    "insert_rank": next_rank,
                    "should_drill_down": should_drill_down,
                    "reason": reason,
                }
                from_table_decisions.append(decision)
                if next_name and next_name not in related_table_set:
                    related_table_set.add(next_name)
                    related_tables.append(next_name)
                _print_lineage_log(
                    "decision",
                    (
                        f"from_table={next_name or '(empty)'}, insert_rank={next_rank}, "
                        f"should_drill_down={should_drill_down}, reason={reason}"
                    ),
                )

                if not next_name:
                    continue
                if should_drill_down:
                    pending.append(
                        {
                            "table_name": next_name,
                            "fields": current["fields"],
                            "insert_rank": next_rank,
                        }
                    )
                    enqueued += 1

        round_records.append(
            {
                "round": round_count,
                "query": {
                    "table_name": current["table_name"],
                    "fields": current["fields"],
                    "insert_rank": current["insert_rank"],
                },
                "from_count": current_count,
                "messages": collected_messages,
                "from_table_decisions": from_table_decisions,
                "entity_decisions": from_table_decisions,
                "enqueued_next_queries": enqueued,
            }
        )

    stopped = not pending
    remaining_queries = len(pending)
    _print_lineage_log(
        "summary",
        (
            f"rounds={round_count}, analysis_count={len(messages)}, "
            f"stopped={stopped}, remaining_queries={remaining_queries}, "
            f"visited_tables={visited_tables}, related_tables={related_tables}"
        ),
    )

    return json.dumps(
        {
            "ok": True,
            "table_name": table_name,
            "fields": normalized_fields,
            "field_count": len(normalized_fields),
            "insert_rank": initial_rank,
            "messages": messages,
            "analysis_count": len(messages),
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
