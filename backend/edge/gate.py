"""安全闸门（开发计划 §7.1）：规则在 config/gate.yaml。

- ``Gate.check(text)``：上云之前先过一遍，命中黑区 / 违法有害 / 未上线能力就直接回复，不调用工具。
- ``Gate.blocked_call(tool, args)``：模型自己拼出来的驾驶类调用（参数里带“领航”“自动泊车”等）也拦下。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.edge.text import normalize

GATE_PATH = Path(__file__).resolve().parents[2] / "config" / "gate.yaml"
CATEGORIES = ("black", "harmful", "unsupported")
CATEGORY_NAMES = {"black": "黑区", "harmful": "违法有害", "unsupported": "未上线能力"}


@dataclass(frozen=True)
class GateRule:
    id: str
    category: str
    patterns: tuple[re.Pattern[str], ...]
    unless: tuple[re.Pattern[str], ...]
    reply: str

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns) and not any(u.search(text) for u in self.unless)


@dataclass(frozen=True)
class GateHit:
    rule: str
    category: str
    reply: str

    @property
    def category_name(self) -> str:
        return CATEGORY_NAMES[self.category]


def _compile(items: Any, where: str) -> tuple[re.Pattern[str], ...]:
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise ValueError(f"{where} 必须是字符串列表")
    return tuple(re.compile(i, re.IGNORECASE) for i in items)


class Gate:
    def __init__(self, rules: tuple[GateRule, ...], blocked_args: tuple[re.Pattern[str], ...]) -> None:
        self.rules = rules
        self.blocked_args = blocked_args

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Gate":
        rules = []
        seen: set[str] = set()
        for item in raw.get("rules") or []:
            rule_id = str(item["id"])
            if rule_id in seen:
                raise ValueError(f"闸门规则 id 重复：{rule_id}")
            seen.add(rule_id)
            if item.get("category") not in CATEGORIES:
                raise ValueError(f"闸门规则 {rule_id} 的 category 只能是 {'/'.join(CATEGORIES)}")
            if not item.get("reply"):
                raise ValueError(f"闸门规则 {rule_id} 缺少 reply")
            rules.append(
                GateRule(
                    rule_id,
                    item["category"],
                    _compile(item.get("patterns"), f"{rule_id}.patterns"),
                    _compile(item.get("unless") or [], f"{rule_id}.unless"),
                    str(item["reply"]),
                )
            )
        blocked = tuple(re.compile(re.escape(w), re.IGNORECASE) for w in raw.get("blocked_args") or [])
        return cls(tuple(rules), blocked)

    def check(self, text: str) -> GateHit | None:
        words = normalize(text)
        for rule in self.rules:
            if rule.matches(words):
                return GateHit(rule.id, rule.category, rule.reply)
        return None

    def blocked_call(self, tool: str, args: dict[str, Any]) -> str | None:
        """参数里带驾驶控制类关键词就返回命中的词，否则 None。子代理的 task 是自然语言，不检查。"""
        if "task" in args and len(args) == 1:
            return None
        for key, value in args.items():
            if key == "command" or not isinstance(value, str):
                continue
            for pattern in self.blocked_args:
                if pattern.search(value):
                    return pattern.pattern.replace("\\", "")
        return None


@lru_cache(maxsize=1)
def load_gate(path: Path = GATE_PATH) -> Gate:
    with path.open(encoding="utf-8") as f:
        return Gate.from_dict(yaml.safe_load(f) or {})
