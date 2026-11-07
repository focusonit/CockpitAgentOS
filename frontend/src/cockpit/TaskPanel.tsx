import { read, Btn } from "../apps/types";
import type { VehicleState } from "../lib/protocol";

interface Task {
  id: number;
  text: string;
  kind: string;
  action: string;
  due: string | null;
  condition: string;
  status: string;
}

interface TaskPanelProps {
  state: VehicleState | null;
  onAdvance: (minutes: number) => void;
  onCancel: (task: Task) => void;
}

const STEPS = [1, 5, 10, 30, 60];

/** 任务大师：模拟时钟 + 定时/条件任务列表。时间只有点“快进”才会走。 */
export function TaskPanel({ state, onAdvance, onCancel }: TaskPanelProps) {
  const now = read(state, "now", "");
  const tasks = read<Task[]>(state, "tasks", []);
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="font-medium">任务大师</span>
        <span className="tabular-nums text-sm" aria-label="模拟时间">
          {now.replace("T", " ").slice(0, 16)}
        </span>
      </header>
      <div className="space-y-2 p-3 text-sm">
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-muted">快进</span>
          {STEPS.map((m) => (
            <Btn key={m} onClick={() => onAdvance(m)}>
              +{m >= 60 ? `${m / 60} 小时` : `${m} 分`}
            </Btn>
          ))}
        </div>
        {tasks.length ? (
          <ul className="divide-y divide-line">
            {tasks.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-2 py-1">
                <span>
                  {t.kind === "reminder" ? "⏰" : "⚙"} {t.action}
                  <span className="block text-xs text-muted">
                    {t.due ? t.due.replace("T", " ").slice(5, 16) : t.condition} · {t.status}
                  </span>
                </span>
                {t.status === "待执行" && <Btn onClick={() => onCancel(t)}>取消</Btn>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted">还没有任务。试试“半小时后关掉空调”。</p>
        )}
      </div>
    </section>
  );
}
