import { useCallback, useEffect, useState, type FormEvent } from "react";

const POLL_MS = 1500;

interface PushedCard {
  name: string;
  distance_km: number;
  eta_min: number;
}

interface CarSummary {
  battery_pct: number;
  range_km: number;
  location: string;
  gear: string;
  speed_kmh: number;
  doors_locked: boolean;
  outside_temp: number;
  cabin_temp: number;
  ac_on: boolean;
  ac_temp: number;
  windows: Record<string, number>;
  nav_active: boolean;
  nav_destination: string;
  nav_pushed: PushedCard | null;
  network: boolean;
  now: string;
}

interface StatusResponse {
  connected: boolean;
  state: CarSummary | null;
}

interface ActionResponse {
  ok: boolean;
  status: string;
  message: string;
  label: string;
  state: CarSummary;
}

type Action = "ac_on" | "ac_off" | "ac_temp" | "lock" | "unlock" | "windows_close" | "push";

async function postAction(action: Action, value?: unknown): Promise<ActionResponse> {
  const resp = await fetch("/api/phone/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, value }),
  });
  const body: unknown = await resp.json();
  if (!resp.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `请求失败（${resp.status}）`);
  }
  return body as ActionResponse;
}

/** 手机 App（/phone）：查车况、远程空调和锁车、把目的地推到车机。 */
export function PhoneApp() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [dest, setDest] = useState("");
  const [temp, setTemp] = useState(22);

  const refresh = useCallback(async () => {
    try {
      const resp = await fetch("/api/phone/status");
      if (!resp.ok) throw new Error(`状态接口返回 ${resp.status}`);
      setStatus((await resp.json()) as StatusResponse);
      setFetchError(null);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "连不上服务");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const run = async (action: Action, value?: unknown) => {
    setBusy(true);
    try {
      const result = await postAction(action, value);
      setToast({ ok: result.ok, text: `${result.label}：${result.message || result.status}` });
      setStatus({ connected: true, state: result.state });
    } catch (err) {
      setToast({ ok: false, text: err instanceof Error ? err.message : "操作失败" });
    } finally {
      setBusy(false);
    }
  };

  const push = (e: FormEvent) => {
    e.preventDefault();
    if (dest.trim()) void run("push", dest.trim());
  };

  const car = status?.state ?? null;
  const offline = !status?.connected;
  const openWindows = car ? Object.entries(car.windows).filter(([, v]) => v > 0).map(([k]) => k) : [];

  return (
    <div className="mx-auto flex min-h-full max-w-sm flex-col gap-3 bg-bg p-4">
      <header className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">我的车</h1>
        <span className={"text-xs " + (offline ? "text-bad" : "text-veh")}>{offline ? "车机不在线" : "已连接"}</span>
      </header>

      {fetchError && <p className="rounded bg-safe-bg px-3 py-2 text-sm text-bad">{fetchError}</p>}
      {offline && !fetchError && (
        <p className="rounded bg-safe-bg px-3 py-2 text-sm">先在电脑上打开座舱页面（http://localhost:5173），手机会绑定最近打开的那辆车。</p>
      )}

      <section className="rounded-xl border border-line bg-panel p-3" aria-label="车况">
        <div className="flex items-end justify-between">
          <div>
            <div className="text-3xl font-semibold tabular-nums">{car ? `${car.battery_pct}%` : "—"}</div>
            <div className="text-xs text-muted">续航 {car?.range_km ?? "—"} 公里</div>
          </div>
          <div className="text-right text-sm">
            <div>{car?.location ?? "—"}</div>
            <div className="text-xs text-muted">
              {car ? `${car.gear} 挡 · ${car.speed_kmh} km/h` : ""}
            </div>
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
          <Stat label="车内" value={car ? `${car.cabin_temp}℃` : "—"} />
          <Stat label="车外" value={car ? `${car.outside_temp}℃` : "—"} />
          <Stat label="车门" value={car ? (car.doors_locked ? "已上锁" : "未上锁") : "—"} warn={car ? !car.doors_locked : false} />
          <Stat label="空调" value={car ? (car.ac_on ? `开 ${car.ac_temp}℃` : "关") : "—"} />
          <Stat label="车窗" value={car ? (openWindows.length ? `${openWindows.length} 扇开着` : "全关") : "—"} warn={openWindows.length > 0} />
          <Stat label="导航" value={car ? (car.nav_active ? car.nav_destination : "未导航") : "—"} />
        </dl>
      </section>

      <section className="rounded-xl border border-line bg-panel p-3" aria-label="远程控制">
        <h2 className="mb-2 text-sm font-medium">远程控制</h2>
        <div className="grid grid-cols-2 gap-2">
          <PhoneBtn disabled={busy || offline} tone="cloud" onClick={() => void run(car?.ac_on ? "ac_off" : "ac_on")}>
            {car?.ac_on ? "关闭空调" : "打开空调"}
          </PhoneBtn>
          <PhoneBtn disabled={busy || offline} onClick={() => void run(car?.doors_locked ? "unlock" : "lock")}>
            {car?.doors_locked ? "解锁" : "锁车"}
          </PhoneBtn>
          <div className="col-span-2 flex items-center gap-2">
            <input
              type="range"
              min={16}
              max={32}
              value={temp}
              onChange={(e) => setTemp(Number(e.target.value))}
              aria-label="空调温度"
              className="flex-1"
            />
            <PhoneBtn disabled={busy || offline} onClick={() => void run("ac_temp", temp)}>
              设为 {temp}℃
            </PhoneBtn>
          </div>
          <PhoneBtn disabled={busy || offline || openWindows.length === 0} className="col-span-2" onClick={() => void run("windows_close")}>
            一键关窗
          </PhoneBtn>
        </div>
      </section>

      <section className="rounded-xl border border-line bg-panel p-3" aria-label="发送目的地到车">
        <h2 className="mb-2 text-sm font-medium">发送目的地到车</h2>
        <form onSubmit={push} className="flex gap-2">
          <input
            value={dest}
            onChange={(e) => setDest(e.target.value)}
            placeholder="例如 首都机场"
            maxLength={40}
            className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1.5 text-sm"
          />
          <PhoneBtn disabled={busy || offline || !dest.trim()} tone="cloud" type="submit">
            发送
          </PhoneBtn>
        </form>
        {car?.nav_pushed && (
          <p className="mt-2 text-xs text-muted">
            车机正弹着路线卡片：{car.nav_pushed.name}（约 {car.nav_pushed.distance_km} 公里），等车里确认出发。
          </p>
        )}
      </section>

      {toast && (
        <p role="status" className={"rounded px-3 py-2 text-sm " + (toast.ok ? "bg-veh-bg" : "bg-safe-bg text-bad")}>
          {toast.text}
        </p>
      )}
      <p className="mt-auto text-center text-xs text-muted">模拟手机 · 操作以车主（主驾）身份下发 · 模拟时间 {car?.now ?? "—"}</p>
    </div>
  );
}

function Stat({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="rounded bg-bg px-1 py-1.5">
      <dt className="text-muted">{label}</dt>
      <dd className={"truncate font-medium " + (warn ? "text-safe" : "")}>{value}</dd>
    </div>
  );
}

function PhoneBtn(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "cloud" | "plain" }) {
  const { tone = "plain", className = "", ...rest } = props;
  const color = tone === "cloud" ? "bg-cloud text-white" : "border border-line bg-bg";
  return <button {...rest} className={`rounded-lg px-3 py-2 text-sm disabled:opacity-40 ${color} ${className}`} />;
}
