"""座椅：加热、通风、按摩（0–3 档）、前后位置（0–100）、靠背角度（度）、零重力、记忆位置。"""

from __future__ import annotations

from backend.vehicle.devices.base import LEVEL_NAMES, Ctx, Handler, Outcome, clamp, per_seat, record

DEFAULT_LEVEL = 2
POSITION_STEP = 10
BACKREST_UPRIGHT, BACKREST_FLAT = 100, 170
BACKREST_MIN = 90
FIELDS = {"座椅": "position", "坐垫": "position", "靠背": "backrest"}
FORWARD = {"前", "上", "调高", "直立"}
BACKWARD = {"后", "下", "调低"}


def level_setter(field: str, label: str) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        level = int(ctx.arg("Number", DEFAULT_LEVEL))
        seats = ctx.seats()
        text = f"{label}已关闭" if level == 0 else f"{label}已调到{LEVEL_NAMES[level]}档"
        return Outcome(updates=per_seat("seats", field, seats, level), message=f"{'、'.join(seats)}{text}")

    return handler


def set_position(ctx: Ctx) -> Outcome:
    direction = ctx.arg("Direction")
    area = ctx.arg("Area")
    if area is None:
        area = "靠背" if direction in ("放平", "直立") else "座椅"
    field = FIELDS.get(area)
    if field is None:  # 腰托、头枕：没有专门状态
        return record(ctx)
    seats = ctx.seats()
    updates: dict[str, int] = {}
    for seat in seats:
        current = ctx.state.get(f"seats.{seat}.{field}")
        updates[f"seats.{seat}.{field}"] = _target(field, current, direction, ctx.arg("Number"))
    return Outcome(updates=updates, message=f"{'、'.join(seats)}{area}已调节")


def _target(field: str, current: int, direction: str | None, number: int | None) -> int:
    if direction == "放平":
        return BACKREST_FLAT
    if direction == "直立":
        return BACKREST_UPRIGHT
    if field == "backrest":
        if number is not None:
            return clamp(int(number), BACKREST_MIN, BACKREST_FLAT)
        sign = 1 if direction in BACKWARD else -1 if direction in FORWARD else 0
        return clamp(current + sign * POSITION_STEP, BACKREST_MIN, BACKREST_FLAT)
    if number is not None:
        return clamp(int(number), 0, 100)
    sign = 1 if direction in FORWARD else -1 if direction in BACKWARD else 0
    return clamp(current + sign * POSITION_STEP, 0, 100)


def zero_g(on: bool) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        seats = ctx.seats()
        return Outcome(updates=per_seat("seats", "zero_g", seats, on), message=f"零重力座椅已{'打开' if on else '关闭'}")

    return handler


def _memory_key(ctx: Ctx, seat: str) -> str:
    return f"座椅记忆.{seat}.{ctx.arg('SeatPosition', '位置一')}"


def save_position(ctx: Ctx) -> Outcome:
    settings = dict(ctx.state.get("settings"))
    for seat in ctx.seats():
        snap = ctx.state.get(f"seats.{seat}")
        settings[_memory_key(ctx, seat)] = {"position": snap["position"], "backrest": snap["backrest"]}
    return Outcome(updates={"settings": settings}, message="座椅位置已保存")


def memory_position(ctx: Ctx) -> Outcome:
    settings = ctx.state.get("settings")
    updates: dict[str, int] = {}
    for seat in ctx.seats():
        saved = settings.get(_memory_key(ctx, seat))
        if saved:
            updates[f"seats.{seat}.position"] = saved["position"]
            updates[f"seats.{seat}.backrest"] = saved["backrest"]
    return Outcome(updates=updates, message="已恢复记忆位置" if updates else "没有保存过这个记忆位置，座椅保持不变")


def recovery(ctx: Ctx) -> Outcome:
    seats = ctx.seats()
    updates = {
        **per_seat("seats", "position", seats, 50),
        **per_seat("seats", "backrest", seats, BACKREST_UPRIGHT),
        **per_seat("seats", "zero_g", seats, False),
    }
    return Outcome(updates=updates, message="座椅已复位")


HANDLERS: dict[str, Handler] = {
    "SeatCtrl.SetHeating": level_setter("heat", "座椅加热"),
    "SeatCtrl.SetVentilation": level_setter("vent", "座椅通风"),
    "SeatCtrl.SetMassage": level_setter("massage", "座椅按摩"),
    "SeatCtrl.SetMassageTime": record,
    "SeatCtrl.NextMassageMode": record,
    "SeatCtrl.SetPosition": set_position,
    "SeatCtrl.OpenZeroGMode": zero_g(True),
    "SeatCtrl.CloseZeroGMode": zero_g(False),
    "SeatCtrl.SavePosition": save_position,
    "SeatCtrl.SetMemoryPosition": memory_position,
    "SeatCtrl.RecoveryPosition": recovery,
}
