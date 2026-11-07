"""火山引擎端到端实时语音（S2S）客户端：连上、开会话、上行音频、指定文本播报、读事件。

鉴权用新版控制台的单个 API Key（请求头 ``X-Api-Key``），只从环境变量 ``VOLC_S2S_API_KEY`` 读取。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

import yaml
from dotenv import load_dotenv

from backend.voice import frames as fr

CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "voice.yaml"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
OPEN_TIMEOUT_S = 10


class VoiceError(RuntimeError):
    """语音服务不可用（没配 key、握手失败、会话失败）。"""


@dataclass(frozen=True)
class VoiceConfig:
    url: str
    resource_id: str
    app_key: str
    model: str
    bot_name: str
    speaker: str
    end_smooth_window_ms: int
    sample_rate_in: int
    sample_rate_out: int
    ack_max_chars: int
    claim_words: tuple[str, ...]
    ack_words: tuple[str, ...]
    system_role: str
    extra: dict[str, Any] = field(default_factory=dict)

    def session_payload(self) -> dict[str, Any]:
        tts: dict[str, Any] = {
            "audio_config": {"channel": 1, "format": "pcm_s16le", "sample_rate": self.sample_rate_out}
        }
        if self.speaker:
            tts["speaker"] = self.speaker
        return {
            "asr": {"extra": {"end_smooth_window_ms": self.end_smooth_window_ms}},
            "tts": tts,
            "dialog": {
                "bot_name": self.bot_name,
                "system_role": self.system_role,
                "extra": {"model": self.model, "strict_audit": False},
            },
        }


@lru_cache(maxsize=1)
def load_voice_config(path: Path = CONFIG_FILE) -> VoiceConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return VoiceConfig(
            url=str(raw["url"]),
            resource_id=str(raw["resource_id"]),
            app_key=str(raw["app_key"]),
            model=str(os.environ.get("VOLC_S2S_MODEL") or raw["model"]),
            bot_name=str(raw.get("bot_name", "豆包"))[:20],
            speaker=str(raw.get("speaker") or ""),
            end_smooth_window_ms=int(raw.get("end_smooth_window_ms", 1500)),
            sample_rate_in=int(raw.get("sample_rate_in", 16000)),
            sample_rate_out=int(raw.get("sample_rate_out", 24000)),
            ack_max_chars=int(raw.get("ack_max_chars", 12)),
            claim_words=tuple(str(w) for w in raw.get("claim_words", [])),
            ack_words=tuple(str(w) for w in raw.get("ack_words", [])),
            system_role=str(raw.get("system_role", "")).strip(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} 配置不完整：{exc}") from exc


class Upstream(Protocol):
    """语音桥看到的语音服务；测试里用脚本化的假实现。"""

    async def send_audio(self, pcm: bytes) -> None: ...

    async def say(self, text: str) -> None: ...

    def events(self) -> AsyncIterator[fr.Frame]: ...

    async def close(self) -> None: ...


def api_key_from_env() -> str:
    load_dotenv(ENV_FILE)
    key = os.environ.get("VOLC_S2S_API_KEY", "").strip()
    if not key:
        raise VoiceError("没有配置 VOLC_S2S_API_KEY，语音不可用（文字输入不受影响）")
    return key


class S2SClient:
    """一条到火山引擎的 WebSocket，一个会话。用 ``await S2SClient.open()`` 创建。"""

    def __init__(self, ws: Any, session_id: str, logid: str) -> None:
        self._ws = ws
        self.session_id = session_id
        self.logid = logid

    @classmethod
    async def open(cls, config: VoiceConfig | None = None) -> "S2SClient":
        import websockets

        config = config or load_voice_config()
        headers = {
            "X-Api-Key": api_key_from_env(),
            "X-Api-Resource-Id": config.resource_id,
            "X-Api-App-Key": config.app_key,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        try:
            ws = await websockets.connect(config.url, additional_headers=headers, open_timeout=OPEN_TIMEOUT_S, max_size=None)
        except Exception as exc:  # 握手失败（鉴权、网络）统一成 VoiceError
            raise VoiceError(f"连接语音服务失败：{exc}") from exc
        client = cls(ws, str(uuid.uuid4()), ws.response.headers.get("X-Tt-Logid", ""))
        try:
            await ws.send(fr.encode(fr.START_CONNECTION))
            await client._expect(fr.CONNECTION_STARTED)
            await ws.send(fr.encode(fr.START_SESSION, config.session_payload(), client.session_id))
            await client._expect(fr.SESSION_STARTED)
        except Exception:
            await client.close()
            raise
        return client

    async def _expect(self, event: int) -> fr.Frame:
        frame = fr.decode(await self._ws.recv())
        if frame.is_error or frame.event != event:
            raise VoiceError(f"语音服务返回错误：{frame.payload or frame.event}（logid {self.logid}）")
        return frame

    async def send_audio(self, pcm: bytes) -> None:
        await self._ws.send(fr.encode(fr.TASK_REQUEST, pcm, self.session_id))

    async def say(self, text: str) -> None:
        """让语音服务念一段指定文字（ChatTTSText：首包带文字，末包 end=true、内容为空）。"""
        await self._ws.send(fr.encode(fr.CHAT_TTS_TEXT, {"start": True, "content": text, "end": False}, self.session_id))
        await self._ws.send(fr.encode(fr.CHAT_TTS_TEXT, {"start": False, "content": "", "end": True}, self.session_id))

    async def events(self) -> AsyncIterator[fr.Frame]:
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                yield fr.decode(bytes(raw))

    async def close(self) -> None:
        try:
            await self._ws.send(fr.encode(fr.FINISH_SESSION, {}, self.session_id))
            await self._ws.send(fr.encode(fr.FINISH_CONNECTION))
        except Exception:  # 连接可能已经断了，关就是了
            pass
        await self._ws.close()
