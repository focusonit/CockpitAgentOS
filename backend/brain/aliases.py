"""别名表：把口语叫法映射到标准名称（config/aliases.yaml）。"""

from __future__ import annotations

from functools import lru_cache

from backend.protocol.spec import CONFIG_DIR, load_yaml

ALIAS_FILE = CONFIG_DIR / "aliases.yaml"


@lru_cache(maxsize=1)
def load_aliases() -> dict[str, str]:
    raw = load_yaml(ALIAS_FILE) if ALIAS_FILE.exists() else {}
    table: dict[str, str] = {}
    for group in raw.values():
        for alias, canonical in (group or {}).items():
            if str(alias) != str(canonical):
                table[str(alias)] = str(canonical)
    return table


def match_aliases(text: str, table: dict[str, str] | None = None) -> dict[str, str]:
    """返回文本里出现的别名。长的优先，避免“大鸟巢”同时命中“鸟巢”。"""
    table = load_aliases() if table is None else table
    found: dict[str, str] = {}
    covered = ""
    for alias in sorted(table, key=len, reverse=True):
        if alias in text and alias not in covered:
            found[alias] = table[alias]
            covered += alias + "|"
    return found
