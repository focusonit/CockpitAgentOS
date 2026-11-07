"""加载协议定义（config/protocol/*.yaml），提供参数校验与类型转换。

协议是唯一来源：function schema、执行器路由表、调试面板控件都从这里生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
PROTOCOL_FILES = ("core.yaml", "longtail.yaml", "apps.yaml")
TOOL_KINDS = ("vehicle", "app", "subagent")

# 车端领域工具。其余工具在 apps.yaml 里：App 工具（导航、音乐、电话……）和子代理，从 M3 开始实现。
VEHICLE_TOOLS = (
    "body",
    "climate",
    "seat",
    "lighting",
    "drive_energy",
    "vehicle_query",
    "system_settings",
    "switch_device",
)

PARAM_TYPES = ("area", "enum", "int", "level", "str")
LEVEL_WORDS = {"关": 0, "关闭": 0, "低": 1, "最低": 1, "一档": 1, "中": 2, "二档": 2, "高": 3, "最高": 3, "三档": 3}
LEVEL_MAX = 3


class InvalidParamError(ValueError):
    """参数不合法：类型不对、不在可选值里，或协议里没有这个参数。"""


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str = "str"
    desc: str = ""
    values: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    examples: tuple[str, ...] = ()

    def coerce(self, raw: Any) -> Any:
        """把大脑给出的参数值转换成协议类型；不合法时抛 InvalidParamError。"""
        if raw is None or raw == "":
            raise InvalidParamError(f"{self.name} 不能为空")
        if self.type == "int":
            return self._coerce_int(raw)
        if self.type == "level":
            return self._coerce_level(raw)
        text = str(raw).strip()
        if self.type == "enum" and text not in self.values:
            raise InvalidParamError(f"{self.name} 只能是 {'、'.join(self.values)}，收到 {text}")
        return text

    def _coerce_int(self, raw: Any) -> int:
        text = str(raw).strip().rstrip("度档%℃")
        try:
            number = float(text)
        except ValueError as exc:
            raise InvalidParamError(f"{self.name} 应为数字，收到 {raw}") from exc
        if not number.is_integer():
            raise InvalidParamError(f"{self.name} 应为整数，收到 {raw}")
        # 超出范围时截到上下限（例如“屏幕亮度调到 20”按最大亮度执行），由处理函数在回复里说明
        value = int(number)
        if self.min is not None:
            value = max(self.min, value)
        if self.max is not None:
            value = min(self.max, value)
        return value

    def _coerce_level(self, raw: Any) -> int:
        text = str(raw).strip()
        if text in LEVEL_WORDS:
            return LEVEL_WORDS[text]
        text = text.removesuffix("档").removesuffix("级")
        if text.isdigit() and 0 <= int(text) <= LEVEL_MAX:
            return int(text)
        raise InvalidParamError(f"{self.name} 应为 0–{LEVEL_MAX} 档或 关/低/中/高，收到 {raw}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "desc": self.desc}
        if self.values:
            out["values"] = list(self.values)
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        if self.examples:
            out["examples"] = list(self.examples)
        return out


@dataclass(frozen=True)
class CommandSpec:
    name: str
    tool: str
    desc: str = ""
    params: tuple[ParamSpec, ...] = ()
    longtail: bool = False

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """返回转换后的参数（不含 command 键）。长尾指令接受任意参数名。"""
        out: dict[str, Any] = {}
        for name, raw in args.items():
            if name == "command" or raw is None or raw == "":
                continue
            spec = self.param(name)
            if spec is None:
                if self.longtail:
                    out[name] = raw if isinstance(raw, (int, float, bool)) else str(raw)
                    continue
                allowed = "、".join(p.name for p in self.params) or "无"
                raise InvalidParamError(f"{self.name} 没有参数 {name}（可用：{allowed}）")
            out[name] = spec.coerce(raw)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool": self.tool,
            "desc": self.desc,
            "longtail": self.longtail,
            "params": [p.to_dict() for p in self.params],
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    desc: str
    commands: dict[str, CommandSpec] = field(default_factory=dict)
    kind: str = "vehicle"  # vehicle | app | subagent
    hidden: bool = False  # 只给触屏操作用，不放进给模型的工具列表


def _param(name: str, raw: dict[str, Any] | None, shared: dict[str, dict[str, Any]]) -> ParamSpec:
    merged = {**shared.get(name, {}), **(raw or {})}
    ptype = merged.get("type", "str")
    if ptype not in PARAM_TYPES:
        raise ValueError(f"参数 {name} 的类型 {ptype} 不在 {PARAM_TYPES} 里")
    if ptype != "enum":
        merged.pop("values", None)
    return ParamSpec(
        name=name,
        type=ptype,
        desc=str(merged.get("desc", "")),
        values=tuple(str(v) for v in merged.get("values", ())),
        min=merged.get("min"),
        max=merged.get("max"),
        examples=tuple(str(v) for v in merged.get("examples", ())),
    )


@dataclass(frozen=True)
class Protocol:
    tools: dict[str, ToolSpec]

    def command(self, name: str) -> CommandSpec | None:
        for tool in self.tools.values():
            if name in tool.commands:
                return tool.commands[name]
        return None

    def find(self, tool: str, command: str) -> CommandSpec | None:
        spec = self.tools.get(tool)
        return spec.commands.get(command) if spec else None

    def of_kind(self, kind: str) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.kind == kind]

    def all_commands(self) -> list[CommandSpec]:
        return [cmd for tool in self.tools.values() for cmd in tool.commands.values()]

    def to_dict(self) -> dict[str, Any]:
        return {
            name: {"desc": tool.desc, "kind": tool.kind, "hidden": tool.hidden, "commands": [c.to_dict() for c in tool.commands.values()]}
            for name, tool in self.tools.items()
        }


def build_protocol(
    core: dict[str, Any], longtail: dict[str, Any] | None = None, apps: dict[str, Any] | None = None
) -> Protocol:
    shared = core.get("params", {})
    tools: dict[str, dict[str, CommandSpec]] = {}
    descs: dict[str, str] = {}
    kinds: dict[str, str] = {}
    hidden: set[str] = set()
    sources = [(core, False), (longtail or {}, True), (apps or {}, False)]
    for source, is_longtail in sources:
        for tool_name, tool in (source.get("tools") or {}).items():
            tool = tool or {}
            if tool.get("desc"):  # 长尾文件先读且没有 desc，不能占住 apps.yaml 的说明
                descs.setdefault(tool_name, tool["desc"])
            if tool.get("hidden"):
                hidden.add(tool_name)
            kind = tool.get("kind")
            if kind is not None:
                if kind not in TOOL_KINDS:
                    raise ValueError(f"工具 {tool_name} 的 kind {kind} 不在 {TOOL_KINDS} 里")
                kinds[tool_name] = kind
            commands = tools.setdefault(tool_name, {})
            for cmd_name, cmd in (tool.get("commands") or {}).items():
                if cmd_name in commands:
                    raise ValueError(f"指令 {tool_name}.{cmd_name} 重复定义")
                cmd = cmd or {}
                params = tuple(
                    _param(pname, praw, shared) for pname, praw in (cmd.get("params") or {}).items()
                )
                commands[cmd_name] = CommandSpec(
                    name=cmd_name, tool=tool_name, desc=cmd.get("desc", ""), params=params, longtail=is_longtail
                )
    return Protocol(
        {name: ToolSpec(name, descs.get(name, ""), cmds, kinds.get(name, "vehicle"), name in hidden) for name, cmds in tools.items()}
    )


@lru_cache(maxsize=1)
def load_protocol() -> Protocol:
    proto_dir = CONFIG_DIR / "protocol"
    core, longtail, apps = (
        load_yaml(proto_dir / name) if (proto_dir / name).exists() else None for name in PROTOCOL_FILES
    )
    return build_protocol(core or {}, longtail, apps)
