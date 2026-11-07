"""通用开关 Device.Open / Device.Close：按 config/devices.yaml 把设备名映射到状态路径。"""

from __future__ import annotations

from backend.protocol.spec import InvalidParamError
from backend.vehicle.devices.base import Ctx, Handler, Outcome, device_table, record


def toggle(on: bool) -> Handler:
    verb = "打开" if on else "关闭"

    def handler(ctx: Ctx) -> Outcome:
        name = str(ctx.arg("Device") or "").strip()
        if not name:
            raise InvalidParamError("Device 不能为空")
        entry = device_table().get(name)
        if entry is None:
            return record(ctx, key=f"设备.{name}", value=on)
        value = entry["open"] if on else entry["close"]
        path: str = entry["path"]
        if "{seat}" in path:
            updates = {path.format(seat=seat): value for seat in ctx.seats()}
        else:
            updates = {path: value}
        return Outcome(updates=updates, message=f"已{verb}{entry['name']}")

    return handler


HANDLERS: dict[str, Handler] = {"Device.Open": toggle(True), "Device.Close": toggle(False)}
