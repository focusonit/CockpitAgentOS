"""执行器：把大脑下发的工具调用落到虚拟设备上。

单条执行流程：检查前置条件规则 → 查协议 → 校验参数 → 调处理函数 → 应用状态更新。
批量执行支持串行（后一条看到前一条的结果）和并行（都基于同一份车况，结果按顺序合并），
可以设超时，``atomic=True`` 时只要有一条失败，整批回滚。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.brain.types import COMMAND_KEY, ToolCall
from backend.protocol import status as st
from backend.protocol.spec import VEHICLE_TOOLS, InvalidParamError, Protocol, load_protocol
from backend.vehicle import rules as rule_engine
from backend.agents import SUBAGENTS
from backend.apps import APP_HANDLERS, record_app
from backend.vehicle.devices import HANDLERS, Ctx, Outcome, record
from backend.vehicle.devices.base import Handler
from backend.vehicle.state import Diff, PathError, VehicleState

DEFAULT_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class Planned:
    """一条调用的执行结果，还没应用到车辆状态上。"""

    call: ToolCall
    updates: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecResult:
    calls: tuple[ToolCall, ...]
    state: VehicleState
    diffs: tuple[Diff, ...] = ()

    @property
    def ok(self) -> bool:
        return all(st.is_ok(c.status) for c in self.calls)


def _fail(tool: str, args: dict[str, Any], code: str, message: str) -> Planned:
    return Planned(ToolCall(tool, args, status=code, result={"message": message}))


def plan(
    state: VehicleState,
    tool: str,
    args: dict[str, Any],
    seat: str,
    *,
    protocol: Protocol | None = None,
    rules: rule_engine.Rules | None = None,
) -> Planned:
    """纯函数：算出一条调用的状态码和要改的路径，不改车辆状态。"""
    protocol = protocol or load_protocol()
    rules = rules or rule_engine.load_rules()
    command = args.get(COMMAND_KEY)
    # 先看前置条件：车况不允许时，不管指令写得对不对都先告诉大脑原因
    raw = {k: v for k, v in args.items() if k != COMMAND_KEY}
    code = rule_engine.check(Ctx(state, tool, str(command or ""), raw, seat, rules.limits), rules)
    if code:
        return _fail(tool, args, code, st.DESCRIPTIONS[code])
    tool_spec = protocol.tools.get(tool)
    if tool_spec is not None and tool_spec.kind == "subagent":
        return _plan_subagent(state, tool, args, seat, rules)
    if not isinstance(command, str) or not command:
        return _fail(tool, args, st.INVALID_PARAM, "缺少 command")
    spec = protocol.find(tool, command)
    if spec is None:
        other = protocol.command(command)
        hint = f"，它属于 {other.tool}" if other else ""
        return _fail(tool, args, st.UNKNOWN_COMMAND, f"{tool} 没有指令 {command}{hint}")
    try:
        params = spec.validate(args)
        ctx = Ctx(state, tool, command, params, seat, rules.limits)
        handler = _handler(tool, command, spec.longtail)
        if handler is None:
            return _fail(tool, args, st.FAILED, f"{command} 没有处理函数")
        outcome: Outcome = handler(ctx)
    except InvalidParamError as exc:
        return _fail(tool, args, st.INVALID_PARAM, str(exc))
    return _planned(tool, args, outcome)


def _handler(tool: str, command: str, longtail: bool) -> Handler | None:
    if tool in VEHICLE_TOOLS:
        return HANDLERS.get(command) or (record if longtail else None)
    return APP_HANDLERS.get((tool, command)) or (record_app if longtail else None)


def _plan_subagent(state: VehicleState, tool: str, args: dict[str, Any], seat: str, rules: rule_engine.Rules) -> Planned:
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        return _fail(tool, args, st.INVALID_PARAM, "子代理需要非空的 task")
    agent = SUBAGENTS.get(tool)
    if agent is None:
        return _fail(tool, args, st.FAILED, f"子代理 {tool} 没有实现")
    outcome = agent(Ctx(state, tool, "", {"task": task.strip()}, seat, rules.limits))
    return _planned(tool, args, outcome)


def _planned(tool: str, args: dict[str, Any], outcome: Outcome) -> Planned:
    result: dict[str, Any] = {"message": outcome.message or st.DESCRIPTIONS[outcome.status]}
    if outcome.data is not None:
        result["data"] = outcome.data
    return Planned(ToolCall(tool, args, status=outcome.status, result=result), outcome.updates)


def execute(state: VehicleState, tool: str, args: dict[str, Any], seat: str, **kwargs: Any) -> ExecResult:
    """同步执行一条调用。"""
    planned = plan(state, tool, args, seat, **kwargs)
    return _apply(state, [planned])


def _apply(state: VehicleState, planned: list[Planned]) -> ExecResult:
    diffs: list[Diff] = []
    calls: list[ToolCall] = []
    for item in planned:
        try:
            state, item_diffs = state.apply(item.updates)
        except PathError as exc:  # 处理函数写错路径属于代码缺陷，不吞掉，标成失败
            calls.append(ToolCall(item.call.tool, item.call.args, status=st.FAILED, result={"message": f"未知路径 {exc}"}))
            continue
        diffs.extend(item_diffs)
        calls.append(item.call)
    return ExecResult(tuple(calls), state, tuple(diffs))


async def _plan_async(state: VehicleState, item: tuple[str, dict[str, Any]], seat: str, latency_s: float, timeout_s: float) -> Planned:
    tool, args = item

    async def run() -> Planned:
        if latency_s:
            await asyncio.sleep(latency_s)  # 模拟设备响应时间
        return plan(state, tool, args, seat)

    try:
        return await asyncio.wait_for(run(), timeout_s)
    except TimeoutError:
        return _fail(tool, args, st.TIMEOUT, f"{args.get(COMMAND_KEY)} 超过 {timeout_s}s 没有响应")


async def run_batch(
    state: VehicleState,
    items: list[tuple[str, dict[str, Any]]],
    seat: str,
    *,
    mode: Literal["serial", "parallel"] = "serial",
    atomic: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    latency_s: float = 0.0,
) -> ExecResult:
    if mode == "parallel":
        planned = await asyncio.gather(*(_plan_async(state, i, seat, latency_s, timeout_s) for i in items))
        result = _apply(state, list(planned))
    elif mode == "serial":
        current, calls, diffs = state, [], []
        for item in items:
            step = _apply(current, [await _plan_async(current, item, seat, latency_s, timeout_s)])
            current = step.state
            calls.extend(step.calls)
            diffs.extend(step.diffs)
        result = ExecResult(tuple(calls), current, tuple(diffs))
    else:
        raise ValueError(f"未知执行模式：{mode}")
    if atomic and not result.ok:
        return _rollback(state, result)
    return result


def _rollback(state: VehicleState, result: ExecResult) -> ExecResult:
    calls = tuple(
        ToolCall(c.tool, c.args, status=st.ROLLED_BACK, result={"message": "同批指令有失败，已撤销"})
        if st.is_ok(c.status)
        else c
        for c in result.calls
    )
    return ExecResult(calls, state, ())
