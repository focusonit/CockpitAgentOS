import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientMessage, Diff, InvokeSource, ReplySource, ServerMessage, ToolCall, TraceEvent, VehicleState, Seat, VoiceState } from "./protocol";
import { PcmPlayer, startMic, type Mic } from "./voice";

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "error" | "system";
  seat?: Seat;
  text: string;
  calls?: ToolCall[];
  /** 流式输出中，还没收到完整回复 */
  streaming?: boolean;
  /** 被新消息打断 */
  interrupted?: boolean;
  /** 语音输入，或语音模型自己的应答 / 闲聊回复 */
  voice?: "asr" | "ack" | "chat";
  /** 回复来源（规划器的回复由后端给出；语音模型的回复是 s2s） */
  source?: ReplySource;
  /** 这条回复已经交给豆包 S2S 念出来 */
  spoken?: boolean;
}

export interface TraceEntry {
  requestId: string;
  events: TraceEvent[];
}

export type ConnectionStatus = "connecting" | "open" | "closed";

const RECONNECT_MS = 1500;
const MAX_TRACES = 30;

let nextId = 1;

export function useCockpitSocket() {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [state, setState] = useState<VehicleState | null>(null);
  const [lastDiff, setLastDiff] = useState<Diff[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [traces, setTraces] = useState<TraceEntry[]>([]);
  const [lastInvoke, setLastInvoke] = useState<ToolCall | null>(null);
  const [brain, setBrain] = useState<string>("");
  const [warning, setWarning] = useState<string | null>(null);
  const [confirmPending, setConfirmPending] = useState(false);
  /** 已经发出 user_say，还没收到完整回复（豆包形象显示“思考中”，体验脚本据此等待下一句） */
  const [busy, setBusy] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const [voiceAvailable, setVoiceAvailable] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("off");
  /** 正在识别的那句话（还没说完） */
  const [voiceText, setVoiceText] = useState("");
  /** 麦克风音量 0–1（每 150ms 更新），用来确认确实在收音 */
  const [voiceLevel, setVoiceLevel] = useState(0);
  const levelRef = useRef(0);
  const micRef = useRef<Mic | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  if (!playerRef.current) playerRef.current = new PcmPlayer();

  const stopMic = useCallback(() => {
    micRef.current?.stop();
    micRef.current = null;
    playerRef.current?.flush();
    setVoiceText("");
    levelRef.current = 0;
    setVoiceLevel(0);
  }, []);

  useEffect(() => {
    if (voiceState === "off") return;
    const id = window.setInterval(() => {
      setVoiceLevel(levelRef.current);
      levelRef.current *= 0.5;
    }, 150);
    return () => window.clearInterval(id);
  }, [voiceState]);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;

    const handle = (msg: ServerMessage) => {
      switch (msg.type) {
        case "hello":
          setConfirmPending(false);
          setBusy(false);
          setState(msg.state);
          setLastDiff([]);
          setBrain(msg.brain);
          setWarning(msg.warning ?? null);
          setVoiceAvailable(Boolean(msg.voice));
          break;
        case "assistant_delta":
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.streaming) return [...prev.slice(0, -1), { ...last, text: last.text + msg.text }];
            return [...prev, { id: nextId++, role: "assistant", text: msg.text, streaming: true }];
          });
          break;
        case "interrupted":
          setBusy(false);
          setMessages((prev) =>
            prev.map((m) => (m.streaming ? { ...m, streaming: false, interrupted: true } : m)),
          );
          break;
        case "state":
          setState(msg.state);
          setLastDiff(msg.diff);
          break;
        case "assistant":
          setBusy(false);
          setConfirmPending(msg.confirm_requested);
          setMessages((prev) => {
            const final = { role: "assistant" as const, text: msg.reply, calls: msg.calls, source: msg.source };
            const last = prev[prev.length - 1];
            if (last?.streaming) return [...prev.slice(0, -1), { ...final, id: last.id }];
            return [...prev, { ...final, id: nextId++ }];
          });
          break;
        case "trace":
          setTraces((prev) => [{ requestId: msg.request_id, events: msg.events }, ...prev].slice(0, MAX_TRACES));
          break;
        case "invoke_result":
          setLastInvoke(msg.call);
          if (msg.source === "phone") {
            const note = `📱 手机${msg.label ? `：${msg.label}` : ""} → ${String((msg.call.result as { message?: string } | null)?.message ?? msg.call.status)}`;
            setMessages((prev) => [...prev, { id: nextId++, role: "system", text: note }]);
          }
          break;
        case "clock": {
          const notes = [...msg.events, ...msg.due.map((d) => `任务到点：${d}`)];
          if (notes.length) {
            setMessages((prev) => [...prev, { id: nextId++, role: "system", text: notes.join("；") }]);
          }
          break;
        }
        case "voice":
          setVoiceState(msg.state);
          if (msg.state === "off") stopMic();
          break;
        case "voice_asr":
          if (!msg.final) {
            setVoiceText(msg.text);
            break;
          }
          setVoiceText("");
          setBusy(true);
          setMessages((prev) => [...prev, { id: nextId++, role: "user", seat: msg.seat, text: msg.text, voice: "asr" }]);
          break;
        case "voice_reply":
          // 应答可能晚于规划器开始流式输出：插到正在流式的那条之前，保证最终回复仍是最后一条
          setMessages((prev) => {
            const item: ChatMessage = {
              id: nextId++, role: "assistant", text: msg.text, voice: msg.kind,
              source: { kind: "s2s", label: msg.kind === "ack" ? "豆包 S2S · 应答" : "豆包 S2S · 闲聊" },
            };
            const last = prev[prev.length - 1];
            return last?.streaming ? [...prev.slice(0, -1), item, last] : [...prev, item];
          });
          break;
        case "voice_tts":
          // 规划器的回复由豆包 S2S 念出来：标在最近一条文字相同的回复上
          setMessages((prev) => {
            const text = msg.text.trim();
            for (let i = prev.length - 1; i >= 0; i--) {
              const m = prev[i];
              if (m.role === "assistant" && !m.voice && m.text.trim() === text) {
                return [...prev.slice(0, i), { ...m, spoken: true }, ...prev.slice(i + 1)];
              }
            }
            return prev;
          });
          break;
        case "voice_barge_in":
          playerRef.current?.flush();
          break;
        case "error":
          setBusy(false);
          setMessages((prev) => [...prev, { id: nextId++, role: "error", text: msg.message }]);
          break;
      }
    };

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.binaryType = "arraybuffer";
      socketRef.current = ws;
      setStatus("connecting");
      ws.onopen = () => setStatus("open");
      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          playerRef.current?.play(event.data); // 语音播报
          return;
        }
        try {
          handle(JSON.parse(event.data) as ServerMessage);
        } catch (err) {
          console.error("无法解析服务端消息", err);
        }
      };
      ws.onclose = () => {
        setStatus("closed");
        stopMic();
        setVoiceState("off");
        if (!stopped) timer = window.setTimeout(connect, RECONNECT_MS);
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socketRef.current?.close();
      micRef.current?.stop();
      playerRef.current?.close();
    };
  }, [stopMic]);

  const send = useCallback((msg: ClientMessage) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(msg));
    return true;
  }, []);

  const say = useCallback(
    (seat: Seat, text: string) => {
      if (send({ type: "user_say", seat, text })) {
        setBusy(true);
        setMessages((prev) => [...prev, { id: nextId++, role: "user", seat, text }]);
      }
    },
    [send],
  );

  const invoke = useCallback(
    (tool: string, seat: Seat, args: Record<string, unknown>, source: InvokeSource = "debug") =>
      send({ type: "invoke", tool, seat, args, source }),
    [send],
  );

  /** 触屏点击：和智能体调用走同一个执行器，source 记为 touch。 */
  const touch = useCallback(
    (tool: string, args: Record<string, unknown>) => invoke(tool, "主驾", args, "touch"),
    [invoke],
  );

  /** 系统事件，例如触屏上的“确认下单”“取消”。 */
  const event = useCallback((name: string) => send({ type: "event", event: name }), [send]);

  /** 清空对话记录（载入体验脚本时用） */
  const clearMessages = useCallback(() => setMessages([]), []);

  const advanceClock = useCallback((minutes: number) => send({ type: "clock", minutes }), [send]);

  /** 打开麦克风开始双工语音；没有语音 key 时服务端会回 voice off + error。 */
  const startVoice = useCallback(
    async (seat: Seat) => {
      if (micRef.current) return;
      playerRef.current?.unlock();
      setVoiceState("connecting");
      try {
        micRef.current = await startMic((pcm) => {
          levelRef.current = Math.max(levelRef.current, peakOf(pcm));
          const ws = socketRef.current;
          if (ws?.readyState === WebSocket.OPEN) ws.send(pcm);
        });
      } catch (err) {
        setVoiceState("off");
        setMessages((prev) => [...prev, { id: nextId++, role: "error", text: `打不开麦克风：${String(err)}` }]);
        return;
      }
      if (!send({ type: "voice_start", seat })) stopMic();
    },
    [send, stopMic],
  );

  const stopVoice = useCallback(() => {
    stopMic();
    send({ type: "voice_stop" });
    setVoiceState("off");
  }, [send, stopMic]);

  const setVoiceSeat = useCallback((seat: Seat) => send({ type: "voice_seat", seat }), [send]);

  return { status, state, lastDiff, messages, traces, lastInvoke, brain, warning, confirmPending, busy, send, say, invoke, touch, event, advanceClock, clearMessages,
    voiceAvailable, voiceState, voiceText, voiceLevel, startVoice, stopVoice, setVoiceSeat };
}

/** 一包 int16 PCM 的峰值，归一到 0–1。 */
function peakOf(pcm: ArrayBuffer): number {
  const samples = new Int16Array(pcm);
  let peak = 0;
  for (const v of samples) peak = Math.max(peak, Math.abs(v));
  return peak / 0x8000;
}
