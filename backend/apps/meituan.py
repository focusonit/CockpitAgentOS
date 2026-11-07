"""美团 App：商户列表、商户详情与菜单、购物车、订单草稿、下单、订座。

触屏操作走隐藏工具 meituan_app；大模型通过 meituan 子代理（backend/agents/meituan.py）
调用这里的同一组函数，所以两种来源的效果完全一致。下单只能在用户确认之后。
"""

from __future__ import annotations

from typing import Any

from backend.apps.common import load, now_of, ok
from backend.apps.navigation import here
from backend.protocol import status as st
from backend.protocol.spec import InvalidParamError
from backend.vehicle.devices.base import Ctx, Handler, Outcome

DELIVERY_FEE = 4
ORDER_STEPS = (("已下单", 0), ("商家已接单", 2), ("骑手配送中", 8), ("已送达", 30))  # 状态, 下单后第几分钟


def merchants() -> list[dict[str, Any]]:
    return load("meituan")["merchants"]


def merchant_by_id(mid: str) -> dict[str, Any] | None:
    return next((m for m in merchants() if m["id"] == mid), None)


def summary(m: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "name", "type", "tags", "avg_price", "rating", "distance_km", "rooms", "near")
    return {k: m[k] for k in keys}


def city_of(state: Any) -> str:
    if state.get("nav.active"):
        from backend.apps.navigation import resolve

        return resolve(state, state.get("nav.destination"))["city"]
    return here(state)["city"]


def find(state: Any, keyword: str = "", *, max_price: int | None = None, sort: str = "距离", kind: str = "") -> list[dict[str, Any]]:
    city = city_of(state)
    hits = []
    for m in merchants():
        if m["city"] != city:
            continue
        if kind and m["type"] != kind:
            continue
        text = m["name"] + " ".join(m["tags"])
        if keyword and keyword not in text and not any(t in keyword for t in m["tags"]):
            continue
        if max_price is not None and m["avg_price"] > max_price:
            continue
        hits.append(m)
    order = {"评分": lambda m: -m["rating"], "人均": lambda m: m["avg_price"]}.get(sort, lambda m: m["distance_km"])
    return [summary(m) for m in sorted(hits, key=order)]


# ---- 以下函数是美团的原子操作：输入车况，返回 (更新, 数据, 回复)，失败时抛 MeituanError ----


class MeituanError(Exception):
    def __init__(self, message: str, status: str = st.FAILED) -> None:
        super().__init__(message)
        self.status = status


Step = tuple[dict[str, Any], dict[str, Any], str]


def do_search(state: Any, keyword: str = "", max_price: int | None = None, sort: str = "距离", kind: str = "") -> Step:
    results = find(state, keyword, max_price=max_price, sort=sort, kind=kind)
    label = keyword or kind or "商户"
    if not results:
        return {"meituan.last_results": [], "meituan.merchant": ""}, {"merchants": []}, f"附近没有找到{label}"
    listing = "；".join(f"{i}. {m['name']}（{m['rating']}分，人均{m['avg_price']}元，{m['distance_km']}公里）" for i, m in enumerate(results[:5], 1))
    return {"meituan.last_results": results, "meituan.merchant": ""}, {"merchants": results}, f"找到{len(results)}家{label}：{listing}"


def do_open(state: Any, index: int | None = None, name: str = "") -> Step:
    results = state.get("meituan.last_results") or []
    picked = None
    if index is not None:
        if not 1 <= index <= len(results):
            raise MeituanError(f"列表里只有{len(results)}家，没有第{index}家", st.INVALID_PARAM)
        picked = results[index - 1]
    elif name:
        picked = next((m for m in results if name in m["name"]), None) or next(
            (summary(m) for m in merchants() if name in m["name"]), None)
    if picked is None:
        raise MeituanError(f"没有找到商户{name}" if name else "要打开哪一家？", st.NOT_FOUND if name else st.INVALID_PARAM)
    m = merchant_by_id(picked["id"])
    menu = "、".join(f"{d['name']}{d['price']}元" + ("（招牌）" if d.get("signature") else "") for d in m["menu"])
    rooms = "，有包间" if m["rooms"] and m["type"] == "餐厅" else ""
    text = f"已打开{m['name']}：{m['rating']}分，人均{m['avg_price']}元，距离{m['distance_km']}公里{rooms}。菜单：{menu}"
    return {"meituan.merchant": m["id"]}, {"merchant": summary(m), "menu": m["menu"]}, text


