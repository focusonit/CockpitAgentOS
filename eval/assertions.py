"""判定一轮的工具调用是否满足期望。

断言类型（由 ``eval/parse_html.py`` 生成）：

- ``call``            ``{tool, command, params:[{name, op, value}]}``：存在一次匹配的调用
- ``any``             ``{options:[断言]}``：满足其中之一
- ``all``             ``{items:[断言]}``：全部满足（只出现在 any 的候选项里）
- ``called``          ``{tools}``：每个工具都被调用过
- ``called_one_of``   ``{tools}``：至少调用了其中一个
- ``not_called``      ``{tools}``：一个都没调用
- ``no_tool``         本轮没有任何工具调用
- ``param``           ``{tool, field, all, any}``：该工具某次调用的 ``field`` 包含 all 中每一项、any 中至少一项
- ``status``          ``{code}``：某次调用返回该状态码
- ``confirm``         本轮弹出了确认
- ``order``           ``{tools}``：按给定顺序出现

参数操作：``eq``、``contains``、``contains_any``、``in``、``range``、``equals_ref``。
``equals_ref`` 形如 ``turn1.poi[0].name``，指向第 1 轮结果 ``data`` 里的值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from backend.brain.types import ToolCall, TurnResult
from backend.protocol.spec import load_protocol

_REF = re.compile(r"^turn(\d+)\.(.+)$")
_REF_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


class RefError(LookupError):
    pass


def resolve_ref(ref: str, history: Sequence[TurnResult]) -> Any:
    match = _REF.match(ref)
    if not match:
        raise RefError(f"引用格式不对：{ref}")
    index = int(match.group(1)) - 1
    if not 0 <= index < len(history):
        raise RefError(f"引用的轮次不存在：{ref}")
    node: Any = history[index].data
    for key, idx in _REF_PART.findall(match.group(2)):
        try:
            node = node[int(idx)] if idx else node[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise RefError(f"引用解析失败：{ref}") from exc
    return node


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


LEVEL_WORDS = {"关": 0, "低": 1, "中": 2, "高": 3}  # 与执行器的档位解析一致
# 调节方向的同义词：用例里“高/调高/提高”混用，执行器按同一方向处理
DIRECTION_WORDS = {
    **dict.fromkeys(("高", "调高", "提高", "升高", "调大", "增大", "增加", "大"), "up"),
    **dict.fromkeys(("低", "调低", "降低", "调小", "减小", "减少", "小"), "down"),
}


# 地址类型：用例里有的写“家/公司”，有的写协议编号 1/2/4
PLACE_WORDS = {"家": "1", "回家": "1", "公司": "2", "单位": "2", "当前位置": "4", "当前": "4"}


def _equal(actual: Any, expected: Any) -> bool:
    left, right = str(actual).strip(), str(expected).strip()
    if left in PLACE_WORDS or right in PLACE_WORDS:
        return PLACE_WORDS.get(left, left) == PLACE_WORDS.get(right, right)
    a, e = _as_number(actual), _as_number(expected)
    if (a is None) != (e is None):  # 一边是数字、一边是档位词时按档位比较
        a = a if a is not None else LEVEL_WORDS.get(str(actual).strip())
        e = e if e is not None else LEVEL_WORDS.get(str(expected).strip())
    if a is not None and e is not None:
        return a == e
    left, right = str(actual).strip(), str(expected).strip()
    if left in DIRECTION_WORDS and right in DIRECTION_WORDS:
        return DIRECTION_WORDS[left] == DIRECTION_WORDS[right]
    return left == right


def check_param(spec: dict[str, Any], args: dict[str, Any], history: Sequence[TurnResult]) -> bool:
    name, op, expected = spec["name"], spec["op"], spec["value"]
    if name not in args:
        # “Position ∈ {所有、全部、空}”里的“空”允许不传
        return op == "in" and "空" in expected
    actual = args[name]
    text = "" if actual is None else str(actual)
    if op == "eq":
        return _equal(actual, expected)
    if op == "contains":
        return str(expected) in text
    if op == "contains_any":
        return any(str(v) in text for v in expected)
    if op == "in":
        return any(_equal(actual, v) or (v == "空" and text == "") for v in expected)
    if op == "range":
        number = _as_number(actual)
        low, high = expected
        return number is not None and low <= number <= high
    if op == "equals_ref":
        try:
            return _equal(actual, resolve_ref(expected, history))
        except RefError:
            return False
    raise ValueError(f"未知参数操作：{op}")


def _is_subagent(tool: str) -> bool:
    spec = load_protocol().tools.get(tool)
    return spec is not None and spec.kind == "subagent"


def _call_matches(spec: dict[str, Any], call: ToolCall, history: Sequence[TurnResult]) -> bool:
    if call.tool != spec["tool"]:
        return False
    # 子代理只收 {task}，没有指令名可比；用例里写的指令只是协议文档的示例归类
    if spec.get("command") and not _is_subagent(spec["tool"]) and call.command != spec["command"]:
        return False
    return all(check_param(p, call.args, history) for p in spec.get("params", []))


def _describe_calls(calls: Sequence[ToolCall]) -> str:
    if not calls:
        return "实际没有调用工具"
    parts = [f"{c.tool}({c.command or ''})" for c in calls]
    return "实际调用：" + "、".join(parts)


def _check_param_line(spec: dict[str, Any], calls: Sequence[ToolCall]) -> bool:
    for call in calls:
        if call.tool != spec["tool"]:
            continue
        text = str(call.args.get(spec["field"], ""))
        if all(v in text for v in spec["all"]) and (not spec["any"] or any(v in text for v in spec["any"])):
            return True
    return False


def _check_order(tools: Sequence[str], calls: Sequence[ToolCall]) -> bool:
    position = 0
    for call in calls:
        if position < len(tools) and call.tool == tools[position]:
            position += 1
    return position == len(tools)


def check(spec: dict[str, Any], result: TurnResult, history: Sequence[TurnResult] = ()) -> Verdict:
    calls = result.calls
    used = {c.tool for c in calls}
    kind = spec["type"]
    checks: dict[str, Callable[[], bool]] = {
        "call": lambda: any(_call_matches(spec, c, history) for c in calls),
        "any": lambda: any(check(o, result, history).ok for o in spec["options"]),
        "all": lambda: all(check(i, result, history).ok for i in spec["items"]),
        "called": lambda: all(t in used for t in spec["tools"]),
        "called_one_of": lambda: any(t in used for t in spec["tools"]),
        "not_called": lambda: not any(t in used for t in spec["tools"]),
        "no_tool": lambda: not calls,
        "param": lambda: _check_param_line(spec, calls),
        "status": lambda: any(c.status == spec["code"] for c in calls),
        "confirm": lambda: result.confirm_requested or any(c.needs_confirm for c in calls),
        "order": lambda: _check_order(spec["tools"], calls),
    }
    if kind not in checks:
        raise ValueError(f"未知断言类型：{kind}")
    if checks[kind]():
        return Verdict(True)
    return Verdict(False, f"{describe(spec)} 未满足；{_describe_calls(calls)}")


def check_turn(expect: Sequence[dict[str, Any]], result: TurnResult, history: Sequence[TurnResult]) -> list[Verdict]:
    return [check(spec, result, history) for spec in expect]


def describe(spec: dict[str, Any]) -> str:
    kind = spec["type"]
    if kind == "call":
        params = "，".join(f"{p['name']} {p['op']} {p['value']}" for p in spec.get("params", []))
        return f"调用 {spec['tool']}→{spec.get('command') or ''}" + (f"（{params}）" if params else "")
    if kind == "any":
        return "满足其一[" + " | ".join(describe(o) for o in spec["options"]) + "]"
    if kind == "all":
        return " 且 ".join(describe(i) for i in spec["items"])
    if kind in ("called", "called_one_of", "not_called", "order"):
        label = {"called": "调用", "called_one_of": "调其一", "not_called": "不调", "order": "按顺序"}[kind]
        return f"{label} {'、'.join(spec['tools'])}"
    if kind == "param":
        extra = f" 且含其一 {'/'.join(spec['any'])}" if spec["any"] else ""
        return f"{spec['tool']}.{spec['field']} 含 {'、'.join(spec['all'])}{extra}"
    if kind == "status":
        return f"状态码 {spec['code']}"
    return {"no_tool": "不调工具", "confirm": "需确认"}.get(kind, kind)
