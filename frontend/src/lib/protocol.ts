// 与 backend/app/main.py 的 WebSocket 协议保持一致。

export type Seat = "主驾" | "副驾" | "左后排" | "右后排";
export const SEATS: Seat[] = ["主驾", "副驾", "左后排", "右后排"];

// 车辆状态是嵌套对象，键可以是中文；前端只读展示，不做本地修改。
export type VehicleState = Record<string, unknown>;

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  status: string;
  result: unknown;
  needs_confirm: boolean;
}

export interface Diff {
  path: string;
  old: unknown;
  new: unknown;
}

export interface TraceEvent {
  stage: string;
  message: string;
  at_ms: number;
  data: Record<string, unknown>;
}

export type ServerMessage =
  | { type: "hello"; session_id: string; state: VehicleState; brain: string; warning?: string; voice?: boolean }
  | { type: "assistant_delta"; text: string }
  | { type: "interrupted" }
  | { type: "assistant"; reply: string; calls: ToolCall[]; confirm_requested: boolean; source?: ReplySource }
  | { type: "state"; state: VehicleState; diff: Diff[] }
  | { type: "trace"; request_id: string; events: TraceEvent[] }
  | { type: "invoke_result"; call: ToolCall; source?: InvokeSource; label?: string }
  | { type: "clock"; now: string; events: string[]; due: string[] }
  | { type: "error"; message: string }
  | { type: "voice"; state: VoiceState; seat: Seat }
  | { type: "voice_asr"; text: string; final: boolean; seat: Seat }
  | { type: "voice_reply"; text: string; kind: "ack" | "chat" }
  | { type: "voice_barge_in" }
  | { type: "voice_tts"; text: string };

/** 一条回复是谁给的：llm 云端大模型 / gate、unsupported 端侧闸门 / fast_path 快速通道 / offline 断网兜底 /
 *  confirm 确认流程 / echo 占位 / rule 车机规则；前端另用 s2s 表示豆包语音模型自己的回复 */
export interface ReplySource {
  kind: string;
  label: string;
}

/** 语音状态：off 关 / listening 在听 / user_speaking 用户在说 / thinking 车机在办 / speaking 在播报 */
export type VoiceState = "off" | "connecting" | "listening" | "user_speaking" | "thinking" | "speaking";

export type ClientMessage =
  | { type: "user_say"; seat: Seat; text: string }
  | { type: "event"; event: string }
  | { type: "set_state"; updates: Record<string, unknown> }
  | { type: "reset"; profile?: string; init?: Record<string, unknown> }
  | { type: "invoke"; tool: string; seat: Seat; args: Record<string, unknown>; source?: InvokeSource }
  | { type: "clock"; minutes: number }
  | { type: "voice_start"; seat: Seat }
  | { type: "voice_seat"; seat: Seat }
  | { type: "voice_stop" };

/** invoke 的来源：触屏点击、调试面板。和智能体调用走同一条执行路径。 */
export type InvokeSource = "touch" | "agent" | "debug" | "phone";

// /api/protocol 的返回结构，与 backend/protocol/spec.py 的 to_dict 一致。
export interface ParamSpec {
  name: string;
  type: "area" | "enum" | "int" | "level" | "str";
  desc: string;
  values?: string[];
  min?: number;
  max?: number;
  examples?: string[];
}

export interface CommandSpec {
  name: string;
  tool: string;
  desc: string;
  longtail: boolean;
  params: ParamSpec[];
}

export interface ProtocolSpec {
  tools: Record<string, { desc: string; commands: CommandSpec[] }>;
  statuses: Record<string, string>;
  seats: string[];
  limits: Record<string, number>;
}

export const OK_STATUSES = new Set(["Success", "PartSucceed"]);

export function getPath(state: VehicleState | null, path: string): unknown {
  return path.split(".").reduce<unknown>((node, key) => {
    if (node && typeof node === "object" && key in node) {
      return (node as Record<string, unknown>)[key];
    }
    return undefined;
  }, state);
}

// /api/apps：模拟 App 的静态数据，动态状态在车况里。
export interface MenuItem {
  name: string;
  price: number;
  signature?: boolean;
}

export interface Merchant {
  id: string;
  name: string;
  type: string;
  tags: string[];
  avg_price: number;
  rating: number;
  distance_km: number;
  rooms: boolean;
  near: string;
  menu?: MenuItem[];
}

export interface Song {
  title: string;
  artist: string;
  album: string;
  duration_s: number;
  local?: boolean;
}

export interface Poi {
  name: string;
  city: string;
  x: number;
  y: number;
  tags: string[];
}

export interface AppsData {
  merchants: Merchant[];
  songs: Song[];
  poi: Poi[];
}