def current_merchant(state: Any) -> dict[str, Any]:
    m = merchant_by_id(state.get("meituan.merchant") or "")
    if m is None:
        raise MeituanError("还没有选商户", st.PRECOND)
    return m


def match_item(m: dict[str, Any], text: str) -> dict[str, Any] | None:
    """菜名出现在文字里的取最长的；说“招牌”取招牌菜。"""
    named = [d for d in m["menu"] if d["name"] in text]
    if named:
        return max(named, key=lambda d: len(d["name"]))
    fuzzy = [d for d in m["menu"] if text and text in d["name"]]
    if fuzzy:
        return fuzzy[0]
    if "招牌" in text or not text:
        return next((d for d in m["menu"] if d.get("signature")), m["menu"][0])
    return None


def _price(dish: dict[str, Any], size: str) -> int:
    return dish["price"] + (dish.get("sizes") or {}).get(size, 0)


def do_add(state: Any, item: str = "", count: int = 1, size: str = "", note: str = "") -> Step:
    m = current_merchant(state)
    dish = match_item(m, item)
    if dish is None:
        raise MeituanError(f"{m['name']}没有{item}", st.NOT_FOUND)
    cart = [c for c in state.get("meituan.cart") or [] if c["merchant_id"] == m["id"]]
    entry = {"merchant_id": m["id"], "item": dish["name"], "count": count, "size": size, "note": note, "price": _price(dish, size)}
    cart = [c for c in cart if not (c["item"] == dish["name"] and c["size"] == size)] + [entry]
    extra = "".join(f"，{x}" for x in (size, note) if x)
    return {"meituan.cart": cart}, {"cart": cart}, f"已把{count}份{dish['name']}{extra}加入购物车"


def do_remove(state: Any, item: str) -> Step:
    cart = [c for c in state.get("meituan.cart") or [] if item not in c["item"]]
    return {"meituan.cart": cart}, {"cart": cart}, f"已从购物车移除{item}"


def resolve_address(state: Any, address: str) -> str:
    favorites = state.get("nav.favorites") or {}
    for key, value in favorites.items():
        if key in address:
            return f"{key}（{value}）"
    return address


def _draft(state: Any, cart: list[dict[str, Any]], address: str) -> dict[str, Any]:
    m = merchant_by_id(cart[0]["merchant_id"])
    total = sum(c["price"] * c["count"] for c in cart) + (DELIVERY_FEE if m["type"] != "餐厅" else 0)
    return {"merchant_id": m["id"], "merchant": m["name"], "items": cart, "address": address, "total": total,
            "kind": "外卖" if m["type"] != "酒店" else "预订"}


def draft_text(draft: dict[str, Any]) -> str:
    items = "、".join(f"{c['item']}{('（' + c['size'] + '）') if c['size'] else ''}×{c['count']}{('，' + c['note']) if c['note'] else ''}" for c in draft["items"])
    where = f"，送到{draft['address']}" if draft["address"] else "，还没有送达地址"
    return f"订单草稿：{draft['merchant']} {items}{where}，合计{draft['total']}元。确认后才会下单"


def do_draft(state: Any, address: str = "") -> Step:
    cart = state.get("meituan.cart") or []
    old = state.get("meituan.draft")
    if not cart:
        raise MeituanError("购物车是空的", st.PRECOND)
    draft = _draft(state, cart, resolve_address(state, address) if address else (old or {}).get("address", ""))
    return {"meituan.draft": draft}, {"draft": draft}, draft_text(draft)


