"""评测用的假大脑。

- ``NullAgent``：永远不调工具，给出基线分数（只有“不调工具”类用例能过）。
- ``OracleAgent``：照着期望反推出一组必然满足的调用，用来验证解析器和判定器本身没有错。
  理论上它应该 100% 通过；不通过的用例说明解析或判定有 bug，或用例自相矛盾。
"""

from __future__ import annotations

from typing import Any, Sequence

from backend.brain.types import COMMAND_KEY, ToolCall, Turn, TurnResult
from backend.trace.recorder import TraceRecorder
from backend.vehicle.state import VehicleState
from eval.assertions import RefError, resolve_ref

_PLACEHOLDER_POI = "示例地点"


class NullAgent:
    async def handle(self, state: VehicleState, turn: Turn, trace: TraceRecorder) -> TurnResult:
        return TurnResult(reply="好的")


def _param_value(spec: dict[str, Any], history: Sequence[TurnResult]) -> Any:
    op, value = spec["op"], spec["value"]
    if op in ("contains_any", "in"):
        first = value[0]
        return "" if first == "空" else first
    if op == "range":
        return (value[0] + value[1]) // 2
    if op == "equals_ref":
        try:
            return resolve_ref(value, history)
        except RefError:
            return _PLACEHOLDER_POI
    return value


class _Builder:
    def __init__(self, history: Sequence[TurnResult]) -> None:
        self.history = history
        self.calls: list[dict[str, Any]] = []
        self.confirm = False
        self.forbidden: set[str] = set()

    def call(self, tool: str, command: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {COMMAND_KEY: command} if command else {}
        entry = {"tool": tool, "args": args, "status": "Success"}
        self.calls.append(entry)
        return entry

    def find(self, tool: str) -> dict[str, Any]:
        for entry in self.calls:
            if entry["tool"] == tool:
                return entry
        return self.call(tool)

    def add(self, spec: dict[str, Any]) -> None:
        kind = spec["type"]
        if kind == "call":
            entry = self.call(spec["tool"], spec.get("command"))
            for p in spec.get("params", []):
                entry["args"][p["name"]] = _param_value(p, self.history)
        elif kind == "any":
            self.add(spec["options"][0])
        elif kind == "all":
            for item in spec["items"]:
                self.add(item)
        elif kind in ("called", "order"):
            for tool in spec["tools"]:
                if kind == "order" or not any(c["tool"] == tool for c in self.calls):
                    self.call(tool)
        elif kind == "called_one_of":
            if not any(c["tool"] in spec["tools"] for c in self.calls):
                self.call(spec["tools"][0])
        elif kind == "param":
            entry = self.find(spec["tool"])
            words = [*spec["all"], *spec["any"][:1]]
            entry["args"][spec["field"]] = " ".join([str(entry["args"].get(spec["field"], "")), *words]).strip()
        elif kind == "status":
            target = self.calls[-1] if self.calls else self.call("system")
            target["status"] = spec["code"]
        elif kind == "confirm":
            self.confirm = True
        elif kind == "not_called":
            self.forbidden.update(spec["tools"])
        elif kind == "no_tool":
            pass
        else:
            raise ValueError(f"未知断言类型：{kind}")

    def result(self) -> TurnResult:
        calls = tuple(
            ToolCall(c["tool"], c["args"], c["status"], needs_confirm=self.confirm)
            for c in self.calls
            if c["tool"] not in self.forbidden
        )
        data: dict[str, Any] = {}
        for c in calls:
            keyword = c.args.get("Keyword") or c.args.get("KeyWord")
            if keyword and (c.command or "").lower().endswith("searchpoi"):
                data["poi"] = [{"name": keyword}]
        return TurnResult(calls=calls, reply="（oracle）", confirm_requested=self.confirm, data=data)


class OracleAgent:
    """按轮次读取期望。每个用例新建一个实例。"""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self._turns = scenario["turns"]
        self._index = 0
        self._history: list[TurnResult] = []

    async def handle(self, state: VehicleState, turn: Turn, trace: TraceRecorder) -> TurnResult:
        builder = _Builder(self._history)
        for spec in self._turns[self._index]["expect"]:
            builder.add(spec)
        result = builder.result()
        self._index += 1
        self._history.append(result)
        return result
