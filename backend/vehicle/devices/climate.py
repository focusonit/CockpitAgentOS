"""空调与香氛。温度 16–32℃，风量 1–8 档，每个座位的温度独立。"""

from __future__ import annotations

from backend.protocol.spec import InvalidParamError
from backend.vehicle.devices.base import LEVEL_NAMES, Ctx, Handler, Outcome, adjust, per_seat

TEMP_MIN, TEMP_MAX = 16, 32
VOLUME_MIN, VOLUME_MAX = 1, 8
FRAGRANCES = ("香氛1", "香氛2", "香氛3")


def set_temperature(ctx: Ctx) -> Outcome:
    seats = ctx.seats("Area")
    current = ctx.state.get(f"ac.temp.{seats[0]}")
    value = adjust(ctx, current, TEMP_MIN, TEMP_MAX)
    updates = {"ac.on": True, **per_seat("ac.temp", "", seats, value)}
    return Outcome(updates=updates, message=f"{'、'.join(seats)}温度已调到 {value}℃")


def set_air_volume(ctx: Ctx) -> Outcome:
    value = adjust(ctx, ctx.state.get("ac.air_volume"), VOLUME_MIN, VOLUME_MAX)
    return Outcome(updates={"ac.air_volume": value}, message=f"风量已调到 {value} 档")


def _mode_updates(mode: str | None, on: bool) -> dict[str, object]:
    if mode is None:
        return {"ac.on": on}
    if mode == "内循环":
        return {"ac.recirc": "内循环" if on else "外循环"}
    if mode == "外循环":
        return {"ac.recirc": "外循环" if on else "内循环"}
    if mode in ("除霜", "除雾"):
        return {"ac.defrost": on}
    if mode == "AC":
        return {"ac.compressor": on, **({"ac.on": True} if on else {})}
    if mode == "自动空调":
        return {"ac.mode": "自动" if on else "手动", **({"ac.on": True} if on else {})}
    if mode == "空气净化":
        return {"ac.purifier": on}
    raise InvalidParamError(f"未知空调模式：{mode}")


def open_mode(ctx: Ctx) -> Outcome:
    mode = ctx.arg("Mode")
    return Outcome(updates=_mode_updates(mode, True), message=f"已打开{mode or '空调'}")


def close_mode(ctx: Ctx) -> Outcome:
    mode = ctx.arg("Mode")
    return Outcome(updates=_mode_updates(mode, False), message=f"已关闭{mode or '空调'}")


def wind_direction(ctx: Ctx) -> Outcome:
    direction = ctx.arg("Direction", "自动")
    return Outcome(updates={"ac.wind_direction": direction}, message=f"出风方向已设为吹{direction}")


def fresh_air(ctx: Ctx) -> Outcome:
    mode = ctx.arg("Mode", "开")
    return Outcome(updates={"ac.fresh_air": mode != "关"}, message=f"新风已{'关闭' if mode == '关' else '打开'}")


def set_fragrance(ctx: Ctx) -> Outcome:
    level = int(ctx.arg("Number", 2))
    if level == 0:
        return Outcome(updates={"fragrance": "关"}, message="香氛已关闭")
    current = ctx.state.get("fragrance")
    updates = {"fragrance_level": LEVEL_NAMES[level], "fragrance": FRAGRANCES[0] if current == "关" else current}
    return Outcome(updates=updates, message=f"香氛浓度已调到{LEVEL_NAMES[level]}")


def fragrance_type(ctx: Ctx) -> Outcome:
    name = ctx.arg("Name")
    if not name:
        current = ctx.state.get("fragrance")
        index = FRAGRANCES.index(current) + 1 if current in FRAGRANCES else 0
        name = FRAGRANCES[index % len(FRAGRANCES)]
    return Outcome(updates={"fragrance": name}, message=f"香型已切换为{name}")


HANDLERS: dict[str, Handler] = {
    "ACCtrl.SetTemperature": set_temperature,
    "ACCtrl.SetAirVolume": set_air_volume,
    "ACCtrl.OpenMode": open_mode,
    "ACCtrl.CloseMode": close_mode,
    "ACCtrl.SetWindDirection": wind_direction,
    "ACCtrl.SetFreshAir": fresh_air,
    "FragranceCtrl.SetFragrance": set_fragrance,
    "FragranceCtrl.SetFragranceType": fragrance_type,
}
