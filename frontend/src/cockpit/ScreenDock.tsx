import { useEffect, useState } from "react";
import { LitePage, PhoneApp } from "../apps/LiteApps";
import { MeituanApp } from "../apps/MeituanApp";
import { MusicApp } from "../apps/MusicApp";
import { NavigationApp } from "../apps/NavigationApp";
import { read, type AppProps } from "../apps/types";
import { DoubaoAvatar, type Mood } from "./DoubaoAvatar";

const TABS = [
  { id: "navigation", name: "导航" },
  { id: "music", name: "音乐" },
  { id: "meituan", name: "美团" },
  { id: "phone", name: "电话" },
] as const;

interface ScreenDockProps extends AppProps {
  confirmPending: boolean;
  onEvent: (name: string) => void;
  mood: Mood;
}

/** 中控屏：跟随车况里的 screen.app（智能体用了哪个 App 就切到哪个），也可以手动点坞栏切换。 */
export function ScreenDock(props: ScreenDockProps) {
  const front = read(props.state, "screen.app", "");
  const [active, setActive] = useState<string>(front || "navigation");

  useEffect(() => {
    if (front) setActive(front);
  }, [front]);

  const open = (id: string, name: string) => {
    setActive(id);
    props.touch("open_page", { command: "APP.Open", App: name });
  };

  const body = () => {
    switch (active) {
      case "navigation":
        return <NavigationApp {...props} />;
      case "music":
        return <MusicApp {...props} />;
      case "meituan":
        return <MeituanApp {...props} />;
      case "phone":
        return <PhoneApp {...props} />;
      default:
        return <LitePage {...props} />;
    }
  };

  return (
    <section className="flex min-h-56 flex-col rounded-lg border border-line bg-panel">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="flex items-center gap-3">
          <span className="font-medium">中控屏</span>
          <DoubaoAvatar mood={props.mood} />
        </span>
        <span className="text-xs text-muted">前台：{front || "桌面"}</span>
      </header>
      <div className="flex-1 p-3">{body()}</div>
      <nav className="flex gap-1 border-t border-line p-2" aria-label="App 切换坞">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => open(tab.id, tab.name)}
            aria-pressed={active === tab.id}
            className={"flex-1 rounded px-2 py-1 " + (active === tab.id ? "bg-cloud text-white" : "bg-bg")}
          >
            {tab.name}
          </button>
        ))}
      </nav>
    </section>
  );
}
