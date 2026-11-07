"""会话网关：网络状态决定一句话能不能上云。

车况里 ``network``（是否联网）+ ``network_weak``（弱网）组合成三档：
- 正常：快速通道没命中就上云。
- 弱网：照常上云，但每次云端请求前加一段延迟，模拟弱网。
- 断网：只走端侧（闸门 + 快速通道）；没命中就直接说网络不好，不调大模型。
"""

from __future__ import annotations

from backend.vehicle.state import VehicleState

ONLINE = "正常"
WEAK = "弱网"
OFFLINE = "断网"
MODES = (ONLINE, WEAK, OFFLINE)

WEAK_DELAY_S = 1.5  # 弱网时每次云端请求前的额外延迟
OFFLINE_REPLY = "现在网络不太好，这个需要联网才能办。车控、空调、车窗和本地音乐这类指令还可以直接跟我说。"


def network_mode(state: VehicleState) -> str:
    if not state.get("network"):
        return OFFLINE
    return WEAK if state.get("network_weak") else ONLINE


def mode_updates(mode: str) -> dict[str, bool]:
    """前端切换网络档位时写进车况的值。"""
    if mode not in MODES:
        raise ValueError(f"网络只能是 {'/'.join(MODES)}")
    return {"network": mode != OFFLINE, "network_weak": mode == WEAK}
