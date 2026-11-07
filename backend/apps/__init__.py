"""模拟 App：每个模块导出 HANDLERS（(工具, 指令) → 处理函数），这里合并成路由表。

App 处理函数和车端处理函数是同一种纯函数 ``(ctx) -> Outcome``，由执行器统一调度，
所以大模型调用和触屏点击（WS invoke，source=touch）走的是同一条路径。
用到的 App 会通过 ``screen.app`` 切到前台。
"""

from __future__ import annotations

from backend.apps import lite, meituan, music, navigation, phone
from backend.apps.common import TOOL_APP, front
from backend.vehicle.devices.base import Ctx, Handler, Outcome, record

APP_HANDLERS: dict[tuple[str, str], Handler] = {}
for _module in (navigation, music, phone, meituan, lite):
    _overlap = APP_HANDLERS.keys() & _module.HANDLERS.keys()
    if _overlap:
        raise RuntimeError(f"App 处理函数重复：{sorted(_overlap)}")
    APP_HANDLERS.update(_module.HANDLERS)


def record_app(ctx: Ctx) -> Outcome:
    """App 的长尾指令：记下参数，并把对应 App 切到前台。"""
    outcome = record(ctx)
    updates = {**outcome.updates, **(front(ctx) if ctx.tool in TOOL_APP else {})}
    return Outcome(outcome.status, updates, outcome.data, outcome.message)


__all__ = ["APP_HANDLERS", "record_app"]
