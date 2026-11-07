"""驾驶与能源。只有主驾能改（规则见 config/rules.yaml），充电上限只能在 P 挡设置。"""

from __future__ import annotations

from backend.vehicle.devices.base import Ctx, Handler, Outcome, record


def drive_mode(ctx: Ctx) -> Outcome:
    mode = str(ctx.arg("Mode", "舒适模式")).removesuffix("模式")
    return Outcome(updates={"drive_mode": mode}, message=f"已切换到{mode}模式")


def energy_recovery(ctx: Ctx) -> Outcome:
    mode = ctx.arg("Mode", "中")
    return Outcome(updates={"energy_recovery": mode}, message=f"能量回收已调到{mode}")


def max_charging(ctx: Ctx) -> Outcome:
    value = int(ctx.arg("Number", 90))
    return Outcome(updates={"max_charging_pct": value}, message=f"充电上限已设为 {value}%")


HANDLERS: dict[str, Handler] = {
    "CarCtrl.SetDriveMode": drive_mode,
    "CarCtrl.SetEnergyRecovery": energy_recovery,
    "CarCtrl.SetMaximumCharging": max_charging,
    "CarCtrl.SetLaneDepartureAssist": record,
    "CarCtrl.SetBrakeStyle": record,
    "CarCtrl.SetAllTerrainMode": record,
    "SuspensionCtrl.SetMode": record,
    "RecorderControl.Open": record,
    "RecorderControl.StartRecording": record,
}
