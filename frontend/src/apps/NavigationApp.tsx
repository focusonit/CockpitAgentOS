import { useState } from "react";
import type { Poi } from "../lib/protocol";
import { Btn, hotRing, read, touched, type AppProps } from "./types";

const STRATEGIES = ["推荐", "躲避拥堵", "高速优先", "不走高速", "少收费", "时间最短", "距离最短"];

interface RoutePoint {
  name: string;
  x: number;
  y: number;
}

interface PushedCard {
  name: string;
  distance_km: number;
  eta_min: number;
  route: RoutePoint[];
  from: string;
}

interface NavResult {
  name: string;
  distance_km?: number;
}

const VIEW = 320;

/** 平面坐标（公里）画成 SVG：y 轴向上，缩放级别越大视野越小。 */
function MapView({ pois, route, location, zoom }: { pois: Poi[]; route: RoutePoint[]; location: Poi | undefined; zoom: number }) {
  const center = location ?? { x: 0, y: 0 };
  const span = Math.max(4, 40 / Math.pow(1.4, zoom - 12)); // 视野宽度（公里）
  const scale = VIEW / span;
  const px = (p: { x: number; y: number }) => [VIEW / 2 + (p.x - center.x) * scale, VIEW / 2 - (p.y - center.y) * scale];
  const line = route.map((p) => px(p).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${VIEW} ${VIEW}`} className="h-56 w-full rounded bg-fast-bg" role="img" aria-label="导航地图">
      {pois.map((p) => {
        const [x, y] = px(p);
        return (
          <g key={p.name}>
            <circle cx={x} cy={y} r={2.5} className="fill-muted" />
            <text x={x + 4} y={y + 3} className="fill-muted text-[8px]">
              {p.name}
            </text>
          </g>
        );
      })}
      {route.length > 1 && <polyline points={line} className="fill-none stroke-cloud" strokeWidth={3} strokeLinejoin="round" />}
      {route.slice(1).map((p, i) => {
        const [x, y] = px(p);
        return <circle key={i} cx={x} cy={y} r={5} className={i === route.length - 2 ? "fill-bad" : "fill-safe"} />;
      })}
      {location && (
        <circle cx={VIEW / 2} cy={VIEW / 2} r={6} className="fill-veh stroke-panel" strokeWidth={2}>
          <title>当前位置：{location.name}</title>
        </circle>
      )}
    </svg>
  );
}

export function NavigationApp({ state, data, diff, touch }: AppProps) {
  const [keyword, setKeyword] = useState("");
  const locationName = read(state, "location", "");
  const allPoi = data?.poi ?? [];
  const location = allPoi.find((p) => p.name === locationName);
  const pois = allPoi.filter((p) => !location || p.city === location.city);
  const active = read(state, "nav.active", false);
  const results = read<NavResult[]>(state, "nav.last_results", []);
  const waypoints = read<string[]>(state, "nav.waypoints", []);
  const pushed = read<PushedCard | null>(state, "nav.pushed", null);
  const route = active || !pushed ? read<RoutePoint[]>(state, "nav.route", []) : pushed.route;
  const link = (command: string) => touch("phone_link", { command });
  const nav = (command: string, args: Record<string, unknown> = {}) => touch("navigation", { command, ...args });

  return (
    <div className="space-y-2">
      <form
        className="flex gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          if (keyword.trim()) nav("Navigation.SearchPoi", { Keyword: keyword.trim() });
        }}
      >
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索目的地"
          className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1 text-sm"
        />
        <Btn tone="cloud" type="submit">导航</Btn>
        <Btn type="button" onClick={() => nav("Navigation.NavigationToFavorite", { Type: "家" })}>回家</Btn>
        <Btn type="button" onClick={() => nav("Navigation.NavigationToFavorite", { Type: "公司" })}>去公司</Btn>
      </form>
      <div className="relative">
        <MapView pois={pois} route={route} location={location} zoom={read(state, "nav.zoom", 14)} />
        <div className="absolute right-1 top-1 flex flex-col gap-1">
          <Btn aria-label="放大地图" onClick={() => nav("Navigation.ZoomIn")}>＋</Btn>
          <Btn aria-label="缩小地图" onClick={() => nav("Navigation.ZoomOut")}>－</Btn>
        </div>
        {pushed && (
          <div
            role="dialog"
            aria-label="手机推送的路线"
            className={"absolute inset-x-2 bottom-2 rounded-lg border border-cloud bg-panel p-2 text-sm shadow-lg" + hotRing(touched(diff, "nav.pushed"))}
          >
            <p className="text-xs text-cloud">📱 来自{pushed.from}的目的地</p>
            <p className="font-medium">{pushed.name}</p>
            <p className="text-muted">
              约 {pushed.distance_km} 公里 · {pushed.eta_min} 分钟
            </p>
            <div className="mt-1 flex gap-1">
              <Btn tone="cloud" onClick={() => link("PhoneLink.AcceptPush")}>出发</Btn>
              <Btn onClick={() => link("PhoneLink.DismissPush")}>忽略</Btn>
            </div>
          </div>
        )}
      </div>
      <div className={"rounded p-2 text-sm" + hotRing(touched(diff, "nav"))}>
        {active ? (
          <>
            <p>
              去 <b>{read(state, "nav.destination", "")}</b>：剩余 {read(state, "nav.distance_km", 0)} 公里，约 {read(state, "nav.eta_min", 0)} 分钟
            </p>
            {waypoints.length > 0 && <p className="text-muted">途经：{waypoints.join("、")}</p>}
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <select
                aria-label="路线偏好"
                value={read(state, "nav.route_strategy", "推荐")}
                onChange={(e) => nav("Navigation.SwitchRouteStrategy", { RouteStrategy: e.target.value })}
                className="rounded border border-line bg-bg px-1 py-1 text-sm"
              >
                {STRATEGIES.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              {waypoints.length > 0 && <Btn onClick={() => nav("Navigation.DeleteAllWaypoints")}>删除途经点</Btn>}
              <Btn tone="safe" onClick={() => nav("Navigation.Cancel")}>结束导航</Btn>
            </div>
          </>
        ) : (
          <p className="text-muted">当前位置：{locationName || "—"}，未在导航</p>
        )}
      </div>
      {results.length > 0 && (
        <ul className="divide-y divide-line rounded border border-line text-sm">
          {results.map((r) => (
            <li key={r.name} className="flex items-center justify-between px-2 py-1">
              <span>
                {r.name}
                {r.distance_km !== undefined && <span className="text-muted"> · {r.distance_km} 公里</span>}
              </span>
              <Btn onClick={() => nav(active ? "Navigation.AddWaypointsByName" : "Navigation.SearchPoi", { Keyword: r.name })}>
                {active ? "加途经点" : "去这里"}
              </Btn>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
