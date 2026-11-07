import type { VoiceState } from "../lib/protocol";
import type { ChatMessage } from "../lib/useCockpitSocket";

export type Mood = "idle" | "thinking" | "speaking" | "asking" | "happy" | "sorry";

const OK_STATUS = new Set(["Success", "PartSucceed"]);

const MOOD_TEXT: Record<Mood, string> = {
  idle: "待命",
  thinking: "思考中",
  speaking: "说话中",
  asking: "等你确认",
  happy: "办好了",
  sorry: "没办成",
};

/** 由对话状态推出豆包的表情。 */
export function moodOf(messages: ChatMessage[], busy: boolean, confirmPending: boolean, voice: VoiceState = "off"): Mood {
  if (confirmPending) return "asking";
  if (voice === "speaking") return "speaking";
  if (voice === "thinking") return "thinking";
  const last = messages[messages.length - 1];
  if (busy) return last?.streaming ? "speaking" : "thinking";
  if (!last) return "idle";
  if (last.role === "error") return "sorry";
  if (last.role !== "assistant" || !last.calls?.length) return "idle";
  return last.calls.every((c) => OK_STATUS.has(c.status)) ? "happy" : "sorry";
}

function Eyes({ mood }: { mood: Mood }) {
  if (mood === "happy") {
    return (
      <g fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
        <path d="M13 21 q4 -5 8 0" />
        <path d="M27 21 q4 -5 8 0" />
      </g>
    );
  }
  if (mood === "thinking") {
    return (
      <g fill="currentColor">
        <circle cx="19" cy="18" r="2.5" />
        <circle cx="33" cy="18" r="2.5" />
      </g>
    );
  }
  const ry = mood === "sorry" ? 2 : 3.5;
  return (
    <g fill="currentColor">
      <ellipse cx="17" cy="20" rx="2.8" ry={ry} />
      <ellipse cx="31" cy="20" rx="2.8" ry={ry} />
      {mood === "sorry" && (
        <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M13 15 l6 2" />
          <path d="M35 15 l-6 2" />
        </g>
      )}
    </g>
  );
}

function Mouth({ mood }: { mood: Mood }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 2.5, strokeLinecap: "round" as const };
  switch (mood) {
    case "speaking":
      return <ellipse cx="24" cy="31" rx="4" ry="3" fill="currentColor" className="avatar-talk" />;
    case "happy":
      return <path d="M17 29 q7 7 14 0" {...common} />;
    case "sorry":
      return <path d="M18 33 q6 -5 12 0" {...common} />;
    case "asking":
      return <circle cx="24" cy="31" r="2.5" {...common} />;
    case "thinking":
      return <path d="M20 31 h8" {...common} />;
    default:
      return <path d="M19 30 q5 3 10 0" {...common} />;
  }
}

interface DoubaoAvatarProps {
  mood: Mood;
}

/** 中控屏上的豆包形象：表情跟着对话状态变。 */
export function DoubaoAvatar({ mood }: DoubaoAvatarProps) {
  const ring = mood === "asking" ? "var(--safe)" : mood === "sorry" ? "var(--muted)" : "var(--cloud)";
  return (
    <span className="flex items-center gap-1.5" data-mood={mood}>
      <svg viewBox="0 0 48 48" width="32" height="32" role="img" aria-label={`豆包：${MOOD_TEXT[mood]}`} className="text-fg">
        <circle cx="24" cy="24" r="21" fill="var(--cloud-bg)" stroke={ring} strokeWidth="2.5" />
        {mood === "thinking" && (
          <g fill={ring} className="avatar-dots">
            <circle cx="38" cy="8" r="2" />
            <circle cx="43" cy="4" r="1.4" />
          </g>
        )}
        {mood === "asking" && (
          <text x="38" y="12" fontSize="12" fontWeight="700" fill={ring}>
            ?
          </text>
        )}
        <Eyes mood={mood} />
        <Mouth mood={mood} />
      </svg>
      <span className="text-xs text-muted">豆包 · {MOOD_TEXT[mood]}</span>
    </span>
  );
}
