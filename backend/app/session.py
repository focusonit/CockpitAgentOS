"""一个 WebSocket 连接对应一个会话：持有当前车辆状态和对话历史。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.brain.echo import Agent
from backend.clock import Tick, advance
from backend.brain.types import ToolCall, Turn, TurnResult
from backend.trace.recorder import TraceRecorder
from backend.vehicle.executor import execute
from backend.vehicle.state import Diff, VehicleState

DeltaSink = Callable[[str], Awaitable[None]]
StateSink = Callable[[VehicleState, list[Diff]], Awaitable[None]]


@dataclass
class Session:
    agent: Agent
    state: VehicleState = field(default_factory=VehicleState)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    history: list[dict[str, Any]] = field(default_factory=list)

    def reset(self, init: dict[str, Any] | None = None, profile: str | None = None) -> None:
        self.state = VehicleState.from_init(init, profile)
        self.history = []
        if hasattr(self.agent, "reset"):
            self.agent.reset()  # 规划器的会话记忆也一起清掉

    def set_state(self, updates: dict[str, Any], *, parse: bool = False) -> list[Diff]:
        self.state, diffs = self.state.apply(updates, parse=parse)
        return diffs

    async def handle(
        self, turn: Turn, on_delta: DeltaSink | None = None, on_state: StateSink | None = None
    ) -> tuple[TurnResult, TraceRecorder]:
        """交给大脑处理一轮。规划器每执行完一批调用就回调 on_state，会话立刻采用新车况，
        这样即使这一轮被新消息打断，已经执行的车控也不会丢。"""
        trace = TraceRecorder()
        trace.add("input", turn.event or turn.text, seat=turn.seat)

        async def adopt(state: VehicleState, diffs: list[Diff]) -> None:
            self.state = state
            if on_state:
                await on_state(state, diffs)

        if getattr(self.agent, "streaming", False):
            result = await self.agent.handle(self.state, turn, trace, on_delta=on_delta, on_state=adopt)
        else:
            result = await self.agent.handle(self.state, turn, trace)
        if result.state is not None:
            self.state = result.state
        self.history = [
            *self.history,
            {"seat": turn.seat, "text": turn.text, "event": turn.event},
            {"assistant": result.reply, "calls": [c.to_dict() for c in result.calls]},
        ]
        trace.add("output", result.reply)
        return result, trace

    def advance(self, minutes: float) -> Tick:
        """推进模拟时钟；到期任务由调用方当作 timer_fire 事件交给大脑。"""
        tick = advance(self.state, minutes)
        self.state = tick.state
        return tick

    def invoke(
        self, tool: str, args: dict[str, Any], seat: str, source: str = "touch"
    ) -> tuple[ToolCall, list[Diff], TraceRecorder]:
        """不经过大脑直接下发一条指令：App 里的触屏点击（source=touch）和调试面板都走这里，
        与大脑发出的调用是同一条执行路径。"""
        trace = TraceRecorder()
        trace.add("invoke", f"{tool}.{args.get('command', '')}", seat=seat, args=args, source=source)
        result = execute(self.state, tool, args, seat)
        call = result.calls[0]
        self.state = result.state
        trace.add("executor", f"{call.status} {call.result.get('message', '')}".strip(), status=call.status, diff=len(result.diffs))
        return call, list(result.diffs), trace
