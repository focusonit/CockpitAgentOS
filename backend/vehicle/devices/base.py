"""虚拟设备的公共部分：处理函数的输入输出、座位解析、设备表、调节工具函数。

处理函数是纯函数：``handler(ctx) -> Outcome``。它只读 ``ctx.state``，
把要改的路径放进 ``Outcome.updates``，由执行器统一应用，所以可以安全地并行或回滚。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

from backend.protocol import status as st
from backend.protocol.spec import CONFIG_DIR, InvalidParamError, load_yaml
from backend.vehicle.state import SEATS, VehicleState

FRONT = ("主驾", "副驾")
REAR = ("左后排", "右后排")
AREA_SEATS: dict[str, tuple[str, ...]] = {
    "主驾": ("主驾",), "主驾驶": ("主驾",), "驾驶位": ("主驾",), "左前": ("主驾",),
    "副驾": ("副驾",), "副驾驶": ("副驾",), "右前": ("副驾",),
    "左后": ("左后排",), "左后排": ("左后排",), "后排左": ("左后排",),
    "右后": ("右后排",), "右后排": ("右后排",), "后排右": ("右后排",),
    "前": FRONT, "前排": FRONT,
    "后": REAR, "后排": REAR,
    "左": ("主驾", "左后排"), "左侧": ("主驾", "左后排"),
    "右": ("副驾", "右后排"), "右侧": ("副驾", "右后排"),
    "所有": SEATS, "全部": SEATS, "全车": SEATS, "全部座位": SEATS, "四门": SEATS,
}
# 方向词 → 调节量的符号
UP_WORDS = {"调高", "高", "上", "提高", "升高", "增大", "加大", "大", "调大"}
DOWN_WORDS = {"调低", "低", "下", "降低", "减小", "调小", "小"}
LEVEL_NAMES = ("关", "低", "中", "高")


@dataclass(frozen=True)
class Ctx:
    state: VehicleState
    tool: str
    command: str
    args: dict[str, Any]
    seat: str
    limits: dict[str, Any] = field(default_factory=dict)

    @property
    def speaker(self) -> str:
        return self.seat if self.seat in SEATS else "主驾"

    def arg(self, name: str, default: Any = None) -> Any:
        return self.args.get(name, default)

    def seats(self, key: str = "Position") -> tuple[str, ...]:
        return resolve_seats(self.args.get(key), self.speaker)


@dataclass(frozen=True)
class Outcome:
    status: str = st.SUCCESS
    updates: dict[str, Any] = field(default_factory=dict)
    data: Any = None
    message: str = ""


Handler = Callable[[Ctx], Outcome]


def resolve_seats(area: Any, speaker: str) -> tuple[str, ...]:
    if area is None or area == "" or area in ("我", "我的", "本座"):
        return (speaker,)
    if isinstance(area, (list, tuple)):
        seats: list[str] = []
        for item in area:
            seats.extend(s for s in resolve_seats(item, speaker) if s not in seats)
        return tuple(seats)
    text = str(area).strip().removesuffix("座椅").removesuffix("座位").removesuffix("车窗")
    if text in AREA_SEATS:
        return AREA_SEATS[text]
    raise InvalidParamError(f"无法识别的位置：{area}")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def direction_sign(direction: Any) -> int:
    if direction in UP_WORDS:
        return 1
    if direction in DOWN_WORDS:
        return -1
    raise InvalidParamError(f"无法识别的方向：{direction}")


def adjust(ctx: Ctx, current: int, low: int, high: int, step: int = 1) -> int:
    """按 Number（目标值）或 Direction（相对调节）算出新值。都没给时抛 InvalidParam。"""
    number = ctx.arg("Number")
    if number is not None:
        return clamp(int(number), low, high)
    direction = ctx.arg("Direction")
    if direction is not None:
        return clamp(current + step * direction_sign(direction), low, high)
    mode = ctx.arg("Mode")
    if mode in ("最大", "最高"):
        return high
    if mode in ("最小", "最低"):
        return low
    raise InvalidParamError(f"{ctx.command} 需要 Number 或 Direction")


def per_seat(prefix: str, suffix: str, seats: tuple[str, ...], value: Any) -> dict[str, Any]:
    tail = f".{suffix}" if suffix else ""
    return {f"{prefix}.{seat}{tail}": value for seat in seats}


def record(ctx: Ctx, key: str | None = None, value: Any = None, **extra: Any) -> Outcome:
    """没有专门状态字段的指令：把参数记到 settings[键]，返回 Success。"""
    settings = dict(ctx.state.get("settings"))
    settings[key or ctx.command] = value if value is not None else (dict(ctx.args) or True)
    return Outcome(updates={"settings": settings, **extra}, message="已设置")


def query(data: Any, message: str) -> Outcome:
    return Outcome(data=data, message=message)


@lru_cache(maxsize=1)
def device_table() -> dict[str, dict[str, Any]]:
    """设备名（含别名）→ 设备定义，来自 config/devices.yaml。"""
    table: dict[str, dict[str, Any]] = {}
    for name, spec in load_yaml(CONFIG_DIR / "devices.yaml").items():
        entry = {**spec, "name": name}
        table[name] = entry
        for alias in spec.get("aliases", []):
            table[alias] = entry
    return table


def normalize_device(name: Any) -> str:
    text = str(name or "").strip()
    entry = device_table().get(text)
    return entry["name"] if entry else text
