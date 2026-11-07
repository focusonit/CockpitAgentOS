"""用 oracle 的期望调用驱动执行器，检查协议和虚拟设备能否承接所有用例。

对每条用例按初始车况建车，逐轮把 oracle 给出的调用交给执行器（同一轮内串行），然后检查：
- 车端调用没有 UnknownCommand / InvalidParam / Failed；
- 用例期望的状态码（status 断言）确实由执行器产生；
- 没有期望状态码的调用都执行成功（Success / PartSucceed）。protocol 集只考指令映射、用默认车况，
  不检查这一条（例如默认空调关着时调风量会返回 NotAC，这是正确行为）。

oracle 只给出断言里写到的参数，所以交给执行器之前先按原话补齐真实大脑会给的参数（见 ``fill_args``）；
断言里连指令都没写的调用（called / called_one_of）只过前置条件规则，没命中规则时计入 skipped。

用法：``uv run python -m eval.exec_check [--set single ...]``
"""

from __future__ import annotations

import argparse
import asyncio
import collections
from dataclasses import dataclass
from typing import Any, Iterable

from backend.protocol import status as st
from backend.protocol.spec import VEHICLE_TOOLS, load_protocol
from backend.vehicle.executor import plan
from backend.vehicle.state import VehicleState
from eval.mocks import OracleAgent
from eval.run_eval import _turn_of, load_scenarios
from backend.trace.recorder import TraceRecorder

BROKEN = {st.UNKNOWN_COMMAND, st.INVALID_PARAM, st.FAILED}
STATUS_LENIENT_SETS = {"protocol"}
DOWN_HINTS = ("低", "小", "暗", "降", "轻", "吵", "弱", "少")
REAR_HINTS = ("后排", "后座")


def fill_args(args: dict[str, Any], text: str) -> dict[str, Any]:
    """按原话补齐 oracle 没给的参数：调节方向，以及“后排”这类目标区域。返回新 dict。"""
    spec = load_protocol().command(str(args.get("command", "")))
    if spec is None:
        return args
    filled = dict(args)
    direction = spec.param("Direction")
    if direction and not any(k in filled for k in ("Number", "Direction", "Mode")):
        down = any(h in text for h in DOWN_HINTS)
        wanted = ("调低", "低", "下") if down else ("调高", "高", "上")
        options = direction.values or wanted
        filled["Direction"] = next((w for w in wanted if w in options), wanted[0])
    if any(h in text for h in REAR_HINTS) and not any(k in filled for k in ("Area", "Position")):
        name = "Position" if spec.param("Position") else "Area" if spec.param("Area") else None
        if name:
            filled[name] = "后排"
    return filled


@dataclass(frozen=True)
class Problem:
    case_id: str
    turn: int
    kind: str  # broken / missing_status / unexpected_status / skipped
    detail: str


def _expected_codes(expect: Iterable[dict[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for spec in expect:
        if spec["type"] == "status":
            codes.add(spec["code"])
        for key in ("options", "items"):
            codes |= _expected_codes(spec.get(key, []))
    return codes


async def check_case(scenario: dict[str, Any]) -> list[Problem]:
    state = VehicleState.from_init(scenario.get("init"), scenario.get("profile"))
    oracle = OracleAgent(scenario)
    problems: list[Problem] = []
    for index, raw in enumerate(scenario["turns"]):
        turn = _turn_of(raw)
        result = await oracle.handle(state, turn, TraceRecorder())
        expected = _expected_codes(raw.get("expect", []))
        produced: set[str] = set()
        for call in result.calls:
            args = fill_args(call.args, turn.text)
            planned = plan(state, call.tool, args, turn.seat)
            code = planned.call.status
            produced.add(code)
            if not args.get("command") and code == st.INVALID_PARAM:
                # 断言没写指令，只能检查前置条件规则，无法真正执行
                problems.append(Problem(scenario["id"], index, "skipped", f"{call.tool} 断言没有写指令"))
                continue
            message = str(planned.call.result.get("message", ""))
            if call.tool not in VEHICLE_TOOLS and code == st.INVALID_PARAM and "需要" in message:
                # App 调用的必填参数断言里没写（真实大脑会给出），无法真正执行
                problems.append(Problem(scenario["id"], index, "skipped", f"{call.tool} 断言缺少必填参数：{message}"))
                continue
            label = f"{call.tool} {args} → {code} {planned.call.result.get('message', '')}"
            if call.tool in VEHICLE_TOOLS and code in BROKEN:
                problems.append(Problem(scenario["id"], index, "broken", label))
            elif not st.is_ok(code) and code not in expected and scenario.get("set") not in STATUS_LENIENT_SETS:
                problems.append(Problem(scenario["id"], index, "unexpected_status", label))
            if st.is_ok(code):
                state, _ = state.apply(planned.updates)
        for code in expected - produced:
            problems.append(Problem(scenario["id"], index, "missing_status", f"期望 {code}，实际 {sorted(produced)}"))
    return problems


async def check_all(sets: list[str] | None = None) -> list[Problem]:
    problems: list[Problem] = []
    for scenario in load_scenarios(sets):
        problems.extend(await check_case(scenario))
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", action="append", dest="sets")
    args = parser.parse_args()
    problems = asyncio.run(check_all(args.sets))
    by_kind = collections.Counter(p.kind for p in problems)
    for p in problems:
        print(f"[{p.kind}] {p.case_id}#{p.turn}: {p.detail}")
    print(f"共 {len(problems)} 个问题：{dict(by_kind)}")


if __name__ == "__main__":
    main()
