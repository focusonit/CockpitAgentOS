"""从测试场景反推长尾指令，生成 config/protocol/longtail.yaml。

长尾指令是车端工具和 App 工具里、core.yaml / apps.yaml 没有手写的那些指令。它们没有专门的状态字段，
执行时把参数记到 ``settings[指令名]``，返回 Success（App 的长尾指令还会把对应 App 切到前台）。

用法：``uv run python -m backend.protocol.gen_longtail``
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

import yaml

from backend.protocol.spec import CONFIG_DIR, VEHICLE_TOOLS, load_yaml

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "data" / "scenarios"
OUTPUT = CONFIG_DIR / "protocol" / "longtail.yaml"
HEADER = """\
# 长尾指令（车控 + App）：由 backend/protocol/gen_longtail.py 从测试场景生成，不要手改。
# 要给某条指令加专门的状态和约束，把它挪到 core.yaml 并写处理函数。
# 参数一律按自由文本处理；examples 是场景里出现过的取值。
"""


def _walk(spec: dict[str, Any], out: list[dict[str, Any]]) -> None:
    kind = spec.get("type")
    if kind == "call":
        out.append(spec)
    for key in ("options", "items"):
        for child in spec.get(key, []):
            _walk(child, out)


def collect_calls() -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for path in sorted(SCENARIO_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            for turn in json.loads(line)["turns"]:
                for expect in turn.get("expect", []):
                    _walk(expect, calls)
    return calls


def build() -> dict[str, Any]:
    core = load_yaml(CONFIG_DIR / "protocol" / "core.yaml")["tools"]
    apps = load_yaml(CONFIG_DIR / "protocol" / "apps.yaml")["tools"]
    app_tools = {name for name, spec in apps.items() if spec.get("kind") == "app" and not spec.get("hidden")}
    known = {(name, cmd) for table in (core, apps) for name, tool in table.items() for cmd in (tool.get("commands") or {})}
    vehicle_known = {cmd for tool in core.values() for cmd in tool["commands"]}
    found: dict[str, dict[str, dict[str, collections.Counter]]] = collections.defaultdict(dict)
    for call in collect_calls():
        tool, command = call.get("tool"), call.get("command")
        if not command or (tool, command) in known:
            continue
        if tool in VEHICLE_TOOLS:
            if command in vehicle_known:
                continue
        elif tool not in app_tools:
            continue
        params = found[tool].setdefault(command, {})
        for p in call.get("params", []):
            counter = params.setdefault(p["name"], collections.Counter())
            values = p["value"] if isinstance(p["value"], list) else [p["value"]]
            counter.update(str(v) for v in values)
    tools: dict[str, Any] = {}
    for tool in sorted(found):
        commands = {}
        for command in sorted(found[tool]):
            params = {
                name: {"type": "str", "examples": [v for v, _ in counter.most_common(6)]}
                for name, counter in sorted(found[tool][command].items())
            }
            commands[command] = {"params": params} if params else {}
        tools[tool] = {"commands": commands}
    return {"tools": tools, "device_names": collect_device_names()}


def collect_device_names() -> list[str]:
    """场景里 Device.Open / Device.Close 用到、但设备表里没有的设备名，写进 switch_device 的参数说明。"""
    table = load_yaml(CONFIG_DIR / "devices.yaml")
    known = set(table) | {a for spec in table.values() for a in spec.get("aliases", [])}
    names: set[str] = set()
    for call in collect_calls():
        if call.get("tool") != "switch_device":
            continue
        for p in call.get("params", []):
            if p["name"] == "Device":
                names.update(str(v) for v in (p["value"] if isinstance(p["value"], list) else [p["value"]]))
    return sorted(names - known)


def main() -> None:
    data = build()
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    OUTPUT.write_text(HEADER + "\n" + body, encoding="utf-8")
    total = sum(len(t["commands"]) for t in data["tools"].values())
    print(f"写入 {OUTPUT}：{total} 条长尾指令，{len(data['device_names'])} 个长尾设备名")


if __name__ == "__main__":
    main()
