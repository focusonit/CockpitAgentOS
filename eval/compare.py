"""两份评测报告逐条对比：两个模型，或同一模型的两版提示词。

    uv run python -m eval.compare eval/reports/A.json eval/reports/B.json [--out 目录] [--no-report]

按用例 id 对齐，只比两边都跑过的用例，每条归为：
- 都过 / 都挂：结果没变
- 修复：A 挂、B 过
- 退化：A 过、B 挂

输出各集合通过率的变化、按指令的变化，以及每条有变化的用例两边的调用、回复和失败原因。
都挂但调用不同的用例单列为“都挂·调用不同”，方便看模型换了哪种错法。
结果写到 ``eval/reports/compare-<时间>.{json,html}``。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.report import _STYLE, _esc

REPORT_DIR = Path(__file__).resolve().parent / "reports"
KINDS = ("退化", "修复", "都挂·调用不同", "都挂", "都过")
MAX_DETAIL = 300


def load_report(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or "meta" not in data:
        raise ValueError(f"{path} 不是评测报告（缺 meta 或 cases）")
    return data


def call_signature(case: dict[str, Any]) -> list[list[str]]:
    """第一次运行里每轮的调用，形如 ``tool{参数}``，用来判断两边做法是否一样。"""
    runs = case.get("runs") or [[]]
    return [
        [f"{c['tool']}{json.dumps(c['args'], ensure_ascii=False, sort_keys=True)}" for c in turn["result"]["calls"]]
        for turn in runs[0]
    ]


def classify(a: dict[str, Any], b: dict[str, Any]) -> str:
    if a["passed"] and b["passed"]:
        return "都过"
    if a["passed"]:
        return "退化"
    if b["passed"]:
        return "修复"
    return "都挂" if call_signature(a) == call_signature(b) else "都挂·调用不同"


def _rate(passed: int, total: int) -> float:
    return passed / total if total else 0.0


def _set_rows(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for name in sorted({a["set"] for a, _ in pairs}) + ["合计"]:
        items = [(a, b) for a, b in pairs if name == "合计" or a["set"] == name]
        a_pass = sum(a["passed"] for a, _ in items)
        b_pass = sum(b["passed"] for _, b in items)
        rows[name] = {
            "cases": len(items),
            "a_passed": a_pass,
            "b_passed": b_pass,
            "a_rate": _rate(a_pass, len(items)),
            "b_rate": _rate(b_pass, len(items)),
            "delta": _rate(b_pass - a_pass, len(items)),
        }
    return rows


def _command_rows(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, dict[str, int]]:
    """按期望指令统计两边通过数，只保留有变化的指令，变化大的在前。"""
    rows: dict[str, dict[str, int]] = {}
    for a, b in pairs:
        for key in a.get("commands") or b.get("commands") or ["（不调工具）"]:
            row = rows.setdefault(key, {"cases": 0, "a_passed": 0, "b_passed": 0})
            row["cases"] += 1
            row["a_passed"] += a["passed"]
            row["b_passed"] += b["passed"]
    changed = {k: v for k, v in rows.items() if v["a_passed"] != v["b_passed"]}
    return dict(sorted(changed.items(), key=lambda kv: (kv[1]["b_passed"] - kv[1]["a_passed"], kv[0])))


def _side(case: dict[str, Any]) -> list[dict[str, Any]]:
    runs = case.get("runs") or [[]]
    return [
        {
            "say": t["say"],
            "calls": [f"{c['tool']}({json.dumps(c['args'], ensure_ascii=False)}) → {c['status']}" for c in t["result"]["calls"]],
            "reply": t["result"].get("reply", ""),
            "failures": t["failures"],
        }
        for t in runs[0]
    ] or [{"say": "", "calls": [], "reply": "", "failures": [case.get("error") or "没有运行结果"]}]


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    a_cases = {c["id"]: c for c in a["cases"]}
    b_cases = {c["id"]: c for c in b["cases"]}
    common = [cid for cid in a_cases if cid in b_cases]
    if not common:
        raise ValueError("两份报告没有共同的用例，无法对比")
    pairs = [(a_cases[cid], b_cases[cid]) for cid in common]
    kinds = {cid: classify(a_cases[cid], b_cases[cid]) for cid in common}
    counts = {k: sum(v == k for v in kinds.values()) for k in KINDS}
    changed = [
        {"id": cid, "set": a_cases[cid]["set"], "kind": kinds[cid], "a": _side(a_cases[cid]), "b": _side(b_cases[cid])}
        for kind in KINDS[:3]
        for cid in common
        if kinds[cid] == kind
    ]
    return {
        "a": a["meta"],
        "b": b["meta"],
        "at": datetime.now().isoformat(timespec="seconds"),
        "common": len(common),
        "only_a": sorted(set(a_cases) - set(b_cases)),
        "only_b": sorted(set(b_cases) - set(a_cases)),
        "counts": counts,
        "sets": _set_rows(pairs),
        "commands": _command_rows(pairs),
        "changed": changed,
    }


def name_of(meta: dict[str, Any]) -> str:
    bits = [meta.get("label") or meta.get("agent", "?")]
    if meta.get("model"):
        bits.append(str(meta["model"]))
    if meta.get("prompt") and meta["prompt"] != "默认":
        bits.append(f"提示词 {meta['prompt']}")
    return " · ".join(bits)


# ---------- 输出 ----------


def _signed(x: float) -> str:
    return f"{x:+.1%}" if x else "±0"


def _side_html(turns: list[dict[str, Any]]) -> str:
    out = ""
    for t in turns:
        calls = "<br>".join(_esc(c) for c in t["calls"]) or "无工具调用"
        fails = "".join(f"<li class='bad'>{_esc(f)}</li>" for f in t["failures"])
        out += (
            f"<p>「{_esc(t['say'])}」<br><code>{calls}</code><br>"
            f"<span class='muted'>答：{_esc(t['reply'][:120])}</span></p>{f'<ul>{fails}</ul>' if fails else ''}"
        )
    return out


def render(result: dict[str, Any]) -> str:
    a_name, b_name = _esc(name_of(result["a"])), _esc(name_of(result["b"]))
    counts = " · ".join(f"{k} {v}" for k, v in result["counts"].items())
    set_rows = "".join(
        f"<tr><td>{_esc(n)}</td><td class='n'>{r['cases']}</td><td class='n'>{r['a_rate']:.1%}</td>"
        f"<td class='n'>{r['b_rate']:.1%}</td>"
        f"<td class='n {'ok' if r['delta'] > 0 else 'bad' if r['delta'] < 0 else ''}'>{_signed(r['delta'])}</td></tr>"
        for n, r in result["sets"].items()
    )
    cmd_rows = "".join(
        f"<tr><td><code>{_esc(k)}</code></td><td class='n'>{r['a_passed']}/{r['cases']}</td>"
        f"<td class='n'>{r['b_passed']}/{r['cases']}</td></tr>"
        for k, r in result["commands"].items()
    )
    cmd_table = (
        f"<h2>按指令的变化</h2><table><tr><th>工具/指令</th><th>A</th><th>B</th></tr>{cmd_rows}</table>"
        if cmd_rows
        else "<h2>按指令的变化</h2><p class='muted'>没有指令的通过数发生变化</p>"
    )
    shown = result["changed"][:MAX_DETAIL]
    blocks = "".join(
        f"<details{' open' if c['kind'] == '退化' else ''}><summary>"
        f"<span class='{'bad' if c['kind'] == '退化' else 'ok' if c['kind'] == '修复' else 'muted'}'>{_esc(c['kind'])}</span> "
        f"<code>{_esc(c['id'])}</code></summary>"
        f"<div class='pair'><div><h3>A</h3>{_side_html(c['a'])}</div><div><h3>B</h3>{_side_html(c['b'])}</div></div></details>"
        for c in shown
    )
    more = f"<p class='muted'>另有 {len(result['changed']) - len(shown)} 条未展开</p>" if len(result["changed"]) > len(shown) else ""
    extra = ""
    if result["only_a"] or result["only_b"]:
        extra = f"<p class='muted'>只在 A：{len(result['only_a'])} 条 · 只在 B：{len(result['only_b'])} 条（不参与对比）</p>"
    style = _STYLE + ".pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}h3{font-size:13px;margin:6px 0 0}" \
        "@media (max-width:700px){.pair{grid-template-columns:1fr}}"
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>评测对比</title><style>{style}</style></head><body><main>"
        f"<h1>评测对比</h1><p>A：{a_name}<br>B：{b_name}</p>"
        f"<p class='muted'>共同用例 {result['common']} 条 · {_esc(counts)} · {_esc(result['at'])}</p>{extra}"
        "<table><tr><th>集合</th><th>用例</th><th>A</th><th>B</th><th>变化</th></tr>"
        f"{set_rows}</table>{cmd_table}<h2>有变化的用例</h2>{blocks or '<p class=muted>两边结果完全一致</p>'}{more}"
        "</main></body></html>"
    )


def print_summary(result: dict[str, Any]) -> None:
    print(f"A：{name_of(result['a'])}\nB：{name_of(result['b'])}")
    print(f"共同用例 {result['common']} 条：" + "，".join(f"{k} {v}" for k, v in result["counts"].items()))
    for n, r in result["sets"].items():
        print(f"{n:<11} A {r['a_rate']:6.1%}   B {r['b_rate']:6.1%}   {_signed(r['delta'])}")
    for c in result["changed"]:
        if c["kind"] in ("退化", "修复"):
            print(f"  {c['kind']} {c['id']}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="两份评测报告逐条对比")
    parser.add_argument("a", help="基线报告 JSON")
    parser.add_argument("b", help="新报告 JSON")
    parser.add_argument("--out", default=str(REPORT_DIR), help="对比报告写到哪个目录")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = compare(load_report(args.a), load_report(args.b))
    except (OSError, ValueError) as exc:
        print(f"无法对比：{exc}", file=sys.stderr)
        return 2
    print_summary(result)
    if not args.no_report:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stem = out / f"compare-{datetime.now():%Y%m%d-%H%M%S}"
        stem.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        stem.with_suffix(".html").write_text(render(result), encoding="utf-8")
        print(f"对比报告：{stem.with_suffix('.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
