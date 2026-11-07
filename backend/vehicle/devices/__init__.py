"""虚拟设备：每个模块导出 HANDLERS（指令名 → 处理函数），这里合并成路由表。"""

from __future__ import annotations

from backend.vehicle.devices import body, climate, drive, lighting, query, seat, switch, system
from backend.vehicle.devices.base import Ctx, Handler, Outcome, record

HANDLERS: dict[str, Handler] = {}
for _module in (body, climate, seat, lighting, drive, query, system, switch):
    _overlap = HANDLERS.keys() & _module.HANDLERS.keys()
    if _overlap:
        raise RuntimeError(f"处理函数重复：{sorted(_overlap)}")
    HANDLERS.update(_module.HANDLERS)

__all__ = ["HANDLERS", "Ctx", "Handler", "Outcome", "record"]
