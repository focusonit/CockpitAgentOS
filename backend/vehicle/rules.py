"""前置条件规则：按 config/rules.yaml 判断一条指令在当前车况下能不能执行。

规则是数据，判断是纯函数。第一条命中的规则生效，返回它的状态码。
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from backend.protocol.spec import CONFIG_DIR, InvalidParamError, load_yaml
from backend.vehicle.devices.base import Ctx, normalize_device, resolve_seats

OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}
KNOWN_KEYS = {"code", "args_not", "tools", "commands", "except", "profile", "seat_not", "devices", "rear_target", "flat", "target_seat", "state"}


@dataclass(frozen=True)
class Rules:
    limits: dict[str, Any]
    rules: tuple[dict[str, Any], ...]


def _matches_command(command: str, patterns: list[str]) -> bool:
    return any(command.startswith(p[:-1]) if p.endswith("*") else command == p for p in patterns)


def _target_seats(ctx: Ctx) -> tuple[str, ...]:
    try:
        return resolve_seats(ctx.args.get("Position") or ctx.args.get("Area"), ctx.speaker)
    except InvalidParamError:
        return (ctx.speaker,)


def _condition(key: str, expected: Any, ctx: Ctx) -> bool:
    if key == "tools":
        return ctx.tool in expected
    if key == "commands":
        return _matches_command(ctx.command, expected)
    if key == "except":
        return not _matches_command(ctx.command, expected)
    if key == "profile":
        return ctx.state.get("profile") in expected
    if key == "seat_not":
        return ctx.speaker != expected
    if key == "devices":
        return "Device" in ctx.args and normalize_device(ctx.args["Device"]) in expected
    if key == "rear_target":
        rear = any("后" in str(ctx.args.get(k, "")) for k in ("Area", "Position"))
        return rear == expected
    if key == "flat":
        return ("放平" in ctx.args.values()) == expected
    if key == "target_seat":
        return expected in _target_seats(ctx)
    if key == "args_not":
        return all(str(ctx.args.get(name, "")) != str(value) for name, value in expected.items())
    if key == "state":
        return all(
            OPS[op](ctx.state.get(path), value) for path, cond in expected.items() for op, value in cond.items()
        )
    raise ValueError(f"未知规则条件：{key}")


def check(ctx: Ctx, rules: Rules | None = None) -> str | None:
    """返回命中的状态码；没有命中返回 None。"""
    for rule in (rules or load_rules()).rules:
        if all(_condition(k, v, ctx) for k, v in rule.items() if k != "code"):
            return rule["code"]
    return None


def build_rules(raw: dict[str, Any]) -> Rules:
    rules = tuple(raw.get("rules") or ())
    for rule in rules:
        unknown = set(rule) - KNOWN_KEYS
        if unknown or "code" not in rule:
            raise ValueError(f"规则写法有误：{rule}（未知条件 {sorted(unknown)}）")
        for cond in (rule.get("state") or {}).values():
            if set(cond) - OPS.keys():
                raise ValueError(f"规则 {rule['code']} 的车况条件只能用 {sorted(OPS)}")
    return Rules(limits=dict(raw.get("limits") or {}), rules=rules)


@lru_cache(maxsize=1)
def load_rules() -> Rules:
    return build_rules(load_yaml(CONFIG_DIR / "rules.yaml"))


__all__ = ["Rules", "build_rules", "check", "load_rules"]
