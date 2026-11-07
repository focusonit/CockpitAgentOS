"""M7：豆包双工语音。帧编解码、语音桥的播报决策和打断、WS 二进制接入；不访问网络。"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, AsyncIterator

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import app
from backend.brain.echo import EchoAgent
from backend.brain.planner import PlannerAgent
from backend.brain.types import ToolCall, Turn, TurnResult
from backend.llm.scripted import ScriptedModel, call, reply
from backend.trace.recorder import TraceRecorder
from backend.trace.source import reply_source
from backend.voice import bridge as bridge_mod
from backend.voice import frames as fr
from backend.voice.bridge import VoiceBridge, is_ack, planner_took_over
from backend.voice.s2s import VoiceError, load_voice_config

CONFIG = load_voice_config()
SEAT_AUDIO = b"\x01\x02" * 320


# ---------- 帧 ----------


def server_frame(event: int, payload: dict[str, Any] | bytes, session_id: str = "s1", error: int | None = None) -> bytes:
    """按服务端格式拼一帧（测试用）。"""
    audio = isinstance(payload, bytes)
    kind = fr.SERVER_ERROR if error is not None else fr.SERVER_AUDIO if audio else fr.SERVER_FULL
    body = payload if audio else json.dumps(payload, ensure_ascii=False).encode()
    out = bytes([0x11, (kind << 4) | fr.FLAG_EVENT, (fr.SER_RAW if audio else fr.SER_JSON) << 4, 0])
    if error is not None:
        out += struct.pack(">I", error)
    sid = b"" if event in fr.CONNECTION_EVENTS else session_id.encode()
    out += struct.pack(">I", event) + struct.pack(">I", len(sid)) + sid
    return out + struct.pack(">I", len(body)) + body


def test_frames_round_trip() -> None:
    frame = fr.decode(server_frame(fr.CHAT_RESPONSE, {"content": "好的"}))
    assert frame.event == fr.CHAT_RESPONSE and frame.session_id == "s1" and frame.payload == {"content": "好的"}
    audio = fr.decode(server_frame(fr.TTS_RESPONSE, b"\x00\x01\x02"))
    assert audio.kind == fr.SERVER_AUDIO and audio.audio == b"\x00\x01\x02"
    err = fr.decode(server_frame(fr.DIALOG_ERROR, {"error": "坏了"}, error=45000001))
    assert err.is_error and err.code == 45000001 and err.payload["error"] == "坏了"
    started = fr.decode(server_frame(fr.CONNECTION_STARTED, {}))
    assert started.event == fr.CONNECTION_STARTED and started.session_id == ""


def test_client_frames_layout() -> None:
    connect = fr.encode(fr.START_CONNECTION)
    assert connect[:4] == bytes([0x11, 0x14, 0x10, 0]) and struct.unpack(">II", connect[4:12]) == (1, 2)
    assert connect[12:] == b"{}"  # 连接级事件：事件号后直接是 payload，不带 id
    audio = fr.encode(fr.TASK_REQUEST, b"\x00" * 640, "abc")
    assert audio[1] >> 4 == fr.CLIENT_AUDIO and audio[2] >> 4 == fr.SER_RAW
    assert audio[12:15] == b"abc" and struct.unpack(">I", audio[15:19])[0] == 640


def test_decode_rejects_truncated_frame() -> None:
    with pytest.raises(fr.FrameError):
        fr.decode(server_frame(fr.CHAT_RESPONSE, {"content": "好的"})[:-3])


def test_missing_key_is_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.voice import s2s

    monkeypatch.setattr(s2s, "load_dotenv", lambda *_: None)
    monkeypatch.delenv("VOLC_S2S_API_KEY", raising=False)
    with pytest.raises(VoiceError, match="VOLC_S2S_API_KEY"):
        s2s.api_key_from_env()


# ---------- 播报决策 ----------


@pytest.mark.parametrize(
    ("text", "ok"),
    [("好的，马上办。", True), ("收到", True), ("", False), ("已为你调高空调温度", False),
     ("正在为你播放周杰伦的《晴天》", False), ("今天天气不错，适合出去走走哦", False)],
)
def test_is_ack(text: str, ok: bool) -> None:
    assert is_ack(text, CONFIG) is ok


def test_planner_took_over() -> None:
    trace = TraceRecorder()
    assert not planner_took_over(TurnResult(reply="你好"), trace)
    assert planner_took_over(TurnResult(calls=[ToolCall("climate", {"command": "ACCtrl.Open"})]), trace)
    assert planner_took_over(TurnResult(reply="要拨打吗", confirm_requested=True), trace)
    trace.add("gate", "驾驶类", route="edge")
    assert planner_took_over(TurnResult(reply="这个我办不了"), trace)


class FakeUpstream:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[fr.Frame | None] = asyncio.Queue()
        self.audio: list[bytes] = []
        self.said: list[str] = []
        self.closed = False
        self.script: list[bytes] = []

    def push(self, *raw: bytes) -> None:
        for r in raw:
            self.queue.put_nowait(fr.decode(r))

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)
        if self.script:  # 收到第一包音频时“听到”用户说话
            script, self.script = self.script, []
            self.push(*script)

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def events(self) -> AsyncIterator[fr.Frame]:
        while (frame := await self.queue.get()) is not None:
            yield frame

    async def close(self) -> None:
        self.closed = True
        self.queue.put_nowait(None)


class FakeHost:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.audio: list[bytes] = []
        self.turns: list[tuple[Turn, Any]] = []
        self.interrupts = 0

    async def send(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def start_turn(self, *turns: Turn, on_result: Any = None) -> None:
        self.turns.extend((t, on_result) for t in turns)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["type"] == kind]


def utterance(text: str) -> list[bytes]:
    return [
        server_frame(fr.ASR_INFO, {}),
        server_frame(fr.ASR_RESPONSE, {"results": [{"text": text[:2], "is_interim": True}]}),
        server_frame(fr.ASR_RESPONSE, {"results": [{"text": text, "is_interim": False}]}),
        server_frame(fr.ASR_ENDED, {}),
    ]


def default_reply(text: str, audio: bytes = b"AA") -> list[bytes]:
    return [
        server_frame(fr.TTS_SENTENCE_START, {"tts_type": "default"}),
        server_frame(fr.CHAT_RESPONSE, {"content": text}),
        server_frame(fr.TTS_RESPONSE, audio),
        server_frame(fr.CHAT_ENDED, {}),
    ]


async def feed(bridge: VoiceBridge, raw: list[bytes]) -> None:
    for r in raw:
        await bridge.handle(fr.decode(r))


def make_bridge(seat: str = "主驾") -> tuple[VoiceBridge, FakeHost, FakeUpstream]:
    host, upstream = FakeHost(), FakeUpstream()
    return VoiceBridge(host, upstream, CONFIG, seat), host, upstream


TASK = TurnResult(calls=[ToolCall("climate", {"command": "ACCtrl.SetTemperature"})], reply="空调已调到 26 度，正在播放周杰伦")


async def test_utterance_becomes_planner_turn_with_seat() -> None:
    bridge, host, _ = make_bridge("副驾")
    await feed(bridge, utterance("有点冷，顺便放首周杰伦"))
    assert host.interrupts == 1  # 开口即打断
    assert [t.text for t, _ in host.turns] == ["有点冷，顺便放首周杰伦"]
    assert host.turns[0][0].seat == "副驾"
    finals = [m for m in host.of("voice_asr") if m["final"]]
    assert finals == [{"type": "voice_asr", "text": "有点冷，顺便放首周杰伦", "final": True, "seat": "副驾"}]


async def test_ack_plays_then_planner_reply_is_spoken() -> None:
    bridge, host, upstream = make_bridge()
    await feed(bridge, utterance("有点冷，顺便放首周杰伦") + default_reply("好的，马上办。"))
    assert host.audio == [b"AA"]  # 短应答不等规划器
    assert host.of("voice_reply") == [{"type": "voice_reply", "text": "好的，马上办。", "kind": "ack"}]
    await host.turns[0][1](TASK, TraceRecorder())
    assert upstream.said == [TASK.reply]
    await feed(bridge, [server_frame(fr.TTS_SENTENCE_START, {"tts_type": "chat_tts_text"}), server_frame(fr.TTS_RESPONSE, b"PP")])
    assert host.audio == [b"AA", b"PP"]


async def test_fabricated_default_is_dropped() -> None:
    bridge, host, upstream = make_bridge()
    await feed(bridge, utterance("有点冷") + default_reply("好的，已为你调高空调温度", b"FAKE"))
    assert host.audio == [] and not host.of("voice_reply")
    await host.turns[0][1](TASK, TraceRecorder())
    await feed(bridge, [server_frame(fr.TTS_RESPONSE, b"LATE")])  # 默认回复迟到的音频也丢掉
    assert host.audio == [] and upstream.said == [TASK.reply]


async def test_chat_speaks_planner_reply_only() -> None:
    bridge, host, upstream = make_bridge()
    await feed(bridge, utterance("讲个笑话") + default_reply("从前有个程序员，他从来不写测试……", b"JOKE"))
    assert host.audio == []
    await host.turns[0][1](TurnResult(reply="笑话来了"), TraceRecorder())
    assert host.audio == [] and upstream.said == ["笑话来了"]
    assert not host.of("voice_reply")


async def test_status_query_is_answered_once() -> None:
    """问车况不调工具：语音模型编的短句不能当应答先播，也不能和规划器的回复一起出现。"""
    bridge, host, upstream = make_bridge()
    await feed(bridge, utterance("什么驾驶模式") + default_reply("当前是标准驾驶模式", b"FAKE"))
    assert host.audio == [] and not host.of("voice_reply")
    await host.turns[0][1](TurnResult(reply="现在是舒适模式，能量回收是中档。"), TraceRecorder())
    assert host.audio == [] and not host.of("voice_reply")
    assert upstream.said == ["现在是舒适模式，能量回收是中档。"]


def test_short_non_ack_is_not_ack() -> None:
    config = load_voice_config()
    assert is_ack("好的，马上办。", config) and is_ack("收到", config)
    assert not is_ack("现在是舒适模式", config) and not is_ack("二十六度", config)


async def test_planner_error_falls_back_to_default() -> None:
    bridge, host, _ = make_bridge()
    await feed(bridge, utterance("今天几号") + default_reply("今天是十一月六号星期五哦", b"DATE"))
    await host.turns[0][1](None, None)
    assert host.audio == [b"DATE"]


async def test_barge_in_discards_old_result() -> None:
    bridge, host, upstream = make_bridge()
    await feed(bridge, utterance("有点冷，顺便放首周杰伦") + default_reply("好的，马上办。"))
    old_hook = host.turns[0][1]
    await feed(bridge, utterance("算了"))
    assert host.interrupts == 2 and len(host.of("voice_barge_in")) == 2
    await old_hook(TASK, TraceRecorder())  # 被打断的那一轮结果作废，不再播
    assert upstream.said == []
    assert host.turns[-1][0].text == "算了"


async def test_empty_utterance_starts_nothing() -> None:
    bridge, host, _ = make_bridge()
    await feed(bridge, [server_frame(fr.ASR_INFO, {}), server_frame(fr.ASR_ENDED, {})])
    assert host.turns == []


async def test_error_frame_reported() -> None:
    bridge, host, _ = make_bridge()
    await feed(bridge, [server_frame(fr.DIALOG_ERROR, {"error": "额度不足"}, error=1)])
    assert "额度不足" in host.of("error")[0]["message"]


# ---------- WS 接入 ----------

client = TestClient(app)


def _drain_until(ws, kind: str, limit: int = 30) -> dict:  # type: ignore[no-untyped-def]
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("text"):
            data = json.loads(msg["text"])
            if data["type"] == kind:
                return data
    raise AssertionError(f"没有收到 {kind}")


def test_voice_start_without_key_keeps_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "make_agent", lambda: (EchoAgent(), None))

    async def no_key() -> Any:
        raise VoiceError("没有配置 VOLC_S2S_API_KEY")

    monkeypatch.setattr(main, "open_upstream", no_key)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "voice_start", "seat": "主驾"})
        assert _drain_until(ws, "voice")["state"] == "off"
        ws.send_bytes(SEAT_AUDIO)  # 语音没开时的音频直接忽略
        ws.send_json({"type": "user_say", "seat": "主驾", "text": "你好"})
        assert _drain_until(ws, "assistant")["reply"]


def test_voice_turn_controls_car_and_text_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    model = ScriptedModel([
        reply("", call("climate", command="ACCtrl.SetTemperature", Number=26),
              call("media_play", command="Music.Play", Singer="周杰伦")),
        reply("你好呀"),
        reply("你好呀"),
    ])
    monkeypatch.setattr(main, "make_agent", lambda: (PlannerAgent(model, weak_delay_s=0), None))
    upstream = FakeUpstream()
    upstream.script = utterance("有点冷，顺便放首周杰伦") + default_reply("好的，马上办。", b"ACK!")

    async def fake_open() -> FakeUpstream:
        return upstream

    monkeypatch.setattr(main, "open_upstream", fake_open)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "voice_start", "seat": "主驾"})
        assert _drain_until(ws, "voice")["state"] == "listening"
        ws.send_bytes(SEAT_AUDIO)
        asr = _drain_until(ws, "voice_asr")
        assert asr["text"]
        result = _drain_until(ws, "assistant")
        assert {c["args"]["command"] for c in result["calls"]} == {"ACCtrl.SetTemperature", "Music.Play"}
        _drain_until(ws, "trace")
        # 文字输入照常可用
        ws.send_json({"type": "user_say", "seat": "主驾", "text": "你好"})
        assert _drain_until(ws, "assistant")["reply"]
        ws.send_json({"type": "voice_stop"})
        assert _drain_until(ws, "voice")["state"] == "off"
    assert upstream.audio == [SEAT_AUDIO] and upstream.closed
    assert len(upstream.said) == 1  # 车控轮次念的是规划器的回复


async def _mic_check_errors(*chunks: bytes) -> list[str]:
    """跑一小段 run()：喂完音频后等麦克风检查触发，返回发出的错误。"""
    host, upstream = FakeHost(), FakeUpstream()
    bridge = VoiceBridge(host, upstream, CONFIG, mic_check_s=0.05)
    task = asyncio.create_task(bridge.run())
    for chunk in chunks:
        await bridge.feed_audio(chunk)
    await asyncio.sleep(0.1)
    await bridge.close()
    await asyncio.wait_for(task, 1)
    return [m["message"] for m in host.of("error")]


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ((), [bridge_mod.NO_AUDIO_HINT]),  # 浏览器没在采集
        ((b"\x00\x00" * 320, b"\x05\x00" * 320), [bridge_mod.SILENT_HINT]),  # 只有静音
        ((b"\x00\x00" * 320, b"\x00\x10" * 320), []),  # 有声音（峰值 4096）
        ((b"\x00\x80" * 320,), []),  # -32768 也算有声音
    ],
)
async def test_mic_check_explains_why_nothing_is_heard(chunks: tuple[bytes, ...], expected: list[str]) -> None:
    assert await _mic_check_errors(*chunks) == expected


async def test_spoken_planner_reply_is_marked() -> None:
    bridge, host, _ = make_bridge()
    await feed(bridge, utterance("什么驾驶模式") + default_reply("当前是标准驾驶模式", b"FAKE"))
    await host.turns[0][1](TurnResult(reply="现在是舒适模式。"), TraceRecorder())
    assert host.of("voice_tts") == [{"type": "voice_tts", "text": "现在是舒适模式。"}]


def _trace(*events: tuple[str, str, dict]) -> TraceRecorder:
    trace = TraceRecorder()
    for stage, message, data in events:
        trace.add(stage, message, **data)
    return trace


@pytest.mark.parametrize(
    ("events", "kind", "label"),
    [
        ([("gateway", "云端规划（正常）", {"route": "cloud"}), ("llm", "第 1 轮：直接回复", {"model": "deepseek-chat", "route": "cloud"})],
         "llm", "云端大模型 · deepseek-chat"),
        ([("fast_path", "快速通道：开空调 → Succeed", {"route": "edge"})], "fast_path", "端侧快速通道"),
        ([("fast_path", "快速通道：开车窗 → Failed，转云端", {"route": "edge"}),
          ("llm", "第 1 轮：window", {"model": "m", "route": "cloud"})], "llm", "云端大模型 · m"),
        ([("gate", "黑区：自动驾驶", {"route": "edge"})], "gate", "端侧安全闸门"),
        ([("unsupported", "未上线：xx", {"route": "edge"})], "unsupported", "端侧闸门 · 未上线能力"),
        ([("gateway", "断网", {"route": "offline"})], "offline", "端侧 · 断网兜底"),
        ([("confirm", "用户确认", {})], "confirm", "端侧 · 确认流程"),
        ([("planner", "EchoAgent 占位", {})], "echo", "Echo 占位（没配大模型）"),
        ([], "rule", "车机规则"),
    ],
)
def test_reply_source(events: list, kind: str, label: str) -> None:
    assert reply_source(_trace(*events)) == {"kind": kind, "label": label}
