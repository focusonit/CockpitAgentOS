"""车辆状态：系统里唯一的真相源。

- 状态是嵌套 dict，路径用点号连接，键可以是中文，例如 ``windows.主驾``。
- 永远不原地修改：``apply`` 返回新的 ``VehicleState`` 和差异列表。
- 测试用例里的初始车况是字符串（``ac.on：开``），``parse_value`` 按默认值的类型转换。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

SEATS = ("主驾", "副驾", "左后排", "右后排")
TIRES = ("左前", "右前", "左后", "右后")

_TRUE_WORDS = {"开", "是", "true", "True", "on", "1"}
_FALSE_WORDS = {"关", "否", "false", "False", "off", "0"}


def _per_seat(value: Any) -> dict[str, Any]:
    return {seat: copy.deepcopy(value) for seat in SEATS}


def default_state() -> dict[str, Any]:
    """一辆停在 P 挡、已上电、联网的高配车。"""
    return {
        "profile": "high",
        # 乘员（M5）：各座位有没有人、是谁；说话人由前端乘员面板选
        "occupants": {
            "主驾": {"present": True, "who": "车主"},
            "副驾": {"present": True, "who": "家人"},
            "左后排": {"present": False, "who": ""},
            "右后排": {"present": False, "who": ""},
        },
        # 行驶与能源
        "power_on": True,
        "speed_kmh": 0,
        "gear": "P",
        "battery_pct": 80,
        "range_km": 420,
        "total_mileage_km": 12680,
        "drive_mode": "舒适",
        "energy_recovery": "中",
        "max_charging_pct": 90,
        "tire_pressure": {tire: 2.5 for tire in TIRES},
        "warnings": [],
        # 环境
        "network": True,
        "network_weak": False,
        "now": "2026-11-05T10:00:00",
        "location": "国贸",
        "outside_temp": 18,
        "cabin_temp": 24,
        "air_quality": "优",
        "pm25": 12,
        # 车身
        "windows": _per_seat(0),
        "sunroof": 0,
        "curtain": 0,
        "sunshades": _per_seat(100),  # 各座位侧窗遮阳帘开度（M5 扩展，100 = 收起不挡光）
        "backdoor": 0,
        "doors_locked": True,
        "charge_port": False,
        "wiper": {"on": False, "speed": "中"},
        "mirrors_folded": False,
        "steering_heat": False,
        # 空调
        "ac": {
            "on": False,
            "recirc": "外循环",
            "temp": _per_seat(24),
            "air_volume": 3,
            "mode": "自动",
            "wind_direction": "自动",
            "fresh_air": False,
            "defrost": False,
            "compressor": True,
            "purifier": False,
        },
        # 香氛：关闭时为“关”，打开时为香型名称
        "fragrance": "关",
        "fragrance_level": "中",
        # 座椅
        "seats": _per_seat({"heat": 0, "vent": 0, "massage": 0, "backrest": 100, "position": 50, "zero_g": False}),
        # 灯光
        "lights": {
            "ambient": {"on": False, "color": "白色", "brightness": 5, "theme": "默认"},
            "reading": False,
            "emblem": "关",
            "headlight_height": 2,
        },
        # 屏幕与 HUD
        "display": {"screen_brightness": 6, "hud_on": True, "hud_height": 5, "hud_brightness": 5, "time_format": "24"},
        # 媒体与导航（由 App 维护）
        "media": {
            "kind": "music",  # music | video | news | podcast | fm
            "playing": False,
            "title": "",
            "artist": "",
            "album": "",
            "volume": 20,
            "muted": False,
            "source": "",
            "progress_s": 0,
            "duration_s": 0,
            "episode": 0,
            "play_mode": "列表循环",
            "queue": [],  # 播放列表，元素是曲库里的歌曲 dict
            "index": 0,
            "favorites": [],  # 收藏的歌名
            "disliked": [],
            "lyrics_open": False,
            "mv_open": False,
        },
        "nav": {
            "active": False,
            "destination": "",
            "eta_min": 0,
            "distance_km": 0.0,
            "route_strategy": "推荐",
            "waypoints": [],
            "route": [],  # 路线折线点 [{x, y}]
            "last_results": [],  # 最近一次搜索结果（POI dict）
            "zoom": 14,
            "overview": False,
            "favorites": {"家": "望京SOHO", "公司": "中关村软件园"},
            # 手机推送过来、还没确认的目的地：{name, distance_km, eta_min, route, from}，导航 App 显示成路线卡片
            "pushed": None,
        },
        # 当前在中控屏前台的 App：navigation / music / video / meituan / phone / photos / settings / 其他应用名；空串是桌面
        "screen": {"app": ""},
        "meituan": {"last_results": [], "merchant": "", "cart": [], "draft": None, "orders": [], "bookings": []},
        "tasks": [],  # 定时与条件任务，由 task_master 维护
        "photos": [],
        "home": {},
        # 手机
        "phone": {"bluetooth": True, "in_call": False, "contact": "", "last_results": [],
                  "call_log": [{"name": "陈经理", "phone": "186 0000 1008", "type": "呼入", "at": "2026-11-05T09:12"}]},
        # 协议长尾指令没有专门的状态字段，执行结果按“指令名 → 参数”记在这里
        "settings": {},
    }


@dataclass(frozen=True)
class Diff:
    path: str
    old: Any
    new: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "old": self.old, "new": self.new}


class PathError(KeyError):
    """路径在车辆状态里不存在。"""


def _walk(data: dict[str, Any], parts: list[str]) -> Any:
    node: Any = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise PathError(".".join(parts))
        node = node[part]
    return node


def parse_value(template: Any, raw: Any) -> Any:
    """按默认值的类型，把字符串形式的车况值转换成真实类型。"""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if isinstance(template, bool):
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        raise ValueError(f"无法把 {raw!r} 解析为开关值")
    if isinstance(template, int):
        number = float(text)
        return int(number) if number.is_integer() else number
    if isinstance(template, float):
        return float(text)
    if isinstance(template, list):
        return [item for item in (s.strip() for s in text.replace("，", "、").split("、")) if item]
    return text


@dataclass(frozen=True)
class VehicleState:
    data: dict[str, Any] = field(default_factory=default_state)

    @classmethod
    def from_init(cls, init: dict[str, Any] | None = None, profile: str | None = None) -> "VehicleState":
        state = cls()
        if profile:
            state, _ = state.apply({"profile": profile})
        if init:
            state, _ = state.apply(init, parse=True)
        return state

    def get(self, path: str) -> Any:
        return copy.deepcopy(_walk(self.data, path.split(".")))

    def has(self, path: str) -> bool:
        try:
            _walk(self.data, path.split("."))
        except PathError:
            return False
        return True

    def apply(self, updates: dict[str, Any], *, parse: bool = False) -> tuple["VehicleState", list[Diff]]:
        """返回 (新状态, 差异)。原状态不变。"""
        new_data = copy.deepcopy(self.data)
        diffs: list[Diff] = []
        for path, raw in updates.items():
            parts = path.split(".")
            parent = _walk(new_data, parts[:-1]) if len(parts) > 1 else new_data
            if not isinstance(parent, dict) or parts[-1] not in parent:
                raise PathError(path)
            old = parent[parts[-1]]
            value = parse_value(old, raw) if parse else raw
            if old != value:
                parent[parts[-1]] = value
                diffs.append(Diff(path, copy.deepcopy(old), copy.deepcopy(value)))
        return VehicleState(new_data), diffs

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False)


def paths(data: dict[str, Any], prefix: str = "") -> Iterable[str]:
    """列出所有叶子路径，供调试面板和上下文组装使用。"""
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from paths(value, path)
        else:
            yield path
