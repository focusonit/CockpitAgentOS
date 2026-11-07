"""车况查询：只读，结果放在 Outcome.data，供大脑组织回复。"""

from __future__ import annotations

from backend.vehicle.devices.base import Ctx, Handler, Outcome, query


def tire_pressure(ctx: Ctx) -> Outcome:
    data = ctx.state.get("tire_pressure")
    return query(data, "胎压：" + "，".join(f"{k} {v} bar" for k, v in data.items()))


def endurance(ctx: Ctx) -> Outcome:
    km = ctx.state.get("range_km")
    return query({"range_km": km}, f"续航 {km} 公里")


def electricity(ctx: Ctx) -> Outcome:
    pct = ctx.state.get("battery_pct")
    return query({"battery_pct": pct}, f"电量 {pct}%")


def total_mileage(ctx: Ctx) -> Outcome:
    km = ctx.state.get("total_mileage_km")
    return query({"total_mileage_km": km}, f"总里程 {km} 公里")


def window_status(ctx: Ctx) -> Outcome:
    data = {"windows": ctx.state.get("windows"), "sunroof": ctx.state.get("sunroof"), "curtain": ctx.state.get("curtain")}
    opened = [seat for seat, pct in data["windows"].items() if pct > 0]
    return query(data, ("打开的车窗：" + "、".join(opened)) if opened else "车窗都关着")


def temperature(ctx: Ctx) -> Outcome:
    data = {"cabin_temp": ctx.state.get("cabin_temp"), "outside_temp": ctx.state.get("outside_temp")}
    return query(data, f"车内 {data['cabin_temp']}℃，车外 {data['outside_temp']}℃")


def ac_state(ctx: Ctx) -> Outcome:
    ac = ctx.state.get("ac")
    return query(ac, f"空调{'开着' if ac['on'] else '关着'}，风量 {ac['air_volume']} 档")


def air_quality(ctx: Ctx) -> Outcome:
    data = {"air_quality": ctx.state.get("air_quality"), "pm25": ctx.state.get("pm25")}
    return query(data, f"空气质量{data['air_quality']}，PM2.5 {data['pm25']}")


def door_status(ctx: Ctx) -> Outcome:
    locked = ctx.state.get("doors_locked")
    return query({"doors_locked": locked, "backdoor": ctx.state.get("backdoor")}, "车门已上锁" if locked else "车门未上锁")


HANDLERS: dict[str, Handler] = {
    "CarInfo.TirePressure": tire_pressure,
    "CarInfo.QueryEnduranceMileage": endurance,
    "CarInfo.QueryQuantityElectricity": electricity,
    "CarInfo.QueryTotalMileage": total_mileage,
    "CarInfo.WindowStatus": window_status,
    "CarInfo.Temperature": temperature,
    "CarInfo.AcState": ac_state,
    "CarInfo.AirQuality": air_quality,
    "CarInfo.DoorStatus": door_status,
}
