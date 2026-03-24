"""将运行时对象转换为可 JSON 序列化结构。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    return {
        "type": message.type,
        "name": getattr(message, "name", None),
        "id": getattr(message, "id", None),
        "content": make_json_safe(message.content),
        "additional_kwargs": make_json_safe(getattr(message, "additional_kwargs", {})),
        "response_metadata": make_json_safe(getattr(message, "response_metadata", {})),
        "tool_calls": make_json_safe(getattr(message, "tool_calls", [])),
        "tool_call_id": getattr(message, "tool_call_id", None),
        "status": getattr(message, "status", None),
    }


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, BaseMessage):
        return _serialize_message(value)

    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]

    if is_dataclass(value):
        return make_json_safe(asdict(value))

    if hasattr(value, "model_dump"):
        try:
            return make_json_safe(value.model_dump())
        except Exception:  # noqa: BLE001
            pass

    if hasattr(value, "dict"):
        try:
            return make_json_safe(value.dict())
        except Exception:  # noqa: BLE001
            pass

    if hasattr(value, "__dict__"):
        try:
            data = {key: item for key, item in vars(value).items() if not key.startswith("_")}
            if data:
                return make_json_safe(data)
        except Exception:  # noqa: BLE001
            pass

    return str(value)
