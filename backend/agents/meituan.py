"""meituan / meituan_place_order 子代理。

确定性解析，不调用大模型：从 task 文本里依次认出要做的步骤（搜索 → 选第几家/评分最高 → 问包间 →
订座 → 加购物车 → 生成草稿/改地址 → 取消），用 backend/apps/meituan.py 里和触屏操作相同的原子函数
逐步执行。meituan 永远只生成草稿；真正下单只走 meituan_place_order，而规划器会先请用户确认。
"""

from __future__ import annotations

import re
from typing import Any

from backend.apps import meituan as mt
from backend.apps.common import CN_NUM, cn_number, front
from backend.vehicle.devices.base import Ctx, Outcome
from backend.vehicle.state import VehicleState

_ORDINAL = re.compile(rf"第\s*({CN_NUM})\s*[家个]")
_PRICE = re.compile(rf"(?:人均|均价|价格)?\s*({CN_NUM})\s*(?:元|块)?\s*(?:以下|以内|之内|左右)")
_COUNT = re.compile(rf"({CN_NUM})\s*(?:份|杯|个|碗|盒|瓶)")
_PEOPLE = re.compile(rf"({CN_NUM})\s*(?:个人|人|位)")
_TIME = re.compile(rf"((?:今天|今晚|明天)?\s*(?:早上|上午|中午|下午|晚上)?\s*{CN_NUM}\s*[点:：](?:半|{CN_NUM}分?)?)")
ORDER_WORDS = ("点", "要", "来", "买", "加", "下单", "外卖", "送")
BOOK_WORDS = ("订座", "订位", "位子", "座位", "订桌", "订个桌", "包间", "订房", "订个酒店", "预订", "订酒店")
TOP_WORDS = ("评分最高", "最好", "评价最好", "分最高")
CANCEL_WORDS = ("取消", "不要了", "算了")


def keywords() -> list[str]:
    words = {t for m in mt.merchants() for t in m["tags"]} | {m["type"] for m in mt.merchants()}
    words |= {re.split(r"[（(]", m["name"])[0] for m in mt.merchants()}
    return sorted(words - {"外卖", "餐厅"}, key=len, reverse=True)


def search_keyword(text: str) -> str:
    return next((k for k in keywords() if k in text), "")


class Pipeline:
    """在一份本地车况上逐步执行原子操作，最后把所有更新合并成一次 Outcome。"""

    def __init__(self, state: VehicleState) -> None:
        self.state = state
        self.updates: dict[str, Any] = {}
        self.data: dict[str, Any] = {}
        self.messages: list[str] = []

    def run(self, step: mt.Step) -> None:
        updates, data, message = step
        self.state, _ = self.state.apply(updates)
        self.updates.update(updates)
        self.data.update(data)
        self.messages.append(message)

    def has_merchant(self) -> bool:
        return bool(self.state.get("meituan.merchant"))


