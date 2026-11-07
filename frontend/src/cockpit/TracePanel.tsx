import type { TraceEntry } from "../lib/useCockpitSocket";

// 车端阶段用绿色，云端阶段用靛蓝，安全闸门用琥珀，与架构图的颜色语义一致。
const STAGE_COLOR: Record<string, string> = {
  input: "text-veh",
  fast_path: "text-veh",
  executor: "text-veh",
  vehicle: "text-veh",
  timer: "text-veh",
  gate: "text-safe",
  unsupported: "text-safe",
  confirm: "text-safe",
  safety: "text-safe",
  gateway: "text-cloud",
  llm: "text-cloud",
  planner: "text-cloud",
  tool: "text-cloud",
  output: "text-muted",
};

// 阶段名换成中文，一眼看出这句话走的是快速通道还是云端
const STAGE_LABEL: Record<string, string> = {
  input: "输入",
  fast_path: "快速通道",
  executor: "执行器",
  timer: "定时",
  gate: "闸门",
  unsupported: "未上线",
  confirm: "确认",
  gateway: "网关",
  llm: "云端",
  output: "输出",
};

interface TracePanelProps {
  traces: TraceEntry[];
}

export function TracePanel({ traces }: TracePanelProps) {
  return (
    <section className="flex min-h-48 flex-1 flex-col rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2 font-medium">链路追踪</header>
      <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs">
        {traces.length === 0 && <p className="text-muted">每次请求会在这里显示各环节和耗时。</p>}
        {traces.map((t) => (
          <div key={t.requestId}>
            <div className="mb-1 font-mono text-muted">#{t.requestId}</div>
            <ol className="space-y-0.5">
              {t.events.map((e, i) => (
                <li key={i} className="flex gap-2">
                  <span className="w-14 shrink-0 text-right font-mono tabular-nums text-muted">{e.at_ms}ms</span>
                  <span className={"w-16 shrink-0 " + (STAGE_COLOR[e.stage] ?? "")} title={e.stage}>
                    {STAGE_LABEL[e.stage] ?? e.stage}
                  </span>
                  <span className="min-w-0 break-words">{e.message}</span>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}
