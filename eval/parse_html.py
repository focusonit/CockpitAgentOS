"""把《v7 测试场景脚本-v1.html》解析成评测用的场景文件。

用法：``uv run python -m eval.parse_html ["v7 测试场景脚本-v1.html"]``

输出：
- ``data/scenarios/<set>.jsonl``：每行一个用例 ``{id, set, src, tags, profile, init, turns}``
- ``data/demos/NN_xxx.yaml``：体验脚本，断言通过 ``case_id`` 引用 multi 集里的用例

断言格式见 ``eval/assertions.py`` 顶部说明。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "v7 测试场景脚本-v1.html"
SCENARIO_DIR = ROOT / "data" / "scenarios"
DEMO_DIR = ROOT / "data" / "demos"

# 页面上的动作 → 系统事件名
EVENTS = {
    "【到点触发定时任务】": "timer_fire",
    "【点“确认”】": "confirm",
    "【点“取消”】": "cancel",
}

_QUOTES = "“”\"「」"
_SPLIT_ALT = re.compile(r"\s*/\s*")
_SPLIT_LIST = re.compile(r"[、，,]\s*")


class ParseError(ValueError):
    pass


def _text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _tools(node: Tag) -> list[str]:
    return [_text(c) for c in node.select("code.tool")]


# ---------- 参数 ----------

def parse_param(raw: str) -> dict[str, Any] | None:
    """``Name = v`` / ``Name 含 v`` / ``Name 含其一 a / b`` / ``Name ∈ {a、b}`` / ``Name {json}``。"""
    raw = raw.strip()
    if raw == "参数不限":
        return None
    name, _, rest = raw.partition(" ")
    rest = rest.strip()
    if not name or not rest:
        raise ParseError(f"无法解析参数：{raw!r}")
    if rest.startswith("{") and rest.endswith("}") and rest[1:2] == '"':
        spec = json.loads(rest)
        (op, value), = spec.items()
        if op not in ("range", "equals_ref"):
            raise ParseError(f"未知参数操作：{op}")
        return {"name": name, "op": op, "value": value}
    if rest.startswith("∈"):
        body = rest[1:].strip().strip("{}")
        return {"name": name, "op": "in", "value": [v.strip() for v in _SPLIT_LIST.split(body)]}
    if rest.startswith("含其一"):
        body = rest[len("含其一"):].strip()
        return {"name": name, "op": "contains_any", "value": _SPLIT_ALT.split(body)}
    if rest.startswith("含"):
        return {"name": name, "op": "contains", "value": rest[1:].strip()}
    if rest.startswith("="):
        return {"name": name, "op": "eq", "value": rest[1:].strip()}
    raise ParseError(f"无法解析参数：{raw!r}")


def parse_call(node: Tag) -> dict[str, Any]:
    params = [p for p in (parse_param(_text(s)) for s in node.select("span.p")) if p]
    return {
        "type": "call",
        "tool": _text(node.select_one("code.tool")),
        "command": _text(node.select_one("code.cmd")) or None,
        "params": params,
    }


# ---------- 断言行 ----------

def _parse_task_param(node: Tag) -> dict[str, Any]:
    """``<code>meituan.task</code> 含 美式，且 含其一 两 / 2``；``含 4、12`` 表示都要包含。"""
    target = _text(node.select_one("code"))
    tool, _, field = target.partition(".")
    body = node.get_text("", strip=True)
    body = body[body.index(target) + len(target):].strip()
    must_part, _, any_part = body.partition("，且")
    must_part = must_part.strip()
    if not must_part.startswith("含"):
        raise ParseError(f"无法解析参数行：{body!r}")
    must = [v for v in _SPLIT_LIST.split(must_part[1:].strip()) if v]
    any_of: list[str] = []
    if any_part:
        any_part = any_part.strip()
        if not any_part.startswith("含其一"):
            raise ParseError(f"无法解析参数行：{body!r}")
        any_of = _SPLIT_ALT.split(any_part[len("含其一"):].strip())
    return {"type": "param", "tool": tool, "field": field or "task", "all": must, "any": any_of}


def parse_line(node: Tag) -> dict[str, Any]:
    classes = node.get("class", [])
    key = _text(node.select_one("span.k"))
    if "dim" in classes:
        if "不调工具" not in _text(node):
            raise ParseError(f"未知 dim 行：{node}")
        return {"type": "no_tool"}
    if key == "调用":
        return {"type": "called", "tools": _tools(node)}
    if key == "调其一":
        return {"type": "called_one_of", "tools": _tools(node)}
    if key == "不调":
        return {"type": "not_called", "tools": _tools(node)}
    if key == "按顺序调用":
        return {"type": "order", "tools": _tools(node)}
    if key == "状态码":
        return {"type": "status", "code": _text(node.select_one("code.st"))}
    if key == "需确认":
        return {"type": "confirm"}
    if key == "参数":
        return _parse_task_param(node)
    raise ParseError(f"未知断言行：{key!r}")


def parse_any(node: Tag) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    for li in node.select(":scope > ol > li"):
        items = parse_expect(li) if li.find(recursive=False) else []
        if not items and "不调工具" in _text(li):
            items = [{"type": "no_tool"}]
        if not items:
            raise ParseError(f"未知候选项：{li}")
        options.append(items[0] if len(items) == 1 else {"type": "all", "items": items})
    return {"type": "any", "options": options}


def parse_expect(container: Tag) -> list[dict[str, Any]]:
    expect: list[dict[str, Any]] = []
    for child in container.find_all(recursive=False):
        classes = child.get("class", [])
        if "call" in classes:
            expect.append(parse_call(child))
        elif "any" in classes:
            expect.append(parse_any(child))
        elif "ln" in classes:
            expect.append(parse_line(child))
        else:
            raise ParseError(f"未知断言块：{classes}")
    return expect


# ---------- 轮次与车况 ----------

def parse_say(say: Tag) -> dict[str, Any]:
    seat = _text(say.select_one("span.seat")) or "主驾"
    act = say.select_one("span.act")
    if act:
        label = _text(act)
        if label not in EVENTS:
            raise ParseError(f"未知动作：{label}")
        return {"seat": seat, "event": EVENTS[label], "label": label}
    seat_node = say.select_one("span.seat")
    text = say.get_text("", strip=True)
    if seat_node:
        text = text[len(seat_node.get_text("", strip=True)):]
    return {"seat": seat, "text": text.strip().strip(_QUOTES)}


def parse_chips(container: Tag | None) -> tuple[dict[str, str], str | None]:
    init: dict[str, str] = {}
    profile: str | None = None
    if container is None:
        return init, profile
    for chip in container.select("span.chip"):
        label = _text(chip)
        if "prof" in chip.get("class", []):
            profile = label.replace("车型", "").strip()
            continue
        path, sep, value = label.partition("：")
        if not sep:
            raise ParseError(f"车况格式不对：{label!r}")
        init[path.strip()] = value.strip()
    return init, profile


def parse_row(row: Tag) -> dict[str, Any]:
    init, profile = parse_chips(row.select_one("div.veh2"))
    turns = []
    for tt in row.select("td.turns2 > div.tt"):
        turn = parse_say(tt.select_one("div.say"))
        turn["expect"] = parse_expect(tt.select_one("div.ex"))
        turns.append(turn)
    return {
        "id": _text(row.select_one("td.id code")),
        "set": row["data-set"],
        "src": _text(row.select_one("td.id div.src")),
        "tags": [_text(t) for t in row.select("td.tags span.tag")],
        "profile": profile,
        "init": init,
        "turns": turns,
    }


def parse_demo(article: Tag) -> dict[str, Any]:
    init, profile = parse_chips(article.select_one("div.veh"))
    turns = []
    case_ids: set[str] = set()
    for li in article.select("ol.turns > li"):
        say = li.select_one("div.say")
        bold = say.select_one("b")
        turn = parse_say(say)
        if bold:
            turn["text"] = _text(bold).strip(_QUOTES)
        turn["note"] = _text(li.select_one("div.exp"))
        summary = _text(li.select_one("details summary"))
        match = re.search(r"（(.+?)）", summary)
        if match:
            case_ids.add(match.group(1))
        turns.append(turn)
    file_name = _text(article.select_one("code.file"))
    return {
        "title": _text(article.select_one("h3")),
        "file": file_name,
        "summary": _text(article.select_one("p.sum")),
        "behaviors": [_text(b) for b in article.select("span.bh")],
        "case_id": sorted(case_ids)[0] if len(case_ids) == 1 else sorted(case_ids),
        "profile": profile,
        "init": init,
        "turns": turns,
    }


def parse_document(html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    cases = [parse_row(r) for r in soup.select("table.cases tbody tr[data-set]")]
    demos = [parse_demo(a) for a in soup.select("article.demo")]
    return cases, demos


def write_outputs(cases: list[dict[str, Any]], demos: list[dict[str, Any]]) -> None:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    by_set: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_set.setdefault(case["set"], []).append(case)
    for name, items in by_set.items():
        lines = (json.dumps(c, ensure_ascii=False) for c in items)
        (SCENARIO_DIR / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for demo in demos:
        target = DEMO_DIR / Path(demo["file"]).name
        target.write_text(yaml.safe_dump(demo, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if argv else DEFAULT_SOURCE
    cases, demos = parse_document(source.read_text(encoding="utf-8"))
    write_outputs(cases, demos)
    counts = Counter(c["set"] for c in cases)
    turns = Counter()
    for c in cases:
        turns[c["set"]] += len(c["turns"])
    for name, n in counts.items():
        print(f"{name:<11} {n:>5} 条用例  {turns[name]:>5} 轮")
    print(f"合计 {len(cases)} 条用例，{len(demos)} 个体验脚本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
