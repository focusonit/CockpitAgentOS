"""LLM 抽象层：规划器只依赖这里的接口，不关心背后是 DeepSeek 还是脚本化的假模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

# 流式输出时，每收到一段回复文字就回调一次
DeltaHandler = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # 模型给出的原始参数文本；JSON 解析失败时 arguments 为空、这里保留原文，便于把错误回给模型
    raw: str = ""
    parse_error: str = ""


@dataclass(frozen=True)
class LLMReply:
    content: str = ""
    tool_calls: tuple[LLMToolCall, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)

    def assistant_message(self) -> dict[str, Any]:
        """转成 OpenAI 格式的 assistant 消息，放回对话里进行下一轮。"""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw or "{}"}}
                for c in self.tool_calls
            ]
        return msg


class LLMError(RuntimeError):
    """调用模型失败（网络、鉴权、限流等）。"""


class ChatModel(Protocol):
    name: str

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaHandler | None = None,
    ) -> LLMReply: ...
