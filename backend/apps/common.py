"""App 层公用：模拟数据加载、中文数字、时间、App 前台切换。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.vehicle.devices.base import Ctx, Outcome

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "apps"

# 领域工具 → 前台 App
TOOL_APP = {
    "navigation": "navigation",
    "nav_query": "navigation",
    "media_play": "music",
    "media_control": "music",
    "phone": "phone",
    "contact_query": "phone",
    "meituan_app": "meituan",
    "meituan": "meituan",
    "meituan_place_order": "meituan",
    "phone_link": "navigation",
}

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
CN_NUM = r"[\d零〇一二两俩三四五六七八九十百]+"


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def cn_number(text: str | None) -> int | None:
    """把“十二”“两”“一百”“35”转成整数；认不出返回 None。"""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "半":
        return None
    total, current = 0, 0
    for ch in text:
        if ch in CN_DIGITS:
            current = CN_DIGITS[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        elif ch.isdigit():
            current = current * 10 + int(ch)
        else:
            return None
    return total + current


def now_of(ctx_or_state: Any) -> datetime:
    state = ctx_or_state.state if isinstance(ctx_or_state, Ctx) else ctx_or_state
    raw = state.get("now")
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return datetime(2026, 11, 5, 10, 0)


def front(ctx: Ctx, app: str | None = None) -> dict[str, Any]:
    """把 App 切到前台的更新。"""
    return {"screen.app": app or TOOL_APP.get(ctx.tool, ctx.tool)}


def ok(ctx: Ctx, message: str, updates: dict[str, Any] | None = None, data: Any = None, app: str | None = None) -> Outcome:
    return Outcome(updates={**front(ctx, app), **(updates or {})}, data=data, message=message)


def split_names(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in re.split(r"[、,，;；和]", str(value)) if p.strip()]
