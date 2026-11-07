import { getPath, SEATS, type Diff, type Seat, type VehicleState } from "../lib/protocol";
import { CarTopView } from "./CarTopView";
import { ClimateBar } from "./ClimateBar";
import { Gauges } from "./Gauges";

interface VehiclePanelProps {
  state: VehicleState | null;
  lastDiff: Diff[];
  speaker: Seat;
  onSpeaker: (seat: Seat) => void;
}

function show(value: unknown): string {
  if (value === true) return "开";
  if (value === false) return "关";
  if (Array.isArray(value)) return value.length ? value.join("、") : "无";
  if (value === undefined || value === null || value === "") return "—";
  return String(value);
}

interface CellProps {
  label: string;
  path: string;
  state: VehicleState | null;
  changed: Set<string>;
  unit?: string;
}

function Cell({ label, path, state, changed, unit = "" }: CellProps) {
  const hot = changed.has(path);
  return (
    <div className={"rounded px-2 py-1 " + (hot ? "bg-veh-bg ring-1 ring-veh" : "bg-bg")}>
      <div className="text-xs text-muted">{label}</div>
      <div className="font-medium tabular-nums">
        {show(getPath(state, path))}
        {unit}
      </div>
    </div>
  );
}

export function VehiclePanel({ state, lastDiff, speaker, onSpeaker }: VehiclePanelProps) {
  const changed = new Set(lastDiff.map((d) => d.path));
  const cell = (label: string, path: string, unit?: string) => (
    <Cell key={path} label={label} path={path} unit={unit} state={state} changed={changed} />
  );

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2 font-medium text-veh">车辆状态（车端）</header>
      <div className="space-y-3 p-3">
        <Gauges state={state} changed={changed} />
        <ClimateBar state={state} changed={changed} />
        <div className="grid gap-3 md:grid-cols-[minmax(200px,240px)_1fr]">
          <CarTopView state={state} changed={changed} speaker={speaker} onSpeaker={onSpeaker} />
          <div className="grid content-start grid-cols-2 gap-2 sm:grid-cols-3">
            {SEATS.map((s) => cell(`${s}靠背`, `seats.${s}.backrest`, "°"))}
            {SEATS.map((s) => cell(`${s}前后`, `seats.${s}.position`))}
            {cell("车顶遮阳帘", "curtain", "%")}
            {SEATS.map((s) => cell(`${s}侧帘`, `sunshades.${s}`, "%"))}
            {cell("雨刮", "wiper.on")}
            {cell("充电口", "charge_port")}
            {cell("氛围灯", "lights.ambient.on")}
            {cell("氛围灯颜色", "lights.ambient.color")}
            {cell("氛围灯亮度", "lights.ambient.brightness")}
            {cell("阅读灯", "lights.reading")}
            {cell("大灯高度", "lights.headlight_height")}
            {cell("屏幕亮度", "display.screen_brightness")}
            {cell("HUD", "display.hud_on")}
            {cell("音量", "media.volume")}
            {cell("静音", "media.muted")}
            {cell("网络", "network")}
            {cell("报警", "warnings")}
          </div>
        </div>
        <Settings state={state} changed={changed} />
      </div>
    </section>
  );
}

/** 没有专门状态字段的指令记在 settings 里（长尾指令、未知设备），这里原样列出。 */
function Settings({ state, changed }: { state: VehicleState | null; changed: Set<string> }) {
  const settings = (getPath(state, "settings") ?? {}) as Record<string, unknown>;
  const entries = Object.entries(settings);
  if (!entries.length) return null;
  return (
    <div>
      <div className="mb-1 text-xs text-muted">其他设置（settings）</div>
      <div className="flex flex-wrap gap-1.5">
        {entries.map(([k, v]) => (
          <span key={k} className={"rounded px-1.5 py-0.5 text-xs " + (changed.has("settings") ? "bg-veh-bg" : "bg-bg")}>
            {k}：{show(v)}
          </span>
        ))}
      </div>
    </div>
  );
}
