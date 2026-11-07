"""体验脚本（data/demos/*.yaml）：由 eval/parse_html.py 从 v7 HTML 生成，前端逐句播放。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEMO_DIR = Path(__file__).resolve().parents[2] / "data" / "demos"


def _check(demo: Any, name: str) -> dict[str, Any]:
    if not isinstance(demo, dict) or not demo.get("title") or not isinstance(demo.get("turns"), list):
        raise ValueError(f"体验脚本 {name} 缺少 title 或 turns")
    for i, turn in enumerate(demo["turns"]):
        if not isinstance(turn, dict) or not turn.get("seat") or not (turn.get("text") or turn.get("event")):
            raise ValueError(f"体验脚本 {name} 第 {i + 1} 句缺少 seat 或 text")
    return demo


@lru_cache(maxsize=1)
def load_demos(directory: Path = DEMO_DIR) -> tuple[dict[str, Any], ...]:
    demos = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            demo = _check(yaml.safe_load(f), path.name)
        demos.append({"id": path.stem, **demo})
    return tuple(demos)
