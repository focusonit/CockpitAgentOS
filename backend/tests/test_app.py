import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import app
from backend.brain.echo import EchoAgent
from backend.brain.planner import PlannerAgent
from backend.llm.base import LLMReply
from backend.llm.scripted import ScriptedModel, call, reply

client = TestClient(app)


@pytest.fixture(autouse=True)
def echo_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认用 EchoAgent，测试不访问真模型。"""
    monkeypatch.setattr(main, "make_agent", lambda: (EchoAgent(), None))


def _use_planner(monkeypatch: pytest.MonkeyPatch, model: object) -> None:
    monkeypatch.setattr(main, "make_agent", lambda: (PlannerAgent(model), None))  # type: ignore[arg-type]


def test_health() -> None:
    assert client.get("/api/health").json() == {"ok": True}


def test_ws_roundtrip() -> None:
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["state"]["gear"] == "P"

        ws.send_json({"type": "user_say", "seat": "副驾", "text": "有点热"})
        reply = ws.receive_json()
        assert reply["type"] == "assistant"
        assert "有点热" in reply["reply"]
        trace = ws.receive_json()
        assert trace["type"] == "trace"
        assert [e["stage"] for e in trace["events"]][0] == "input"


def test_ws_set_state_and_errors() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_state", "updates": {"speed_kmh": "80", "ac.on": "开"}})
        msg = ws.receive_json()
        assert msg["state"]["speed_kmh"] == 80
        assert {d["path"] for d in msg["diff"]} == {"speed_kmh", "ac.on"}

        ws.send_json({"type": "set_state", "updates": {"bogus.path": 1}})
        assert ws.receive_json()["type"] == "error"

        ws.send_json({"type": "user_say", "seat": "后备箱", "text": "hi"})
        assert ws.receive_json()["type"] == "error"

        ws.send_json({"type": "reset", "profile": "basic"})
        assert ws.receive_json()["state"]["profile"] == "basic"


def test_protocol_endpoint() -> None:
    body = client.get("/api/protocol").json()
    assert "climate" in body["tools"]
    assert body["statuses"]["NotAC"]
    assert "主驾" in body["seats"]
    visible = [name for name, tool in body["tools"].items() if not tool["hidden"]]
    assert len(client.get("/api/protocol/openai").json()) == len(visible)
    assert "meituan_app" not in visible  # 触屏专用的工具不给模型


def test_ws_invoke() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json(
            {"type": "invoke", "tool": "seat", "seat": "副驾", "args": {"command": "SeatCtrl.SetHeating", "Number": "高"}}
        )
        result = ws.receive_json()
        assert result["type"] == "invoke_result" and result["call"]["status"] == "Success"
        state = ws.receive_json()
        assert state["state"]["seats"]["副驾"]["heat"] == 3
        assert state["diff"] == [{"path": "seats.副驾.heat", "old": 0, "new": 3}]
        assert ws.receive_json()["type"] == "trace"

        ws.send_json({"type": "invoke", "tool": "seat", "args": "bad"})
        assert ws.receive_json()["type"] == "error"


def test_ws_planner_streams_and_pushes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedModel([reply("", call("switch_device", command="Device.Open", Device="空调"))])
    _use_planner(monkeypatch, model)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["brain"] == "PlannerAgent"
        ws.send_json({"type": "user_say", "seat": "主驾", "text": "打开空调"})
        state = ws.receive_json()
        assert state["type"] == "state" and state["state"]["ac"]["on"] is True
        assert any(d["path"] == "ac.on" for d in state["diff"])
        answer = ws.receive_json()
        assert answer["type"] == "assistant" and answer["calls"][0]["status"] == "Success"
        assert ws.receive_json()["type"] == "trace"


def test_ws_new_message_interrupts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(_messages: list) -> LLMReply:
        await asyncio.sleep(5)
        return reply("太慢了")

    class Model(ScriptedModel):
        async def complete(self, messages, tools=None, on_delta=None):  # type: ignore[override]
            if not self.requests:
                self.requests.append({})
                return await slow(messages)
            return await super().complete(messages, tools, on_delta)

    _use_planner(monkeypatch, Model([reply("好的，换个话题")]))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_say", "seat": "主驾", "text": "讲个很长的故事"})
        ws.send_json({"type": "user_say", "seat": "主驾", "text": "算了，不讲了"})
        assert ws.receive_json()["type"] == "interrupted"
        assert ws.receive_json() == {"type": "assistant_delta", "text": "好的，换个话题"}
        answer = ws.receive_json()
        assert answer["type"] == "assistant" and answer["reply"] == "好的，换个话题"
