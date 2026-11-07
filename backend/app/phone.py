"""手机端（/phone 页面）：查车况、远程开空调、锁车、推送目的地到车机。

模拟器里每个 WS 连接是一辆车（一个会话）。手机绑定最近连上的那辆车：
- ``GET /api/phone/status``：车况摘要，手机页面轮询。
- ``POST /api/phone/action {action, value?}``：手机只能发白名单里的几种操作，
  落到车上就是一次 ``source=phone`` 的 invoke，和触屏、大模型走同一个执行器；
  执行结果、车况和链路追踪会推给这辆车的 WS，车机界面实时看到变化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.session import Session
from backend.protocol import status as st

PHONE_SEAT = "主驾"  # 手机操作按车主（主驾）身份执行
MAX_KEYWORD_LEN = 40
STATUS_PATHS = {
    "battery_pct": "battery_pct",
    "range_km": "range_km",
    "location": "location",
    "gear": "gear",
    "speed_kmh": "speed_kmh",
    "doors_locked": "doors_locked",
    "outside_temp": "outside_temp",
    "cabin_temp": "cabin_temp",
    "ac_on": "ac.on",
    "ac_temp": "ac.temp.主驾",
    "windows": "windows",
    "nav_active": "nav.active",
    "nav_destination": "nav.destination",
    "nav_pushed": "nav.pushed",
    "network": "network",
    "now": "now",
}


class CarLink(Protocol):
    """手机能看到的一辆车：会话 + 往车机推消息的方法。"""

    session: Session

    async def send(self, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class PhoneAction:
    tool: str
    command: str
    label: str
    value_key: str | None = None  # 需要 value 时，value 填到这个参数里


ACTIONS: dict[str, PhoneAction] = {
    "ac_on": PhoneAction("switch_device", "Device.Open", "远程打开空调"),
    "ac_off": PhoneAction("switch_device", "Device.Close", "远程关闭空调"),
    "ac_temp": PhoneAction("climate", "ACCtrl.SetTemperature", "远程设置空调温度", "Number"),
    "lock": PhoneAction("body", "CarCtrl.DoorLocked", "远程锁车"),
    "unlock": PhoneAction("body", "CarCtrl.DoorUnlocked", "远程解锁"),
    "windows_close": PhoneAction("body", "WindowCtrl.Close", "远程关窗"),
    "push": PhoneAction("phone_link", "PhoneLink.PushDestination", "推送目的地", "Keyword"),
}


class PhoneError(ValueError):
    """手机请求不合法（未知操作、缺参数、车不在线）。"""


def build_args(action: str, value: Any) -> tuple[str, dict[str, Any], str]:
    """校验手机请求，返回 (工具, 参数, 中文标签)。"""
    spec = ACTIONS.get(action)
    if spec is None:
        raise PhoneError(f"手机不支持这个操作：{action}")
    args: dict[str, Any] = {"command": spec.command}
    if spec.tool == "switch_device":
        args["Device"] = "空调"
    if spec.command == "WindowCtrl.Close":
        args["Position"] = "所有"
    if spec.value_key == "Number":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 16 <= value <= 32:
            raise PhoneError("温度必须是 16–32 之间的数字")
        args["Number"] = int(value)
    elif spec.value_key == "Keyword":
        text = str(value or "").strip()
        if not text or len(text) > MAX_KEYWORD_LEN:
            raise PhoneError(f"目的地不能为空，最长 {MAX_KEYWORD_LEN} 个字")
        args["Keyword"] = text
    return spec.tool, args, spec.label


def summarize(session: Session) -> dict[str, Any]:
    return {key: session.state.get(path) for key, path in STATUS_PATHS.items()}


class PhoneBridge:
    """登记在线的车；手机总是操作最近连上的那一辆。"""

    def __init__(self) -> None:
        self._cars: list[CarLink] = []

    def attach(self, car: CarLink) -> None:
        self._cars = [*self._cars, car]

    def detach(self, car: CarLink) -> None:
        self._cars = [c for c in self._cars if c is not car]

    @property
    def car(self) -> CarLink | None:
        return self._cars[-1] if self._cars else None

    def status(self) -> dict[str, Any]:
        car = self.car
        if car is None:
            return {"connected": False, "state": None}
        return {"connected": True, "session_id": car.session.session_id, "state": summarize(car.session)}

    async def act(self, action: str, value: Any = None) -> dict[str, Any]:
        tool, args, label = build_args(action, value)
        car = self.car
        if car is None:
            raise PhoneError("车机不在线：先在电脑上打开座舱页面")
        call, diffs, trace = car.session.invoke(tool, args, PHONE_SEAT, source="phone")
        await car.send({"type": "invoke_result", "call": call.to_dict(), "source": "phone", "label": label})
        await car.send({"type": "state", "state": car.session.state.to_dict(), "diff": [d.to_dict() for d in diffs]})
        await car.send({"type": "trace", **trace.to_dict()})
        return {
            "ok": st.is_ok(call.status),
            "status": call.status,
            "message": call.result.get("message", ""),
            "label": label,
            "state": summarize(car.session),
        }

