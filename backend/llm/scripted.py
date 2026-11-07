"""脚本化的假模型：按顺序返回预先写好的回复，用于单元测试，不访问网络。"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from backend.llm.base import DeltaHandler, LLMReply, LLMToolCall

# 一步可以是固定回复，也可以是根据当前消息动态生成回复的函数
Step = LLMReply | Callable[[list[dict[str, Any]]], LLMReply]


def call(name: str, call_id: str | None = None, **arguments: Any) -> LLMToolCall:
    raw = json.dumps(arguments, ensure_ascii=False)
    return LLMToolCall(call_id or f"call_{name}_{abs(hash(raw)) % 10_000}", name, arguments, raw)


def reply(content: str = "", *calls: LLMToolCall) -> LLMReply:
    return LLMReply(content, tuple(calls))


class ScriptedModel:
    name = "scripted"

    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[dict[str, Any]] = []  # 每次请求的消息和工具，供测试检查

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaHandler | None = None,
    ) -> LLMReply:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools or []})
        if not self._steps:
            raise AssertionError("ScriptedModel 的脚本已经用完")
        step = self._steps.pop(0)
        result = step(messages) if callable(step) else step
        if on_delta and result.content:
            await on_delta(result.content)
        return result
