"""执行器：错误指令、串行 / 并行、超时、原子回滚。"""

from __future__ import annotations

from backend.protocol import status as st
from backend.vehicle.executor import execute, run_batch
from backend.vehicle.state import VehicleState

HEAT = ("seat", {"command": "SeatCtrl.SetHeating", "Number": "3"})
TEMP_UP = ("climate", {"command": "ACCtrl.SetTemperature", "Direction": "调高"})
BAD = ("climate", {"command": "ACCtrl.SetTemperature", "Number": "abc"})


def test_unknown_command_hints_owner_tool() -> None:
    out = execute(VehicleState(), "climate", {"command": "SeatCtrl.SetHeating"}, "主驾")
    assert out.calls[0].status == st.UNKNOWN_COMMAND
    assert "seat" in out.calls[0].result["message"]


def test_unknown_param_rejected() -> None:
    out = execute(VehicleState(), "seat", {"command": "SeatCtrl.SetHeating", "Color": "红"}, "主驾")
    assert out.calls[0].status == st.INVALID_PARAM


def test_app_tool_runs_its_handler_and_comes_to_front() -> None:
    out = execute(VehicleState(), "navigation", {"command": "Navigation.SearchPoi", "Keyword": "首都机场"}, "主驾")
    assert out.ok
    assert out.state.get("nav.active") and out.state.get("screen.app") == "navigation"


def test_unknown_app_command_rejected() -> None:
    out = execute(VehicleState(), "navigation", {"command": "Navi.Go", "Destination": "机场"}, "主驾")
    assert out.calls[0].status == st.UNKNOWN_COMMAND


async def test_serial_sees_previous_result() -> None:
    state = VehicleState()
    out = await run_batch(state, [TEMP_UP, TEMP_UP], "主驾", mode="serial")
    assert out.ok
    assert out.state.get("ac.temp.主驾") == state.get("ac.temp.主驾") + 2


async def test_parallel_uses_same_snapshot() -> None:
    state = VehicleState()
    out = await run_batch(state, [TEMP_UP, TEMP_UP], "主驾", mode="parallel")
    assert out.state.get("ac.temp.主驾") == state.get("ac.temp.主驾") + 1


async def test_parallel_independent_devices() -> None:
    out = await run_batch(VehicleState(), [HEAT, TEMP_UP], "主驾", mode="parallel")
    assert out.ok
    assert out.state.get("seats.主驾.heat") == 3


async def test_non_atomic_keeps_successes() -> None:
    out = await run_batch(VehicleState(), [HEAT, BAD], "主驾")
    assert [c.status for c in out.calls] == [st.SUCCESS, st.INVALID_PARAM]
    assert out.state.get("seats.主驾.heat") == 3


async def test_atomic_rolls_back() -> None:
    state = VehicleState()
    out = await run_batch(state, [HEAT, BAD], "主驾", atomic=True)
    assert [c.status for c in out.calls] == [st.ROLLED_BACK, st.INVALID_PARAM]
    assert out.state == state and out.diffs == ()


async def test_timeout() -> None:
    out = await run_batch(VehicleState(), [HEAT], "主驾", timeout_s=0.01, latency_s=0.2)
    assert out.calls[0].status == st.TIMEOUT
    assert out.state.get("seats.主驾.heat") == 0
