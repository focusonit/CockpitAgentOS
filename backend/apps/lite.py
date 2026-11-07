"""轻量模拟：打开/关闭应用和页面、拍照、小程序、AI 壁纸。"""

from __future__ import annotations

from backend.apps.common import now_of, ok
from backend.vehicle.devices.base import Ctx, Handler, Outcome, record

APP_IDS = {
    "导航": "navigation", "地图": "navigation", "高德地图": "navigation", "百度地图": "navigation",
    "音乐": "music", "QQ音乐": "music", "网易云音乐": "music", "酷狗音乐": "music", "视频": "music",
    "美团": "meituan", "外卖": "meituan", "电话": "phone", "通讯录": "phone",
    "相册": "photos", "图库": "photos", "相机": "photos",
}


def app_id(name: str) -> str:
    return APP_IDS.get(name, name)


def open_app(ctx: Ctx) -> Outcome:
    name = str(ctx.arg("App") or "")
    return ok(ctx, f"已打开{name}", app=app_id(name))


def close_app(ctx: Ctx) -> Outcome:
    name = str(ctx.arg("App") or "")
    current = ctx.state.get("screen.app")
    updates = {"screen.app": ""} if not name or app_id(name) == current else {}
    return Outcome(updates=updates, message=f"已关闭{name}")


def uninstall(ctx: Ctx) -> Outcome:
    outcome = record(ctx)
    return Outcome(updates=outcome.updates, message=f"已卸载{ctx.arg('App') or '应用'}")


def page(name: str, text: str) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        return ok(ctx, text, app=name)

    return handler


def home(ctx: Ctx) -> Outcome:
    return Outcome(updates={"screen.app": ""}, message="已回到桌面")


def snap(kind: str) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        photos = list(ctx.state.get("photos") or [])
        photo = {"id": len(photos) + 1, "kind": kind, "at": now_of(ctx).isoformat(timespec="minutes")}
        return ok(ctx, f"已{kind}，照片存到了相册", {"photos": [*photos, photo]}, {"photo": photo}, app="photos")

    return handler


def story(ctx: Ctx) -> Outcome:
    title = str(ctx.arg("Resource") or "睡前故事")
    updates = {"media.kind": "story", "media.playing": True, "media.title": title, "media.artist": "故事小程序",
               "media.progress_s": 0, "media.duration_s": 600, "media.queue": [], "media.index": 0}
    return ok(ctx, f"开始讲《{title}》", updates, app="music")


def wallpaper(ctx: Ctx) -> Outcome:
    content = str(ctx.arg("Content") or "星空")
    settings = {**ctx.state.get("settings"), "wallpaper": content}
    return ok(ctx, f"已生成“{content}”壁纸并设为桌面", {"settings": settings}, app="")


HANDLERS: dict[tuple[str, str], Handler] = {
    ("open_page", "APP.Open"): open_app,
    ("open_page", "APP.Close"): close_app,
    ("open_page", "APP.Uninstall"): uninstall,
    ("open_page", "PageCtrl.OpenACSettings"): page("ac_settings", "已打开空调设置"),
    ("open_page", "PageCtrl.OpenSeatSettings"): page("seat_settings", "已打开座椅设置"),
    ("open_page", "PageCtrl.CloseSeatSettings"): home,
    ("open_page", "GlobalCtrl.Back"): home,
    ("open_page", "GlobalCtrl.Home"): home,
    ("system_apps", "PhoneLink.OpenHiCar"): page("hicar", "已打开 HiCar"),
    ("system_apps", "TakePhoto.Selfie"): snap("自拍"),
    ("system_apps", "TakePhoto.Photo.Snap"): snap("拍照"),
    ("mini_program", "MiniProgram.PlayStorytelling"): story,
    ("mini_program", "MiniProgram.QueryPackage"): page("mini_program", "你有 1 个快递：顺丰，正在派送中，预计今天下午送达"),
    ("mini_program", "MiniProgram.QueryQueue"): page("mini_program", "前面还有 6 桌，预计等待 20 分钟"),
    ("ai_creation", "PaintWallpapers.Create"): wallpaper,
}
