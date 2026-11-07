"""FastAPI 入口：HTTP 健康检查 + WebSocket 会话。

启动：``uv run uvicorn backend.app.main:app --reload --port 8000``

WebSocket 协议（JSON，``type`` 字段区分）：
- 客户端 → 服务端：``user_say {seat, text}``、``event {seat, event}``、``set_state {updates}``、``reset {init?, profile?}``、
  ``invoke {tool, args, seat}``（调试面板直接下发指令）、``voice_start {seat}`` / ``voice_stop`` / ``voice_seat {seat}``（M7 语音）；
  语音开着时，二进制帧是麦克风音频（PCM 16k 单声道 int16）
- 服务端 → 客户端：``hello {session_id, state, brain, warning?, voice}``、``assistant_delta {text}``（流式回复片段）、
  ``assistant {reply, calls}``、``interrupted {}``（上一轮被新消息打断）、``state {state, diff}``、
  ``trace {request_id, events}``、``invoke_result {call}``、``error {message}``；
  语音：``voice {state}``、``voice_asr {text, final, seat}``、``voice_reply {text, kind}``、``voice_barge_in``，
  二进制帧是要播的音频（pcm_s16le 24k）

user_say / event 在后台任务里处理；处理中又来一条 user_say / event 时，取消正在进行的那一轮
（已经执行的车控保留），再处理新的一条。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from backend.app.demos import load_demos
from backend.app.phone import PhoneBridge, PhoneError

from backend.app.session import Session
from backend.apps.common import load as load_app_data
from backend.brain.echo import Agent, EchoAgent
from backend.brain.planner import TIMER_EVENT, PlannerAgent
from backend.brain.types import Turn
from backend.llm import LLMError, model_from_env
from backend.protocol import status as st
from backend.protocol.schema import openai_tools
from backend.protocol.spec import load_protocol
from backend.trace.source import reply_source
from backend.vehicle.rules import load_rules
from backend.vehicle.state import PathError, VehicleState
from backend.voice.bridge import ResultHook, VoiceBridge
from backend.voice.s2s import S2SClient, Upstream, VoiceError, api_key_from_env, load_voice_config

logger = logging.getLogger("cockpit")
app = FastAPI(title="Agentic 座舱模拟系统")

VALID_SEATS = {"主驾", "副驾", "左后排", "右后排", "系统"}
MAX_TEXT_LEN = 500
MAX_CLOCK_MINUTES = 24 * 60
INVOKE_SOURCES = ("touch", "agent", "debug", "phone")
MAX_AUDIO_FRAME = 64 * 1024
phone_bridge = PhoneBridge()


async def open_upstream() -> Upstream:
    """连火山引擎 S2S；测试里换成假的。"""
    return await S2SClient.open(load_voice_config())


def voice_available() -> bool:
    try:
        api_key_from_env()
    except VoiceError:
        return False
    return True


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/state")
async def default_vehicle_state() -> dict[str, Any]:
    return VehicleState().to_dict()


@app.get("/api/protocol")
async def protocol() -> dict[str, Any]:
    """协议定义：调试面板据此生成控件。"""
    proto = load_protocol()
    return {
        "tools": proto.to_dict(),
        "statuses": st.DESCRIPTIONS,
        "seats": sorted(VALID_SEATS - {"系统"}),
        "limits": load_rules().limits,
    }


@app.get("/api/apps")
async def apps_data() -> dict[str, Any]:
    """模拟 App 的静态数据（商户和菜单、曲库、地图点位），前端画界面用；动态状态都在车况里。"""
    return {
        "merchants": load_app_data("meituan")["merchants"],
        "songs": load_app_data("music")["songs"],
        "poi": load_app_data("poi")["poi"],
    }


@app.get("/api/demos")
async def demos() -> list[dict[str, Any]]:
    return list(load_demos())


@app.get("/api/phone/status")
async def phone_status() -> dict[str, Any]:
    return phone_bridge.status()


@app.post("/api/phone/action")
async def phone_action(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await phone_bridge.act(str(payload.get("action", "")), payload.get("value"))
    except PhoneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/protocol/openai")
async def protocol_openai() -> list[dict[str, Any]]:
    """给 LLM 的 function schema，方便查看。"""
    return openai_tools(load_protocol())


def _parse_turn(msg: dict[str, Any]) -> Turn:
    seat = msg.get("seat", "主驾")
    if seat not in VALID_SEATS:
        raise ValueError(f"未知座位：{seat}")
    if msg["type"] == "event":
        return Turn(seat="系统", event=str(msg.get("event", ""))[:MAX_TEXT_LEN])
    text = str(msg.get("text", "")).strip()
    if not text:
        raise ValueError("输入为空")
    return Turn(seat=seat, text=text[:MAX_TEXT_LEN])


def make_agent() -> tuple[Agent, str | None]:
    """有 DeepSeek key 就用规划器，否则退回 EchoAgent 并给出提示。"""
    try:
        return PlannerAgent(model_from_env()), None
    except LLMError as exc:
        logger.warning("规划器不可用，退回 EchoAgent：%s", exc)
        return EchoAgent(), f"未配置大模型，当前只回显输入（{exc}）"


class Connection:
    """一个 WS 连接：串行化发送，并管理正在处理的那一轮。"""

    def __init__(self, ws: WebSocket, session: Session) -> None:
        self.ws = ws
        self.session = session
        self.current: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self.voice: VoiceBridge | None = None
        self._voice_task: asyncio.Task[None] | None = None

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_json(payload)

    async def send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(data)

    async def start_voice(self, seat: str) -> None:
        if self.voice:
            self.voice.seat = seat
            await self.send({"type": "voice", "state": "listening", "seat": seat})
            return
        try:
            upstream = await open_upstream()
        except VoiceError as exc:
            await self.send({"type": "voice", "state": "off", "seat": seat})
            await self.send({"type": "error", "message": str(exc)})
            return
        self.voice = VoiceBridge(self, upstream, load_voice_config(), seat)
        self._voice_task = asyncio.create_task(self._run_voice(self.voice))

    async def _run_voice(self, bridge: VoiceBridge) -> None:
        try:
            await bridge.run()
        finally:
            if self.voice is bridge:
                self.voice = None

    async def stop_voice(self) -> None:
        bridge, task = self.voice, self._voice_task
        self.voice, self._voice_task = None, None
        if bridge:
            await bridge.close()
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if bridge:
            try:
                await self.send({"type": "voice", "state": "off", "seat": bridge.seat})
            except (RuntimeError, WebSocketDisconnect):  # 车机 WS 已经断了
                pass

    async def feed_audio(self, data: bytes) -> None:
        if not self.voice or not 0 < len(data) <= MAX_AUDIO_FRAME:
            return
        try:
            await self.voice.feed_audio(data)
        except Exception as exc:  # 语音服务断了：关掉语音，文字输入照常
            logger.warning("上行音频失败：%s", exc)
            await self.stop_voice()
            await self.send({"type": "error", "message": "语音连接已断开，请重新打开麦克风"})

    async def interrupt(self) -> None:
        task, self.current = self.current, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await self.send({"type": "interrupted"})

    async def start_turn(self, *turns: Turn, on_result: ResultHook | None = None) -> None:
        """在后台依次处理这些轮次（多个到期任务排队触发）；新消息会打断。
        ``on_result`` 给语音桥用：每轮结束回调 (result, trace)，出错时回调 (None, None)。"""
        await self.interrupt()

        async def run_all() -> None:
            for turn in turns:
                await self._run_turn(turn, on_result)

        self.current = asyncio.create_task(run_all())

    async def _run_turn(self, turn: Turn, on_result: ResultHook | None = None) -> None:
        async def on_delta(text: str) -> None:
            await self.send({"type": "assistant_delta", "text": text})

        async def on_state(state: VehicleState, diffs: list[Any]) -> None:
            await self.send({"type": "state", "state": state.to_dict(), "diff": [d.to_dict() for d in diffs]})

        before = self.session.state
        try:
            result, trace = await self.session.handle(turn, on_delta=on_delta, on_state=on_state)
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            logger.warning("模型调用失败：%s", exc)
            await self.send({"type": "error", "message": f"大模型调用失败：{exc}"})
            if on_result:
                await on_result(None, None)
            return
        except Exception:  # 不让后台任务静默死掉
            logger.exception("处理一轮时出错")
            await self.send({"type": "error", "message": "处理这句话时出错了，请重试"})
            if on_result:
                await on_result(None, None)
            return
        await self.send({"type": "assistant", **result.to_dict(), "source": reply_source(trace)})
        if self.session.state is not before and not getattr(self.session.agent, "streaming", False):
            await self.send({"type": "state", "state": self.session.state.to_dict(), "diff": []})
        await self.send({"type": "trace", **trace.to_dict()})
        if on_result:
            await on_result(result, trace)


async def _dispatch(conn: Connection, msg: dict[str, Any]) -> None:
    session = conn.session
    send = conn.send
    kind = msg.get("type")
    if kind in ("user_say", "event"):
        await conn.start_turn(_parse_turn(msg))
    elif kind == "set_state":
        updates = msg.get("updates")
        if not isinstance(updates, dict):
            raise ValueError("updates 必须是对象")
        diffs = session.set_state(updates, parse=True)
        await send({"type": "state", "state": session.state.to_dict(), "diff": [d.to_dict() for d in diffs]})
    elif kind == "invoke":
        tool, args, seat = msg.get("tool"), msg.get("args"), msg.get("seat", "主驾")
        if not isinstance(tool, str) or not isinstance(args, dict):
            raise ValueError("invoke 需要 tool（字符串）和 args（对象）")
        if seat not in VALID_SEATS:
            raise ValueError(f"未知座位：{seat}")
        source = msg.get("source", "touch")
        if source not in INVOKE_SOURCES:
            raise ValueError(f"source 只能是 {'/'.join(INVOKE_SOURCES)}")
        call, diffs, trace = session.invoke(tool, args, seat, source)
        await send({"type": "invoke_result", "call": call.to_dict(), "source": source})
        await send({"type": "state", "state": session.state.to_dict(), "diff": [d.to_dict() for d in diffs]})
        await send({"type": "trace", **trace.to_dict()})
    elif kind == "clock":
        minutes = msg.get("minutes")
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not 0 < minutes <= MAX_CLOCK_MINUTES:
            raise ValueError(f"minutes 必须是 0–{MAX_CLOCK_MINUTES} 之间的数字")
        tick = session.advance(float(minutes))
        await send({"type": "state", "state": session.state.to_dict(), "diff": [d.to_dict() for d in tick.diffs]})
        await send({"type": "clock", "now": session.state.get("now"), "events": list(tick.events),
                    "due": [t["text"] for t in tick.due]})
        if tick.due:
            await conn.start_turn(*[Turn(seat="系统", event=TIMER_EVENT) for _ in tick.due])
    elif kind in ("voice_start", "voice_seat"):
        seat = msg.get("seat", "主驾")
        if seat not in VALID_SEATS - {"系统"}:
            raise ValueError(f"未知座位：{seat}")
        if kind == "voice_start":
            await conn.start_voice(seat)
        elif conn.voice:
            conn.voice.seat = seat
    elif kind == "voice_stop":
        await conn.stop_voice()
    elif kind == "reset":
        await conn.interrupt()
        session.reset(msg.get("init"), msg.get("profile"))
        await send({"type": "state", "state": session.state.to_dict(), "diff": []})
    else:
        raise ValueError(f"未知消息类型：{kind}")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    agent, warning = make_agent()
    session = Session(agent=agent)
    conn = Connection(ws, session)
    hello: dict[str, Any] = {
        "type": "hello",
        "session_id": session.session_id,
        "state": session.state.to_dict(),
        "brain": type(agent).__name__,
    }
    if warning:
        hello["warning"] = warning
    hello["voice"] = voice_available()
    await conn.send(hello)
    phone_bridge.attach(conn)
    try:
        while True:
            raw = await ws.receive()
            if raw["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(raw.get("code", 1000))
            if raw.get("bytes") is not None:
                await conn.feed_audio(raw["bytes"])
                continue
            try:
                msg = json.loads(raw.get("text") or "")
            except json.JSONDecodeError:
                await conn.send({"type": "error", "message": "消息不是合法 JSON"})
                continue
            try:
                if not isinstance(msg, dict):
                    raise ValueError("消息必须是 JSON 对象")
                await _dispatch(conn, msg)
            except (ValueError, PathError, KeyError) as exc:
                logger.info("拒绝消息 %s: %s", msg, exc)
                await conn.send({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        logger.info("会话 %s 断开", session.session_id)
    finally:
        phone_bridge.detach(conn)
        await conn.stop_voice()
        if conn.current and not conn.current.done():
            conn.current.cancel()
