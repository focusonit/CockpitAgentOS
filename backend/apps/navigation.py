"""导航 App：地点搜索、路线与策略、途经点、收藏地点。

地图是平面坐标（公里），距离 = 直线距离 × 1.3，时间按路线策略的平均车速算。
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from backend.apps.common import ok, load, split_names
from backend.protocol import status as st
from backend.protocol.spec import InvalidParamError
from backend.vehicle.devices.base import Ctx, Handler, Outcome, clamp

ROAD_FACTOR = 1.3
STRATEGY_SPEED = {"推荐": 35, "躲避拥堵": 40, "高速优先": 55, "时间最短": 50, "不走高速": 30, "少收费": 32, "距离最短": 30}
FAVORITE_TYPES = {"1": "家", "2": "公司", "家": "家", "公司": "公司", "回家": "家", "单位": "公司"}
MAX_RESULTS = 5


def all_poi() -> list[dict[str, Any]]:
    return load("poi")["poi"]


def poi_by_name(name: str) -> dict[str, Any] | None:
    for p in all_poi():
        if p["name"] == name:
            return p
    return None


def here(state: Any) -> dict[str, Any]:
    """当前位置；位置名不在地图里时放在北京国贸。"""
    name = str(state.get("location") or "国贸")
    found = poi_by_name(name) or next((p for p in all_poi() if name in p["name"]), None)
    return found or {"name": name, "city": "北京", "x": 0, "y": 0, "tags": []}


def distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) * ROAD_FACTOR


def _synthesize(keyword: str, city: str) -> dict[str, Any]:
    """地图里没有的地点：按名字散列出一个稳定的坐标，保证同名地点每次都在同一个位置。"""
    digest = hashlib.md5(keyword.encode()).digest()
    return {"name": keyword, "city": city, "x": (digest[0] - 128) / 12, "y": (digest[1] - 128) / 12, "tags": [], "synthetic": True}


def search(state: Any, keyword: str, *, near: dict[str, Any] | None = None, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
    """按名称和标签搜索同城地点，按距离排序；名称完全一致的排最前。"""
    origin = near or here(state)
    keyword = keyword.strip().removeprefix("附近的").removeprefix("附近")
    hits = []
    for p in all_poi():
        if p["city"] != origin["city"]:
            continue
        exact = p["name"] == keyword
        if exact or keyword in p["name"] or p["name"] in keyword or any(t in keyword or keyword in t for t in p["tags"]):
            hits.append((not exact, distance(origin, p), p))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [_with_distance(p, origin) for _, _, p in hits[:limit]]


def _with_distance(p: dict[str, Any], origin: dict[str, Any]) -> dict[str, Any]:
    return {**p, "distance_km": round(distance(origin, p), 1)}


def resolve(state: Any, keyword: str) -> dict[str, Any]:
    """导航目的地：最匹配的地点，找不到就合成一个。"""
    favorites = state.get("nav.favorites") or {}
    if keyword in favorites:
        keyword = favorites[keyword]
    found = search(state, keyword, limit=1)
    return found[0] if found else _synthesize(keyword, here(state)["city"])


def route_updates(state: Any, dest: dict[str, Any], waypoints: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    stops = [here(state), *waypoints, dest]
    km = sum(distance(a, b) for a, b in zip(stops, stops[1:]))
    minutes = max(1, round(km / STRATEGY_SPEED.get(strategy, 35) * 60))
    return {
        "nav.active": True,
        "nav.destination": dest["name"],
        "nav.distance_km": round(km, 1),
        "nav.eta_min": minutes,
        "nav.route_strategy": strategy,
        "nav.waypoints": [w["name"] for w in waypoints],
        "nav.route": [{"name": s["name"], "x": s["x"], "y": s["y"]} for s in stops],
        "nav.overview": False,
        "nav.pushed": None,  # 开始导航后手机推送的卡片就收起
    }


def _summary(updates: dict[str, Any]) -> str:
    via = updates["nav.waypoints"]
    via_text = f"，途经{'、'.join(via)}" if via else ""
    return f"开始导航去{updates['nav.destination']}{via_text}，全程约{updates['nav.distance_km']}公里，预计{updates['nav.eta_min']}分钟"


def _current_waypoints(state: Any) -> list[dict[str, Any]]:
    return [resolve(state, name) for name in state.get("nav.waypoints") or []]


def _navigate(ctx: Ctx, dest: dict[str, Any], waypoints: list[dict[str, Any]] | None = None, strategy: str | None = None) -> Outcome:
    strategy = strategy or ctx.state.get("nav.route_strategy") or "推荐"
    updates = route_updates(ctx.state, dest, waypoints or [], strategy)
    return ok(ctx, _summary(updates), updates, {"poi": [dest]}, app="navigation")


def search_poi(ctx: Ctx) -> Outcome:
    keyword = ctx.arg("Keyword") or ctx.arg("KeyWord")
    if not keyword:
        raise InvalidParamError("Navigation.SearchPoi 需要 Keyword")
    via = [resolve(ctx.state, name) for name in split_names(ctx.arg("Waypoint"))]
    return _navigate(ctx, resolve(ctx.state, str(keyword)), via)


def to_favorite(ctx: Ctx) -> Outcome:
    kind = FAVORITE_TYPES.get(str(ctx.arg("Type") or ctx.arg("Number") or "家"))
    if kind is None:
        raise InvalidParamError(f"没有这个收藏地点：{ctx.arg('Type') or ctx.arg('Number')}")
    address = (ctx.state.get("nav.favorites") or {}).get(kind)
    if not address:
        return Outcome(st.FAILED, message=f"还没有设置{kind}的地址")
    return _navigate(ctx, resolve(ctx.state, address), strategy=ctx.state.get("nav.route_strategy"))


def _list(ctx: Ctx, keyword: str, near: dict[str, Any] | None = None, verb: str = "附近") -> Outcome:
    results = search(ctx.state, keyword, near=near)
    if not results:
        return ok(ctx, f"{verb}没有找到{keyword}", {"nav.last_results": []}, {"poi": []}, app="navigation")
    names = "；".join(f"{i}. {p['name']}（{p['distance_km']}公里）" for i, p in enumerate(results, 1))
    return ok(ctx, f"{verb}找到{len(results)}个{keyword}：{names}", {"nav.last_results": results}, {"poi": results}, app="navigation")


def search_around(ctx: Ctx) -> Outcome:
    keyword = ctx.arg("Keyword") or ctx.arg("KeyWord")
    if not keyword:
        raise InvalidParamError(f"{ctx.command} 需要关键词")
    return _list(ctx, str(keyword))


def add_waypoint(ctx: Ctx) -> Outcome:
    """沿途搜索 / 添加途经点：导航中就把最近的结果加成途经点，否则当周边搜索。"""
    keyword = str(ctx.arg("Keyword") or ctx.arg("KeyWord") or "")
    if not keyword:
        raise InvalidParamError(f"{ctx.command} 需要关键词")
    if not ctx.state.get("nav.active"):
        return _list(ctx, keyword, verb="沿途")
    dest = resolve(ctx.state, ctx.state.get("nav.destination"))
    found = search(ctx.state, keyword, near=dest, limit=1) or search(ctx.state, keyword, limit=1)
    if not found:
        return ok(ctx, f"沿途没有找到{keyword}", app="navigation")
    outcome = _navigate(ctx, dest, [*_current_waypoints(ctx.state), found[0]])
    return Outcome(outcome.status, outcome.updates, {"poi": found}, f"已添加途经点{found[0]['name']}。" + outcome.message)


def switch_strategy(ctx: Ctx) -> Outcome:
    strategy = ctx.arg("RouteStrategy") or "推荐"
    if not ctx.state.get("nav.active"):
        return ok(ctx, f"路线偏好已设为{strategy}，下次导航生效", {"nav.route_strategy": strategy}, app="navigation")
    dest = resolve(ctx.state, ctx.state.get("nav.destination"))
    outcome = _navigate(ctx, dest, _current_waypoints(ctx.state), strategy)
    return Outcome(outcome.status, outcome.updates, None, f"已切换为{strategy}，" + outcome.message.removeprefix("开始"))


def delete_waypoints(ctx: Ctx) -> Outcome:
    if not ctx.state.get("nav.active"):
        return ok(ctx, "当前没有途经点", app="navigation")
    outcome = _navigate(ctx, resolve(ctx.state, ctx.state.get("nav.destination")), [])
    return Outcome(outcome.status, outcome.updates, None, "已删除全部途经点。" + outcome.message.removeprefix("开始"))


def query_distance(ctx: Ctx) -> Outcome:
    if not ctx.state.get("nav.active"):
        return ok(ctx, "当前没有在导航", app="navigation")
    s = ctx.state
    return ok(ctx, f"距离{s.get('nav.destination')}还有{s.get('nav.distance_km')}公里，约{s.get('nav.eta_min')}分钟",
              data={"distance_km": s.get("nav.distance_km"), "eta_min": s.get("nav.eta_min")}, app="navigation")


def zoom(step: int) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        value = clamp(int(ctx.state.get("nav.zoom")) + step, 3, 18)
        return ok(ctx, f"地图已{'放大' if step > 0 else '缩小'}", {"nav.zoom": value, "nav.overview": False}, app="navigation")

    return handler


def overview(ctx: Ctx) -> Outcome:
    return ok(ctx, "已显示全览", {"nav.overview": True}, app="navigation")


CLEAR_ROUTE = {"nav.active": False, "nav.destination": "", "nav.eta_min": 0, "nav.distance_km": 0.0,
               "nav.waypoints": [], "nav.route": []}


def cancel(ctx: Ctx) -> Outcome:
    if not ctx.state.get("nav.active"):
        return ok(ctx, "当前没有在导航", app="navigation")
    return ok(ctx, "已结束导航", CLEAR_ROUTE, app="navigation")


def close(ctx: Ctx) -> Outcome:
    return Outcome(updates={**CLEAR_ROUTE, "screen.app": ""}, message="已关闭导航")


def query_poi(ctx: Ctx) -> Outcome:
    keyword = ctx.arg("KeyWord") or ctx.arg("Keyword")
    if not keyword:
        raise InvalidParamError("Navigation.SearchPOI 需要 KeyWord")
    return _list(ctx, str(keyword), verb="")


CURRENT_TYPES = ("4", "当前", "当前位置", "我的位置")


def address_by_type(ctx: Ctx) -> Outcome:
    if str(ctx.arg("Type") or "") in CURRENT_TYPES:
        here = ctx.state.get("location")
        name = here.get("name", "") if isinstance(here, dict) else str(here)
        return ok(ctx, f"你现在在{name}", data={"poi": [resolve(ctx.state, name)]}, app="navigation")
    kind = FAVORITE_TYPES.get(str(ctx.arg("Type") or "家"), "家")
    address = (ctx.state.get("nav.favorites") or {}).get(kind, "")
    return ok(ctx, f"{kind}的地址是{address}" if address else f"还没有设置{kind}的地址",
              data={"poi": [resolve(ctx.state, address)] if address else []}, app="navigation")


# ---------- 手车互联：手机推送目的地 ----------


def push_destination(ctx: Ctx) -> Outcome:
    keyword = str(ctx.arg("Keyword") or "").strip()
    if not keyword:
        raise InvalidParamError("PhoneLink.PushDestination 需要 Keyword")
    dest = resolve(ctx.state, keyword)
    preview = route_updates(ctx.state, dest, [], ctx.state.get("nav.route_strategy") or "推荐")
    card = {
        "name": dest["name"],
        "distance_km": preview["nav.distance_km"],
        "eta_min": preview["nav.eta_min"],
        "route": preview["nav.route"],
        "from": "手机",
    }
    message = f"收到手机推送的目的地：{dest['name']}，约{card['distance_km']}公里，已在导航里弹出路线卡片"
    return ok(ctx, message, {"nav.pushed": card}, {"poi": [dest]}, app="navigation")


def accept_push(ctx: Ctx) -> Outcome:
    card = ctx.state.get("nav.pushed")
    if not card:
        return Outcome(st.FAILED, message="没有待确认的手机推送")
    outcome = _navigate(ctx, resolve(ctx.state, card["name"]))
    return Outcome(outcome.status, {**outcome.updates, "nav.pushed": None}, outcome.data, outcome.message)


def dismiss_push(ctx: Ctx) -> Outcome:
    if not ctx.state.get("nav.pushed"):
        return ok(ctx, "没有待确认的手机推送", app="navigation")
    return ok(ctx, "已忽略手机推送的目的地", {"nav.pushed": None}, app="navigation")


HANDLERS: dict[tuple[str, str], Handler] = {
    ("navigation", "Navigation.SearchPoi"): search_poi,
    ("navigation", "Navigation.NavigationToFavorite"): to_favorite,
    ("navigation", "Navigation.SearchAround"): search_around,
    ("navigation", "Navigation.AlongSearch"): add_waypoint,
    ("navigation", "Navigation.AddWaypointsByName"): add_waypoint,
    ("navigation", "Navigation.SwitchRouteStrategy"): switch_strategy,
    ("navigation", "Navigation.DeleteAllWaypoints"): delete_waypoints,
    ("navigation", "Navigation.QueryDistanceTime"): query_distance,
    ("navigation", "Navigation.ZoomIn"): zoom(1),
    ("navigation", "Navigation.ZoomOut"): zoom(-1),
    ("navigation", "Navigation.OpenOverview"): overview,
    ("navigation", "Navigation.Cancel"): cancel,
    ("navigation", "Navigation.Close"): close,
    ("nav_query", "Navigation.SearchPOI"): query_poi,
    ("nav_query", "Navigation.GetAddressByType"): address_by_type,
    ("phone_link", "PhoneLink.PushDestination"): push_destination,
    ("phone_link", "PhoneLink.AcceptPush"): accept_push,
    ("phone_link", "PhoneLink.DismissPush"): dismiss_push,
}
