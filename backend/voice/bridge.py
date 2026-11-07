"""语音桥：把火山引擎 S2S 的听、说接到规划器上（开发计划 §11 A 方案）。

- 用户一句话说完（ASREnded）→ 识别文字当作一句 user_say，走和文字输入完全相同的规划器路径。
- 语音模型自己的回复先缓存：是“好的，马上办”这类短应答就直接播；像在报结果（带“已”“为你”等字）
  或较长的，等规划器办完再定——规划器调了工具 / 要确认 / 走了端侧规则，就丢掉它、改念规划器的回复；
  规划器没调工具（闲聊），就照播语音模型的回复。
- 用户开口（ASRInfo）= 打断：前端清空播放队列，正在跑的规划器任务取消，缓存清掉。
"""

from __future__ import annotations

import array
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from backend.brain.types import Turn, TurnResult
from backend.trace.recorder import TraceRecorder
from backend.voice import frames as fr
from backend.voice.s2s import Upstream, VoiceConfig

logger = logging.getLogger("cockpit.voice")

ResultHook = Callable[[TurnResult | None, TraceRecorder | None], Awaitable[None]]
EDGE_ROUTES = ("edge", "offline")
TTS_PLANNER = "chat_tts_text"
MIC_CHECK_S = 4.0  # 打开语音后多久检查一次麦克风有没有数据
SILENT_PEAK = 200  # int16 峰值低于这个算全是静音
NO_AUDIO_HINT = "没收到麦克风的音频：浏览器可能没在采集（换 Chrome、确认地址栏的麦克风权限后，关掉语音再打开）"
SILENT_HINT = "麦克风只收到静音：检查系统设置里的输入设备和输入音量，或换一个麦克风"


class VoiceHost(Protocol):
    """语音桥需要的 WS 连接能力（``backend.app.main.Connection`` 实现）。"""

    async def send(self, payload: dict[str, Any]) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def interrupt(self) -> None: ...

    async def start_turn(self, *turns: Turn, on_result: ResultHook | None = None) -> None: ...


def is_ack(text: str, config: VoiceConfig) -> bool:
    """短应答：不长、带应答词（好的 / 收到…），也不像在报结果。

    只看长度不够：“现在是标准模式”也很短，但那是语音模型在编车况。
    """
    text = text.strip()
    if not text or len(text) > config.ack_max_chars or any(w in text for w in config.claim_words):
        return False
    return any(w in text for w in config.ack_words)


def planner_took_over(result: TurnResult, trace: TraceRecorder) -> bool:
    """这一轮是不是车机在办事（这时语音模型自己的回复不可信，要换成规划器的回复）。"""
    if result.calls or result.confirm_requested:
        return True
    return any(e.data.get("route") in EDGE_ROUTES for e in trace.events)


@dataclass
class _Reply:
    """一次语音模型默认回复的缓存。``decision``：None 未定 / play 播 / drop 丢。"""

    generation: int
    audio: list[bytes] = field(default_factory=list)
    text: str = ""
    chat_ended: bool = False
    decision: str | None = None
    shown: bool = False
    kind: str = "ack"


def _peak(pcm: bytes) -> int:
    """一包 int16 小端 PCM 的绝对值峰值。"""
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return max((abs(v) for v in samples), default=0)


