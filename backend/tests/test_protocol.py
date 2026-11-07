"""协议加载、参数转换和 function schema 生成。"""

from __future__ import annotations

import pytest

from backend.protocol.schema import openai_tools
from backend.protocol.spec import VEHICLE_TOOLS, InvalidParamError, ParamSpec, build_protocol, load_protocol


def test_protocol_covers_vehicle_tools() -> None:
    proto = load_protocol()
    assert set(VEHICLE_TOOLS) <= set(proto.tools)
    assert len(proto.all_commands()) > 300


def test_int_coerce_strips_units_and_clamps() -> None:
    p = ParamSpec("Number", "int", min=16, max=32)
    assert p.coerce("22度") == 22
    assert p.coerce("40") == 32
    assert p.coerce(10) == 16
    with pytest.raises(InvalidParamError):
        p.coerce("22.5")
    with pytest.raises(InvalidParamError):
        p.coerce("热一点")


def test_level_and_enum_coerce() -> None:
    assert ParamSpec("Number", "level").coerce("高") == 3
    assert ParamSpec("Number", "level").coerce("2档") == 2
    enum = ParamSpec("Mode", "enum", values=("运动", "舒适"))
    assert enum.coerce("运动") == "运动"
    with pytest.raises(InvalidParamError):
        enum.coerce("赛道")


def test_shared_params_inherited_and_duplicates_rejected() -> None:
    core = {
        "params": {"Number": {"type": "int", "min": 0, "max": 5}},
        "tools": {"t": {"desc": "x", "commands": {"A.B": {"params": {"Number": {}}}}}},
    }
    proto = build_protocol(core)
    assert proto.command("A.B").param("Number").max == 5
    with pytest.raises(ValueError):
        build_protocol(core, {"tools": {"t": {"commands": {"A.B": {}}}}})


def test_openai_schema_shape() -> None:
    tools = openai_tools(load_protocol())
    names = [t["function"]["name"] for t in tools]
    assert set(VEHICLE_TOOLS) <= set(names)
    climate = next(t for t in tools if t["function"]["name"] == "climate")["function"]
    props = climate["parameters"]["properties"]
    assert "ACCtrl.SetTemperature" in props["command"]["enum"]
    assert climate["parameters"]["required"] == ["command"]
    assert "用于" in props["Number"]["description"]