def _number(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return cn_number(match.group(1)) if match else None


def _item_text(text: str, merchant: dict[str, Any]) -> str:
    named = [d["name"] for d in merchant["menu"] if d["name"] in text]
    if named:
        return max(named, key=len)
    short = [d["name"] for d in merchant["menu"] if any(part and part in text for part in (d["name"][:2], d["name"][-2:]))]
    return short[0] if short else ("招牌" if "招牌" in text else "")


def _size(text: str, merchant: dict[str, Any]) -> str:
    sizes = {s for d in merchant["menu"] for s in (d.get("sizes") or {})}
    return next((s for s in sizes if s in text), "")


def _note(text: str, merchant: dict[str, Any]) -> str:
    options = [o for d in merchant["menu"] for o in (d.get("options") or [])]
    return "，".join(dict.fromkeys(o for o in options if o in text))


def _address(text: str) -> str:
    match = re.search(r"送到([^，,。]+)", text)
    if match:
        return match.group(1).strip()
    return next((w for w in ("公司", "家") if f"到{w}" in text or f"送{w}" in text), "")


def _wants_order(text: str) -> bool:
    return bool(_COUNT.search(text)) or any(w in text for w in ("点一", "点两", "点份", "要大份", "大份", "外卖", "下单", "招牌"))


def _search(p: Pipeline, text: str) -> None:
    kind = "酒店" if "酒店" in text else ""
    keyword = "" if kind else search_keyword(text)
    max_price = _number(_PRICE, text)
    sort = "评分" if any(w in text for w in TOP_WORDS) else "距离"
    p.run(mt.do_search(p.state, keyword, max_price, sort, kind))


def meituan_agent(ctx: Ctx) -> Outcome:
    text = ctx.arg("task")
    p = Pipeline(ctx.state)
    try:
        if any(w in text for w in CANCEL_WORDS) and ctx.state.get("meituan.draft"):
            p.run(mt.do_cancel_draft(p.state))
            return _finish(ctx, p)
        current = mt.merchant_by_id(ctx.state.get("meituan.merchant") or "")
        keyword = search_keyword(text)
        new_search = bool(keyword or "酒店" in text) and not (current and keyword and keyword in current["name"] + "".join(current["tags"]))
        if new_search:
            _search(p, text)
        index = _number(_ORDINAL, text)
        if index is not None:
            p.run(mt.do_open(p.state, index=index))
        elif any(w in text for w in TOP_WORDS) and p.state.get("meituan.last_results"):
            best = max(p.state.get("meituan.last_results"), key=lambda m: m["rating"])
            p.run(mt.do_open(p.state, name=best["name"]))
        if "包间" in text and not any(w in text for w in ("订", "预")):
            return _rooms(ctx, p)
        booking = any(w in text for w in BOOK_WORDS) or (_PEOPLE.search(text) and _TIME.search(text))
        if booking:
            _ensure_merchant(p)
            time_match = _TIME.search(text)
            p.run(mt.do_book(p.state, _number(_PEOPLE, text) or (1 if "酒店" in text else None),
                             time_match.group(1).strip() if time_match else ("今晚" if "今晚" in text else "")))
            return _finish(ctx, p)
        address = _address(text)
        if _wants_order(text) or (address and p.state.get("meituan.cart")):
            _ensure_merchant(p)
            if _wants_order(text):
                _add(p, text)
            p.run(mt.do_draft(p.state, address))
        if not p.messages:
            if p.has_merchant():  # 只问了当前商户的情况
                p.run(mt.do_open(p.state, name=mt.current_merchant(p.state)["name"]))
            else:
                _search(p, text)
    except mt.MeituanError as exc:
        if p.messages:
            return Outcome(exc.status, {**front(ctx, "meituan"), **p.updates}, p.data or None, "；".join([*p.messages, str(exc)]))
        return Outcome(exc.status, front(ctx, "meituan"), message=str(exc))
    return _finish(ctx, p)


def _ensure_merchant(p: Pipeline) -> None:
    if p.has_merchant():
        return
    results = p.state.get("meituan.last_results") or []
    if not results:
        raise mt.MeituanError("先告诉我要哪家店")
    p.run(mt.do_open(p.state, index=1))


def _add(p: Pipeline, text: str) -> None:
    merchant = mt.current_merchant(p.state)
    item = _item_text(text, merchant)
    count = _number(_COUNT, text) or 1
    size, note = _size(text, merchant), _note(text, merchant)
    cart = p.state.get("meituan.cart") or []
    if not item and cart:  # “要大份的”：改购物车里已有的那一项
        last = cart[-1]
        p.run(mt.do_remove(p.state, last["item"]))
        p.run(mt.do_add(p.state, last["item"], last["count"], size or last["size"], note or last["note"]))
        return
    p.run(mt.do_add(p.state, item, count, size, note))


def _rooms(ctx: Ctx, p: Pipeline) -> Outcome:
    merchant = mt.current_merchant(p.state)
    answer = f"{merchant['name']}有包间，可以帮你预订" if merchant["rooms"] else f"{merchant['name']}没有包间，只有大厅座位"
    return Outcome(updates={**front(ctx, "meituan"), **p.updates},
                   data={**p.data, "merchant": mt.summary(merchant), "rooms": merchant["rooms"]}, message=answer)


def _finish(ctx: Ctx, p: Pipeline) -> Outcome:
    return Outcome(updates={**front(ctx, "meituan"), **p.updates}, data=p.data or None, message="；".join(p.messages))


def place_order_agent(ctx: Ctx) -> Outcome:
    try:
        updates, data, message = mt.do_place(ctx.state)
    except mt.MeituanError as exc:
        return Outcome(exc.status, message=str(exc))
    return Outcome(updates={**front(ctx, "meituan"), **updates}, data=data, message=message)
