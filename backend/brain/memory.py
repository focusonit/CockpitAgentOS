"""会话内记忆：偏好、挂起任务、待确认的调用、对话历史。只在一次会话里有效，不跨会话保存。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

MAX_HISTORY_TURNS = 8
MAX_PREFERENCES = 10
MAX_PENDING = 5


@dataclass(frozen=True)
class HeldCall:
    """需要用户确认才执行的调用。"""

    tool: str
    args: dict[str, Any]
    seat: str


@dataclass(frozen=True)
class Memory:
    preferences: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()  # 挂起任务栈，栈顶在最后
    held: tuple[HeldCall, ...] = ()
    # 每轮一组 OpenAI 格式的消息（user → assistant/tool … → assistant）
    turns: tuple[tuple[dict[str, Any], ...], ...] = ()
    # 最近一轮的结构化结果，供“第二家”“再高一点”这类指代
    last_data: dict[str, Any] = field(default_factory=dict)

    def remember(self, text: str) -> "Memory":
        text = text.strip()
        if not text or text in self.preferences:
            return self
        return replace(self, preferences=(*self.preferences, text)[-MAX_PREFERENCES:])

    def push_pending(self, task: str) -> "Memory":
        task = task.strip()
        if not task:
            return self
        return replace(self, pending=(*[p for p in self.pending if p != task], task)[-MAX_PENDING:])

    def pop_pending(self, task: str = "") -> "Memory":
        if not self.pending:
            return self
        if task and task in self.pending:
            return replace(self, pending=tuple(p for p in self.pending if p != task))
        return replace(self, pending=self.pending[:-1])

    def add_turn(self, messages: list[dict[str, Any]]) -> "Memory":
        return replace(self, turns=(*self.turns, tuple(messages))[-MAX_HISTORY_TURNS:])

    def history_messages(self) -> list[dict[str, Any]]:
        return [m for turn in self.turns for m in turn]
