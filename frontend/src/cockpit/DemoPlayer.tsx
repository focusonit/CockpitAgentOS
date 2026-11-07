import { useEffect, useState } from "react";
import type { ClientMessage, Seat } from "../lib/protocol";

interface DemoTurn {
  seat: Seat;
  text: string;
  note?: string;
}

interface Demo {
  id: string;
  title: string;
  summary?: string;
  behaviors?: string[];
  profile?: string | null;
  init?: Record<string, unknown> | null;
  turns: DemoTurn[];
}

interface DemoPlayerProps {
  busy: boolean;
  send: (msg: ClientMessage) => boolean;
  say: (seat: Seat, text: string) => void;
  onSeat: (seat: Seat) => void;
  clearMessages: () => void;
}

/** 等上一句回复完，再隔这么久说下一句，留时间看车况变化 */
const AUTO_GAP_MS = 1500;

/** 体验脚本（data/demos/*.yaml）：载入初始车况，逐句或自动播放。 */
export function DemoPlayer({ busy, send, say, onSeat, clearMessages }: DemoPlayerProps) {
  const [demos, setDemos] = useState<Demo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [loaded, setLoaded] = useState<Demo | null>(null);
  const [index, setIndex] = useState(0);
  const [auto, setAuto] = useState(false);

  useEffect(() => {
    fetch("/api/demos")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((list: Demo[]) => {
        setDemos(list);
        setSelected(list[0]?.id ?? "");
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "加载失败"));
  }, []);

  const load = () => {
    const demo = demos.find((d) => d.id === selected);
    if (!demo) return;
    if (!send({ type: "reset", init: demo.init ?? undefined, profile: demo.profile ?? undefined })) return;
    clearMessages();
    setLoaded(demo);
    setIndex(0);
    setAuto(false);
  };

  const next = () => {
    const turn = loaded?.turns[index];
    if (!turn || busy) return;
    onSeat(turn.seat);
    say(turn.seat, turn.text);
    setIndex((i) => i + 1);
  };

  const done = !loaded || index >= loaded.turns.length;

  useEffect(() => {
    if (!auto || busy) return;
    if (done) {
      setAuto(false);
      return;
    }
    const timer = window.setTimeout(next, index === 0 ? 300 : AUTO_GAP_MS);
    return () => window.clearTimeout(timer);
    // next 每次渲染都会变，只在这些状态变化时重新排期
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, busy, index, done]);

  const upcoming = loaded?.turns[index];
  const btn = "rounded px-2 py-0.5 disabled:opacity-40";

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="font-medium">体验脚本</span>
        <select
          aria-label="选择体验脚本"
          className="min-w-0 flex-1 rounded border border-line bg-panel px-1 text-sm"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {demos.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title}
            </option>
          ))}
        </select>
        <button className={btn + " bg-bg"} onClick={load} disabled={!selected}>
          载入
        </button>
      </header>
      <div className="space-y-2 p-3 text-sm">
        {error && <p className="text-safe">体验脚本加载失败：{error}</p>}
        {!loaded && !error && <p className="text-muted">选一个脚本点“载入”，会重置车况并按脚本设好初始状态。</p>}
        {loaded && (
          <>
            {loaded.summary && <p className="text-xs text-muted">{loaded.summary}</p>}
            <ol className="space-y-1">
              {loaded.turns.map((t, i) => (
                <li
                  key={i}
                  className={
                    "rounded px-2 py-1 text-xs " +
                    (i < index ? "text-muted line-through decoration-line" : i === index ? "bg-cloud-bg" : "")
                  }
                >
                  <span className="font-medium">{t.seat}：</span>
                  {t.text}
                  {i === index && t.note && <span className="mt-0.5 block text-muted">看点：{t.note}</span>}
                </li>
              ))}
            </ol>
            <div className="flex items-center gap-2">
              <button className={btn + " bg-cloud text-white"} onClick={next} disabled={done || busy || auto}>
                下一句
              </button>
              <button className={btn + " bg-bg"} onClick={() => setAuto((a) => !a)} disabled={done}>
                {auto ? "暂停" : "自动播放"}
              </button>
              <span className="ml-auto text-xs text-muted">
                {done ? "播放完了" : busy ? "等豆包回复…" : `第 ${index + 1} / ${loaded.turns.length} 句 · ${upcoming?.seat}`}
              </span>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
