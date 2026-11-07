import { getPath, type ClientMessage, type VehicleState } from "../lib/protocol";
import type { ConnectionStatus } from "../lib/useCockpitSocket";

interface ScenarioBarProps {
  state: VehicleState | null;
  status: ConnectionStatus;
  send: (msg: ClientMessage) => boolean;
}

const GEARS = ["P", "R", "N", "D"];
const MAX_SPEED = 200;
// 与 backend/edge/gateway.py 的 mode_updates 一致：断网只走快速通道，弱网给云端请求加延迟
const NETWORK_MODES = ["正常", "弱网", "断网"] as const;
type NetworkMode = (typeof NETWORK_MODES)[number];
const NETWORK_UPDATES: Record<NetworkMode, Record<string, boolean>> = {
  正常: { network: true, network_weak: false },
  弱网: { network: true, network_weak: true },
  断网: { network: false, network_weak: false },
};

function networkMode(state: VehicleState | null): NetworkMode {
  if (getPath(state, "network") === false) return "断网";
  return getPath(state, "network_weak") === true ? "弱网" : "正常";
}

const STATUS_TEXT: Record<ConnectionStatus, string> = { connecting: "连接中", open: "已连接", closed: "已断开" };

export function ScenarioBar({ state, status, send }: ScenarioBarProps) {
  const set = (path: string, value: unknown) => send({ type: "set_state", updates: { [path]: value } });
  const input = "rounded border border-line bg-panel px-1";
  const toggle = (label: string, path: string) => (
    <label className="flex items-center gap-1">
      <input type="checkbox" checked={getPath(state, path) === true} onChange={(e) => set(path, e.target.checked)} />
      {label}
    </label>
  );

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line bg-panel px-3 py-2">
      <strong>座舱模拟</strong>
      <label className="flex items-center gap-1">
        车型
        <select
          className={input}
          value={String(getPath(state, "profile") ?? "high")}
          onChange={(e) => send({ type: "reset", profile: e.target.value })}
        >
          <option value="high">高配</option>
          <option value="basic">低配</option>
        </select>
      </label>
      {toggle("上电", "power_on")}
      <label className="flex items-center gap-1">
        车速
        <input
          type="number"
          min={0}
          max={MAX_SPEED}
          className={input + " w-16"}
          value={Number(getPath(state, "speed_kmh") ?? 0)}
          onChange={(e) => set("speed_kmh", Math.max(0, Math.min(MAX_SPEED, Number(e.target.value) || 0)))}
        />
      </label>
      <label className="flex items-center gap-1">
        档位
        <select className={input} value={String(getPath(state, "gear") ?? "P")} onChange={(e) => set("gear", e.target.value)}>
          {GEARS.map((g) => (
            <option key={g}>{g}</option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1">
        网络
        <select
          className={input + (networkMode(state) === "正常" ? "" : " text-safe")}
          value={networkMode(state)}
          onChange={(e) => send({ type: "set_state", updates: NETWORK_UPDATES[e.target.value as NetworkMode] })}
        >
          {NETWORK_MODES.map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
      </label>
      {toggle("蓝牙", "phone.bluetooth")}
      <button className="rounded bg-bg px-2 py-0.5" onClick={() => send({ type: "reset" })}>
        重置
      </button>
      <span className={"ml-auto text-xs " + (status === "open" ? "text-veh" : "text-safe")}>{STATUS_TEXT[status]}</span>
    </header>
  );
}
