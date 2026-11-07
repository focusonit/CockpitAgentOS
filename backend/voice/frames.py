"""火山引擎端到端实时语音（S2S）的二进制帧。

帧格式（大端）：4 字节头 + [code] + [event] + [connect/session id] + payload 长度 + payload。
- 头：版本 1、头长 1（×4 字节）；消息类型 + flags；序列化（0 原始、1 JSON）+ 压缩（0 不压缩）；保留 0。
- flags 0b0100 表示带 event 号；错误帧（类型 0b1111）在 event 前多一个 4 字节错误码。
- 连接类事件（1/2/50/51/52）带 connect id（可以为空），会话类事件带 session id。
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

# 消息类型
CLIENT_FULL = 0b0001
CLIENT_AUDIO = 0b0010
SERVER_FULL = 0b1001
SERVER_AUDIO = 0b1011
SERVER_ERROR = 0b1111

FLAG_EVENT = 0b0100
SER_RAW = 0
SER_JSON = 1

# 客户端事件
START_CONNECTION = 1
FINISH_CONNECTION = 2
START_SESSION = 100
FINISH_SESSION = 102
TASK_REQUEST = 200  # 上传音频
CHAT_TTS_TEXT = 500  # 指定文本合成音频，用来播规划器的结果
CHAT_TEXT_QUERY = 501

# 服务端事件
CONNECTION_STARTED = 50
CONNECTION_FAILED = 51
CONNECTION_FINISHED = 52
SESSION_STARTED = 150
SESSION_FINISHED = 152
SESSION_FAILED = 153
TTS_SENTENCE_START = 350
TTS_SENTENCE_END = 351
TTS_RESPONSE = 352  # 音频
TTS_ENDED = 359
ASR_INFO = 450  # 听到首字：用来打断播报
ASR_RESPONSE = 451
ASR_ENDED = 459  # 用户一句话说完
CHAT_RESPONSE = 550  # 语音模型自己的回复文字（流式）
CHAT_ENDED = 559
DIALOG_ERROR = 599

CONNECTION_EVENTS = {START_CONNECTION, FINISH_CONNECTION, CONNECTION_STARTED, CONNECTION_FAILED, CONNECTION_FINISHED}


class FrameError(ValueError):
    """收到的帧格式不对。"""


@dataclass(frozen=True)
class Frame:
    kind: int
    event: int | None = None
    session_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    audio: bytes = b""
    code: int | None = None

    @property
    def is_error(self) -> bool:
        return self.kind == SERVER_ERROR


def encode(event: int, payload: dict[str, Any] | bytes | None = None, session_id: str | None = None) -> bytes:
    """客户端帧。payload 是 bytes 时按音频帧发（TaskRequest），否则按 JSON 文本帧发。"""
    audio = isinstance(payload, (bytes, bytearray))
    body = bytes(payload) if audio else json.dumps(payload or {}, ensure_ascii=False).encode()
    kind, ser = (CLIENT_AUDIO, SER_RAW) if audio else (CLIENT_FULL, SER_JSON)
    out = bytes([0x11, (kind << 4) | FLAG_EVENT, ser << 4, 0]) + struct.pack(">I", event)
    if event not in CONNECTION_EVENTS and session_id is not None:  # 连接级事件不带任何 id
        sid = session_id.encode()
        out += struct.pack(">I", len(sid)) + sid
    return out + struct.pack(">I", len(body)) + body


def _take(data: bytes, pos: int, size: int) -> tuple[bytes, int]:
    if pos + size > len(data):
        raise FrameError("帧长度不够")
    return data[pos : pos + size], pos + size


def _u32(data: bytes, pos: int) -> tuple[int, int]:
    raw, pos = _take(data, pos, 4)
    return struct.unpack(">I", raw)[0], pos


def decode(data: bytes) -> Frame:
    """服务端帧。"""
    if len(data) < 4:
        raise FrameError("帧太短")
    header_len = (data[0] & 0x0F) * 4
    kind, flags, ser = data[1] >> 4, data[1] & 0x0F, data[2] >> 4
    pos = header_len
    code = None
    if kind == SERVER_ERROR:
        code, pos = _u32(data, pos)
    event = None
    session_id = ""
    if flags & FLAG_EVENT:
        event, pos = _u32(data, pos)
        size, pos = _u32(data, pos)
        raw_id, pos = _take(data, pos, size)
        if event not in CONNECTION_EVENTS:
            session_id = raw_id.decode(errors="replace")
    size, pos = _u32(data, pos)
    body, _ = _take(data, pos, size)
    if kind == SERVER_AUDIO:
        return Frame(kind, event, session_id, audio=body, code=code)
    payload: dict[str, Any] = {}
    if body and (ser == SER_JSON or kind == SERVER_ERROR):
        try:
            parsed = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FrameError(f"payload 不是 JSON：{exc}") from exc
        payload = parsed if isinstance(parsed, dict) else {"value": parsed}
    return Frame(kind, event, session_id, payload, code=code)
