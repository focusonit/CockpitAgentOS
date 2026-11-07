"""模拟时钟：车况里的 ``now`` 只在这里往前走。

``advance(state, minutes)`` 推进时间，并让依赖时间的状态跟着变化：导航剩余里程和时间
（到达时结束导航并改当前位置）、播放进度（播完切下一首）、美团订单状态。
到期的定时任务不会自动执行，由调用方用 ``fire_task`` 取出，交给大脑当作系统事件处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from backend.apps.common import now_of
from backend.apps.meituan import order_status
from backend.vehicle.state import Diff, VehicleState

PENDING = "待执行"
DONE = "已完成"
CANCELLED = "已取消"


@dataclass(frozen=True)
class Tick:
    state: VehicleState
    diffs: tuple[Diff, ...]
    events: tuple[str, ...]  # 例如“已到达故宫博物院”
    due: tuple[dict[str, Any], ...]  # 到期还没执行的任务


def _nav(state: VehicleState, minutes: float) -> tuple[dict[str, Any], list[str]]:
    if not state.get("nav.active"):
        return {}, []
    eta = float(state.get("nav.eta_min") or 0)
    distance = float(state.get("nav.distance_km") or 0)
    left = eta - minutes
    if left > 0:
        ratio = left / eta if eta else 0
        return {"nav.eta_min": round(left), "nav.distance_km": round(distance * ratio, 1)}, []
    destination = state.get("nav.destination")
    updates = {"nav.active": False, "nav.eta_min": 0, "nav.distance_km": 0.0, "nav.waypoints": [], "nav.route": [],
               "location": destination, "speed_kmh": 0}
    return updates, [f"已到达{destination}"]


def _media(state: VehicleState, minutes: float) -> dict[str, Any]:
    if not state.get("media.playing"):
        return {}
    duration = int(state.get("media.duration_s") or 0)
    progress = int(state.get("media.progress_s") or 0) + int(minutes * 60)
    queue = state.get("media.queue") or []
    if not duration or progress < duration:
        return {"media.progress_s": progress}
    if state.get("media.kind") == "music" and queue:
        index = (int(state.get("media.index") or 0) + 1) % len(queue)
        song = queue[index]
        return {"media.index": index, "media.title": song["title"], "media.artist": song["artist"],
                "media.album": song.get("album", ""), "media.duration_s": song["duration_s"], "media.progress_s": 0}
    return {"media.progress_s": duration, "media.playing": False}


def _orders(state: VehicleState, now: datetime) -> dict[str, Any]:
    orders = state.get("meituan.orders") or []
    updated = []
    for order in orders:
        placed = datetime.fromisoformat(order["placed_at"])
        updated.append({**order, "status": order_status(order, (now - placed).total_seconds() / 60)})
    return {"meituan.orders": updated} if updated != orders else {}


def due_tasks(state: VehicleState) -> list[dict[str, Any]]:
    now = now_of(state)
    tasks = [t for t in state.get("tasks") or [] if t["status"] == PENDING and t.get("due")]
    return sorted((t for t in tasks if datetime.fromisoformat(t["due"]) <= now), key=lambda t: t["due"])


def advance(state: VehicleState, minutes: float) -> Tick:
    if minutes <= 0:
        raise ValueError("只能往前推进时间")
    now = now_of(state) + timedelta(minutes=minutes)
    nav, events = _nav(state, minutes)
    updates = {"now": now.isoformat(timespec="seconds"), **nav, **_media(state, minutes)}
    state, diffs = state.apply(updates)
    state, order_diffs = state.apply(_orders(state, now))
    # 到达目的地时，触发条件是“到达某地”的任务也算到期
    arrived = [e.removeprefix("已到达") for e in events]
    if arrived:
        state, task_diffs = state.apply({"tasks": [_arrive(t, arrived[0], now, state.get("nav.favorites") or {}) for t in state.get("tasks") or []]})
        order_diffs = [*order_diffs, *task_diffs]
    return Tick(state, (*diffs, *order_diffs), tuple(events), tuple(due_tasks(state)))


def _arrive(task: dict[str, Any], place: str, now: datetime, favorites: dict[str, str]) -> dict[str, Any]:
    condition = task.get("condition") or ""
    if task["status"] != PENDING or not condition.startswith("到达"):
        return task
    target = condition.removeprefix("到达")
    if target and (target in place or place in target or favorites.get(target) == place):
        return {**task, "due": now.isoformat(timespec="seconds")}
    return task


def next_task(state: VehicleState) -> dict[str, Any] | None:
    """最早的待执行任务（有时间的按时间，条件任务排在后面）。"""
    tasks = [t for t in state.get("tasks") or [] if t["status"] == PENDING]
    return min(tasks, key=lambda t: (t.get("due") is None, t.get("due") or "", t["id"]), default=None)


def fire_task(state: VehicleState, task: dict[str, Any]) -> tuple[VehicleState, list[Diff]]:
    """把时钟拨到任务时间（没到的话）并把任务标成已完成。"""
    updates: dict[str, Any] = {"tasks": [{**t, "status": DONE} if t["id"] == task["id"] else t for t in state.get("tasks")]}
    if task.get("due") and datetime.fromisoformat(task["due"]) > now_of(state):
        updates["now"] = task["due"]
    new_state, diffs = state.apply(updates)
    return new_state, list(diffs)


def event_text(task: dict[str, Any]) -> str:
    if task["kind"] == "reminder":
        return f"定时提醒到点：{task['action']}。请直接提醒用户，不调用工具"
    return f"定时任务到点：{task['action']}。现在执行这件事"
