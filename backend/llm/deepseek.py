"""DeepSeek（OpenAI 兼容接口）。key 只从环境变量 DEEPSEEK_API_KEY 读取。"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import APIError, AsyncOpenAI

from backend.llm.base import DeltaHandler, LLMError, LLMReply, LLMToolCall

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_S = 30.0
# 两个模型默认都会先思考再作答；座舱场景对时延敏感，默认关掉（实测 flash 从约 2.2s 降到约 0.9s）
THINKING_ENV = "DEEPSEEK_THINKING"


def parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.strip():
        return {}, ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"参数不是合法 JSON：{exc.msg}"
    if not isinstance(value, dict):
        return {}, "参数必须是 JSON 对象"
    return value, ""


class DeepSeekModel:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        thinking: bool = False,
        temperature: float = 0.2,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.name = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=2)
        self._thinking = thinking
        self._temperature = temperature

    @classmethod
    def from_env(cls, model: str | None = None) -> "DeepSeekModel":
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise LLMError("没有配置 DEEPSEEK_API_KEY：把它写进项目根目录的 .env，或设置环境变量")
        return cls(
            key,
            model=model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL,
            base_url=os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
            thinking=os.environ.get(THINKING_ENV, "").lower() in ("1", "true", "on"),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaHandler | None = None,
    ) -> LLMReply:
        kwargs: dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": self._temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": {"thinking": {"type": "enabled" if self._thinking else "disabled"}},
        }
        if tools:
            kwargs["tools"] = tools
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            return await _collect(stream, on_delta)
        except APIError as exc:
            raise LLMError(f"DeepSeek 调用失败：{exc.__class__.__name__}: {exc.message}") from exc


async def _collect(stream: Any, on_delta: DeltaHandler | None) -> LLMReply:
    """把流式分片拼回完整回复：文字边到边回调，工具调用按 index 累积参数片段。"""
    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] = {}
    async for chunk in stream:
        if chunk.usage:
            usage = chunk.usage.model_dump(exclude_none=True)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            content.append(delta.content)
            if on_delta:
                await on_delta(delta.content)
        for part in delta.tool_calls or []:
            slot = calls.setdefault(part.index, {"id": "", "name": "", "args": ""})
            if part.id:
                slot["id"] = part.id
            if part.function and part.function.name:
                slot["name"] += part.function.name
            if part.function and part.function.arguments:
                slot["args"] += part.function.arguments
    tool_calls = []
    for index in sorted(calls):
        slot = calls[index]
        args, error = parse_arguments(slot["args"])
        tool_calls.append(LLMToolCall(slot["id"] or f"call_{index}", slot["name"], args, slot["args"], error))
    return LLMReply("".join(content), tuple(tool_calls), usage)
