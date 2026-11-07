"""M4：安全闸门、快速通道、会话网关（断网 / 弱网）。"""

from __future__ import annotations

from typing import Any

import pytest

from backend.brain.planner import PlannerAgent
from backend.brain.types import Turn, TurnResult
from backend.edge.fast_path import load_fast_path
from backend.edge.gate import load_gate
from backend.edge.gateway import OFFLINE_REPLY, mode_updates, network_mode
from backend.edge.text import core
from backend.llm.scripted import ScriptedModel, call, reply
from backend.protocol import status as st
from backend.trace.recorder import TraceRecorder
from backend.vehicle.executor import run_batch
from backend.vehicle.state import VehicleState
from eval.assertions import check_turn
from eval.run_eval import load_scenarios

SCENARIOS = load_scenarios()


def _allows_no_tool(expect: list[dict[str, Any]]) -> bool:
    for e in expect:
        if e["type"] == "no_tool":
            return True
        if e["type"] == "any" and any(o["type"] == "no_tool" for o in e["options"]):
            return True
    return bool(expect) and all(e["type"] == "not_called" for e in expect)


def _agent(*steps: Any, **kw: Any) -> tuple[PlannerAgent, ScriptedModel]:
    model = ScriptedModel(list(steps))
    return PlannerAgent(model, weak_delay_s=0, **kw), model


async def _say(agent: PlannerAgent, state: VehicleState, text: str) -> tuple[TurnResult, TraceRecorder]:
    trace = TraceRecorder()
    return await agent.handle(state, Turn(seat="主驾", text=text), trace), trace


def _stages(trace: TraceRecorder) -> list[str]:
    return [e.stage for e in trace.events]


# ---------- 闸门 ----------


def test_gate_hits_only_turns_that_allow_no_tool() -> None:
    gate = load_gate()
    hits, bad = 0, []
    for case in SCENARIOS:
        for i, turn in enumerate(case["turns"]):
            hit = gate.check(turn.get("text") or "")
            if hit:
                hits += 1
                if not _allows_no_tool(turn.get("expect") or []):
                    bad.append((case["id"], i, turn["text"], hit.rule))
    assert not bad, bad
    assert hits >= 15  # permission 集合里的黑区/违法/未上线都要能挡住


@pytest.mark.parametrize(
    ("text", "category"),
    [("打开领航智驾", "black"), ("帮我挂到倒挡", "black"), ("帮我拨打400客服", "unsupported")],
)
def test_gate_categories(text: str, category: str) -> None:
    hit = load_gate().check(text)
    assert hit is not None and hit.category == category


def test_gate_blocked_call_checks_args_not_subagent_task() -> None:
    gate = load_gate()
    assert gate.blocked_call("switch_device", {"command": "Device.Open", "Device": "领航辅助"}) == "领航"
    assert gate.blocked_call("switch_device", {"command": "Device.Open", "Device": "空调"}) is None
    assert gate.blocked_call("car_expert", {"task": "自动泊车怎么用"}) is None


async def test_planner_gate_replies_without_model() -> None:
    agent, model = _agent()
    result, trace = await _say(agent, VehicleState(), "打开领航智驾")
    assert result.calls == () and result.reply
    assert model.requests == []
    assert "gate" in _stages(trace)


async def test_planner_unsupported_stage() -> None:
    agent, _ = _agent()
    _, trace = await _say(agent, VehicleState(), "帮我拨打400客服")
    assert "unsupported" in _stages(trace)


async def test_model_built_driving_call_is_intercepted() -> None:
    agent, _ = _agent(
        reply("", call("switch_device", command="Device.Open", Device="NOA领航")),
        reply("这类驾驶功能不能语音控制。"),
    )
    result, trace = await _say(agent, VehicleState(), "开一下那个辅助")
    assert result.calls[0].status == st.FAILED
    assert result.state is None
    assert any(e.stage == "gate" for e in trace.events)


# ---------- 快速通道 ----------


def test_core_strips_politeness() -> None:
    assert core("请帮我打开空调吧！") == "打开空调"


async def test_fast_path_hits_satisfy_case_expectations() -> None:
    fast = load_fast_path()
    good, bad = 0, []
    for case in SCENARIOS:
        turn = case["turns"][0]
        hit = fast.match(turn.get("text") or "")
        if hit is None:
            continue
        state = VehicleState.from_init(case.get("init"), case.get("profile"))
        outcome = await run_batch(state, [(hit.tool, hit.args)], turn["seat"], mode="serial")
        if not st.is_ok(outcome.calls[0].status):
            continue  # 联网时执行失败会转云端
        verdicts = check_turn(turn["expect"], TurnResult(tuple(outcome.calls)), [])
        if all(v.ok for v in verdicts):
            good += 1
        else:
            bad.append((case["id"], turn["text"], hit.label))
    assert not bad, bad
    assert good >= 200


def test_fast_path_rejects_multi_intent() -> None:
    assert load_fast_path().match("关闭空调关闭音乐") is None


async def test_planner_fast_path_skips_model() -> None:
    agent, model = _agent()
    result, trace = await _say(agent, VehicleState(), "帮我打开空调")
    assert model.requests == []
    assert result.calls[0].status == st.SUCCESS
    assert result.state is not None and result.state.get("ac.on") is True
    assert "fast_path" in _stages(trace)
    assert agent.memory.history_messages()  # 进了会话记忆，后续轮次能接上


async def test_fast_path_failure_goes_to_cloud_when_online() -> None:
    agent, model = _agent(
        reply("", call("switch_device", command="Device.Open", Device="HUD")),
        reply("这台车没有 HUD。"),
    )
    result, _ = await _say(agent, VehicleState.from_init(profile="basic"), "打开HUD")
    assert len(model.requests) == 2
    assert result.reply == "这台车没有 HUD。"


# ---------- 网关 ----------


def test_network_modes_roundtrip() -> None:
    for mode in ("正常", "弱网", "断网"):
        state, _ = VehicleState().apply(mode_updates(mode))
        assert network_mode(state) == mode
    with pytest.raises(ValueError):
        mode_updates("5G")


def _offline() -> VehicleState:
    return VehicleState().apply(mode_updates("断网"))[0]


async def test_offline_fast_path_still_works() -> None:
    agent, model = _agent()
    result, _ = await _say(agent, _offline(), "打开空调")
    assert model.requests == []
    assert result.state is not None and result.state.get("ac.on") is True


async def test_offline_online_music_returns_notnet() -> None:
    agent, model = _agent()
    result, _ = await _say(agent, _offline(), "播放周杰伦的稻香")
    assert model.requests == []
    assert result.calls[0].status == st.NOT_NET


async def test_offline_miss_does_not_call_model() -> None:
    agent, model = _agent()
    result, trace = await _say(agent, _offline(), "导航去最近的加油站")
    assert model.requests == [] and result.calls == ()
    assert result.reply == OFFLINE_REPLY
    assert any(e.stage == "gateway" and e.data.get("route") == "offline" for e in trace.events)


async def test_weak_network_delays_cloud_request(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("backend.brain.planner.asyncio.sleep", fake_sleep)
    model = ScriptedModel([reply("你好！")])
    agent = PlannerAgent(model, weak_delay_s=1.5)
    weak = VehicleState().apply(mode_updates("弱网"))[0]
    _, trace = await _say(agent, weak, "你好")
    assert slept == [1.5]
    llm = [e for e in trace.events if e.stage == "llm"]
    assert llm and llm[0].data["route"] == "cloud" and llm[0].data["weak_delay_ms"] == 1500
