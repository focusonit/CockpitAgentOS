import { getPath, type VehicleState } from "../lib/protocol";

interface ClimateBarProps {
  state: VehicleState | null;
  changed: Set<string>;
}

const MAX_AIR = 8;

function Chip({ label, on, hot }: { label: string; on: boolean; hot: boolean }) {
  return (
    <span
      className={
        "rounded px-1.5 py-0.5 text-xs " +
        (on ? "bg-veh-bg text-veh" : "bg-bg text-muted") +
        (hot ? " ring-1 ring-veh" : "")
      }
    >
      {label}
    </span>
  );
}

/** 空调条：开关、风量、风向、循环和各模式。 */
export function ClimateBar({ state, changed }: ClimateBarProps) {
  const on = getPath(state, "ac.on") === true;
  const air = Number(getPath(state, "ac.air_volume") ?? 0);
  const is = (path: string) => changed.has(path);
  const flag = (path: string) => getPath(state, path) === true;

  return (
    <div className={"flex flex-wrap items-center gap-2 rounded px-2 py-1.5 " + (is("ac.on") ? "bg-veh-bg" : "bg-bg")}>
      <span className={"font-medium " + (on ? "text-veh" : "text-muted")}>空调 {on ? "开" : "关"}</span>
      <span className={"flex items-end gap-0.5 " + (is("ac.air_volume") ? "rounded ring-1 ring-veh" : "")} title={`风量 ${air}`}>
        {Array.from({ length: MAX_AIR }, (_, i) => (
          <span
            key={i}
            className={"inline-block w-1.5 rounded-sm " + (on && i < air ? "bg-veh" : "bg-line")}
            style={{ height: 4 + i * 2 }}
          />
        ))}
        <span className="ml-1 text-xs tabular-nums text-muted">{air} 档</span>
      </span>
      <Chip label={`风向 ${String(getPath(state, "ac.wind_direction") ?? "—")}`} on={on} hot={is("ac.wind_direction")} />
      <Chip label={String(getPath(state, "ac.recirc") ?? "—")} on={on} hot={is("ac.recirc")} />
      <Chip label={`${String(getPath(state, "ac.mode") ?? "")}`} on={on} hot={is("ac.mode")} />
      <Chip label="AC" on={flag("ac.compressor")} hot={is("ac.compressor")} />
      <Chip label="除雾" on={flag("ac.defrost")} hot={is("ac.defrost")} />
      <Chip label="净化" on={flag("ac.purifier")} hot={is("ac.purifier")} />
      <Chip label="新风" on={flag("ac.fresh_air")} hot={is("ac.fresh_air")} />
      <Chip label={`香氛 ${String(getPath(state, "fragrance") ?? "关")}`} on={getPath(state, "fragrance") !== "关"} hot={is("fragrance")} />
      <Chip label="方向盘加热" on={flag("steering_heat")} hot={is("steering_heat")} />
    </div>
  );
}
