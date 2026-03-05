"""Agent 构建与消息调用模块（同步、异步与流式输出）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.intent_router import route_with_skill_intent
from app.skill_catalog import list_skills
from app.tools import (
    get_weather,
    query_field_lineage_step,
    query_field_lineage_until_stop,
    search_knowledge_base,
)


def _extract_text(message: BaseMessage, *, strip: bool = True) -> str:
    """从 LangChain 消息对象中抽取可显示文本。"""
    content = message.content
    if isinstance(content, str):
        return content.strip() if strip else content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        merged = "".join(text_parts)
        return merged.strip() if strip else merged
    return ""


def _read_field(obj: Any, key: str) -> Any:
    """兼容 dict/对象 两种结构读取字段。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_tool_calls(message: BaseMessage) -> list[dict[str, Any]]:
    """从消息中提取 tool call 与 tool call chunk。"""
    extracted: list[dict[str, Any]] = []

    raw_tool_calls = getattr(message, "tool_calls", None)
    if isinstance(raw_tool_calls, list):
        for call in raw_tool_calls:
            extracted.append(
                {
                    "kind": "tool_call",
                    "id": _read_field(call, "id"),
                    "name": _read_field(call, "name"),
                    "args": _read_field(call, "args"),
                }
            )

    raw_tool_call_chunks = getattr(message, "tool_call_chunks", None)
    if isinstance(raw_tool_call_chunks, list):
        for chunk in raw_tool_call_chunks:
            extracted.append(
                {
                    "kind": "tool_call_chunk",
                    "id": _read_field(chunk, "id"),
                    "name": _read_field(chunk, "name"),
                    "args": _read_field(chunk, "args"),
                    "index": _read_field(chunk, "index"),
                }
            )

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                extracted.append(
                    {
                        "kind": "raw_tool_call",
                        "id": raw.get("id"),
                        "name": function.get("name"),
                        "args": function.get("arguments"),
                    }
                )

    return extracted


def _debug_print_tool_calls(message: BaseMessage, metadata: dict[str, Any] | None = None) -> None:
    """临时调试输出：打印字段血缘工具调用信息。"""
    tool_calls = _extract_tool_calls(message)
    if not tool_calls:
        return

    lineage_tools = {"query_field_lineage_step", "query_field_lineage_until_stop"}
    hit_lineage_tool = any(item.get("name") in lineage_tools for item in tool_calls)
    if not hit_lineage_tool:
        return

    lineage_calls = [item for item in tool_calls if item.get("name") in lineage_tools]
    payload = json.dumps(lineage_calls, ensure_ascii=False, default=str)
    node = (metadata or {}).get("langgraph_node", "unknown")
    print(f"\n[debug:lineage_tool_call:node={node}] {payload}", flush=True)


@lru_cache(maxsize=1)
def get_agent():
    """构建并缓存 deep agent，避免每轮对话重复初始化。"""
    settings = get_settings()
    model = ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.model_name,
        temperature=settings.temperature,
    )
    # 使用 virtual_mode 让 "/skills/..."、"/memory/..." 这类路径相对 project_root 解析。
    backend = FilesystemBackend(root_dir=settings.project_root, virtual_mode=True)

    loaded_skills = list_skills(settings.project_root, settings.skill_sources)
    if loaded_skills:
        print("[startup:skills] 已注入的 skill 元数据：")
        for item in loaded_skills:
            print(
                f"- name={item['name']} | source={item['source']} | path={item['path']} | description={item['description']}"
            )
    else:
        print("[startup:skills] 未发现可注入的 skill 元数据。")

    return create_deep_agent(
        model=model,
        tools=[
            get_weather,
            search_knowledge_base,
            query_field_lineage_step,
            query_field_lineage_until_stop,
        ],
        system_prompt=settings.system_prompt,
        skills=list(settings.skill_sources),
        memory=list(settings.memory_sources),
        backend=backend,
        checkpointer=InMemorySaver(),
        name="deepagent-skills-backend",
    )


async def chat_once(message: str, thread_id: str) -> str:
    """异步单次调用：返回该轮对话的最终文本。"""
    routed_message, immediate = route_with_skill_intent(message)
    if immediate is not None:
        return immediate

    agent = get_agent()
    payload = {"messages": [{"role": "user", "content": routed_message or message}]}
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke(payload, config=config)

    messages = result.get("messages", [])
    for candidate in reversed(messages):
        if isinstance(candidate, AIMessage):
            text = _extract_text(candidate)
            if text:
                return text

    if messages:
        last = messages[-1]
        if isinstance(last, BaseMessage):
            fallback_text = _extract_text(last)
            if fallback_text:
                return fallback_text

    return ""


def chat_once_sync(message: str, thread_id: str) -> str:
    """同步单次调用：返回该轮对话的最终文本。"""
    routed_message, immediate = route_with_skill_intent(message)
    if immediate is not None:
        return immediate

    agent = get_agent()
    payload = {"messages": [{"role": "user", "content": routed_message or message}]}
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke(payload, config=config)

    messages = result.get("messages", [])
    for candidate in reversed(messages):
        if isinstance(candidate, AIMessage):
            text = _extract_text(candidate)
            if text:
                return text

    if messages:
        last = messages[-1]
        if isinstance(last, BaseMessage):
            fallback_text = _extract_text(last)
            if fallback_text:
                return fallback_text

    return ""


def stream_chat_sync(message: str, thread_id: str) -> Iterator[str]:
    """同步流式调用：逐块产出模型文本，供终端实时打印。"""
    routed_message, immediate = route_with_skill_intent(message)
    if immediate is not None:
        yield immediate
        return

    agent = get_agent()
    payload = {"messages": [{"role": "user", "content": routed_message or message}]}
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    seen_chunk_in_round = False
    for emitted, metadata in agent.stream(payload, config=config, stream_mode="messages"):
        if isinstance(emitted, (AIMessageChunk, AIMessage)):
            _debug_print_tool_calls(emitted, metadata)

        if metadata.get("langgraph_node") != "model":
            continue

        if isinstance(emitted, AIMessageChunk):
            text = _extract_text(emitted, strip=False)
            if text:
                seen_chunk_in_round = True
                yield text
            continue

        if isinstance(emitted, AIMessage):
            # 某些模型可能直接返回完整消息；若未接收到 chunk 则兜底输出。
            text = _extract_text(emitted, strip=False)
            if text and not seen_chunk_in_round:
                yield text
            seen_chunk_in_round = False
