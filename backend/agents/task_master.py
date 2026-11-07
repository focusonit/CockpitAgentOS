"""task_master 子代理：定时与条件任务。

确定性解析，不调用大模型：从 task 文本里认出时间或条件（相对时间、几点、下班后、到达某地、电量低于），
剩下的部分就是到点要做的事。任务存在 ``tasks``，由模拟时钟（backend/clock.py）判断到期，
到期时交给大脑当作系统事件执行。也支持“我有哪些提醒”和“把它取消了”。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from backend.apps.common import CN_NUM, cn_number, now_of
from backend.clock import CANCELLED, PENDING
from backend.protocol import status as st
from backend.vehicle.devices.base import Ctx, Outcome

OFF_WORK = (18, 0)
LIST_WORDS = ("哪些", "有什么", "列出", "查看", "看看", "列表", "几个")
CANCEL_WORDS = ("取消", "删除", "删掉", "不用提醒", "去掉")
DAY_OFFSET = {"今天": 0, "今晚": 0, "明天": 1, "明早": 1, "明晚": 1, "后天": 2}
PM_WORDS = ("下午", "晚上", "今晚", "明晚", "傍晚")

_RELATIVE = re.compile(rf"(?:(半)|({CN_NUM})(个半|个)?)\s*(分钟|小时|钟头)(?:之?后|以后)")
_CLOCK = re.compile(rf"(今天|今晚|明天|明早|明晚|后天)?\s*(早上|上午|中午|下午|晚上|傍晚)?\s*({CN_NUM})\s*[点:：时]\s*(半|{CN_NUM}分?)?")
_ARRIVE = re.compile(r"(?:等?到了?|到达|快到)([^，,。的时后]{1,12}?)(?:的时候|时|后|以后|之后)")
_BATTERY = re.compile(rf"电量(?:低于|少于|不到)\s*({CN_NUM})\s*%?")


def _relative(match: re.Match[str], now: datetime) -> datetime:
    half, number, suffix, unit = match.groups()
    value = 0.5 if half else float(cn_number(number) or 0) + (0.5 if suffix == "个半" else 0)
    minutes = value * (60 if unit in ("小时", "钟头") else 1)
    return now + timedelta(minutes=minutes)


def _clock(match: re.Match[str], now: datetime) -> datetime | None:
    day, period, hour_text, minute_text = match.groups()
    hour = cn_number(hour_text)
    if hour is None or hour > 24:
        return None
    if (period in PM_WORDS or day in PM_WORDS) and hour < 12:
        hour += 12
    if period == "中午" and hour < 6:
        hour += 12
    minute = 30 if minute_text == "半" else (cn_number((minute_text or "").removesuffix("分")) or 0)
    target = now.replace(hour=hour % 24, minute=min(minute, 59), second=0) + timedelta(days=DAY_OFFSET.get(day or "", 0))
    if not day and target <= now:
        target += timedelta(days=1)
    return target


def parse_trigger(text: str, now: datetime) -> tuple[datetime | None, str, str]:
    """返回 (到期时间, 条件, 去掉时间/条件后的文字)。"""
    for pattern, kind in ((_RELATIVE, "relative"), (_CLOCK, "clock")):
        match = pattern.search(text)
        if match:
            due = _relative(match, now) if kind == "relative" else _clock(match, now)
            if due is not None:
                return due, "", _strip(text, match)
    if "下班" in text:
        due = now.replace(hour=OFF_WORK[0], minute=OFF_WORK[1], second=0)
        due = due if due > now else due + timedelta(days=1)
        return due, "", re.sub(r"等?下班(之?后|以后|的时候|时)?", "", text)
    match = _ARRIVE.search(text)
    if match:
        return None, f"到达{match.group(1).strip()}", _strip(text, match)
    match = _BATTERY.search(text)
    if match:
        return None, f"电量低于{cn_number(match.group(1))}%", _strip(text, match)
    return None, "", text


def _strip(text: str, match: re.Match[str]) -> str:
    return (text[: match.start()] + text[match.end():]).strip(" ，,。")


def _action(rest: str) -> tuple[str, str]:
    """(kind, action)：带“提醒”的是提醒，其余是到点执行的车控或操作。"""
    rest = re.sub(r"^(再|就|帮我|请|给我)+", "", rest.strip(" ，,。")).strip()
    if "提醒" in rest:
        action = re.sub(r"(提醒我?|叫我|通知我)(一下)?", "", rest).strip(" ，,。") or "提醒"
        return "reminder", action
    return "action", rest or "提醒"


def _describe(task: dict[str, Any]) -> str:
    when = task["condition"] or datetime.fromisoformat(task["due"]).strftime("%m月%d日 %H:%M")
    verb = "提醒" if task["kind"] == "reminder" else "执行"
    return f"{when}{verb}：{task['action']}"


def _pending(state: Any) -> list[dict[str, Any]]:
    return [t for t in state.get("tasks") or [] if t["status"] == PENDING]


def list_tasks(ctx: Ctx) -> Outcome:
    tasks = _pending(ctx.state)
    if not tasks:
        return Outcome(data={"tasks": []}, message="目前没有待执行的提醒或任务")
    text = "；".join(f"{i}. {_describe(t)}" for i, t in enumerate(tasks, 1))
    return Outcome(data={"tasks": tasks}, message=f"你有{len(tasks)}个待执行的任务：{text}")


def cancel_task(ctx: Ctx) -> Outcome:
    task_text = ctx.arg("task")
    tasks = _pending(ctx.state)
    if not tasks:
        return Outcome(st.NOT_FOUND, message="没有可以取消的任务")
    index = re.search(rf"第\s*({CN_NUM})\s*[个条]", task_text)
    named = [t for t in tasks if t["action"] and t["action"] in task_text]
    if index and 1 <= (cn_number(index.group(1)) or 0) <= len(tasks):
        targets = [tasks[(cn_number(index.group(1)) or 1) - 1]]
    elif "全部" in task_text or "所有" in task_text:
        targets = tasks
    else:  # 没说是哪个：取消提到的那个，否则取消最近创建的
        targets = named[:1] or [max(tasks, key=lambda t: t["id"])]
    ids = {t["id"] for t in targets}
    updated = [{**t, "status": CANCELLED} if t["id"] in ids else t for t in ctx.state.get("tasks")]
    return Outcome(updates={"tasks": updated}, data={"cancelled": targets},
                   message="已取消：" + "；".join(_describe(t) for t in targets))


def create_task(ctx: Ctx) -> Outcome:
    text = ctx.arg("task")
    now = now_of(ctx)
    due, condition, rest = parse_trigger(text, now)
    if due is None and not condition:
        return Outcome(st.INVALID_PARAM, message="没听出什么时候做，告诉我具体时间或条件")
    kind, action = _action(rest)
    tasks = list(ctx.state.get("tasks") or [])
    task = {
        "id": max((t["id"] for t in tasks), default=0) + 1,
        "text": text,
        "kind": kind,
        "action": action,
        "due": due.isoformat(timespec="seconds") if due else None,
        "condition": condition,
        "status": PENDING,
        "created_at": now.isoformat(timespec="seconds"),
        "seat": ctx.speaker,
    }
    return Outcome(updates={"tasks": [*tasks, task]}, data={"task": task}, message=f"好的，{_describe(task)}")


def task_master(ctx: Ctx) -> Outcome:
    text = ctx.arg("task")
    if any(w in text for w in CANCEL_WORDS):
        return cancel_task(ctx)
    if any(w in text for w in LIST_WORDS) and ("提醒" in text or "任务" in text or "定时" in text):
        return list_tasks(ctx)
    return create_task(ctx)
