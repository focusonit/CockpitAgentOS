"""规划器循环：用脚本化的假模型测试，不访问网络。"""

from __future__ import annotations

from typing import Any

from backend.brain.memory import Memory
from backend.brain.planner import PlannerAgent, classify_answer
from backend.brain.prompt import build_user_message
from backend.brain.types import Turn
from backend.llm.scripted import ScriptedModel, call, reply
from backend.protocol import status as st
from backend.trace.recorder import TraceRecorder
from backend.vehicle.state import VehicleState


def _agent(*steps: Any) -> tuple[PlannerAgent, ScriptedModel]:
    model = ScriptedModel(list(steps))
    return PlannerAgent(model), model


async def _say(agent: PlannerAgent, state: VehicleState, text: str, seat: str = "主驾"):
    return await agent.handle(state, Turn(seat=seat, text=text), TraceRecorder())


async def test_parallel_vehicle_calls_finish_without_second_llm_round() -> None:
    agent, model = _agent(
        reply(
            "",
            call("climate", command="ACCtrl.SetTemperature", Number=22),
            call("body", command="WindowCtrl.Close"),
        )
    )
    result = await _say(agent, VehicleState(), "空调22度，关窗")
    assert [c.status for c in result.calls] == [st.SUCCESS, st.SUCCESS]
    assert len(model.requests) == 1  # 全部车控成功：用执行结果拼回复，不再问模型
    assert "22" in result.reply
    assert result.state is not None and result.state.get("ac.temp.主驾") == 22


async def test_failed_call_goes_back_to_model() -> None:
    agent, model = _agent(
        reply("", call("switch_device", command="Device.Open", Device="HUD")),
        reply("这台车没有 HUD，可以把导航投到中控屏。"),
    )
    result = await _say(agent, VehicleState.from_init(profile="basic"), "打开HUD")
    assert result.calls[0].status == st.NOT_FOUND
    assert len(model.requests) == 2
    tool_msg = model.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "NotFound" in tool_msg["content"]
    assert result.reply.startswith("这台车没有 HUD")
    assert result.state is None


async def test_confirm_is_held_then_executed_on_yes() -> None:
    agent, _ = _agent(
        reply("", call("phone", command="BcallCall.DialContact")),
        reply("要帮你呼叫道路救援吗？"),
    )
    first = await _say(agent, VehicleState(), "帮我叫道路救援")
    assert first.confirm_requested
    assert first.calls[0].status == st.PENDING_CONFIRM
    second = await _say(agent, VehicleState(), "确认")
    assert [c.status for c in second.calls] == [st.SUCCESS]
    assert second.reply.startswith("已确认")
    assert agent.memory.held == ()


async def test_confirm_cancelled_on_no() -> None:
    agent, model = _agent(
        reply("", call("open_page", command="APP.Uninstall", App="抖音")),
        reply("确定卸载抖音吗？"),
    )
    await _say(agent, VehicleState(), "卸载抖音")
    result = await _say(agent, VehicleState(), "算了")
    assert result.calls == () and "取消" in result.reply
    assert len(model.requests) == 2  # 取消不再调用模型


async def test_other_topic_drops_held_call() -> None:
    agent, _ = _agent(
        reply("", call("phone", command="EcallCall.DialContact")),
        reply("要紧急呼叫吗？"),
        reply("今天天气不错。"),
    )
    await _say(agent, VehicleState(), "紧急呼叫")
    result = await _say(agent, VehicleState(), "今天天气怎么样")
    assert result.calls == () and agent.memory.held == ()


async def test_preference_is_internal_and_shows_up_next_turn() -> None:
    agent, model = _agent(
        reply("", call("remember_preference", text="怕冷")),
        reply("记住了，你怕冷。"),
        reply("", call("climate", command="ACCtrl.SetTemperature", Number=26)),
    )
    first = await _say(agent, VehicleState(), "我比较怕冷")
    assert first.calls == ()  # 记偏好不算对外的工具调用
    await _say(agent, VehicleState(), "开空调")
    context = model.requests[2]["messages"][-1]["content"]
    assert "【已记下的偏好】怕冷" in context
    # 历史只保留原话，不带车况
    history = [m for m in model.requests[2]["messages"] if m["role"] == "user"]
    assert "【车况】" not in history[0]["content"]


async def test_bad_arguments_and_unknown_tool_are_reported_back() -> None:
    bad = call("climate", command="ACCtrl.SetTemperature")
    bad = type(bad)(bad.id, bad.name, {}, "{oops", parse_error="参数不是合法 JSON")
    agent, model = _agent(reply("", bad, call("teleport", command="Go")), reply("抱歉没办成。"))
    result = await _say(agent, VehicleState(), "随便")
    assert [c.status for c in result.calls] == [st.INVALID_PARAM, st.UNKNOWN_COMMAND]
    assert len(model.requests) == 2


async def test_rounds_are_capped() -> None:
    steps = [reply("", call("web_search", task="天气")) for _ in range(4)]
    agent, model = _agent(*steps)
    agent.max_rounds = 4
    result = await _say(agent, VehicleState(), "查天气")
    assert len(model.requests) == 4 and "没能" in result.reply


async def test_state_and_delta_callbacks() -> None:
    agent, _ = _agent(reply("", call("switch_device", command="Device.Open", Device="空调")))
    seen: list[Any] = []

    async def on_state(state: VehicleState, diffs: list) -> None:
        seen.append((state.get("ac.on"), [d.path for d in diffs]))

    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    await agent.handle(VehicleState(), Turn(seat="主驾", text="开空调"), TraceRecorder(), on_delta, on_state)
    assert seen == [(True, ["ac.on"])]

    agent2, _ = _agent(reply("你好呀"))
    await agent2.handle(VehicleState(), Turn(seat="主驾", text="你好"), TraceRecorder(), on_delta)
    assert deltas == ["你好呀"]


async def test_event_turn_runs_as_driver() -> None:
    agent, model = _agent(reply("", call("seat", command="SeatCtrl.SetHeating", Number="高")))
    result = await agent.handle(VehicleState(), Turn(seat="系统", event="到点：打开座椅加热"), TraceRecorder())
    assert result.state is not None and result.state.get("seats.主驾.heat") == 3
    assert "【系统事件】到点" in model.requests[0]["messages"][-1]["content"]


def test_classify_answer() -> None:
    assert classify_answer("确认。") == "yes"
    assert classify_answer("好的") == "yes"
    assert classify_answer("确认下单") == "yes"
    assert classify_answer("算了吧") == "no"
    assert classify_answer("不要") == "no"
    assert classify_answer("导航去公司") is None


def test_context_includes_aliases_and_pending() -> None:
    memory = Memory().push_pending("导航去机场")
    text = build_user_message(VehicleState(), Turn(seat="副驾", text="去大裤衩"), memory, {"大裤衩": "中央电视台总部大楼"})
    assert "【别名】大裤衩 = 中央电视台总部大楼" in text
    assert "【挂起的任务（最后一项在栈顶）】导航去机场" in text
    assert text.endswith("【说话人】副驾\n【用户】去大裤衩")
