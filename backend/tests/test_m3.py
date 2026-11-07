"""M3：模拟 App、子代理、模拟时钟，以及规划器对定时/确认事件的处理。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agents.task_master import parse_trigger
from backend.app import main
from backend.app.main import app
from backend.brain.planner import PlannerAgent
from backend.brain.types import Turn
from backend.clock import DONE, PENDING, advance, next_task
from backend.llm.scripted import ScriptedModel, call, reply
from backend.protocol import status as st
from backend.trace.recorder import TraceRecorder
from backend.vehicle.executor import execute
from backend.vehicle.state import VehicleState

NOW = datetime(2026, 11, 5, 10, 0)


def _run(state: VehicleState, tool: str, args: dict[str, Any], seat: str = "主驾"):
    out = execute(state, tool, args, seat)
    return out.calls[0], out.state


# ---------- 美团 ----------


def test_meituan_hotpot_second_shop_then_order_flow() -> None:
    call1, state = _run(VehicleState(), "meituan", {"task": "找家附近的火锅，第二家看看"})
    assert call1.status == st.SUCCESS
    second = state.get("meituan.last_results")[1]
    assert state.get("meituan.merchant") == second["id"]
    assert state.get("screen.app") == "meituan"

    call2, state = _run(state, "meituan", {"task": "帮我点两份招牌菜"})
    draft = state.get("meituan.draft")
    assert call2.status == st.SUCCESS and draft["items"][0]["count"] == 2
    assert state.get("meituan.orders") == []  # 只有草稿，没下单

    _, state = _run(state, "meituan", {"task": "送到公司"})
    assert "中关村软件园" in state.get("meituan.draft")["address"]
    call3, state = _run(state, "meituan_place_order", {"task": "下单"})
    assert call3.status == st.SUCCESS and state.get("meituan.draft") is None
    assert state.get("meituan.orders")[0]["status"] == "已下单"
    assert advance(state, 10).state.get("meituan.orders")[0]["status"] == "骑手配送中"
    assert advance(state, 40).state.get("meituan.orders")[0]["status"] == "已送达"


def test_place_order_without_draft_fails() -> None:
    call1, _ = _run(VehicleState(), "meituan_place_order", {"task": "下单"})
    assert not st.is_ok(call1.status)


def test_touch_and_agent_share_meituan_state() -> None:
    _, state = _run(VehicleState(), "meituan_app", {"command": "Meituan.Search", "Keyword": "火锅"})
    _, state = _run(state, "meituan_app", {"command": "Meituan.OpenMerchant", "Index": 1})
    first = state.get("meituan.last_results")[0]
    call1, state = _run(state, "meituan", {"task": "点一份招牌菜"})
    assert call1.status == st.SUCCESS and state.get("meituan.draft")["merchant_id"] == first["id"]


# ---------- 音乐：手动切歌后“这首收藏” ----------


def test_touch_next_then_favorite_this_song() -> None:
    _, state = _run(VehicleState(), "media_play", {"command": "Music.Play", "Singer": "周杰伦"})
    first = state.get("media.title")
    _, state = _run(state, "media_control", {"command": "GlobalCtrl.Next"})  # 触屏切歌
    current = state.get("media.title")
    assert current and current != first
    _, state = _run(state, "media_control", {"command": "GlobalCtrl.AddFavorite"})
    assert [f["title"] for f in state.get("media.favorites")] == [current]


def test_music_needs_network_unless_local() -> None:
    offline = VehicleState().apply({"network": False})[0]
    call1, _ = _run(offline, "media_play", {"command": "Music.Play", "Song": "晴天"})
    assert call1.status == st.NOT_NET


# ---------- 任务大师与时钟 ----------


def test_parse_trigger_variants() -> None:
    due, cond, rest = parse_trigger("半小时后关掉空调", NOW)
    assert due == datetime(2026, 11, 5, 10, 30) and "空调" in rest
    assert parse_trigger("十分钟后关上车窗", NOW)[0] == datetime(2026, 11, 5, 10, 10)
    assert parse_trigger("明天早上8点提醒我开会", NOW)[0] == datetime(2026, 11, 6, 8, 0)
    assert parse_trigger("到公司时提醒我拿快递", NOW)[1] == "到达公司"


def test_task_master_create_list_cancel() -> None:
    call1, state = _run(VehicleState(), "task_master", {"task": "半小时后关掉空调"})
    assert call1.status == st.SUCCESS and state.get("tasks")[0]["status"] == PENDING
    call2, _ = _run(state, "task_master", {"task": "我有哪些提醒"})
    assert "空调" in call2.result["message"]
    _, state = _run(state, "task_master", {"task": "取消关空调的任务"})
    assert next_task(state) is None
    bad, _ = _run(VehicleState(), "task_master", {"task": "关空调"})
    assert bad.status == st.INVALID_PARAM


def test_clock_drives_navigation_to_arrival_and_due_tasks() -> None:
    _, state = _run(VehicleState(), "navigation", {"command": "Navigation.SearchPoi", "Keyword": "公司"})
    _, state = _run(state, "task_master", {"task": "到公司时提醒我拿快递"})
    eta = state.get("nav.eta_min")
    assert state.get("nav.active") and eta > 0
    tick = advance(state, eta + 1)
    assert not tick.state.get("nav.active")
    assert tick.events and tick.events[0].startswith("已到达")
    assert [t["action"] for t in tick.due] and "快递" in tick.due[0]["action"]


# ---------- 其他子代理与轻量 App ----------


def test_car_expert_uses_vehicle_warnings() -> None:
    state = VehicleState.from_init({"tire_pressure.左后": "1.9", "warnings": "胎压低"})
    call1, _ = _run(state, "car_expert", {"task": "仪表盘亮了个感叹号的灯是什么意思"})
    assert "左后" in call1.result["message"] and "胎压" in call1.result["message"]


def test_web_search_and_home_need_network() -> None:
    call1, _ = _run(VehicleState(), "web_search", {"task": "北京今天天气怎么样"})
    assert call1.status == st.SUCCESS and "北京" in call1.result["message"]
    offline = VehicleState().apply({"network": False})[0]
    assert _run(offline, "web_search", {"task": "天气"})[0].status == st.NOT_NET
    call2, state = _run(VehicleState(), "home", {"task": "打开家里的空调和热水器"})
    assert state.get("home") == {"空调": True, "热水器": True}


def test_subagent_requires_task() -> None:
    assert _run(VehicleState(), "web_search", {"task": ""})[0].status == st.INVALID_PARAM


# ---------- 规划器：定时事件、触屏确认/取消、结果合并 ----------


async def test_timer_fire_marks_task_done_and_runs_action() -> None:
    model = ScriptedModel(
        [
            reply("", call("task_master", task="半小时后关掉空调")),
            reply("好的，半小时后关空调。"),
            reply("", call("switch_device", command="Device.Close", Device="空调")),
        ]
    )
    agent = PlannerAgent(model)
    state = VehicleState.from_init({"ac.on": "开"})
    first = await agent.handle(state, Turn(seat="主驾", text="半小时后关掉空调"), TraceRecorder())
    state = first.state
    fired = await agent.handle(state, Turn(seat="系统", event="timer_fire"), TraceRecorder())
    assert fired.calls[0].tool == "switch_device"
    assert fired.state.get("ac.on") is False
    assert fired.state.get("tasks")[0]["status"] == DONE
    assert fired.state.get("now").startswith("2026-11-05T10:30")
    assert "定时任务到点" in model.requests[2]["messages"][-1]["content"]


async def test_confirm_and_cancel_events_resolve_held_order() -> None:
    def scripted() -> ScriptedModel:
        return ScriptedModel(
            [
                reply("", call("meituan", task="找家火锅，点一份招牌菜送到公司")),
                reply("草稿好了，确认下单吗？"),
                reply("", call("meituan_place_order", task="下单")),
                reply("确认下单吗？"),
            ]
        )

    async def prepare(agent: PlannerAgent) -> VehicleState:
        r1 = await agent.handle(VehicleState(), Turn(seat="主驾", text="找家火锅点招牌菜送公司"), TraceRecorder())
        r2 = await agent.handle(r1.state, Turn(seat="主驾", text="下单吧"), TraceRecorder())
        assert r2.confirm_requested and r2.calls[0].status == st.PENDING_CONFIRM
        return r1.state

    agent = PlannerAgent(scripted())
    state = await prepare(agent)
    done = await agent.handle(state, Turn(seat="系统", event="confirm"), TraceRecorder())
    assert done.calls[0].tool == "meituan_place_order" and done.calls[0].status == st.SUCCESS
    assert done.state.get("meituan.orders")

    agent = PlannerAgent(scripted())
    state = await prepare(agent)
    cancelled = await agent.handle(state, Turn(seat="系统", event="cancel"), TraceRecorder())
    assert cancelled.calls == () and agent.memory.held == ()


async def test_turn_data_merges_dict_keys_for_refs() -> None:
    model = ScriptedModel(
        [reply("", call("nav_query", command="Navigation.SearchPOI", KeyWord="充电站")), reply("找到了几个充电站。")]
    )
    result = await PlannerAgent(model).handle(VehicleState(), Turn(seat="主驾", text="附近充电站"), TraceRecorder())
    assert result.data["poi"][0]["name"]


# ---------- WS：clock 消息与 invoke source ----------


def test_ws_clock_fires_due_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedModel([reply("", call("body", command="WindowCtrl.Close"))])
    monkeypatch.setattr(main, "make_agent", lambda: (PlannerAgent(model), None))
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "invoke", "tool": "task_master", "args": {"task": "十分钟后关车窗"}, "source": "agent"})
        assert ws.receive_json()["source"] == "agent"
        ws.receive_json(), ws.receive_json()
        ws.send_json({"type": "clock", "minutes": 15})
        assert ws.receive_json()["type"] == "state"
        clock = ws.receive_json()
        assert clock["type"] == "clock" and clock["due"]
        kinds = []
        while "assistant" not in kinds:
            kinds.append(ws.receive_json()["type"])
        ws.send_json({"type": "clock", "minutes": -1})
        while (msg := ws.receive_json())["type"] != "error":
            pass


def test_apps_data_endpoint() -> None:
    data = TestClient(app).get("/api/apps").json()
    assert data["merchants"][0]["menu"] and data["songs"] and data["poi"][0]["name"] == "国贸"


def test_web_search_miss_is_not_found() -> None:
    hit, _ = _run(VehicleState(), "web_search", {"task": "最近有什么好看的电影"})
    assert hit.status == st.SUCCESS and "热映" in hit.result["message"]
    miss, _ = _run(VehicleState(), "web_search", {"task": "火星上的房价"})
    assert miss.status == st.NOT_FOUND
