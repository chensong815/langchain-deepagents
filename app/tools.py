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
FIELD_LINEAGE_STOP_MESSAGE = "该阶段无目标字段相关血缘"
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


@tool
def get_weather(location: str) -> str:
    """查询天气信息（示例桩工具）。"""
    return f"Weather lookup result for {location}: sunny, 22C."


@tool
def search_knowledge_base(query: str) -> str:
    """检索内部知识库（示例桩工具）。"""
    return f"Knowledge search result for '{query}': no indexed documents yet."


@tool
def query_field_lineage_step(name: str, target_col: str) -> str:
    """调用字段血缘接口，返回单轮结果，兼容新版返回结构。"""
    payload = {"name": name, "target_col": target_col}
    response = _post_json(FIELD_LINEAGE_ENDPOINT, payload, timeout=FIELD_LINEAGE_TIMEOUT_SECONDS)
    if not response.get("ok"):
        return json.dumps(response, ensure_ascii=False)

    message = str(response.get("message", "")).strip()
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
            count = len(full_lineage_paths)

    result = {
        "ok": True,
        "name": str(response.get("name", name)),
        "target_col": str(response.get("target_col", target_col)),
        "business_entities": business_entities,
        "count": count,
        "full_lineage_paths": full_lineage_paths,
        "message": message,
        "should_continue": message != FIELD_LINEAGE_STOP_MESSAGE,
        "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
    }
    return json.dumps(result, ensure_ascii=False)


@tool
def query_field_lineage_until_stop(name: str, target_col: str, max_rounds: int = DEFAULT_FIELD_LINEAGE_MAX_ROUNDS) -> str:
    """循环调用字段血缘接口并汇总每轮 message，直到出现停止消息或达到最大轮次。"""
    rounds = max(1, min(max_rounds, 100))
    messages: list[str] = []
    last_response: dict[str, Any] | None = None

    for _ in range(rounds):
        raw = query_field_lineage_step.invoke({"name": name, "target_col": target_col})
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

        message = str(parsed.get("message", "")).strip()
        messages.append(message)
        last_response = parsed

        if not parsed.get("should_continue", False):
            return json.dumps(
                {
                    "ok": True,
                    "name": name,
                    "target_col": target_col,
                    "messages": messages,
                    "rounds": len(messages),
                    "stopped": True,
                    "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
                },
                ensure_ascii=False,
            )

    return json.dumps(
        {
            "ok": True,
            "name": name,
            "target_col": target_col,
            "messages": messages,
            "rounds": len(messages),
            "stopped": False,
            "stop_message": FIELD_LINEAGE_STOP_MESSAGE,
            "details": "Reached max_rounds before stop message.",
            "last_should_continue": bool((last_response or {}).get("should_continue", False)),
        },
        ensure_ascii=False,
    )
