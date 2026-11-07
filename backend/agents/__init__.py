"""子代理：参数只有 task（自然语言），由子代理自己完成多步操作。

M3 的子代理都是确定性的规则实现（不调用大模型），处理函数签名和车端/App 一样是 ``(ctx) -> Outcome``，
由执行器调度；ctx.args 只有 ``{"task": str}``。
"""

from __future__ import annotations

from backend.agents.experts import car_expert, home, web_search
from backend.agents.meituan import meituan_agent, place_order_agent
from backend.agents.task_master import task_master
from backend.vehicle.devices.base import Handler

SUBAGENTS: dict[str, Handler] = {
    "task_master": task_master,
    "meituan": meituan_agent,
    "meituan_place_order": place_order_agent,
    "car_expert": car_expert,
    "web_search": web_search,
    "home": home,
}
