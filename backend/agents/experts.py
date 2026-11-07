"""car_expert / web_search / home 子代理：查模拟知识库或搜索结果，home 记下家居设备开关。

都是确定性实现，不调用大模型、不联网。car_expert 会结合当前车况（报警、胎压）作答。
"""

from __future__ import annotations

from typing import Any

from backend.apps.common import load
from backend.protocol import status as st
from backend.vehicle.devices.base import Ctx, Outcome
from backend.vehicle.state import TIRES

STANDARD_PRESSURE = 2.3


def _vehicle_facts(state: Any) -> str:
    facts = []
    warnings = state.get("warnings") or []
    if warnings:
        facts.append("当前报警：" + "、".join(warnings))
    pressures = state.get("tire_pressure") or {}
    low = [f"{t}胎 {pressures[t]} bar" for t in TIRES if float(pressures.get(t, 2.5)) < STANDARD_PRESSURE]
    if low:
        facts.append("胎压偏低：" + "、".join(low) + "（标准 2.5 bar）")
    return "；".join(facts)


def car_expert(ctx: Ctx) -> Outcome:
    text = ctx.arg("task")
    kb = load("knowledge")
    facts = _vehicle_facts(ctx.state)
    entry = next((e for e in kb["entries"] if any(k in text for k in e["keywords"])), None)
    if entry is None and facts and any(w in text for w in ("灯", "报警", "要紧", "能开", "继续开", "什么意思")):
        entry = kb["entries"][0] if "胎压" in facts else None  # 问“这个灯”时，按当前报警作答
    answer = entry["answer"] if entry else kb["fallback"]
    if facts and entry and any(k in facts for k in entry["keywords"] + ["胎压"]):
        answer = f"{facts}。{answer}"
    data = {"answer": answer, "setting": entry["setting"] if entry else ""}
    return Outcome(data=data, message=answer)


def web_search(ctx: Ctx) -> Outcome:
    text = ctx.arg("task")
    if not ctx.state.get("network"):
        return Outcome(st.NOT_NET, message="没有网络，暂时没法联网搜索")
    db = load("search")
    entry = next((e for e in db["entries"] if all(k in text for k in e["keywords"])), None)
    if entry is None:
        # 模拟搜索库里没有：报 NotFound，别让链路上显示 Success 却答不出东西
        return Outcome(st.NOT_FOUND, data={"answer": ""}, message=db["fallback"])
    return Outcome(data={"answer": entry["answer"]}, message=entry["answer"])


HOME_DEVICES = ("空调", "灯", "热水器", "扫地机器人", "窗帘", "电饭煲", "加湿器", "净化器")


def home(ctx: Ctx) -> Outcome:
    text = ctx.arg("task")
    if not ctx.state.get("network"):
        return Outcome(st.NOT_NET, message="没有网络，连不上家里的设备")
    devices = [d for d in HOME_DEVICES if d in text]
    if not devices:
        return Outcome(st.NOT_FOUND, message="家里没有找到这个设备")
    on = not any(w in text for w in ("关", "停"))
    current = dict(ctx.state.get("home") or {})
    updated = {**current, **{d: on for d in devices}}
    verb = "打开" if on else "关闭"
    return Outcome(updates={"home": updated}, data={"home": updated}, message=f"已{verb}家里的{'、'.join(devices)}")
