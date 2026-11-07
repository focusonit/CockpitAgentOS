"""把评测结果渲染成单文件 HTML 报告（无 JS，失败用例在前）。"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

_STYLE = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--ok:#2f7d4f;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe6;--muted:#9a998f;--card:#201f1d;--line:#34332f;--ok:#6cc58f;--bad:#f2867d}}
:root[data-theme="dark"]{--bg:#161615;--fg:#ecebe6;--muted:#9a998f;--card:#201f1d;--line:#34332f;--ok:#6cc58f;--bad:#f2867d}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"PingFang SC",sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;background:var(--card);margin:16px 0}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
td.n{text-align:right;font-variant-numeric:tabular-nums}.ok{color:var(--ok)}.bad{color:var(--bad)}
details{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:6px 0;padding:6px 10px}
h2{font-size:16px;margin:20px 0 4px}summary{cursor:pointer}code{font-size:12px}ul{margin:4px 0 4px 18px;padding:0}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _summary_table(summary: dict[str, dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr><td>{_esc(name)}</td><td class='n'>{r['passed']}/{r['cases']}</td>"
        f"<td class='n'>{r['pass_rate']:.1%}</td><td class='n'>{r['pass_at_1']:.1%}</td></tr>"
        for name, r in summary.items()
    )
    return f"<table><tr><th>集合</th><th>通过</th><th>稳定通过率</th><th>pass@1</th></tr>{rows}</table>"


MAX_COMMAND_ROWS = 80


def _tool_table(by_tool: dict[str, dict[str, Any]]) -> str:
    if not by_tool:
        return ""
    rows = "".join(
        f"<tr><td><code>{_esc(tool)}</code></td><td class='n'>{r['passed']}/{r['cases']}</td>"
        f"<td class='n'>{r['pass_rate']:.1%}</td><td class='n'>{r['commands_all_pass']}/{r['commands']}</td></tr>"
        for tool, r in by_tool.items()
    )
    return (
        "<h2>按工具</h2><table><tr><th>期望工具</th><th>用例通过</th><th>通过率</th><th>全过的指令</th></tr>"
        f"{rows}</table>"
    )


def _command_table(by_command: dict[str, dict[str, Any]]) -> str:
    failing = [(k, r) for k, r in by_command.items() if r["passed"] < r["cases"]]
    if not by_command:
        return ""
    if not failing:
        return f"<h2>按指令</h2><p class='ok'>{len(by_command)} 个指令的用例全部通过</p>"
    shown = failing[:MAX_COMMAND_ROWS]
    rows = "".join(
        f"<tr><td><code>{_esc(k)}</code></td><td class='n'>{r['passed']}/{r['cases']}</td>"
        f"<td><code>{_esc('、'.join(r['failed_ids'][:6]))}{'…' if len(r['failed_ids']) > 6 else ''}</code></td></tr>"
        for k, r in shown
    )
    more = f"<p class='muted'>另有 {len(failing) - len(shown)} 个指令有失败</p>" if len(failing) > len(shown) else ""
    return (
        f"<h2>按指令</h2><p class='muted'>共 {len(by_command)} 个指令，{len(failing)} 个有失败用例（失败多的在前）</p>"
        f"<table><tr><th>工具/指令</th><th>通过</th><th>失败用例</th></tr>{rows}</table>{more}"
    )


def _meta_line(meta: dict[str, Any]) -> str:
    parts = [f"大脑 {_esc(meta['agent'])}"]
    if meta.get("label"):
        parts.append(f"标签 {_esc(meta['label'])}")
    if meta.get("model"):
        parts.append(f"模型 {_esc(meta['model'])}")
    if meta.get("prompt") and meta["prompt"] != "默认":
        parts.append(f"提示词 {_esc(meta['prompt'])}")
    parts += [f"重复 {meta['repeat']} 次", _esc(meta["at"])]
    return " · ".join(parts)


def _case_block(case: dict[str, Any]) -> str:
    mark = "<span class='ok'>通过</span>" if case["passed"] else "<span class='bad'>失败</span>"
    body = f"<p class='bad'>{_esc(case['error'])}</p>" if case["error"] else ""
    for index, run in enumerate(case["runs"], 1):
        if len(case["runs"]) > 1:
            body += f"<p class='muted'>第 {index} 次</p>"
        for turn in run:
            calls = "、".join(
                f"{c['tool']}({_esc(c['args'])}) → {c['status']}" for c in turn["result"]["calls"]
            ) or "无工具调用"
            fails = "".join(f"<li class='bad'>{_esc(f)}</li>" for f in turn["failures"])
            body += f"<p>「{_esc(turn['say'])}」<br><code>{_esc(calls)}</code></p>"
            body += f"<ul>{fails}</ul>" if fails else ""
    return f"<details><summary>{mark} <code>{_esc(case['id'])}</code></summary>{body}</details>"


def render(report: dict[str, Any], max_cases: int = 400) -> str:
    meta = report["meta"]
    cases = sorted(report["cases"], key=lambda c: c["passed"])
    shown = cases[:max_cases]
    more = f"<p class='muted'>另有 {len(cases) - len(shown)} 条未展开</p>" if len(cases) > len(shown) else ""
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>评测报告</title><style>{_STYLE}</style></head><body><main>"
        f"<h1>评测报告</h1><p class='muted'>{_meta_line(meta)}</p>"
        f"{_summary_table(report['summary'])}"
        f"{_tool_table(report.get('by_tool', {}))}{_command_table(report.get('by_command', {}))}"
        "<h2>用例</h2>"
        f"{''.join(_case_block(c) for c in shown)}{more}</main></body></html>"
    )


def write_html(report: dict[str, Any], path: Path) -> None:
    path.write_text(render(report), encoding="utf-8")
