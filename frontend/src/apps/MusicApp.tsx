import type { Song } from "../lib/protocol";
import { Btn, hotRing, read, touched, type AppProps } from "./types";

const MODES = ["列表循环", "单曲循环", "随机播放", "顺序播放"];

function mmss(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 模拟歌词：曲库里没有真实歌词，按进度高亮几行占位文字。 */
function Lyrics({ title, progress, duration }: { title: string; progress: number; duration: number }) {
  const lines = [`《${title}》`, "（模拟歌词第一句）", "（模拟歌词第二句）", "（副歌）", "（模拟歌词结尾）"];
  const at = duration ? Math.min(lines.length - 1, Math.floor((progress / duration) * lines.length)) : 0;
  return (
    <div className="rounded bg-bg p-2 text-center text-sm" aria-label="歌词">
      {lines.map((l, i) => (
        <p key={i} className={i === at ? "font-medium text-cloud" : "text-muted"}>
          {l}
        </p>
      ))}
    </div>
  );
}

export function MusicApp({ state, data, diff, touch }: AppProps) {
  const title = read(state, "media.title", "");
  const playing = read(state, "media.playing", false);
  const progress = read(state, "media.progress_s", 0);
  const duration = read(state, "media.duration_s", 0);
  const queue = read<Song[]>(state, "media.queue", []);
  const index = read(state, "media.index", 0);
  const favorites = read<Song[]>(state, "media.favorites", []);
  const online = read(state, "network", true);
  const ctrl = (command: string, args: Record<string, unknown> = {}) => touch("media_control", { command, ...args });
  const play = (command: string, args: Record<string, unknown> = {}) => touch("media_play", { command, ...args });
  const isFavorite = favorites.some((f) => f.title === title);
  const list = queue.length ? queue : (data?.songs ?? []).slice(0, 8);

  return (
    <div className="space-y-2">
      {!online && <p className="rounded bg-safe-bg px-2 py-1 text-sm text-safe">没有网络，只能放本地音乐</p>}
      <div className={"rounded p-2" + hotRing(touched(diff, "media"))}>
        {title ? (
          <>
            <p className="font-medium">
              {title} <span className="text-sm text-muted">· {read(state, "media.artist", "")}</span>
            </p>
            <div className="mt-1 flex items-center gap-2 text-xs tabular-nums text-muted">
              <span>{mmss(progress)}</span>
              <div className="h-1 flex-1 rounded bg-line">
                <div className="h-1 rounded bg-cloud" style={{ width: duration ? `${(progress / duration) * 100}%` : 0 }} />
              </div>
              <span>{mmss(duration)}</span>
            </div>
          </>
        ) : (
          <p className="text-muted">没有在播放</p>
        )}
        <div className="mt-2 flex flex-wrap gap-1">
          <Btn aria-label="上一首" onClick={() => ctrl("GlobalCtrl.Previous")}>⏮</Btn>
          <Btn tone="cloud" aria-label={playing ? "暂停" : "播放"} onClick={() => (playing ? ctrl("GlobalCtrl.Pause") : title ? ctrl("GlobalCtrl.Resume") : play("Music.Play"))}>
            {playing ? "⏸" : "▶"}
          </Btn>
          <Btn aria-label="下一首" onClick={() => ctrl("GlobalCtrl.Next")}>⏭</Btn>
          <Btn aria-pressed={isFavorite} disabled={!title} onClick={() => ctrl("GlobalCtrl.AddFavorite")}>
            {isFavorite ? "♥ 已收藏" : "♡ 收藏"}
          </Btn>
          <Btn disabled={!title} onClick={() => play("Music.Dislike")}>不喜欢</Btn>
          <Btn disabled={!title} onClick={() => play(read(state, "media.lyrics_open", false) ? "Music.CloseLyrics" : "Music.OpenLyrics")}>歌词</Btn>
          <Btn disabled={!title} onClick={() => play(read(state, "media.mv_open", false) ? "Music.CloseMV" : "Music.PlayMV")}>MV</Btn>
          <select
            aria-label="播放模式"
            value={read(state, "media.play_mode", "列表循环")}
            onChange={(e) => ctrl("GlobalCtrl.SetPlayMode", { Mode: e.target.value })}
            className="rounded border border-line bg-bg px-1 text-sm"
          >
            {MODES.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>
      {read(state, "media.mv_open", false) && (
        <div className="flex h-24 items-center justify-center rounded bg-fg text-sm text-bg">正在播放《{title}》MV（模拟）</div>
      )}
      {read(state, "media.lyrics_open", false) && <Lyrics title={title} progress={progress} duration={duration} />}
      <div>
        <p className="mb-1 text-xs text-muted">{queue.length ? "播放列表" : "推荐歌曲"}</p>
        <ul className="divide-y divide-line rounded border border-line text-sm">
          {list.map((s, i) => (
            <li key={`${s.title}-${i}`}>
              <button
                onClick={() => play("Music.Play", { Song: s.title, Singer: s.artist })}
                className={"flex w-full justify-between px-2 py-1 text-left " + (queue.length && i === index ? "text-cloud" : "")}
              >
                <span>{s.title}</span>
                <span className="text-muted">{s.artist}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      {favorites.length > 0 && <p className="text-xs text-muted">收藏：{favorites.map((f) => f.title).join("、")}</p>}
    </div>
  );
}
