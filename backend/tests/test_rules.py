"""前置条件规则（开发计划 7.3）：用真实大脑会给的参数，检查每类状态码。"""

from __future__ import annotations

from typing import Any

import pytest

from backend.protocol import status as st
from backend.vehicle.executor import plan
from backend.vehicle.state import VehicleState

MOVING = {"speed_kmh": "60", "gear": "D"}

CASES: list[tuple[str, dict[str, Any] | None, str | None, str, str, dict[str, Any], str]] = [
    # (说明, init, profile, 座位, 工具, 参数, 期望状态码)
    ("行驶中开后备箱", MOVING, None, "主驾", "body", {"command": "BackdoorCtrl.Open"}, st.PRECOND_SPEED),
    ("行驶中用通用开关开后备箱", MOVING, None, "主驾", "switch_device",
     {"command": "Device.Open", "Device": "后备箱"}, st.PRECOND_SPEED),
    ("行驶中调主驾座椅", MOVING, None, "主驾", "seat",
     {"command": "SeatCtrl.SetPosition", "Direction": "后"}, st.PRECOND_SPEED),
    ("行驶中放平副驾", MOVING, None, "副驾", "seat",
     {"command": "SeatCtrl.SetPosition", "Area": "靠背", "Direction": "放平"}, st.PRECOND_SPEED),
    ("行驶中调副驾座椅可以", MOVING, None, "副驾", "seat",
     {"command": "SeatCtrl.SetPosition", "Direction": "后"}, st.SUCCESS),
    ("行驶中解锁车门", MOVING, None, "主驾", "body", {"command": "CarCtrl.DoorUnlocked"}, st.PRECOND_SPEED),
    ("未上电开座椅加热", {"power_on": "关"}, None, "主驾", "seat",
     {"command": "SeatCtrl.SetHeating"}, st.PRECOND_POWER),
    ("未上电用通用开关开空调", {"power_on": "关"}, None, "主驾", "switch_device",
     {"command": "Device.Open", "Device": "空调"}, st.PRECOND_POWER),
    ("未上电断言没写指令也能拦", {"power_on": "关"}, None, "主驾", "seat", {}, st.PRECOND_POWER),
    ("副驾改驾驶模式", None, None, "副驾", "drive_energy",
     {"command": "CarCtrl.SetDriveMode", "Mode": "运动"}, st.PRECOND_USER),
    ("副驾开记录仪可以", None, None, "副驾", "drive_energy", {"command": "RecorderControl.Open"}, st.SUCCESS),
    ("非 P 挡开充电口", {"gear": "D"}, None, "主驾", "drive_energy",
     {"command": "CarCtrl.OpenChargePort"}, st.PRECOND),
    ("空调关着调风量", {"ac.on": "关"}, None, "主驾", "climate",
     {"command": "ACCtrl.SetAirVolume", "Direction": "调高"}, st.NOT_AC),
    ("低配开天窗", None, "basic", "主驾", "body", {"command": "SkylightCtrl.Open"}, st.NOT_FOUND),
    ("低配开 HUD", None, "basic", "主驾", "switch_device", {"command": "Device.Open", "Device": "HUD"}, st.NOT_FOUND),
    ("低配开后排空调", None, "basic", "主驾", "switch_device",
     {"command": "Device.Open", "Device": "空调", "Position": "后排"}, st.NOT_FOUND),
    ("低配调后排温度", None, "basic", "主驾", "climate",
     {"command": "ACCtrl.SetTemperature", "Area": "后排", "Number": "22"}, st.NOT_FOUND),
    ("低配调主驾温度可以", None, "basic", "主驾", "climate",
     {"command": "ACCtrl.SetTemperature", "Number": "22"}, st.SUCCESS),
    ("蓝牙断开打电话", {"phone.bluetooth": "关"}, None, "主驾", "phone",
     {"command": "PhoneCall.DialByName", "Name": "妈妈"}, st.BT_DISCONNECTED),
    ("断网放在线音乐", {"network": "关"}, None, "主驾", "media_play",
     {"command": "Music.Play", "Name": "晴天"}, st.NOT_NET),
    ("断网放本地音乐可以", {"network": "关"}, None, "主驾", "media_play",
     {"command": "Music.Play", "Source": "本地"}, st.SUCCESS),
]


@pytest.mark.parametrize(
    ("init", "profile", "seat", "tool", "args", "code"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_rule_status(
    init: dict[str, Any] | None, profile: str | None, seat: str, tool: str, args: dict[str, Any], code: str
) -> None:
    state = VehicleState.from_init(init, profile)
    assert plan(state, tool, args, seat).call.status == code


def test_blocked_call_changes_nothing() -> None:
    state = VehicleState.from_init(MOVING, None)
    planned = plan(state, "body", {"command": "BackdoorCtrl.Open"}, "主驾")
    assert planned.updates == {}
    assert planned.call.result["message"] == st.DESCRIPTIONS[st.PRECOND_SPEED]
