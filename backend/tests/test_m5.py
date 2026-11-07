"""M5：乘员与音区（侧窗遮阳帘）、手机互联（推送目的地、远程空调）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import app
from backend.app.phone import PhoneError, build_args
from backend.brain.echo import EchoAgent
from backend.brain.planner import PlannerAgent
from backend.brain.types import Turn
from backend.llm.scripted import ScriptedModel, call, reply
from backend.protocol import status as st
from backend.trace.recorder import TraceRecorder
from backend.vehicle.executor import execute
from backend.vehicle.state import SEATS, VehicleState

client = TestClient(app)


@pytest.fixture(autouse=True)
def echo_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "make_agent", lambda: (EchoAgent(), None))


# ---------- 侧窗遮阳帘：说话人那一侧 ----------


def test_sunshade_close_only_speaker_side() -> None:
    result = execute(VehicleState(), "body", {"command": "SunshadeCtrl.Close"}, "副驾")
    assert result.calls[0].status == st.SUCCESS
    assert result.state.get("sunshades.副驾") == 0
    for seat in SEATS:
        if seat != "副驾":
            assert result.state.get(f"sunshades.{seat}") == 100
    assert result.state.get("curtain") == VehicleState().get("curtain")  # 车顶遮阳帘不动


def test_sunshade_open_with_position() -> None:
    closed, _ = VehicleState().apply({"sunshades.左后排": 0, "sunshades.右后排": 0})
    result = execute(closed, "body", {"command": "SunshadeCtrl.Open", "Position": "后排"}, "主驾")
    assert result.state.get("sunshades.左后排") == 100 and result.state.get("sunshades.右后排") == 100


async def test_planner_passes_speaker_seat_to_sunshade() -> None:
    model = ScriptedModel([reply("", call("body", command="SunshadeCtrl.Close")), reply("好的")])
    agent = PlannerAgent(model, weak_delay_s=0)
    result = await agent.handle(VehicleState(), Turn(seat="副驾", text="有点晒"), TraceRecorder())
    assert result.state is not None
    assert result.state.get("sunshades.副驾") == 0 and result.state.get("sunshades.主驾") == 100


def test_occupants_default_and_editable() -> None:
    state = VehicleState()
    assert state.get("occupants.主驾.present") is True
    assert state.get("occupants.左后排.present") is False
    edited, _ = state.apply({"occupants.左后排.present": "开", "occupants.左后排.who": "孩子"}, parse=True)
    assert edited.get("occupants.左后排.present") is True and edited.get("occupants.左后排.who") == "孩子"


# ---------- 手机推送目的地 ----------


def _push(state: VehicleState, keyword: str = "首都机场") -> VehicleState:
    result = execute(state, "phone_link", {"command": "PhoneLink.PushDestination", "Keyword": keyword}, "主驾")
    assert result.calls[0].status == st.SUCCESS
    return result.state


def test_push_shows_route_card_without_navigating() -> None:
    state = _push(VehicleState())
    card = state.get("nav.pushed")
    assert card["name"] == "北京首都国际机场" and card["from"] == "手机"
    assert card["distance_km"] > 0 and len(card["route"]) == 2
    assert state.get("screen.app") == "navigation"
    assert state.get("nav.active") is False


def test_accept_push_starts_navigation_and_clears_card() -> None:
    result = execute(_push(VehicleState()), "phone_link", {"command": "PhoneLink.AcceptPush"}, "主驾")
    assert result.state.get("nav.active") is True
    assert result.state.get("nav.destination") == "北京首都国际机场"
    assert result.state.get("nav.pushed") is None


def test_dismiss_push_and_accept_without_push() -> None:
    result = execute(_push(VehicleState()), "phone_link", {"command": "PhoneLink.DismissPush"}, "主驾")
    assert result.state.get("nav.pushed") is None and result.state.get("nav.active") is False
    missing = execute(VehicleState(), "phone_link", {"command": "PhoneLink.AcceptPush"}, "主驾")
    assert missing.calls[0].status == st.FAILED


def test_voice_navigation_also_clears_card() -> None:
    result = execute(_push(VehicleState()), "navigation", {"command": "Navigation.SearchPoi", "Keyword": "首都机场"}, "主驾")
    assert result.state.get("nav.active") is True and result.state.get("nav.pushed") is None


def test_phone_link_hidden_from_model() -> None:
    names = {t["function"]["name"] for t in client.get("/api/protocol/openai").json()}
    assert "phone_link" not in names and "body" in names


# ---------- 手机接口 ----------


@pytest.mark.parametrize(
    ("action", "value"),
    [("fly", None), ("ac_temp", 40), ("ac_temp", True), ("push", ""), ("push", "长" * 41)],
)
def test_phone_rejects_bad_requests(action: str, value: object) -> None:
    with pytest.raises(PhoneError):
        build_args(action, value)


def test_phone_offline_when_no_car() -> None:
    assert client.get("/api/phone/status").json() == {"connected": False, "state": None}
    resp = client.post("/api/phone/action", json={"action": "ac_on"})
    assert resp.status_code == 400 and "不在线" in resp.json()["detail"]


def _drain_until(ws, kind: str) -> dict:  # type: ignore[no-untyped-def]
    for _ in range(10):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"没有收到 {kind}")


def test_phone_remote_ac_and_push_reach_car() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # hello
        status = client.get("/api/phone/status").json()
        assert status["connected"] is True and status["state"]["ac_on"] is False

        resp = client.post("/api/phone/action", json={"action": "ac_on"}).json()
        assert resp["ok"] and resp["state"]["ac_on"] is True
        result = _drain_until(ws, "invoke_result")
        assert result["source"] == "phone"
        state = _drain_until(ws, "state")
        assert state["state"]["ac"]["on"] is True

        resp = client.post("/api/phone/action", json={"action": "push", "value": "首都机场"}).json()
        assert resp["ok"] and resp["state"]["nav_pushed"]["name"] == "北京首都国际机场"
        state = _drain_until(ws, "state")
        assert state["state"]["screen"]["app"] == "navigation"
        assert state["state"]["nav"]["pushed"]["name"] == "北京首都国际机场"
    assert client.get("/api/phone/status").json()["connected"] is False


# ---------- 体验脚本 ----------


def test_demos_endpoint_lists_all_scripts() -> None:
    demos = client.get("/api/demos").json()
    assert len(demos) == 9
    first = demos[0]
    assert first["id"] == "01_commute" and first["title"] and first["turns"][0]["seat"] in SEATS


def test_demo_init_is_loadable_and_speakers_are_seats() -> None:
    for demo in client.get("/api/demos").json():
        VehicleState.from_init(demo.get("init"), demo.get("profile"))  # 初始车况的路径都存在
        assert all(t["seat"] in SEATS and t["text"] for t in demo["turns"]), demo["id"]


def test_demo_loader_rejects_broken_script(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from backend.app.demos import load_demos

    (tmp_path / "bad.yaml").write_text("title: 坏脚本\nturns:\n  - text: 没有座位\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_demos.__wrapped__(tmp_path)
