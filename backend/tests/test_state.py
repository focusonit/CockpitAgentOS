import pytest

from backend.vehicle.state import PathError, VehicleState, parse_value


def test_default_state_basics() -> None:
    state = VehicleState()
    assert state.get("gear") == "P"
    assert state.get("windows.主驾") == 0
    assert state.get("ac.temp.副驾") == 24


def test_apply_is_immutable_and_returns_diffs() -> None:
    state = VehicleState()
    new_state, diffs = state.apply({"windows.主驾": 50, "ac.on": True})
    assert state.get("windows.主驾") == 0
    assert new_state.get("windows.主驾") == 50
    assert {d.path for d in diffs} == {"windows.主驾", "ac.on"}


def test_apply_no_change_no_diff() -> None:
    _, diffs = VehicleState().apply({"gear": "P"})
    assert diffs == []


def test_unknown_path_raises() -> None:
    with pytest.raises(PathError):
        VehicleState().apply({"windows.天窗": 1})
    assert not VehicleState().has("nope.x")


def test_get_returns_copy() -> None:
    state = VehicleState()
    seat = state.get("seats.主驾")
    seat["heat"] = 3
    assert state.get("seats.主驾.heat") == 0


@pytest.mark.parametrize(
    ("template", "raw", "expected"),
    [
        (False, "开", True),
        (True, "关", False),
        (0, "60", 60),
        (0, "2.1", 2.1),
        (2.5, "2.1", 2.1),
        ([], "左后胎压低、电量低", ["左后胎压低", "电量低"]),
        ("", "内循环", "内循环"),
    ],
)
def test_parse_value(template: object, raw: str, expected: object) -> None:
    assert parse_value(template, raw) == expected


def test_parse_bool_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_value(False, "半开")


def test_from_init_with_profile_and_chips() -> None:
    state = VehicleState.from_init({"speed_kmh": "60", "ac.on": "开", "media.title": "晴天"}, profile="basic")
    assert state.get("profile") == "basic"
    assert state.get("speed_kmh") == 60
    assert state.get("ac.on") is True
    assert state.get("media.title") == "晴天"
