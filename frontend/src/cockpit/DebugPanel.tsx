import { useMemo, useState } from "react";
import { OK_STATUSES, SEATS, type ParamSpec, type Seat, type ToolCall } from "../lib/protocol";
import { useProtocol } from "../lib/useProtocol";

interface DebugPanelProps {
  lastInvoke: ToolCall | null;
  onInvoke: (tool: string, seat: Seat, args: Record<string, unknown>) => boolean;
}

const AREA_OPTIONS = ["主驾", "副驾", "左后排", "右后排", "前排", "后排", "左侧", "右侧", "所有"];
const LEVEL_OPTIONS = ["0", "1", "2", "3"];
const input = "w-full rounded border border-line bg-panel px-1.5 py-0.5";

function ParamControl({ param, value, onChange }: { param: ParamSpec; value: string; onChange: (v: string) => void }) {
  const select = (options: string[]) => (
    <select className={input} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">（不填）</option>
      {options.map((o) => (
        <option key={o}>{o}</option>
      ))}
    </select>
  );
  if (param.type === "enum") return select(param.values ?? []);
  if (param.type === "level") return select(LEVEL_OPTIONS);
  if (param.type === "area") return select(AREA_OPTIONS);
  if (param.type === "int") {
    return (
      <input
        type="number"
        className={input}
        min={param.min}
        max={param.max}
        placeholder={param.min !== undefined ? `${param.min}–${param.max ?? ""}` : ""}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  const listId = `ex-${param.name}`;
  return (
    <>
      <input className={input} list={listId} value={value} placeholder={param.examples?.[0] ?? ""} onChange={(e) => onChange(e.target.value)} />
      {param.examples?.length ? (
        <datalist id={listId}>
          {param.examples.map((x) => (
            <option key={x} value={x} />
          ))}
        </datalist>
      ) : null}
    </>
  );
}

/** 调试面板：控件从 /api/protocol 生成，直接把指令下发给执行器，不经过大脑。 */
export function DebugPanel({ lastInvoke, onInvoke }: DebugPanelProps) {
  const { spec, error } = useProtocol();
  const [tool, setTool] = useState("climate");
  const [command, setCommand] = useState("");
  const [filter, setFilter] = useState("");
  const [showLongtail, setShowLongtail] = useState(false);
  const [seat, setSeat] = useState<Seat>("主驾");
  const [values, setValues] = useState<Record<string, string>>({});

  const commands = useMemo(() => {
    const all = spec?.tools[tool]?.commands ?? [];
    const q = filter.trim();
    return all.filter((c) => (showLongtail || !c.longtail) && (!q || c.name.includes(q) || c.desc.includes(q)));
  }, [spec, tool, filter, showLongtail]);
  const current = commands.find((c) => c.name === command) ?? commands[0];

  const pickTool = (name: string) => {
    setTool(name);
    setCommand("");
    setValues({});
  };
  const pickCommand = (name: string) => {
    setCommand(name);
    setValues({});
  };
  const run = () => {
    if (!current) return;
    const args = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
    onInvoke(current.tool, seat, { command: current.name, ...args });
  };

  if (error) return <Shell><p className="p-3 text-bad">{error}</p></Shell>;
  if (!spec) return <Shell><p className="p-3 text-muted">协议加载中…</p></Shell>;

  const ok = lastInvoke ? OK_STATUSES.has(lastInvoke.status) : false;
  const message = lastInvoke && typeof lastInvoke.result === "object" && lastInvoke.result !== null
    ? String((lastInvoke.result as Record<string, unknown>).message ?? "")
    : "";

  return (
    <Shell>
      <div className="space-y-2 p-3">
        <div className="grid grid-cols-[3.5rem_1fr] items-center gap-x-2 gap-y-1.5">
          <span className="text-xs text-muted">工具</span>
          <select className={input} value={tool} onChange={(e) => pickTool(e.target.value)}>
            {Object.entries(spec.tools).map(([name, t]) => (
              <option key={name} value={name}>
                {name}（{t.commands.length}）
              </option>
            ))}
          </select>
          <span className="text-xs text-muted">筛选</span>
          <div className="flex items-center gap-2">
            <input className={input} placeholder="指令名或说明" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <label className="flex shrink-0 items-center gap-1 text-xs">
              <input type="checkbox" checked={showLongtail} onChange={(e) => setShowLongtail(e.target.checked)} />
              长尾
            </label>
          </div>
          <span className="text-xs text-muted">指令</span>
          <select className={input} value={current?.name ?? ""} onChange={(e) => pickCommand(e.target.value)}>
            {commands.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
                {c.longtail ? " ·长尾" : ""}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted">座位</span>
          <select className={input} value={seat} onChange={(e) => setSeat(e.target.value as Seat)}>
            {SEATS.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        {current?.desc && <p className="text-xs text-muted">{current.desc}</p>}
        {current && current.params.length > 0 && (
          <div className="grid grid-cols-[3.5rem_1fr] items-center gap-x-2 gap-y-1.5 border-t border-line pt-2">
            {current.params.map((p) => (
              <ParamRow key={p.name} param={p}>
                <ParamControl param={p} value={values[p.name] ?? ""} onChange={(v) => setValues((prev) => ({ ...prev, [p.name]: v }))} />
              </ParamRow>
            ))}
          </div>
        )}
        <button className="w-full rounded bg-veh px-2 py-1 font-medium text-panel disabled:opacity-50" disabled={!current} onClick={run}>
          执行
        </button>
        {lastInvoke && (
          <div className={"rounded px-2 py-1.5 text-xs " + (ok ? "bg-veh-bg" : "bg-safe-bg")}>
            <div className={"font-medium " + (ok ? "text-veh" : "text-safe")}>
              {lastInvoke.status} · {spec.statuses[lastInvoke.status] ?? ""}
            </div>
            <div>{message}</div>
            <div className="mt-0.5 break-all text-muted">{JSON.stringify(lastInvoke.args)}</div>
          </div>
        )}
      </div>
    </Shell>
  );
}

function ParamRow({ param, children }: { param: ParamSpec; children: React.ReactNode }) {
  return (
    <>
      <span className="truncate text-xs text-muted" title={param.desc}>
        {param.name}
      </span>
      <div>{children}</div>
    </>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2 font-medium text-veh">调试面板（直接下发指令）</header>
      {children}
    </section>
  );
}
