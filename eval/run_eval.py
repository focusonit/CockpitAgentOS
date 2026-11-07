"""评测入口。

    uv run python -m eval.run_eval --agent oracle
    uv run python -m eval.run_eval --agent null --set single --set hard
    uv run python -m eval.run_eval --agent oracle --only permission-001 --repeat 3
    uv run python -m eval.run_eval --agent deepseek --set single --set hard --scope vehicle

``--scope vehicle`` 只跑期望里只涉及车端工具（或不调工具）的用例，即 M2 的验收范围。

每个用例：按初始车况建车 → 逐轮把输入交给大脑 → 用断言判定。
``--repeat N`` 时一条用例 N 次全过才算稳定通过（pass^N），同时给出平均单次通过率。
结果写到 ``eval/reports/<时间>-<agent>[-<label>].{json,html}``。

对比两个模型或两版提示词（M6）：

    uv run python -m eval.run_eval --agent deepseek --set protocol --model deepseek-chat --label chat
    uv run python -m eval.run_eval --agent deepseek --set protocol --prompt prompts/v2.txt --label v2
    uv run python -m eval.compare eval/reports/A.json eval/reports/B.json

报告里按“期望工具 / 指令”分组统计通过率，``protocol`` 集合据此看哪些指令还没覆盖好。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.brain.echo import Agent, EchoAgent
from backend.protocol.spec import VEHICLE_TOOLS
from backend.brain.types import Turn, TurnResult
from backend.trace.recorder import TraceRecorder
from backend.vehicle.state import VehicleState
from eval.assertions import check_turn
from eval.mocks import NullAgent, OracleAgent
from eval.report import write_html

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "data" / "scenarios"
REPORT_DIR = ROOT / "eval" / "reports"
SET_ORDER = ("oral", "single", "hard", "multi", "permission", "protocol")

LABEL_RE = re.compile(r"[\w\-]{1,40}")

AgentFactory = Callable[[dict[str, Any]], Agent]

@dataclass(frozen=True)
class BrainOptions:
    """真模型大脑的可选项：换模型名、换系统提示词。"""

    model: str | None = None
    prompt_file: str | None = None


def read_prompt(path: str | None) -> str | None:
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"提示词文件是空的：{path}")
    return text


def _deepseek_factory(options: BrainOptions = BrainOptions()) -> AgentFactory:
    """延迟导入：只有用到真模型时才要求配置 key。每条用例一个新规划器（会话记忆互不影响）。"""
    from backend.brain.planner import PlannerAgent
    from backend.brain.prompt import SYSTEM_PROMPT
    from backend.llm import model_from_env

    model = model_from_env(options.model)
    prompt = read_prompt(options.prompt_file) or SYSTEM_PROMPT
    return lambda _scenario: PlannerAgent(model, system_prompt=prompt)


AGENTS: dict[str, Callable[..., AgentFactory]] = {
    "oracle": lambda _o=None: OracleAgent,
    "null": lambda _o=None: lambda _scenario: NullAgent(),
    "echo": lambda _o=None: lambda _scenario: EchoAgent(),
    "deepseek": _deepseek_factory,
}


def expected_tools(specs: Iterable[dict[str, Any]]) -> set[str]:
    tools: set[str] = set()
    for spec in specs:
        if spec["type"] in ("call", "param"):
            tools.add(spec["tool"])
        elif spec["type"] in ("called", "called_one_of", "order", "not_called"):
            tools.update(spec["tools"] if spec["type"] != "not_called" else ())
        tools |= expected_tools(spec.get("options", []) + spec.get("items", []))
    return tools


def expected_commands(specs: Iterable[dict[str, Any]]) -> set[str]:
    """期望里出现的“工具/指令”（没写指令的只记工具名），报告按它分组。"""
    keys: set[str] = set()
    for spec in specs:
        if spec["type"] == "call":
            keys.add(f"{spec['tool']}/{spec['command']}" if spec.get("command") else spec["tool"])
        elif spec["type"] in ("called", "called_one_of", "order"):
            keys.update(spec["tools"])
        keys |= expected_commands(spec.get("options", []) + spec.get("items", []))
    return keys


def case_commands(scenario: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(set().union(*(expected_commands(t["expect"]) for t in scenario["turns"]))))


def sample_per_command(scenarios: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """按期望指令均匀抽样：一条用例只要它涉及的某个指令还没抽满 n 条就保留。"""
    counts: dict[str, int] = {}
    kept = []
    for s in scenarios:
        keys = case_commands(s) or ("（不调工具）",)
        if any(counts.get(k, 0) < n for k in keys):
            kept.append(s)
            for k in keys:
                counts[k] = counts.get(k, 0) + 1
    return kept


def in_vehicle_scope(scenario: dict[str, Any]) -> bool:
    """期望只涉及车端工具（或要求不调工具）。"""
    used = set().union(*(expected_tools(t["expect"]) for t in scenario["turns"]))
    return used <= set(VEHICLE_TOOLS)


@dataclass(frozen=True)
class TurnOutcome:
    say: str
    result: TurnResult
    failures: tuple[str, ...]


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    set_name: str
    runs: tuple[tuple[TurnOutcome, ...], ...] = field(default_factory=tuple)
    error: str = ""
    commands: tuple[str, ...] = ()

    @property
    def run_passes(self) -> list[bool]:
        return [all(not t.failures for t in run) for run in self.runs]

    @property
    def passed(self) -> bool:
        return not self.error and bool(self.runs) and all(self.run_passes)


def load_scenarios(sets: Iterable[str] | None = None, only: Iterable[str] | None = None) -> list[dict[str, Any]]:
    wanted_sets = list(sets) if sets else list(SET_ORDER)
    wanted_ids = set(only or [])
    scenarios: list[dict[str, Any]] = []
    for name in wanted_sets:
        path = SCENARIO_DIR / f"{name}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"找不到 {path}，先运行 uv run python -m eval.parse_html")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                if not wanted_ids or case["id"] in wanted_ids:
                    scenarios.append(case)
    return scenarios


def _turn_of(raw: dict[str, Any]) -> Turn:
    if "event" in raw:
        return Turn(seat="系统", event=raw["event"])
    return Turn(seat=raw["seat"], text=raw["text"])


async def run_case_once(scenario: dict[str, Any], factory: AgentFactory) -> tuple[TurnOutcome, ...]:
    agent = factory(scenario)
    state = VehicleState.from_init(scenario["init"], scenario.get("profile"))
    history: list[TurnResult] = []
    outcomes: list[TurnOutcome] = []
    for raw in scenario["turns"]:
        result = await agent.handle(state, _turn_of(raw), TraceRecorder())
        if result.state is not None:  # 大脑执行了工具，后面的轮次要看到新车况
            state = result.state
        verdicts = check_turn(raw["expect"], result, history)
        history.append(result)
        say = raw.get("label") or raw.get("text", "")
        outcomes.append(TurnOutcome(say, result, tuple(v.reason for v in verdicts if not v.ok)))
    return tuple(outcomes)


async def run_case(scenario: dict[str, Any], factory: AgentFactory, repeat: int) -> CaseOutcome:
    commands = case_commands(scenario)
    try:
        runs = tuple([await run_case_once(scenario, factory) for _ in range(repeat)])
    except Exception as exc:  # noqa: BLE001 — 单条用例出错不能中断整次评测
        return CaseOutcome(scenario["id"], scenario["set"], error=f"{type(exc).__name__}: {exc}", commands=commands)
    return CaseOutcome(scenario["id"], scenario["set"], runs, commands=commands)


async def run_all(
    scenarios: list[dict[str, Any]], factory: AgentFactory, repeat: int = 1, concurrency: int = 8
) -> list[CaseOutcome]:
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(s: dict[str, Any]) -> CaseOutcome:
        async with semaphore:
            return await run_case(s, factory, repeat)

    return list(await asyncio.gather(*(guarded(s) for s in scenarios)))


def print_failures(outcomes: list[CaseOutcome]) -> None:
    for o in outcomes:
        if o.passed:
            continue
        print(f"✗ {o.case_id}  {o.error}")
        for t in (o.runs[0] if o.runs else ()):
            calls = "; ".join(f"{c.tool}{json.dumps(c.args, ensure_ascii=False)}={c.status}" for c in t.result.calls)
            print(f"   说：{t.say}\n   调：{calls or '（无）'}\n   答：{t.result.reply[:80]}")
            for f in t.failures:
                print(f"   ⚠ {f}")


def summarize(outcomes: list[CaseOutcome]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name in [*SET_ORDER, "合计"]:
        items = [o for o in outcomes if name == "合计" or o.set_name == name]
        if not items:
            continue
        runs = [p for o in items for p in (o.run_passes or [False])]
        summary[name] = {
            "cases": len(items),
            "passed": sum(o.passed for o in items),
            "pass_rate": sum(o.passed for o in items) / len(items),
            "pass_at_1": sum(runs) / len(runs),
        }
    return summary


def by_command(outcomes: list[CaseOutcome]) -> dict[str, dict[str, Any]]:
    """按期望的“工具/指令”统计：一条用例涉及几个指令就分别计入。失败多的排前面。"""
    rows: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        for key in o.commands or ("（不调工具）",):
            row = rows.setdefault(key, {"cases": 0, "passed": 0, "failed_ids": []})
            row["cases"] += 1
            if o.passed:
                row["passed"] += 1
            else:
                row["failed_ids"].append(o.case_id)
    ordered = sorted(rows.items(), key=lambda kv: (-(kv[1]["cases"] - kv[1]["passed"]), kv[0]))
    return {k: {**v, "pass_rate": v["passed"] / v["cases"]} for k, v in ordered}


def by_tool(commands: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, int]] = {}
    for key, row in commands.items():
        tool = key.split("/", 1)[0]
        agg = rows.setdefault(tool, {"cases": 0, "passed": 0, "commands": 0, "commands_all_pass": 0})
        agg["cases"] += row["cases"]
        agg["passed"] += row["passed"]
        agg["commands"] += 1
        agg["commands_all_pass"] += row["passed"] == row["cases"]
    return {k: {**v, "pass_rate": v["passed"] / v["cases"]} for k, v in sorted(rows.items())}


def to_json(outcomes: list[CaseOutcome], meta: dict[str, Any]) -> dict[str, Any]:
    commands = by_command(outcomes)
    return {
        "meta": meta,
        "summary": summarize(outcomes),
        "by_tool": by_tool(commands),
        "by_command": commands,
        "cases": [
            {
                "id": o.case_id,
                "set": o.set_name,
                "passed": o.passed,
                "error": o.error,
                "commands": list(o.commands),
                "runs": [
                    [{"say": t.say, "failures": list(t.failures), "result": t.result.to_dict()} for t in run]
                    for run in o.runs
                ],
            }
            for o in outcomes
        ],
    }


def brain_model_name(factory: AgentFactory, scenario: dict[str, Any]) -> str | None:
    """规划器用的模型名（mock 大脑没有），写进报告 meta。"""
    model = getattr(factory(scenario), "model", None)
    return getattr(model, "name", None)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="座舱助手评测")
    parser.add_argument("--agent", default="oracle", choices=sorted(AGENTS))
    parser.add_argument("--set", action="append", dest="sets", choices=SET_ORDER)
    parser.add_argument("--only", action="append", help="只跑指定用例 id，可重复")
    parser.add_argument("--scope", choices=("all", "vehicle"), default="all", help="vehicle：只跑车控相关用例")
    parser.add_argument("--limit", type=int, default=0, help="每个集合最多跑多少条（0 为不限）")
    parser.add_argument("--per-command", type=int, default=0, help="每个期望指令最多跑多少条（0 为不限），用来均匀抽样协议集")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--show-fail", action="store_true", help="打印失败用例的输入、实际调用和失败原因")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--model", help="真模型的模型名，默认读 DEEPSEEK_MODEL")
    parser.add_argument("--prompt", help="替换系统提示词的文本文件，用来比较两版提示词")
    parser.add_argument("--label", default="", help="这次评测的名字，写进报告文件名和 meta，对比时显示")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat 至少为 1")
    if args.label and not LABEL_RE.fullmatch(args.label):
        parser.error("--label 只能用字母、数字、中文、横线和下划线，最长 40 个字符")
    if (args.model or args.prompt) and args.agent != "deepseek":
        parser.error("--model / --prompt 只对 --agent deepseek 有效")

    scenarios = load_scenarios(args.sets, args.only)
    if args.scope == "vehicle":
        scenarios = [s for s in scenarios if in_vehicle_scope(s)]
    if args.per_command > 0:
        scenarios = sample_per_command(scenarios, args.per_command)
    if args.limit > 0:
        counts: dict[str, int] = {}
        kept = []
        for s in scenarios:
            counts[s["set"]] = counts.get(s["set"], 0) + 1
            if counts[s["set"]] <= args.limit:
                kept.append(s)
        scenarios = kept
    if not scenarios:
        print("没有匹配的用例", file=sys.stderr)
        return 2
    options = BrainOptions(args.model, args.prompt)
    factory = AGENTS[args.agent](options)
    outcomes = asyncio.run(run_all(scenarios, factory, args.repeat, args.concurrency))
    meta = {
        "agent": args.agent,
        "repeat": args.repeat,
        "scope": args.scope,
        "at": datetime.now().isoformat(timespec="seconds"),
        "label": args.label,
        "model": brain_model_name(factory, scenarios[0]) or args.model,
        "prompt": args.prompt or "默认",
        "sets": sorted({s["set"] for s in scenarios}),
    }
    report = to_json(outcomes, meta)

    if args.show_fail:
        print_failures(outcomes)
    for name, row in report["summary"].items():
        print(f"{name:<11} {row['passed']:>5}/{row['cases']:<5} {row['pass_rate']:6.1%}   pass@1 {row['pass_at_1']:6.1%}")
    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        suffix = f"-{args.label}" if args.label else ""
        stem = REPORT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{args.agent}{suffix}"
        stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        write_html(report, stem.with_suffix(".html"))
        print(f"报告：{stem.with_suffix('.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
