"""虚拟设备的物理约束：每个处理函数的典型行为和边界。"""

from __future__ import annotations

from typing import Any

import pytest

from backend.protocol import status as st
from backend.protocol.spec import VEHICLE_TOOLS, InvalidParamError, load_protocol
from backend.vehicle.devices import HANDLERS
from backend.vehicle.devices.base import resolve_seats
from backend.vehicle.executor import ExecResult, execute
from backend.vehicle.state import VehicleState


def run(state: VehicleState, tool: str, command: str, seat: str = "主驾", **args: Any) -> ExecResult:
    return execute(state, tool, {"command": command, **args}, seat)


@pytest.fixture
def car() -> VehicleState:
    return VehicleState()


def test_every_core_command_has_handler() -> None:
    missing = [c.name for c in load_protocol().all_commands() if not c.longtail and c.tool in VEHICLE_TOOLS and c.name not in HANDLERS]
    assert missing == []


# ---------- 区域解析 ----------


@pytest.mark.parametrize(
    ("area", "speaker", "expected"),
    [
        (None, "副驾", ["副驾"]),
        ("后排", "主驾", ["左后排", "右后排"]),
        ("全车", "主驾", ["主驾", "副驾", "左后排", "右后排"]),
        ("主驾座椅", "副驾", ["主驾"]),
        ("左侧", "主驾", ["主驾", "左后排"]),
    ],
)
def test_resolve_seats(area: str | None, speaker: str, expected: list[str]) -> None:
    assert sorted(resolve_seats(area, speaker)) == sorted(expected)


def test_resolve_seats_unknown_area() -> None:
    with pytest.raises(InvalidParamError):
        resolve_seats("车顶", "主驾")


# ---------- 车身 ----------


def test_window_opens_fully_when_slow(car: VehicleState) -> None:
    out = run(car, "body", "WindowCtrl.Open", Position="副驾")
    assert out.ok and out.state.get("windows.副驾") == 100


def test_window_partial_at_high_speed(car: VehicleState) -> None:
    fast, _ = car.apply({"speed_kmh": 130})
    out = run(fast, "body", "WindowCtrl.Open", Position="主驾")
    assert out.calls[0].status == st.PART_SUCCEED
    assert out.state.get("windows.主驾") == 30


def test_window_vent_mode_opens_all_a_crack(car: VehicleState) -> None:
    out = run(car, "body", "WindowCtrl.OpenMode", Mode="通风")
    assert {out.state.get(f"windows.{s}") for s in ("主驾", "副驾", "左后排", "右后排")} == {10}


def test_backdoor_degree_and_lock(car: VehicleState) -> None:
    assert run(car, "body", "CarCtrl.SetBackdoorDegree", Number="60").state.get("backdoor") == 60
    assert run(car, "body", "CarCtrl.DoorUnlocked").state.get("doors_locked") is False


# ---------- 空调 ----------


def test_temperature_clamped_and_turns_ac_on(car: VehicleState) -> None:
    off, _ = car.apply({"ac.on": False})
    out = run(off, "climate", "ACCtrl.SetTemperature", Number="40")
    assert out.ok
    assert out.state.get("ac.temp.主驾") == 32
    assert out.state.get("ac.on") is True


def test_temperature_direction_per_area(car: VehicleState) -> None:
    out = run(car, "climate", "ACCtrl.SetTemperature", Area="后排", Direction="调低")
    base = car.get("ac.temp.左后排")
    assert out.state.get("ac.temp.左后排") < base
    assert out.state.get("ac.temp.右后排") < base
    assert out.state.get("ac.temp.主驾") == car.get("ac.temp.主驾")


def test_adjust_without_amount_is_invalid(car: VehicleState) -> None:
    on, _ = car.apply({"ac.on": True})
    out = run(on, "climate", "ACCtrl.SetAirVolume")
    assert out.calls[0].status == st.INVALID_PARAM
    assert out.diffs == () and out.state == on


def test_ac_modes(car: VehicleState) -> None:
    assert run(car, "climate", "ACCtrl.OpenMode", Mode="内循环").state.get("ac.recirc") == "内循环"
    assert run(car, "climate", "ACCtrl.OpenMode", Mode="除雾").state.get("ac.defrost") is True
    assert run(car, "climate", "ACCtrl.CloseMode").state.get("ac.on") is False


