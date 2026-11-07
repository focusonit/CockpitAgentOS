"""规划器：用 LLM 的 function calling 跑“规划 → 执行 → 回读 → 再规划”循环。

每一轮把模型发出的工具调用交给执行器（同一次回复里的调用互不依赖，按顺序执行），
把状态码和结果回给模型；全部是车控且都成功时直接用执行结果拼回复，省掉一次模型调用。
需要确认的操作先拦下，等用户下一句确认后再执行。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from backend.brain.aliases import match_aliases
from backend.clock import event_text, fire_task, next_task
from backend.edge.fast_path import FastPath, load_fast_path
from backend.edge.gate import Gate, load_gate
from backend.edge.gateway import OFFLINE, OFFLINE_REPLY, WEAK, WEAK_DELAY_S, network_mode
from backend.brain.memory import HeldCall, Memory
from backend.brain.prompt import (
    INTERNAL_TOOL_NAMES,
    INTERNAL_TOOLS,
    SYSTEM_PROMPT,
    build_user_message,
    history_user_message,
)
from backend.brain.types import ToolCall, Turn, TurnResult
from backend.llm.base import ChatModel, DeltaHandler, LLMToolCall
from backend.protocol import status as st
from backend.protocol.schema import openai_tools
from backend.protocol.spec import VEHICLE_TOOLS, Protocol, load_protocol
from backend.trace.recorder import TraceRecorder
from backend.vehicle.executor import run_batch
from backend.vehicle.state import Diff, VehicleState

MAX_ROUNDS = 4
CONFIRM_COMMANDS = frozenset({"BcallCall.DialContact", "EcallCall.DialContact", "APP.Uninstall"})
CONFIRM_TOOLS = frozenset({"meituan_place_order"})
FALLBACK_SEAT = "主驾"  # 系统事件没有说话人，按主驾执行
_PUNCT = re.compile(r"[\s，。！？、,.!?~～]+")
YES_WORDS = frozenset({"确认", "确定", "好", "好的", "是", "是的", "可以", "对", "行", "没问题", "嗯", "下单", "确认下单", "打吧", "拨打", "卸载", "继续"})
NO_WORDS = frozenset({"取消", "不要", "算了", "不用", "不", "不了", "别", "先不", "再想想"})

EVENT_ANSWERS = {"confirm": "yes", "cancel": "no"}  # 触屏点“确认/取消”
TIMER_EVENT = "timer_fire"

# 每执行完一批调用就回调一次，让会话立刻采用新车况（被打断时已执行的部分也不会丢）
StateHandler = Callable[[VehicleState, list[Diff]], Awaitable[None]]


def needs_confirm(tool: str, args: dict[str, Any]) -> bool:
    return tool in CONFIRM_TOOLS or args.get("command") in CONFIRM_COMMANDS


def classify_answer(text: str) -> str | None:
    """对待确认操作的回答：yes / no / None（说的是别的事）。"""
    words = _PUNCT.sub("", text)
    if words in YES_WORDS or words.startswith(("确认", "确定")):
        return "yes"
    if words in NO_WORDS or words.startswith(("取消", "不要", "算了")):
        return "no"
    return None


@dataclass(frozen=True)
class _Executed:
    llm_call: LLMToolCall
    call: ToolCall | None  # 内部工具（记偏好、挂起任务）不算对外的工具调用
    content: dict[str, Any]


class PlannerAgent:
    """一个会话一个实例：实例里保存会话内记忆。"""

    streaming = True  # handle 支持 on_delta / on_state 回调

    def __init__(
        self,
        model: ChatModel,
        *,
        protocol: Protocol | None = None,
        include_longtail: bool = True,
        max_rounds: int = MAX_ROUNDS,
        gate: Gate | None = None,
        fast_path: FastPath | None = None,
        weak_delay_s: float = WEAK_DELAY_S,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.gate = gate or load_gate()
        self.fast_path = fast_path or load_fast_path()
        self.weak_delay_s = weak_delay_s
        self.protocol = protocol or load_protocol()
        self.tools = [*openai_tools(self.protocol, include_longtail=include_longtail), *INTERNAL_TOOLS]
        self.max_rounds = max_rounds
        self.memory = Memory()

    def reset(self) -> None:
        self.memory = Memory()

    async def handle(
        self,
        state: VehicleState,
        turn: Turn,
        trace: TraceRecorder,
        on_delta: DeltaHandler | None = None,
        on_state: StateHandler | None = None,
    ) -> TurnResult:
        seat = FALLBACK_SEAT if turn.is_event else turn.seat
        if turn.is_event and turn.event in EVENT_ANSWERS:
            if not self.memory.held:
                return TurnResult(reply="没有需要确认的操作。")
            return await self._answer_held(state, turn, seat, EVENT_ANSWERS[turn.event], trace, on_delta, on_state)
        if self.memory.held and not turn.is_event:
            answer = classify_answer(turn.text)
            if answer is not None:
                return await self._answer_held(state, turn, seat, answer, trace, on_delta, on_state)
            self.memory = replace(self.memory, held=())

        if not turn.is_event:
            edge = await self._edge(state, turn, seat, trace, on_state)
            if edge is not None:
                return edge

        changed = False
        if turn.is_event and turn.event == TIMER_EVENT:
            task = next_task(state)
            if task is None:
                return TurnResult(reply="")
            state, diffs = fire_task(state, task)
            changed = True
            if on_state:
                await on_state(state, diffs)
            turn = replace(turn, event=event_text(task))
            trace.add("timer", f"触发任务 {task['id']}：{task['text']}")

        mode = network_mode(state)
        if mode == OFFLINE:
            trace.add("gateway", "断网：快速通道没命中，不上云", route="offline")
            return await self._reply_only(turn, OFFLINE_REPLY, state if changed else None)
        trace.add("gateway", f"云端规划（{mode}）", route="cloud", network=mode)

        aliases = match_aliases(turn.text if not turn.is_event else turn.event or "")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *self.memory.history_messages(),
            {"role": "user", "content": build_user_message(state, turn, self.memory, aliases)},
        ]
        recorded: list[dict[str, Any]] = [{"role": "user", "content": history_user_message(turn)}]
        calls: list[ToolCall] = []
        data: dict[str, Any] = {}
        reply = ""
        confirm = False
        try:
            for round_no in range(1, self.max_rounds + 1):
                started = time.perf_counter()
                if mode == WEAK and self.weak_delay_s > 0:
                    await asyncio.sleep(self.weak_delay_s)
                answer = await self.model.complete(messages, self.tools, on_delta)
                trace.add(
                    "llm",
                    f"第 {round_no} 轮：" + ("、".join(c.name for c in answer.tool_calls) or "直接回复"),
                    ms=round((time.perf_counter() - started) * 1000),
                    model=self.model.name,
                    usage=answer.usage,
                    route="cloud",
                    weak_delay_ms=round(self.weak_delay_s * 1000) if mode == WEAK else 0,
                )
                if not answer.tool_calls:
                    reply = answer.content.strip()
                    recorded.append({"role": "assistant", "content": reply})
                    break
                messages.append(answer.assistant_message())
                recorded.append(answer.assistant_message())
                executed, state, diffs = await self._execute(state, answer.tool_calls, seat, trace)
                if diffs:
                    changed = True
                    if on_state:
                        await on_state(state, diffs)
                for item in executed:
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": item.llm_call.id,
                        "content": json.dumps(item.content, ensure_ascii=False),
                    }
                    messages.append(tool_msg)
                    recorded.append(tool_msg)
                    if item.call is not None:
                        calls.append(item.call)
                        confirm = confirm or item.call.needs_confirm
                        _collect(data, item.call)
                if _can_finish(executed):
                    reply = _summarize(executed)
                    recorded.append({"role": "assistant", "content": reply})
                    break
            else:
                reply = "这件事我没能一次办完，你可以换个说法再试试。"
                recorded.append({"role": "assistant", "content": reply})
        finally:
            if not reply:
                recorded.append({"role": "assistant", "content": "（被打断）"})
            self.memory = replace(self.memory.add_turn(recorded), last_data=data)

        return TurnResult(tuple(calls), reply, confirm, data, state if changed else None)

    async def _execute(
        self, state: VehicleState, llm_calls: tuple[LLMToolCall, ...], seat: str, trace: TraceRecorder
    ) -> tuple[list[_Executed], VehicleState, list[Diff]]:
        """一次回复里的调用：内部工具和需确认的就地处理，其余按顺序交给执行器。"""
        results: dict[int, _Executed] = {}
        batch: list[tuple[int, LLMToolCall]] = []
        held: list[HeldCall] = []
        for index, lc in enumerate(llm_calls):
            if lc.parse_error:
                call = ToolCall(lc.name, lc.arguments, st.INVALID_PARAM, {"message": lc.parse_error})
                results[index] = _Executed(lc, call, _content(call))
            elif lc.name in INTERNAL_TOOL_NAMES:
                results[index] = _Executed(lc, None, self._internal(lc))
            elif lc.name not in self.protocol.tools:
                call = ToolCall(lc.name, lc.arguments, st.UNKNOWN_COMMAND, {"message": f"没有工具 {lc.name}"})
                results[index] = _Executed(lc, call, _content(call))
            elif (word := self.gate.blocked_call(lc.name, lc.arguments)) is not None:
                call = ToolCall(
                    lc.name, lc.arguments, st.FAILED, {"message": f"驾驶控制类操作已被安全闸门拦截（{word}）"}
                )
                results[index] = _Executed(lc, call, {**_content(call), "hint": "向用户说明这类操作不能由语音控制"})
                trace.add("gate", f"拦截模型调用 {lc.name}：参数含“{word}”", category="black", word=word)
            elif needs_confirm(lc.name, lc.arguments):
                held.append(HeldCall(lc.name, lc.arguments, seat))
                call = ToolCall(
                    lc.name, lc.arguments, st.PENDING_CONFIRM, {"message": "已拦下，等待用户确认"}, needs_confirm=True
                )
                results[index] = _Executed(lc, call, {**_content(call), "hint": "向用户说明要做什么并请求确认"})
            else:
                batch.append((index, lc))
        if held:
            self.memory = replace(self.memory, held=tuple(held))
        diffs: list[Diff] = []
        if batch:
            outcome = await run_batch(state, [(lc.name, lc.arguments) for _, lc in batch], seat, mode="serial")
            state, diffs = outcome.state, list(outcome.diffs)
            for (index, lc), call in zip(batch, outcome.calls):
                results[index] = _Executed(lc, call, _content(call))
            trace.add(
                "executor",
                "、".join(f"{c.tool}.{c.command or ''} {c.status}" for c in outcome.calls),
                calls=len(outcome.calls),
                diff=len(diffs),
            )
        return [results[i] for i in range(len(llm_calls))], state, diffs

    async def _edge(
        self,
        state: VehicleState,
        turn: Turn,
        seat: str,
        trace: TraceRecorder,
        on_state: StateHandler | None,
    ) -> TurnResult | None:
        """端侧：先过安全闸门，再试快速通道；都没命中返回 None，交给网关和云端。"""
        hit = self.gate.check(turn.text)
        if hit is not None:
            stage = "unsupported" if hit.category == "unsupported" else "gate"
            trace.add(stage, f"{hit.category_name}：{hit.rule}", category=hit.category, rule=hit.rule, route="edge")
            return await self._reply_only(turn, hit.reply, None)
        fast = self.fast_path.match(turn.text)
        if fast is None:
            return None
        started = time.perf_counter()
        outcome = await run_batch(state, [(fast.tool, fast.args)], seat, mode="serial")
        call = outcome.calls[0]
        ms = round((time.perf_counter() - started) * 1000)
        if not st.is_ok(call.status) and network_mode(state) != OFFLINE:
            # 端侧执行不了（设备不存在、前置条件不满足……）：交给云端换个办法，状态没变不用回滚
            trace.add("fast_path", f"快速通道：{fast.label} → {call.status}，转云端", rule=fast.rule, route="edge", ms=ms)
            return None
        trace.add("fast_path", f"快速通道：{fast.label} → {call.status}", rule=fast.rule, route="edge", ms=ms)
        new_state = outcome.state if outcome.diffs else None
        if outcome.diffs and on_state:
            await on_state(outcome.state, list(outcome.diffs))
        reply = _message(call).rstrip("。") + "。"
        if not st.is_ok(call.status):
            desc = st.DESCRIPTIONS.get(call.status, "")
            if desc and desc not in reply:
                reply = f"{reply.rstrip('。')}（{desc}）。"
        data: dict[str, Any] = {}
        _collect(data, call)
        self.memory = replace(
            self.memory.add_turn(
                [{"role": "user", "content": history_user_message(turn)}, {"role": "assistant", "content": reply}]
            ),
            last_data=data,
        )
        return TurnResult((call,), reply, False, data, new_state)

    async def _reply_only(self, turn: Turn, reply: str, new_state: VehicleState | None) -> TurnResult:
        """端侧直接回复（闸门、断网）：不流式，整句随 assistant 发出。"""
        self.memory = replace(
            self.memory.add_turn(
                [{"role": "user", "content": history_user_message(turn)}, {"role": "assistant", "content": reply}]
            ),
            last_data={},
        )
        return TurnResult(reply=reply, state=new_state)

    def _internal(self, lc: LLMToolCall) -> dict[str, Any]:
        args = lc.arguments
        if lc.name == "remember_preference":
            self.memory = self.memory.remember(str(args.get("text", "")))
            return {"status": st.SUCCESS, "message": "已记下", "preferences": list(self.memory.preferences)}
        action, task = args.get("action"), str(args.get("task", ""))
        if action == "push":
            self.memory = self.memory.push_pending(task)
        elif action == "pop":
            self.memory = self.memory.pop_pending(task)
        else:
            return {"status": st.INVALID_PARAM, "message": "action 只能是 push 或 pop"}
        return {"status": st.SUCCESS, "pending": list(self.memory.pending)}

    async def _answer_held(
        self,
        state: VehicleState,
        turn: Turn,
        seat: str,
        answer: str,
        trace: TraceRecorder,
        on_delta: DeltaHandler | None,
        on_state: StateHandler | None,
    ) -> TurnResult:
        held = self.memory.held
        self.memory = replace(self.memory, held=())
        if answer == "no":
            reply = "好的，已取消。"
            calls: tuple[ToolCall, ...] = ()
            new_state = None
            trace.add("confirm", "用户取消", calls=len(held))
        else:
            outcome = await run_batch(state, [(h.tool, h.args) for h in held], seat, mode="serial")
            calls = outcome.calls
            new_state = outcome.state if outcome.diffs else None
            if outcome.diffs and on_state:
                await on_state(outcome.state, list(outcome.diffs))
            reply = "已确认，" + "；".join(_message(c) for c in calls)
            trace.add("confirm", "用户确认，执行被拦下的调用", calls=len(calls))
        if on_delta:
            await on_delta(reply)
        self.memory = self.memory.add_turn(
            [{"role": "user", "content": history_user_message(turn)}, {"role": "assistant", "content": reply}]
        )
        data: dict[str, Any] = {}
        for c in calls:
            _collect(data, c)
        return TurnResult(calls, reply, False, data, new_state)


def _collect(data: dict[str, Any], call: ToolCall) -> None:
    """把调用结果里的 data 收进本轮数据：dict 的键直接合并（后续轮次按 turnN.poi[0].name 引用），
    其它类型按指令名存。"""
    if not isinstance(call.result, dict) or "data" not in call.result:
        return
    value = call.result["data"]
    if isinstance(value, dict):
        data.update(value)
    elif value is not None:
        data[call.command or call.tool] = value


def _message(call: ToolCall) -> str:
    if isinstance(call.result, dict):
        return str(call.result.get("message", "")) or st.DESCRIPTIONS.get(call.status, call.status)
    return st.DESCRIPTIONS.get(call.status, call.status)


def _content(call: ToolCall) -> dict[str, Any]:
    out: dict[str, Any] = {"status": call.status, "message": _message(call)}
    if call.status not in (st.SUCCESS, st.PENDING_CONFIRM):
        out["status_desc"] = st.DESCRIPTIONS.get(call.status, "")
    if isinstance(call.result, dict) and "data" in call.result:
        out["data"] = call.result["data"]
    return out


def _can_finish(executed: list[_Executed]) -> bool:
    """全是车控且都成功：不用再问模型，直接拼回复。"""
    real = [e.call for e in executed if e.call is not None]
    return bool(real) and len(real) == len(executed) and all(
        c.tool in VEHICLE_TOOLS and st.is_ok(c.status) for c in real
    )


def _summarize(executed: list[_Executed]) -> str:
    parts = [_message(e.call) for e in executed if e.call is not None]
    return "，".join(dict.fromkeys(p.rstrip("。") for p in parts if p)) + "。"
