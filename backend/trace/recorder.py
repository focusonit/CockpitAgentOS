"""每次请求的链路记录：按阶段记下事件和耗时，前端调试面板直接展示。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceEvent:
    stage: str  # 例如 edge.asr / fast_path / planner / tool / safety / vehicle
    message: str
    at_ms: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "message": self.message, "at_ms": round(self.at_ms, 1), "data": self.data}


class TraceRecorder:
    """单次请求内使用；事件只追加，导出时生成不可变快照。"""

    def __init__(self, request_id: str | None = None) -> None:
        self.request_id = request_id or uuid.uuid4().hex[:8]
        self._start = time.perf_counter()
        self._events: list[TraceEvent] = []

    def add(self, stage: str, message: str, **data: Any) -> TraceEvent:
        event = TraceEvent(stage, message, (time.perf_counter() - self._start) * 1000, data)
        self._events.append(event)
        return event

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def to_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "events": [e.to_dict() for e in self._events]}