def do_cancel_draft(state: Any) -> Step:
    if not state.get("meituan.draft"):
        return {}, {}, "当前没有待确认的订单"
    return {"meituan.draft": None, "meituan.cart": []}, {}, "已取消订单草稿"


def do_place(state: Any) -> Step:
    draft = state.get("meituan.draft")
    if not draft:
        raise MeituanError("没有可以提交的订单草稿，先选好商品", st.PRECOND)
    if not draft.get("address") and draft.get("kind") == "外卖":
        raise MeituanError("订单还没有送达地址", st.INVALID_PARAM)
    orders = list(state.get("meituan.orders") or [])
    order = {**draft, "id": f"MT{len(orders) + 1:04d}", "status": ORDER_STEPS[0][0],
             "placed_at": now_of(state).isoformat(timespec="minutes")}
    updates = {"meituan.orders": [*orders, order], "meituan.draft": None, "meituan.cart": []}
    return updates, {"order": order}, f"已下单{order['merchant']}，订单号{order['id']}，合计{order['total']}元，预计30分钟送达"


def do_book(state: Any, people: int | None, time: str) -> Step:
    m = current_merchant(state)
    if not people or not time:
        raise MeituanError("订座需要人数和时间", st.INVALID_PARAM)
    booking = {"merchant": m["name"], "people": people, "time": time, "room": m["rooms"]}
    bookings = [*(state.get("meituan.bookings") or []), booking]
    what = "房间" if m["type"] == "酒店" else ("包间" if m["rooms"] and people >= 4 else "座位")
    return {"meituan.bookings": bookings}, {"booking": booking}, f"已在{m['name']}预订{time} {people}人的{what}"


def order_status(order: dict[str, Any], minutes_since: float) -> str:
    current = ORDER_STEPS[0][0]
    for name, at in ORDER_STEPS:
        if minutes_since >= at:
            current = name
    return current


# ---- 触屏操作 ----


def _wrap(fn: Any) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        try:
            updates, data, text = fn(ctx)
        except MeituanError as exc:
            return Outcome(exc.status, message=str(exc))
        return ok(ctx, text, updates, data or None, app="meituan")

    return handler


def _need(value: Any, message: str) -> Any:
    if value in (None, ""):
        raise InvalidParamError(message)
    return value


HANDLERS: dict[tuple[str, str], Handler] = {
    ("meituan_app", "Meituan.Search"): _wrap(lambda c: do_search(c.state, str(c.arg("Keyword") or ""), c.arg("MaxPrice"), c.arg("Sort") or "距离")),
    ("meituan_app", "Meituan.OpenMerchant"): _wrap(lambda c: do_open(c.state, c.arg("Index"), str(c.arg("Name") or ""))),
    ("meituan_app", "Meituan.AddToCart"): _wrap(lambda c: do_add(c.state, str(c.arg("Item") or ""), c.arg("Count") or 1, str(c.arg("Size") or ""), str(c.arg("Note") or ""))),
    ("meituan_app", "Meituan.RemoveFromCart"): _wrap(lambda c: do_remove(c.state, _need(c.arg("Item"), "需要 Item"))),
    ("meituan_app", "Meituan.MakeDraft"): _wrap(lambda c: do_draft(c.state, str(c.arg("Address") or ""))),
    ("meituan_app", "Meituan.CancelDraft"): _wrap(lambda c: do_cancel_draft(c.state)),
    ("meituan_app", "Meituan.PlaceOrder"): _wrap(lambda c: do_place(c.state)),
    ("meituan_app", "Meituan.Book"): _wrap(lambda c: do_book(c.state, c.arg("People"), str(c.arg("Time") or ""))),
    ("meituan_app", "Meituan.Back"): _wrap(lambda c: ({"meituan.merchant": ""}, {}, "已返回商户列表")),
}
