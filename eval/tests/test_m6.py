"""M6：子代理用例不比指令名、按指令统计、换提示词、两份报告逐条对比。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.brain.planner import PlannerAgent
from backend.brain.types import ToolCall, Turn, TurnResult
from backend.llm.scripted import ScriptedModel, reply
from backend.protocol.spec import load_protocol
from backend.trace.recorder import TraceRecorder
from backend.vehicle.state import VehicleState
from eval import compare as cmp
from eval import run_eval
from eval.assertions import check
from eval.report import render


def _result(*calls: ToolCall) -> TurnResult:
    return TurnResult(reply="好的", calls=tuple(calls))


# ---------- 协议集覆盖 ----------


def test_subagent_call_ignores_example_command() -> None:
    spec = {"type": "call", "tool": "home", "command": "HomeSceneMode.CreateScene", "params": []}
    home = ToolCall("home", {"task": "到家时打开空气净化器"}, "Success")
    assert check(spec, _result(home), []).ok


def test_vehicle_call_still_checks_command() -> None:
    spec = {"type": "call", "tool": "body", "command": "WindowCtrl.Open", "params": []}
    wrong = ToolCall("body", {"command": "WindowCtrl.Close"}, "Success")
    assert not check(spec, _result(wrong), []).ok


def test_every_protocol_command_is_registered_or_subagent() -> None:
    protocol = load_protocol()
    missing = []
    for line in run_eval.SCENARIO_DIR.joinpath("protocol.jsonl").read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        for key in run_eval.case_commands(case):
            tool, _, command = key.partition("/")
            spec = protocol.tools.get(tool)
            if spec is None or (command and spec.kind != "subagent" and command not in spec.commands):
                missing.append((case["id"], key))
    assert missing == []


# ---------- 按指令统计 ----------


def test_expected_commands_walks_any_and_all() -> None:
    specs = [
        {"type": "any", "options": [
            {"type": "call", "tool": "body", "command": "WindowCtrl.Open"},
            {"type": "all", "items": [{"type": "called", "tools": ["navigation"]}]},
        ]},
        {"type": "no_tool"},
    ]
    assert run_eval.expected_commands(specs) == {"body/WindowCtrl.Open", "navigation"}


def test_by_command_and_by_tool_count_failures_first() -> None:
    outcomes = [
        run_eval.CaseOutcome("a", "protocol", ((),), commands=("body/X",)),
        run_eval.CaseOutcome("b", "protocol", error="boom", commands=("body/Y",)),
        run_eval.CaseOutcome("c", "protocol", ((),), commands=()),
    ]
    commands = run_eval.by_command(outcomes)
    assert list(commands)[0] == "body/Y" and commands["body/Y"]["failed_ids"] == ["b"]
    assert commands["（不调工具）"]["passed"] == 1
    tools = run_eval.by_tool(commands)
    assert tools["body"] == {"cases": 2, "passed": 1, "commands": 2, "commands_all_pass": 1, "pass_rate": 0.5}


def test_oracle_protocol_report_has_command_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_eval, "REPORT_DIR", tmp_path)
    assert run_eval.main(["--agent", "oracle", "--set", "protocol", "--limit", "40", "--label", "基线"]) == 0
    [report_file] = tmp_path.glob("*-oracle-基线.json")
    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["meta"]["label"] == "基线" and report["meta"]["sets"] == ["protocol"]
    assert report["by_command"] and all(r["passed"] == r["cases"] for r in report["by_command"].values())
    html = report_file.with_suffix(".html").read_text(encoding="utf-8")
    assert "按工具" in html and "全部通过" in html


@pytest.mark.parametrize("argv", [["--label", "../x"], ["--label", "a.b"], ["--model", "deepseek-chat"]])
def test_bad_cli_options_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        run_eval.main(["--agent", "oracle", "--set", "oral", "--no-report", *argv])


# ---------- 换提示词 ----------


def test_planner_uses_custom_system_prompt() -> None:
    model = ScriptedModel([reply("你好")])
    agent = PlannerAgent(model, weak_delay_s=0, system_prompt="你是测试提示词")
    asyncio.run(agent.handle(VehicleState(), Turn(seat="主驾", text="讲个笑话吧随便聊聊"), TraceRecorder()))
    assert model.requests[0]["messages"][0] == {"role": "system", "content": "你是测试提示词"}


def test_read_prompt_rejects_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "p.txt"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError):
        run_eval.read_prompt(str(empty))
    assert run_eval.read_prompt(None) is None


# ---------- 逐条对比 ----------


def _case(cid: str, passed: bool, calls: list[dict] | None = None, set_name: str = "protocol") -> dict:
    turn = {"say": "说", "failures": [] if passed else ["没调对"],
            "result": {"calls": calls or [], "reply": "好", "confirm_requested": False, "data": {}}}
    return {"id": cid, "set": set_name, "passed": passed, "error": "", "commands": [f"body/{cid}"], "runs": [[turn]]}


def _report(label: str, cases: list[dict]) -> dict:
    return {"meta": {"agent": "deepseek", "label": label, "model": "m", "prompt": "默认", "repeat": 1, "at": "t"},
            "summary": {}, "cases": cases}


CALL_A = [{"tool": "body", "args": {"command": "A"}, "status": "Success"}]
CALL_B = [{"tool": "body", "args": {"command": "B"}, "status": "Success"}]


def test_compare_classifies_each_case() -> None:
    a = _report("A", [_case("1", True), _case("2", False), _case("3", True), _case("4", False, CALL_A),
                      _case("5", False, CALL_A), _case("only-a", True)])
    b = _report("B", [_case("1", True), _case("2", True), _case("3", False), _case("4", False, CALL_A),
                      _case("5", False, CALL_B), _case("only-b", True)])
    result = cmp.compare(a, b)
    assert result["counts"] == {"退化": 1, "修复": 1, "都挂·调用不同": 1, "都挂": 1, "都过": 1}
    assert [c["kind"] for c in result["changed"]] == ["退化", "修复", "都挂·调用不同"]
    assert result["only_a"] == ["only-a"] and result["only_b"] == ["only-b"]
    assert result["sets"]["合计"]["a_passed"] == 2 and result["sets"]["合计"]["delta"] == 0
    assert set(result["commands"]) == {"body/2", "body/3"}
    html = cmp.render(result)
    assert "退化" in html and "body" in html and "<script" not in html


def test_compare_rejects_disjoint_reports() -> None:
    with pytest.raises(ValueError):
        cmp.compare(_report("A", [_case("1", True)]), _report("B", [_case("2", True)]))


def test_compare_cli_writes_report(tmp_path: Path) -> None:
    paths = []
    for name, ok in (("a", True), ("b", False)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(_report(name, [_case("1", ok)]), ensure_ascii=False), encoding="utf-8")
        paths.append(str(path))
    assert cmp.main([*paths, "--out", str(tmp_path)]) == 0
    [html] = tmp_path.glob("compare-*.html")
    assert "评测对比" in html.read_text(encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert cmp.main([paths[0], str(bad), "--no-report"]) == 2


def test_compare_escapes_model_output() -> None:
    evil = [{"tool": "body", "args": {"command": "<script>alert(1)</script>"}, "status": "Failed"}]
    result = cmp.compare(_report("A", [_case("1", True)]), _report("B", [_case("1", False, evil)]))
    assert "<script>alert" not in cmp.render(result)
    assert "<script>alert" not in render(_report("B", [_case("1", False, evil)]) | {"by_command": {}})


def test_sample_per_command_covers_every_command() -> None:
    cases = run_eval.load_scenarios(["protocol"])
    sampled = run_eval.sample_per_command(cases, 1)
    all_keys = set().union(*(run_eval.case_commands(c) for c in cases))
    assert set().union(*(run_eval.case_commands(c) for c in sampled)) == all_keys
    assert len(sampled) < len(cases) / 1.5
