"""灯光：氛围灯（颜色、亮度 0–10、主题）、车标灯、大灯高度（0–5）、阅读灯。"""

from __future__ import annotations

from backend.vehicle.devices.base import Ctx, Handler, Outcome, adjust, record

BRIGHTNESS_MAX = 10
HEADLIGHT_MAX = 5


def set_color(ctx: Ctx) -> Outcome:
    color = ctx.arg("Color", "白色")
    return Outcome(updates={"lights.ambient.on": True, "lights.ambient.color": color}, message=f"氛围灯已换成{color}")


def set_light(ctx: Ctx) -> Outcome:
    value = adjust(ctx, ctx.state.get("lights.ambient.brightness"), 0, BRIGHTNESS_MAX)
    updates = {"lights.ambient.brightness": value, "lights.ambient.on": value > 0}
    return Outcome(updates=updates, message=f"氛围灯亮度已调到 {value}" if value else "氛围灯已关闭")


def switch_theme(ctx: Ctx) -> Outcome:
    theme = ctx.arg("Name", "下一个主题")
    return Outcome(updates={"lights.ambient.on": True, "lights.ambient.theme": theme}, message=f"氛围灯主题已切换为{theme}")


def emblem(ctx: Ctx) -> Outcome:
    color = ctx.arg("Color", "白色")
    return Outcome(updates={"lights.emblem": color}, message=f"车标灯已设为{color}")


def headlight_height(ctx: Ctx) -> Outcome:
    value = adjust(ctx, ctx.state.get("lights.headlight_height"), 0, HEADLIGHT_MAX)
    return Outcome(updates={"lights.headlight_height": value}, message=f"大灯高度已调到 {value}")


def reading(on: bool) -> Handler:
    return lambda ctx: Outcome(updates={"lights.reading": on}, message=f"阅读灯已{'打开' if on else '关闭'}")


HANDLERS: dict[str, Handler] = {
    "AtlightsCtrl.SetColor": set_color,
    "AtlightsCtrl.SetLight": set_light,
    "AtlightsCtrl.SwitchTheme": switch_theme,
    "CarCtrl.SetEmblemLamp": emblem,
    "CarCtrl.SetHeadlampDelay": record,
    "HeadlightCtrl.SetHeight": headlight_height,
    "CarCtrl.OpenReadingLamp": reading(True),
    "CarCtrl.CloseReadingLamp": reading(False),
}
