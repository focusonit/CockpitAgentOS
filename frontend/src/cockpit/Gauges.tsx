import { getPath, type VehicleState } from "../lib/protocol";

interface GaugesProps {
  state: VehicleState | null;
  changed: Set<string>;
}

const MAX_SPEED = 200;
const ARC = 251; // 半圆弧长 ≈ π × 80

function Dial({ label, value, max, unit, color, hot }: { label: string; value: number; max: number; unit: string; color: string; hot: boolean }) {
  const ratio = Math.max(0, Math.min(1, value / max));
  return (
    <svg viewBox="0 0 200 120" className="w-full max-w-[180px]" role="img" aria-label={`${label} ${value}${unit}`}>
      <path d="M20 105 A80 80 0 0 1 180 105" fill="none" stroke="var(--line)" strokeWidth={12} strokeLinecap="round" />
      {ratio > 0 && <path
        d="M20 105 A80 80 0 0 1 180 105"
        fill="none"
        stroke={color}
        strokeWidth={12}
        strokeLinecap="round"
        strokeDasharray={`${ARC * ratio} ${ARC}`}
      />}
      <text x={100} y={88} textAnchor="middle" fontSize={30} fontWeight={600} fill={hot ? "var(--veh)" : "var(--fg)"}>
        {value}
      </text>
      <text x={100} y={110} textAnchor="middle" fontSize={12} fill="var(--muted)">
        {label} {unit}
      </text>
    </svg>
  );
}

/** 仪表：车速、电量，加上档位、续航、驾驶模式等读数。 */
export function Gauges({ state, changed }: GaugesProps) {
  const n = (path: string) => Number(getPath(state, path) ?? 0);
  const battery = n("battery_pct");
  const reading = (label: string, path: string, unit = "") => (
    <div key={path} className={"rounded px-2 py-1 " + (changed.has(path) ? "bg-veh-bg ring-1 ring-veh" : "bg-bg")}>
      <div className="text-xs text-muted">{label}</div>
      <div className="font-medium tabular-nums">
        {String(getPath(state, path) ?? "—")}
        {unit}
      </div>
    </div>
  );

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Dial label="车速" value={n("speed_kmh")} max={MAX_SPEED} unit="km/h" color="var(--cloud)" hot={changed.has("speed_kmh")} />
      <Dial
        label="电量"
        value={battery}
        max={100}
        unit="%"
        color={battery < 20 ? "var(--bad)" : "var(--veh)"}
        hot={changed.has("battery_pct")}
      />
      <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-3">
        {reading("档位", "gear")}
        {reading("续航", "range_km", " km")}
        {reading("驾驶模式", "drive_mode")}
        {reading("能量回收", "energy_recovery")}
        {reading("车外", "outside_temp", "℃")}
        {reading("车内", "cabin_temp", "℃")}
      </div>
    </div>
  );
}