class VoiceBridge:
    def __init__(
        self, host: VoiceHost, upstream: Upstream, config: VoiceConfig, seat: str = "主驾", mic_check_s: float = MIC_CHECK_S
    ) -> None:
        self.host = host
        self.upstream = upstream
        self.config = config
        self.seat = seat
        self._generation = 0
        self._reply = _Reply(0)
        self._asr_text = ""
        self._tts_type = ""
        self._planner_busy = False
        self._closed = False
        self.mic_check_s = mic_check_s
        self.audio_bytes = 0
        self.audio_peak = 0

    # ---------- 上行 ----------

    async def feed_audio(self, pcm: bytes) -> None:
        if self._closed:
            return
        self.audio_bytes += len(pcm)
        self.audio_peak = max(self.audio_peak, _peak(pcm))
        await self.upstream.send_audio(pcm)

    async def close(self) -> None:
        self._closed = True
        logger.info("语音会话结束：上行音频 %d 字节，峰值 %d", self.audio_bytes, self.audio_peak)
        await self.upstream.close()

    # ---------- 下行 ----------

    async def run(self) -> None:
        """读语音服务的事件直到连接结束。"""
        await self._state("listening")
        watchdog = asyncio.create_task(self._check_mic())
        try:
            async for frame in self.upstream.events():
                await self.handle(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 语音断了不影响文字输入
            logger.warning("语音连接中断：%s", exc)
            await self.host.send({"type": "error", "message": f"语音连接中断：{exc}"})
        finally:
            watchdog.cancel()
            if not self._closed:
                await self._state("off")

    async def _check_mic(self) -> None:
        """打开语音几秒后还没有可用的声音，就告诉用户卡在哪一段，而不是一直显示“在听”。"""
        await asyncio.sleep(self.mic_check_s)
        if self._closed:
            return
        if not self.audio_bytes:
            await self.host.send({"type": "error", "message": NO_AUDIO_HINT})
        elif self.audio_peak < SILENT_PEAK:
            await self.host.send({"type": "error", "message": SILENT_HINT})

    async def handle(self, frame: fr.Frame) -> None:
        if frame.is_error or frame.event in (fr.DIALOG_ERROR, fr.SESSION_FAILED):
            await self.host.send({"type": "error", "message": f"语音服务出错：{frame.payload.get('error') or frame.payload}"})
            return
        event = frame.event
        if event == fr.ASR_INFO:
            await self._barge_in()
        elif event == fr.ASR_RESPONSE:
            await self._asr(frame.payload)
        elif event == fr.ASR_ENDED:
            await self._utterance_done()
        elif event == fr.TTS_SENTENCE_START:
            self._tts_type = str(frame.payload.get("tts_type", ""))
        elif event == fr.TTS_RESPONSE:
            await self._audio(frame.audio)
        elif event == fr.CHAT_RESPONSE:
            self._reply.text += str(frame.payload.get("content", ""))
        elif event == fr.CHAT_ENDED:
            await self._chat_ended()
        elif event == fr.TTS_ENDED and not self._planner_busy:
            await self._state("listening")

    async def _state(self, state: str) -> None:
        await self.host.send({"type": "voice", "state": state, "seat": self.seat})

    async def _barge_in(self) -> None:
        self._generation += 1
        self._reply = _Reply(self._generation)
        self._asr_text = ""
        self._tts_type = ""
        self._planner_busy = False
        await self.host.send({"type": "voice_barge_in"})
        await self.host.interrupt()
        await self._state("user_speaking")

    async def _asr(self, payload: dict[str, Any]) -> None:
        results = payload.get("results") or []
        if not results:
            return
        text = str(results[0].get("text", "")).strip()
        if text:
            self._asr_text = text
            await self.host.send({"type": "voice_asr", "text": text, "final": False, "seat": self.seat})

    async def _utterance_done(self) -> None:
        text, self._asr_text = self._asr_text, ""
        if not text:
            await self._state("listening")
            return
        generation = self._generation
        self._planner_busy = True
        await self.host.send({"type": "voice_asr", "text": text, "final": True, "seat": self.seat})
        await self._state("thinking")

        async def on_result(result: TurnResult | None, trace: TraceRecorder | None) -> None:
            await self._planner_done(generation, result, trace)

        await self.host.start_turn(Turn(seat=self.seat, text=text), on_result=on_result)

    async def _audio(self, audio: bytes) -> None:
        if self._tts_type == TTS_PLANNER:  # 规划器回复的合成音频，直接播
            await self._forward(audio)
            return
        reply = self._reply
        if reply.decision == "play":
            await self._forward(audio)
        elif reply.decision is None:
            reply.audio.append(audio)

    async def _forward(self, audio: bytes) -> None:
        if audio:
            await self.host.send_bytes(audio)

    async def _chat_ended(self) -> None:
        reply = self._reply
        reply.chat_ended = True
        if reply.decision is None and is_ack(reply.text, self.config):
            reply.kind = "ack"
            await self._play(reply)
        elif reply.decision == "play":
            await self._show(reply)

    async def _play(self, reply: _Reply) -> None:
        reply.decision = "play"
        buffered, reply.audio = reply.audio, []
        if buffered:
            await self._state("speaking")
        for chunk in buffered:
            await self._forward(chunk)
        if reply.chat_ended:
            await self._show(reply)

    async def _show(self, reply: _Reply) -> None:
        if not reply.shown and reply.text.strip():
            reply.shown = True
            await self.host.send({"type": "voice_reply", "text": reply.text.strip(), "kind": reply.kind})

    async def _planner_done(self, generation: int, result: TurnResult | None, trace: TraceRecorder | None) -> None:
        if generation != self._generation:  # 已经被打断，结果作废
            return
        self._planner_busy = False
        reply = self._reply
        # 规划器的回复始终为准：语音模型看不到车况，问状态时会编（“当前是标准驾驶模式”），
        # 两边都播就会一问两答。没播的默认回复丢掉；已经播了的只可能是短应答。
        if result is not None and result.reply.strip():
            if reply.decision is None:
                reply.decision = "drop"
                reply.audio = []
            await self._state("speaking")
            await self.upstream.say(result.reply.strip())
            # 告诉前端这条回复是用豆包 S2S 的声音念的（文字来源仍是规划器）
            await self.host.send({"type": "voice_tts", "text": result.reply.strip()})
            return
        if result is not None and trace is not None and planner_took_over(result, trace):
            if reply.decision is None:
                reply.decision = "drop"
                reply.audio = []
            await self._state("listening")
            return
        # 规划器出错或没给回复：语音模型自己的回复兜底
        if reply.decision is None:
            reply.kind = "chat"
            await self._play(reply)
        if reply.decision != "play" or not reply.text:
            await self._state("listening")
