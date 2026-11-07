"""从协议生成给 LLM 用的 OpenAI function schema：每个领域工具一个函数。

函数参数是 ``command`` 枚举加上该工具所有指令参数的并集，参数说明里写明哪些指令用到它。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.protocol.spec import CONFIG_DIR, CommandSpec, ParamSpec, Protocol, ToolSpec, load_yaml

AREA_HINT = "主驾、副驾、左后排、右后排、前排、后排、左侧、右侧、所有。不填为说话人的座位"


def _json_type(param: ParamSpec) -> dict[str, Any]:
    if param.type == "int":
        schema: dict[str, Any] = {"type": "integer"}
        if param.min is not None:
            schema["minimum"] = param.min
        if param.max is not None:
            schema["maximum"] = param.max
        return schema
    if param.type == "level":
        return {"type": "string", "description": "0–3 或 关/低/中/高"}
    if param.type == "area":
        return {"type": "string", "description": AREA_HINT}
    return {"type": "string"}


@lru_cache(maxsize=1)
def device_names() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """（设备表里的设备名, 长尾设备名）：告诉模型 switch_device 能开关哪些东西。"""
    table = load_yaml(CONFIG_DIR / "devices.yaml") or {}
    longtail = load_yaml(CONFIG_DIR / "protocol" / "longtail.yaml") or {}
    return tuple(table), tuple(longtail.get("device_names", []))


def _device_hint(include_longtail: bool) -> str:
    core, extra = device_names()
    names = [*core, *(extra if include_longtail else ())]
    return "设备名，尽量用这些标准名称：" + "、".join(names) + "。不在列表里的就填用户说的名字"


def subagent_schema(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.desc,
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "完整的任务描述，保留用户的原始要求和条件"}},
                "required": ["task"],
            },
        },
    }


def _command_line(cmd: CommandSpec) -> str:
    """指令一行：名字、参数名、说明。长尾指令没有说明，至少让模型知道它要哪些参数。"""
    params = f"({'、'.join(p.name for p in cmd.params)})" if cmd.params else ""
    return f"{cmd.name}{params}：{cmd.desc}" if cmd.desc else f"{cmd.name}{params}"


def tool_schema(tool: ToolSpec, *, include_longtail: bool = True) -> dict[str, Any]:
    if tool.kind == "subagent":
        return subagent_schema(tool)
    commands = [c for c in tool.commands.values() if include_longtail or not c.longtail]
    properties: dict[str, Any] = {}
    users: dict[str, list[str]] = {}
    # 同名参数在不同指令里取值不同（例如 Direction：调高/调低 与 面/脚）：enum 取并集，说明里按指令列出
    values_by_cmd: dict[str, dict[str, tuple[str, ...]]] = {}
    for cmd in commands:
        for param in cmd.params:
            users.setdefault(param.name, []).append(cmd.name)
            if param.values:
                values_by_cmd.setdefault(param.name, {})[cmd.name] = tuple(param.values)
            if param.name not in properties:
                properties[param.name] = _json_type(param)
    for name, prop in properties.items():
        by_cmd = values_by_cmd.get(name, {})
        if by_cmd:
            prop["enum"] = list(dict.fromkeys(v for vals in by_cmd.values() for v in vals))
        hint = prop.get("description", "")
        if len(set(by_cmd.values())) > 1:
            detail = "；".join(f"{c} 可选 {'/'.join(v)}" for c, v in by_cmd.items())
            prop["description"] = f"{hint}（{detail}）" if hint else detail
        else:
            used_by = "、".join(users[name][:6]) + ("等" if len(users[name]) > 6 else "")
            prop["description"] = f"{hint}（用于 {used_by}）" if hint else f"用于 {used_by}"
    if tool.name == "switch_device" and "Device" in properties:
        properties["Device"]["description"] = _device_hint(include_longtail)
    command_lines = [_command_line(c) for c in commands]
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.desc + "\n指令：\n" + "\n".join(command_lines),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "enum": [c.name for c in commands]}, **properties},
                "required": ["command"],
            },
        },
    }


def openai_tools(protocol: Protocol, *, include_longtail: bool = True) -> list[dict[str, Any]]:
    return [tool_schema(t, include_longtail=include_longtail) for t in protocol.tools.values() if not t.hidden]
