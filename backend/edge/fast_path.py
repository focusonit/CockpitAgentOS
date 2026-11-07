"""快速通道：端侧正则规则引擎，规则在 config/fast_path.yaml。

``FastPath.match(text)`` 整句命中时返回要执行的那一条调用（tool + args），否则 None。
执行交给调用方（规划器），和云端发出的调用走同一个执行器。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.edge.text import core

ROOT = Path(__file__).resolve().parents[2]
FAST_PATH = ROOT / "config" / "fast_path.yaml"
DEVICES = ROOT / "config" / "devices.yaml"
LONGTAIL = ROOT / "config" / "protocol" / "longtail.yaml"
MUSIC = ROOT / "data" / "apps" / "music.json"

_PLACEHOLDER = re.compile(r"@([a-z_]+)")
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class FastRule:
    id: str
    patterns: tuple[re.Pattern[str], ...]
    tool: str
    command: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FastHit:
    rule: str
    tool: str
    args: dict[str, Any]

    @property
    def label(self) -> str:
        params = "，".join(f"{k}={v}" for k, v in self.args.items() if k != "command")
        return f"{self.tool}.{self.args['command']}" + (f"（{params}）" if params else "")


def _alternation(words: list[str]) -> str:
    unique = sorted({w for w in words if w}, key=lambda w: (-len(w), w))
    return "|".join(re.escape(w) for w in unique)


def _number(value: str) -> int | str:
    if value.isdigit():
        return int(value)
    return _CN_DIGITS.get(value, value)


class FastPath:
    def __init__(self, rules: tuple[FastRule, ...], maps: dict[str, dict[str, str]]) -> None:
        self.rules = rules
        self.maps = maps

    @classmethod
    def from_dict(cls, raw: dict[str, Any], vocab: dict[str, list[str]]) -> "FastPath":
        words = {**{k: list(v) for k, v in (raw.get("vocab") or {}).items()}, **vocab}
        excluded = set(raw.get("exclude_devices") or [])
        words["device"] = [d for d in words.get("device", []) if d not in excluded]

        def expand(pattern: str, rule_id: str, exclude: dict[str, list[str]]) -> re.Pattern[str]:
            def sub(m: re.Match[str]) -> str:
                name = m.group(1)
                if name not in words:
                    raise ValueError(f"快速通道规则 {rule_id} 用了未知词表 @{name}")
                skip = set(exclude.get(name) or [])
                return _alternation([w for w in words[name] if w not in skip])

            return re.compile(_PLACEHOLDER.sub(sub, pattern), re.IGNORECASE)

        rules = []
        seen: set[str] = set()
        for item in raw.get("rules") or []:
            rule_id = str(item["id"])
            if rule_id in seen:
                raise ValueError(f"快速通道规则 id 重复：{rule_id}")
            seen.add(rule_id)
            if not item.get("tool") or not item.get("command") or not item.get("patterns"):
                raise ValueError(f"快速通道规则 {rule_id} 需要 tool、command、patterns")
            rules.append(
                FastRule(
                    rule_id,
                    tuple(expand(p, rule_id, item.get("exclude") or {}) for p in item["patterns"]),
                    str(item["tool"]),
                    str(item["command"]),
                    dict(item.get("args") or {}),
                )
            )
        return cls(tuple(rules), {k: dict(v) for k, v in (raw.get("maps") or {}).items()})

    def match(self, text: str) -> FastHit | None:
        words = core(text)
        if not words:
            return None
        for rule in self.rules:
            for pattern in rule.patterns:
                m = pattern.fullmatch(words)
                if m:
                    return FastHit(rule.id, rule.tool, self._args(rule, m))
        return None

    def _args(self, rule: FastRule, m: re.Match[str]) -> dict[str, Any]:
        args: dict[str, Any] = {"command": rule.command}
        for key, template in rule.args.items():
            if not (isinstance(template, str) and template.startswith("$")):
                args[key] = template
                continue
            group = template[1:]
            value = m.groupdict().get(group)
            if not value:
                continue
            value = self.maps.get(group, {}).get(value, value)
            args[key] = _number(value) if group == "n" else value
        return args


def default_vocab() -> dict[str, list[str]]:
    """设备名、歌名、歌手：和协议、曲库保持一致，不在 YAML 里重复写。"""
    with DEVICES.open(encoding="utf-8") as f:
        devices = yaml.safe_load(f) or {}
    names = [*devices.keys(), *(a for d in devices.values() for a in d.get("aliases") or [])]
    with LONGTAIL.open(encoding="utf-8") as f:
        names += (yaml.safe_load(f) or {}).get("device_names") or []
    with MUSIC.open(encoding="utf-8") as f:
        songs = json.load(f)["songs"]
    return {
        "device": names,
        "song": [s["title"] for s in songs],
        "singer": [s["artist"] for s in songs if s["artist"] not in ("群星", "儿歌")],
    }


@lru_cache(maxsize=1)
def load_fast_path(path: Path = FAST_PATH) -> FastPath:
    with path.open(encoding="utf-8") as f:
        return FastPath.from_dict(yaml.safe_load(f) or {}, default_vocab())
