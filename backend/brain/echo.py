"""M0 占位大脑：不调用模型，只回显输入。M2 换成真正的规划器。"""

from __future__ import annotations

from typing import Protocol

from backend.brain.types import Turn, TurnResult
from backend.trace.recorder import TraceRecorder
from backend.vehicle.state import VehicleState


class Agent(Protocol):
    async def handle(self, state: VehicleState, turn: Turn, trace: TraceRecorder) -> TurnResult: ...


class EchoAgent:
    async def handle(self, state: VehicleState, turn: Turn, trace: TraceRecorder) -> TurnResult:
        trace.add("planner", "EchoAgent 占位，未调用模型")
        if turn.is_event:
            return TurnResult(reply=f"收到系统事件：{turn.event}")
        return TurnResult(reply=f"（{turn.seat}）我听到了：{turn.text}")
