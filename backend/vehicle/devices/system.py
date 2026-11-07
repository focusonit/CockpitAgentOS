"""系统设置：音量（0–40）、静音、屏幕与 HUD 亮度、蓝牙、助手音色、时间格式。"""

from __future__ import annotations

from backend.vehicle.devices.base import Ctx, Handler, Outcome, adjust, record

VOLUME_MAX = 40
VOLUME_STEP = 5
SCREEN_MAX = 10


def set_volume(ctx: Ctx) -> Outcome:
    channel = ctx.arg("Channel", "媒体")
    if channel != "媒体":
        return record(ctx, key=f"音量.{channel}")
    value = adjust(ctx, ctx.state.get("media.volume"), 0, VOLUME_MAX, step=VOLUME_STEP)
    return Outcome(updates={"media.volume": value, "media.muted": False}, message=f"音量已调到 {value}")


def mute(on: bool) -> Handler:
    return lambda ctx: Outcome(updates={"media.muted": on}, message="已静音" if on else "已取消静音")


def level_setter(path: str, label: str) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        value = adjust(ctx, ctx.state.get(path), 1, SCREEN_MAX)
        return Outcome(updates={path: value}, message=f"{label}已调到 {value}")

    return handler


def bluetooth(on: bool) -> Handler:
    return lambda ctx: Outcome(updates={"phone.bluetooth": on}, message=f"蓝牙已{'连接' if on else '断开'}")


def time_format(ctx: Ctx) -> Outcome:
    value = str(ctx.arg("Category", "24"))
    return Outcome(updates={"display.time_format": value}, message=f"时间已改为 {value} 小时制")


HANDLERS: dict[str, Handler] = {
    "SoundCtrl.SetVolume": set_volume,
    "SoundCtrl.Off": mute(True),
    "SoundCtrl.On": mute(False),
    "ScreenCtrl.SetBrightness": level_setter("display.screen_brightness", "屏幕亮度"),
    "HudCtrl.SetHeight": level_setter("display.hud_height", "HUD 高度"),
    "HudCtrl.SetBrightness": level_setter("display.hud_brightness", "HUD 亮度"),
    "BlueToothCtrl.DisConnectBlueTooth": bluetooth(False),
    "BlueToothCtrl.ConnectBluetooh": bluetooth(True),
    "VoiceAssistant.SetTimbre": record,
    "SystemCtrl.SetTimeFormat": time_format,
}