# ---------- 座椅 ----------


def test_seat_heating_levels(car: VehicleState) -> None:
    assert run(car, "seat", "SeatCtrl.SetHeating", Number="高").state.get("seats.主驾.heat") == 3
    assert run(car, "seat", "SeatCtrl.SetHeating", "副驾").state.get("seats.副驾.heat") == 2


def test_seat_heating_level_out_of_range(car: VehicleState) -> None:
    assert run(car, "seat", "SeatCtrl.SetHeating", Number="5").calls[0].status == st.INVALID_PARAM


def test_seat_flat_backrest(car: VehicleState) -> None:
    out = run(car, "seat", "SeatCtrl.SetPosition", "副驾", Area="靠背", Direction="放平")
    assert out.state.get("seats.副驾.backrest") == 170


def test_seat_memory_roundtrip(car: VehicleState) -> None:
    moved = run(car, "seat", "SeatCtrl.SetPosition", "副驾", Number="80").state
    saved = run(moved, "seat", "SeatCtrl.SavePosition", "副驾", SeatPosition="位置一").state
    reset = run(saved, "seat", "SeatCtrl.RecoveryPosition", "副驾").state
    assert reset.get("seats.副驾.position") == 50
    restored = run(reset, "seat", "SeatCtrl.SetMemoryPosition", "副驾", SeatPosition="位置一").state
    assert restored.get("seats.副驾.position") == 80


# ---------- 灯光 / 系统 ----------


def test_ambient_brightness_turns_on(car: VehicleState) -> None:
    out = run(car, "lighting", "AtlightsCtrl.SetLight", Number="8")
    assert out.state.get("lights.ambient.brightness") == 8
    assert out.state.get("lights.ambient.on") is True


def test_screen_brightness_clamps_to_max(car: VehicleState) -> None:
    out = run(car, "system_settings", "ScreenCtrl.SetBrightness", Number="20")
    assert out.ok and out.state.get("display.screen_brightness") == 10


def test_volume_up_unmutes(car: VehicleState) -> None:
    muted, _ = car.apply({"media.muted": True})
    out = run(muted, "system_settings", "SoundCtrl.SetVolume", Direction="调高")
    assert out.state.get("media.volume") > car.get("media.volume")
    assert out.state.get("media.muted") is False


# ---------- 通用开关 ----------


@pytest.mark.parametrize(
    ("device", "path", "value"),
    [
        ("空调", "ac.on", True),
        ("后备箱", "backdoor", 100),
        ("雨刷", "wiper.on", True),
        ("香氛", "fragrance", "香氛1"),
        ("外后视镜", "mirrors_folded", False),
    ],
)
def test_switch_device_open(car: VehicleState, device: str, path: str, value: Any) -> None:
    start, _ = car.apply({"ac.on": False, "mirrors_folded": True})
    out = run(start, "switch_device", "Device.Open", Device=device)
    assert out.ok and out.state.get(path) == value


def test_switch_device_seat_template(car: VehicleState) -> None:
    out = run(car, "switch_device", "Device.Open", "左后排", Device="座椅加热")
    assert out.state.get("seats.左后排.heat") == 2
    assert out.state.get("seats.主驾.heat") == 0


def test_switch_unknown_device_is_recorded(car: VehicleState) -> None:
    out = run(car, "switch_device", "Device.Open", Device="冰箱")
    assert out.ok and out.state.get("settings")["设备.冰箱"] is True


def test_longtail_command_is_recorded(car: VehicleState) -> None:
    longtail = next(c for c in load_protocol().all_commands() if c.longtail and c.tool == "body")
    out = execute(car, "body", {"command": longtail.name, "Anything": "x"}, "主驾")
    assert out.ok and out.state.get("settings") != {}


def test_query_returns_data_without_changing_state(car: VehicleState) -> None:
    out = run(car, "vehicle_query", "CarInfo.QueryQuantityElectricity")
    assert out.ok and out.diffs == ()
    assert out.calls[0].result.get("message")
