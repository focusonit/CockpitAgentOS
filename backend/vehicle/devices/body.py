"""车身：车窗、天窗、遮阳帘、尾门、门锁、后视镜、雨刮、充电口。开度都是 0–100 的百分比。"""

from __future__ import annotations

from backend.protocol import status as st
from backend.vehicle.devices.base import Ctx, Handler, Outcome, per_seat, record
from backend.vehicle.state import SEATS

FULL = 100
VENT_PERCENT = 10
TILT_PERCENT = 20
WIPER_SPEEDS = ("关", "低", "中", "高")


def window_open(ctx: Ctx) -> Outcome:
    seats = ctx.seats()
    percent = int(ctx.arg("ActionPercent", FULL))
    max_speed = ctx.limits.get("window_full_open_max_speed", 120)
    partial = ctx.limits.get("window_partial_percent", 30)
    if ctx.state.get("speed_kmh") >= max_speed and percent > partial:
        return Outcome(
            status=st.PART_SUCCEED,
            updates=per_seat("windows", "", seats, partial),
            message=f"车速较高，车窗只打开了 {partial}%",
        )
    return Outcome(updates=per_seat("windows", "", seats, percent), message="车窗已打开")


def window_close(ctx: Ctx) -> Outcome:
    return Outcome(updates=per_seat("windows", "", ctx.seats(), 0), message="车窗已关闭")


def window_mode(ctx: Ctx) -> Outcome:
    return Outcome(updates=per_seat("windows", "", SEATS, VENT_PERCENT), message="车窗已开启通风模式")


def sunshade(value: int, verb: str) -> Handler:
    def handle(ctx: Ctx) -> Outcome:
        seats = ctx.seats()
        return Outcome(updates=per_seat("sunshades", "", seats, value), message=f"{'、'.join(seats)}侧窗遮阳帘已{verb}")

    return handle


def stop(ctx: Ctx) -> Outcome:
    return Outcome(message="已停止")


def resume(ctx: Ctx) -> Outcome:
    return Outcome(message="已继续")


def opening(path: str, value: int, message: str) -> Handler:
    return lambda ctx: Outcome(updates={path: value}, message=message)


def skylight_open(ctx: Ctx) -> Outcome:
    if ctx.arg("Mode") == "起翘":
        return Outcome(updates={"sunroof": TILT_PERCENT}, message="天窗已起翘")
    return Outcome(updates={"sunroof": FULL}, message="天窗已打开")


def backdoor_degree(ctx: Ctx) -> Outcome:
    return Outcome(updates={"backdoor": int(ctx.arg("Number", FULL))}, message="尾门开启角度已设置")


def wiper_speed(ctx: Ctx) -> Outcome:
    level = int(ctx.arg("Number", 2))
    updates = {"wiper.on": level > 0}
    if level > 0:
        updates["wiper.speed"] = WIPER_SPEEDS[level]
    return Outcome(updates=updates, message=f"雨刮已调到{WIPER_SPEEDS[level]}档" if level else "雨刮已关闭")


HANDLERS: dict[str, Handler] = {
    "WindowCtrl.Open": window_open,
    "WindowCtrl.Close": window_close,
    "WindowCtrl.OpenMode": window_mode,
    "WindowCtrl.Pause": stop,
    "SkylightCtrl.Open": skylight_open,
    "SkylightCtrl.Close": opening("sunroof", 0, "天窗已关闭"),
    "SkylightCtrl.Pause": stop,
    "SkylightCtrl.Resume": resume,
    "CurtainCtrl.Open": opening("curtain", FULL, "遮阳帘已打开"),
    "CurtainCtrl.Close": opening("curtain", 0, "遮阳帘已关闭"),
    "CurtainCtrl.Pause": stop,
    "CurtainCtrl.Resume": resume,
    "SunshadeCtrl.Open": sunshade(FULL, "收起"),
    "SunshadeCtrl.Close": sunshade(0, "拉上"),
    "SkyCurtainCtrl.Open": lambda ctx: Outcome(updates={"sunroof": FULL, "curtain": FULL}, message="天窗和遮阳帘已打开"),
    "SkyCurtainCtrl.Close": lambda ctx: Outcome(updates={"sunroof": 0, "curtain": 0}, message="天窗和遮阳帘已关闭"),
    "BackdoorCtrl.Open": opening("backdoor", FULL, "尾门已打开"),
    "BackdoorCtrl.Close": opening("backdoor", 0, "尾门已关闭"),
    "BackdoorCtrl.Pause": stop,
    "BackdoorCtrl.Resume": resume,
    "CarCtrl.SetBackdoorDegree": backdoor_degree,
    "CarCtrl.DoorUnlocked": opening("doors_locked", False, "车门已解锁"),
    "CarCtrl.DoorLocked": opening("doors_locked", True, "车门已上锁"),
    "CarCtrl.SetRearviewMirror": record,
    "WiperCtrl.SetWiperSpeed": wiper_speed,
    "CarCtrl.OpenChargePort": opening("charge_port", True, "充电口盖已打开"),
    "CarCtrl.CloseChargePort": opening("charge_port", False, "充电口盖已关闭"),
}
