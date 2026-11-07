import { getPath, type AppsData, type Diff, type VehicleState } from "../lib/protocol";

export type Touch = (tool: string, args: Record<string, unknown>) => boolean;

export interface AppProps {
  state: VehicleState | null;
  data: AppsData | null;
  diff: Diff[];
  touch: Touch;
}

/** 本次变更里有没有以 prefix 开头的路径，用来高亮刚被改动的区域。 */
export function touched(diff: Diff[], prefix: string): boolean {
  return diff.some((d) => d.path === prefix || d.path.startsWith(prefix + "."));
}

export function read<T>(state: VehicleState | null, path: string, fallback: T): T {
  const value = getPath(state, path);
  return (value ?? fallback) as T;
}

export const hotRing = (hot: boolean) => (hot ? " ring-1 ring-veh bg-veh-bg" : "");

export function Btn(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "cloud" | "safe" | "plain" }) {
  const { tone = "plain", className = "", ...rest } = props;
  const color = tone === "cloud" ? "bg-cloud text-white" : tone === "safe" ? "bg-safe text-white" : "bg-bg border border-line";
  return <button {...rest} className={`rounded px-2 py-1 text-sm disabled:opacity-40 ${color} ${className}`} />;
}
