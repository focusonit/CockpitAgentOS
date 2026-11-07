import asyncio
from collections import Counter

import pytest

from backend.brain.types import ToolCall, TurnResult
from backend.vehicle.state import VehicleState
from eval.assertions import check, check_param
from eval.parse_html import DEFAULT_SOURCE, parse_document, parse_param
from eval.run_eval import AGENTS, run_all, summarize


@pytest.fixture(scope="module")
def parsed() -> tuple[list[dict], list[dict]]:
    if not DEFAULT_SOURCE.exists():
        pytest.skip("缺少测试场景 HTML")
    return parse_document(DEFAULT_SOURCE.read_text(encoding="utf-8"))


def test_case_counts(parsed: tuple[list[dict], list[dict]]) -> None:
    cases, demos = parsed
    assert Counter(c["set"] for c in cases) == {
        "oral": 20, "single": 213, "hard": 40, "multi": 40, "permission": 40, "protocol": 1222,
    }
    assert sum(len(c["turns"]) for c in cases if c["set"] == "multi") == 110
    assert len(demos) == 9
    assert len({c["id"] for c in cases}) == len(cases)


def test_every_initial_state_is_loadable(parsed: tuple[list[dict], list[dict]]) -> None:
    cases, demos = parsed
    for item in [*cases, *demos]:
        VehicleState.from_init(item["init"], item.get("profile"))


def test_demo_cases_exist(parsed: tuple[list[dict], list[dict]]) -> None:
    cases, demos = parsed
    ids = {c["id"] for c in cases}
    for demo in demos:
        refs = demo["case_id"] if isinstance(demo["case_id"], list) else [demo["case_id"]]
        assert refs and set(refs) <= ids


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("参数不限", None),
        ("Area = 主驾", {"name": "Area", "op": "eq", "value": "主驾"}),
        ("Keyword 含 橘子洲", {"name": "Keyword", "op": "contains", "value": "橘子洲"}),
        ("Keyword 含其一 鸟巢 / 国家体育场", {"name": "Keyword", "op": "contains_any", "value": ["鸟巢", "国家体育场"]}),
        ("Position ∈ {所有、全部、空}", {"name": "Position", "op": "in", "value": ["所有", "全部", "空"]}),
        ('ActionPercent {"range": [40, 60]}', {"name": "ActionPercent", "op": "range", "value": [40, 60]}),
    ],
)
def test_parse_param(raw: str, expected: dict | None) -> None:
    assert parse_param(raw) == expected


def _result(*calls: ToolCall, **kw: object) -> TurnResult:
    return TurnResult(calls=calls, **kw)


def test_param_ops() -> None:
    assert check_param({"name": "Number", "op": "eq", "value": "20"}, {"Number": 20}, [])
    assert check_param({"name": "P", "op": "range", "value": [40, 60]}, {"P": 50}, [])
    assert not check_param({"name": "P", "op": "range", "value": [40, 60]}, {"P": 70}, [])
    assert check_param({"name": "Position", "op": "in", "value": ["所有", "空"]}, {}, [])
    history = [TurnResult(data={"poi": [{"name": "特来电充电站"}]})]
    assert check_param({"name": "Keyword", "op": "equals_ref", "value": "turn1.poi[0].name"}, {"Keyword": "特来电充电站"}, history)
    assert not check_param({"name": "Keyword", "op": "equals_ref", "value": "turn2.poi[0].name"}, {"Keyword": "x"}, history)


def test_check_kinds() -> None:
    climate = ToolCall("climate", {"command": "ACCtrl.SetTemperature", "Area": "主驾", "Number": 20})
    blocked = ToolCall("seat", {"command": "SeatCtrl.SetPosition"}, status="PrecondSpeedNotSupported")
    call_spec = {"type": "call", "tool": "climate", "command": "ACCtrl.SetTemperature",
                 "params": [{"name": "Area", "op": "eq", "value": "主驾"}]}
    assert check(call_spec, _result(climate)).ok
    assert not check(call_spec, _result(blocked)).ok
    assert check({"type": "status", "code": "PrecondSpeedNotSupported"}, _result(blocked)).ok
    assert check({"type": "no_tool"}, _result()).ok
    assert not check({"type": "not_called", "tools": ["seat"]}, _result(blocked)).ok
    assert check({"type": "order", "tools": ["climate", "seat"]}, _result(climate, blocked)).ok
    assert not check({"type": "order", "tools": ["seat", "climate"]}, _result(climate, blocked)).ok
    task = ToolCall("meituan", {"task": "两杯瑞幸美式少冰"})
    assert check({"type": "param", "tool": "meituan", "field": "task", "all": ["美式"], "any": ["两", "2"]}, _result(task)).ok
    assert check({"type": "confirm"}, _result(task, confirm_requested=True)).ok
    reason = check(call_spec, _result()).reason
    assert "未满足" in reason and "没有调用" in reason


def test_oracle_passes_everything_and_null_is_baseline(parsed: tuple[list[dict], list[dict]]) -> None:
    cases, _ = parsed
    oracle = summarize(asyncio.run(run_all(cases, AGENTS["oracle"]())))
    assert oracle["合计"]["passed"] == len(cases)
    null = summarize(asyncio.run(run_all(cases, AGENTS["null"]())))
    assert 0 < null["合计"]["passed"] < len(cases) * 0.1


async def test_executor_handles_oral_and_single() -> None:
    """M1 验收：oral 和 single 里的车控指令都能执行，期望的状态码都由执行器产生。"""
    from eval.exec_check import check_all

    problems = [p for p in await check_all(["oral", "single"]) if p.kind != "skipped"]
    assert problems == []


async def test_executor_produces_permission_statuses() -> None:
    from eval.exec_check import check_all

    problems = [p for p in await check_all(["permission", "multi"]) if p.kind != "skipped"]
    assert problems == []
