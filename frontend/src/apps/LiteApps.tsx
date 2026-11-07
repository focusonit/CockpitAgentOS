import { Btn, read, type AppProps } from "./types";

interface CallLog {
  name: string;
  phone: string;
  type: string;
  at: string;
}

export function PhoneApp({ state, touch }: AppProps) {
  const inCall = read(state, "phone.in_call", false);
  const log = read<CallLog[]>(state, "phone.call_log", []);
  const phone = (command: string, args: Record<string, unknown> = {}) => touch("phone", { command, ...args });
  return (
    <div className="space-y-2 text-sm">
      {inCall ? (
        <div className="flex items-center justify-between rounded bg-veh-bg p-2">
          <span>正在和 {read(state, "phone.contact", "")} 通话</span>
          <Btn tone="safe" onClick={() => phone("PhoneCall.HangUp")}>挂断</Btn>
        </div>
      ) : (
        <p className="text-muted">蓝牙{read(state, "phone.bluetooth", false) ? "已连接" : "未连接"}</p>
      )}
      <ul className="divide-y divide-line rounded border border-line">
        {[...log].reverse().map((c, i) => (
          <li key={i} className="flex items-center justify-between px-2 py-1">
            <span>
              {c.name} <span className="text-xs text-muted">{c.type} · {c.at.slice(11)}</span>
            </span>
            <Btn onClick={() => phone("PhoneCall.DialContact", { Name: c.name })}>拨打</Btn>
          </li>
        ))}
      </ul>
    </div>
  );
}

const PAGE_NAMES: Record<string, string> = {
  ac_settings: "空调设置",
  seat_settings: "座椅设置",
  hicar: "HiCar 手机互联",
  photos: "相册",
  mini_program: "小程序",
  ai_creation: "AI 壁纸",
};

/** 其他轻量页面：只显示当前打开的是什么，以及刚产生的照片、壁纸、家居状态。 */
export function LitePage({ state, touch }: AppProps) {
  const app = read(state, "screen.app", "");
  const photos = read<unknown[]>(state, "photos", []);
  const home = read<Record<string, boolean>>(state, "home", {});
  return (
    <div className="space-y-2 text-sm">
      <p>
        当前页面：<b>{PAGE_NAMES[app] ?? (app || "桌面")}</b>
      </p>
      {Array.isArray(photos) && photos.length > 0 && <p className="text-muted">相册里有 {photos.length} 张照片</p>}
      {Object.keys(home).length > 0 && (
        <p className="text-muted">
          家里：{Object.entries(home).map(([k, v]) => `${k}${v ? "开" : "关"}`).join("、")}
        </p>
      )}
      {app && <Btn onClick={() => touch("open_page", { command: "GlobalCtrl.Home" })}>回到桌面</Btn>}
    </div>
  );
}
