import { useState, type FormEvent } from "react";
import { SEATS, type Seat, type VoiceState } from "../lib/protocol";
import type { ChatMessage, ConnectionStatus } from "../lib/useCockpitSocket";

interface ChatPanelProps {
  messages: ChatMessage[];
  status: ConnectionStatus;
  brain: string;
  warning: string | null;
  onSay: (seat: Seat, text: string) => void;
  confirmPending: boolean;
  onEvent: (name: string) => void;
  seat: Seat;
  onSeat: (seat: Seat) => void;
  voiceAvailable: boolean;
  voiceState: VoiceState;
  /** 正在识别、还没说完的那句话 */
  voiceText: string;
  /** 麦克风音量 0–1 */
  voiceLevel: number;
  onVoiceToggle: () => void;
}

const VOICE_LABEL: Record<VoiceState, string> = {
  off: "",
  connecting: "正在连接语音…",
  listening: "在听，请说话",
  user_speaking: "听你说…",
  thinking: "车机在办…",
  speaking: "豆包在说（开口即可打断）",
};

/** 来源标签配色：云端靛蓝、闸门琥珀、快速通道和其余端侧环节绿色 */
const SOURCE_STYLE: Record<string, string> = {
  llm: "border-cloud text-cloud",
  s2s: "border-cloud text-cloud bg-cloud-bg",
  gate: "border-safe text-safe",
  unsupported: "border-safe text-safe",
  fast_path: "border-veh text-veh bg-fast-bg",
};

function SourceTag({ m }: { m: ChatMessage }) {
  if (m.role !== "assistant") return null;
  const label = m.source?.label ?? (m.streaming ? "云端大模型 · 生成中" : m.interrupted ? "云端大模型" : null);
  if (!label) return null;
  const style = SOURCE_STYLE[m.source?.kind ?? "llm"] ?? "border-veh text-veh";
  return (
    <div className="mb-0.5 flex flex-wrap gap-1 text-[10px] leading-4">
      <span className={`rounded border px-1 ${style}`} title="这条回复是谁给的">{label}</span>
      {m.spoken && <span className="rounded border border-cloud bg-cloud-bg px-1 text-cloud" title="由豆包 S2S 念出来">🔊 豆包 S2S 播报</span>}
    </div>
  );
}

export function ChatPanel({
  messages, status, brain, warning, onSay, confirmPending, onEvent, seat, onSeat,
  voiceAvailable, voiceState, voiceText, voiceLevel, onVoiceToggle,
}: ChatPanelProps) {
  const voiceOn = voiceState !== "off";
  const [text, setText] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    onSay(seat, value);
    setText("");
  };

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-line bg-panel">
      <header className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <span className="font-medium">对话</span>
        {brain && <span className="text-xs text-muted">{brain === "PlannerAgent" ? "DeepSeek 规划器" : brain}</span>}
      </header>
      {warning && <p className="border-b border-line bg-safe-bg px-3 py-1.5 text-xs text-bad">{warning}</p>}
      <div className="flex-1 space-y-2 overflow-y-auto p-3" aria-live="polite">
        {messages.length === 0 && <p className="text-muted">试试说“有点热”或“打开主驾车窗”。</p>}
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "text-right" : ""}>
            <div
              className={
                "inline-block max-w-[90%] rounded-lg px-3 py-1.5 text-left " +
                (m.role === "user" ? "bg-cloud-bg" : m.role === "error" ? "bg-safe-bg text-bad" : m.role === "system" ? "bg-fast-bg text-muted text-xs" : "bg-veh-bg")
              }
            >
              {m.seat && <span className="mr-1 text-xs text-muted">{m.seat}</span>}
              <SourceTag m={m} />
              {m.voice === "asr" && <span className="mr-1 text-xs text-muted" title="豆包 S2S 识别">🎙</span>}
              {m.text}
              {m.streaming && <span className="ml-0.5 animate-pulse text-muted">▍</span>}
              {m.interrupted && <span className="ml-1 text-xs text-muted">（已打断）</span>}
              {m.calls && m.calls.length > 0 && (
                <ul className="mt-1 text-xs text-muted">
                  {m.calls.map((c, i) => (
                    <li key={i}>
                      {c.tool} {String(c.args.command ?? "")} → {c.status}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))}
      </div>
      {voiceOn && (
        <div className="flex items-center gap-2 border-t border-line bg-cloud-bg px-3 py-1.5 text-xs" aria-live="polite">
          <span
            className={
              "inline-block size-2 rounded-full " +
              (voiceState === "user_speaking" ? "animate-pulse bg-veh" : voiceState === "speaking" ? "animate-pulse bg-cloud" : "bg-muted")
            }
          />
          <span className="text-muted">{VOICE_LABEL[voiceState]}</span>
          <span className="h-1.5 w-12 overflow-hidden rounded bg-line" title="麦克风音量" aria-label="麦克风音量">
            <span className="block h-full bg-veh transition-[width]" style={{ width: `${Math.min(100, Math.round(voiceLevel * 300))}%` }} />
          </span>
          {voiceText && <span className="truncate">「{voiceText}」</span>}
        </div>
      )}
      {confirmPending && (
        <div className="flex items-center gap-2 border-t border-line bg-safe-bg px-3 py-1.5 text-xs">
          <span className="flex-1 text-safe">有操作等待确认，可以点按钮，也可以直接说“确认”或“取消”。</span>
          <button className="rounded bg-safe px-2 py-0.5 text-white" onClick={() => onEvent("confirm")}>
            确认
          </button>
          <button className="rounded bg-bg px-2 py-0.5" onClick={() => onEvent("cancel")}>
            取消
          </button>
        </div>
      )}
      <form onSubmit={submit} className="flex gap-2 border-t border-line p-2">
        <select
          aria-label="音区"
          value={seat}
          onChange={(e) => onSeat(e.target.value as Seat)}
          className="rounded border border-line bg-panel px-1"
        >
          {SEATS.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={status === "open" ? "输入指令…" : "连接中…"}
          disabled={status !== "open"}
          maxLength={500}
          className="min-w-0 flex-1 rounded border border-line bg-panel px-2 py-1"
        />
        <button
          type="button"
          onClick={onVoiceToggle}
          disabled={status !== "open" || (!voiceAvailable && !voiceOn)}
          aria-pressed={voiceOn}
          aria-label={voiceOn ? "关闭语音" : "打开语音"}
          title={voiceAvailable ? (voiceOn ? "关闭语音" : "双工语音：说话即可，豆包说话时开口能打断") : "没有配置 VOLC_S2S_API_KEY，语音不可用"}
          className={"rounded px-2 py-1 disabled:opacity-50 " + (voiceOn ? "bg-veh text-white" : "border border-line bg-panel")}
        >
          {voiceOn ? "■ 语音" : "🎙"}
        </button>
        <button
          type="submit"
          disabled={status !== "open"}
          className="rounded bg-cloud px-3 py-1 text-white disabled:opacity-50"
        >
          发送
        </button>
      </form>
    </section>
  );
}
