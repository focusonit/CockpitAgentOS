"""音乐 / 视频 / 新闻 App：曲库、播放队列、上一首下一首、收藏与不喜欢、歌词和 MV。

没网时的 NotNet 由前置条件规则判断（Source=本地 除外），这里只负责从本地曲库选歌。
"""

from __future__ import annotations

from typing import Any

from backend.apps.common import cn_number, load, ok
from backend.protocol import status as st
from backend.vehicle.devices.base import Ctx, Handler, Outcome, clamp

SEEK_STEP_S = 15


def library() -> list[dict[str, Any]]:
    return load("music")["songs"]


def videos() -> list[dict[str, Any]]:
    return load("music")["videos"]


def _song(s: dict[str, Any]) -> dict[str, Any]:
    return {"title": s["title"], "artist": s["artist"], "album": s.get("album", ""), "duration_s": s.get("duration_s", 240)}


def _match(song: dict[str, Any], *, title: Any, singer: Any, style: Any, album: Any) -> bool:
    if title and str(title) not in song["title"] and song["title"] not in str(title):
        return False
    if singer and str(singer) not in song["artist"]:
        return False
    if album and str(album) not in song.get("album", ""):
        return False
    if style and not any(str(style) in t or t in str(style) for t in song.get("styles", [])):
        return False
    return True


def _disliked(state: Any) -> set[str]:
    return {d.get("title") for d in state.get("media.disliked") or []}


def _local_only(ctx: Ctx) -> bool:
    return ctx.arg("Source") == "本地" or not ctx.state.get("network")


def _start(ctx: Ctx, queue: list[dict[str, Any]], index: int = 0, source: str = "", message: str = "") -> Outcome:
    song = queue[index]
    updates = {
        "media.kind": "music", "media.playing": True, "media.title": song["title"], "media.artist": song["artist"],
        "media.album": song.get("album", ""), "media.duration_s": song["duration_s"], "media.progress_s": 0,
        "media.queue": queue, "media.index": index, "media.source": source, "media.episode": 0, "media.mv_open": False,
    }
    text = message or f"正在播放{song['artist']}的《{song['title']}》"
    return ok(ctx, text, updates, {"song": song, "queue": [q["title"] for q in queue]}, app="music")


def play(ctx: Ctx) -> Outcome:
    title, singer, style, album = ctx.arg("Song"), ctx.arg("Singer"), ctx.arg("Style"), ctx.arg("Album")
    local = _local_only(ctx)
    skip = _disliked(ctx.state)
    pool = [s for s in library() if (s.get("local") or not local) and s["title"] not in skip]
    hits = [_song(s) for s in pool if _match(s, title=title, singer=singer, style=style, album=album)]
    source = "本地" if local else str(ctx.arg("Source") or "")
    if hits:
        return _start(ctx, hits, source=source)
    if local:
        return Outcome(st.FAILED, message="本地曲库里没有符合要求的歌")
    if title:  # 曲库没有的歌：当作在线曲库里有，合成一首
        song = {"title": str(title), "artist": str(singer or "未知歌手"), "album": str(album or ""), "duration_s": 240}
        return _start(ctx, [song], source=source)
    return _start(ctx, [_song(s) for s in pool], source=source,
                  message=f"没找到完全符合的，先给你放{pool[0]['artist']}的《{pool[0]['title']}》")


def play_album(ctx: Ctx) -> Outcome:
    return play(ctx)


def open_app(ctx: Ctx) -> Outcome:
    if ctx.arg("Source") == "本地":
        return play(ctx)
    return ok(ctx, "已打开音乐", app="music")


def close(ctx: Ctx) -> Outcome:
    updates = {"media.playing": False, "media.lyrics_open": False, "media.mv_open": False}
    if ctx.state.get("screen.app") == "music":
        updates["screen.app"] = ""
    return Outcome(updates=updates, message="已关闭音乐")


def _current(ctx: Ctx) -> Outcome | None:
    if not ctx.state.get("media.title"):
        return Outcome(st.FAILED, message="现在没有在播放")
    return None


def toggle(path: str, value: bool, text: str) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        return _current(ctx) or ok(ctx, text, {path: value}, app="music")

    return handler


def _queue(ctx: Ctx) -> list[dict[str, Any]]:
    queue = list(ctx.state.get("media.queue") or [])
    if queue:
        return queue
    # 没有队列（例如初始车况里直接给了正在播放的歌）：用曲库当队列，从当前歌开始
    songs = [_song(s) for s in library()]
    current = ctx.state.get("media.title")
    index = next((i for i, s in enumerate(songs) if s["title"] == current), None)
    if index is None and current:
        songs.insert(0, {"title": current, "artist": ctx.state.get("media.artist") or "", "album": "", "duration_s": 240})
        index = 0
    return songs[index or 0:] + songs[: index or 0]


def step(delta: int) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        failed = _current(ctx)
        if failed:
            return failed
        if ctx.state.get("media.kind") == "video":
            episode = max(1, int(ctx.state.get("media.episode") or 1) + delta)
            return ok(ctx, f"正在播放《{ctx.state.get('media.title')}》第{episode}集",
                      {"media.episode": episode, "media.progress_s": 0, "media.playing": True}, app="music")
        if ctx.state.get("media.kind") != "music":
            return ok(ctx, "已切换到下一条" if delta > 0 else "已切换到上一条", {"media.progress_s": 0}, app="music")
        queue = _queue(ctx)
        skip = _disliked(ctx.state)
        index = int(ctx.state.get("media.index") or 0) if ctx.state.get("media.queue") else 0
        for _ in range(len(queue)):
            index = (index + delta) % len(queue)
            if queue[index]["title"] not in skip:
                break
        return _start(ctx, queue, index, ctx.state.get("media.source") or "")

    return handler


def pause(ctx: Ctx) -> Outcome:
    return _current(ctx) or ok(ctx, "已暂停", {"media.playing": False}, app="music")


def resume(ctx: Ctx) -> Outcome:
    return _current(ctx) or ok(ctx, "继续播放", {"media.playing": True}, app="music")


def seek(sign: int) -> Handler:
    def handler(ctx: Ctx) -> Outcome:
        failed = _current(ctx)
        if failed:
            return failed
        duration = int(ctx.state.get("media.duration_s") or 240)
        value = clamp(int(ctx.state.get("media.progress_s") or 0) + sign * SEEK_STEP_S, 0, duration)
        return ok(ctx, f"已{'快进' if sign > 0 else '快退'}{SEEK_STEP_S}秒", {"media.progress_s": value}, app="music")

    return handler


MODE_NAMES = {"SingleLoop": "单曲循环", "ListLoop": "列表循环", "ShufflePlay": "随机播放", "SequencePlay": "顺序播放"}


def set_mode(ctx: Ctx) -> Outcome:
    mode = ctx.arg("Mode") or "列表循环"
    mode = MODE_NAMES.get(mode, mode)
    return ok(ctx, f"已切换为{mode}", {"media.play_mode": mode}, app="music")


def _now_playing(ctx: Ctx) -> dict[str, Any]:
    return {"title": ctx.state.get("media.title"), "artist": ctx.state.get("media.artist")}


def add_favorite(ctx: Ctx) -> Outcome:
    failed = _current(ctx)
    if failed:
        return failed
    song = _now_playing(ctx)
    favorites = list(ctx.state.get("media.favorites") or [])
    if any(f.get("title") == song["title"] for f in favorites):
        return ok(ctx, f"《{song['title']}》已经在收藏里了", app="music")
    return ok(ctx, f"已收藏《{song['title']}》", {"media.favorites": [*favorites, song]}, {"song": song}, app="music")


def dislike(ctx: Ctx) -> Outcome:
    failed = _current(ctx)
    if failed:
        return failed
    song = _now_playing(ctx)
    disliked = [*(ctx.state.get("media.disliked") or []), song]
    nxt = step(1)(Ctx(ctx.state.apply({"media.disliked": disliked})[0], ctx.tool, ctx.command, ctx.args, ctx.seat))
    return Outcome(nxt.status, {**nxt.updates, "media.disliked": disliked}, nxt.data,
                   f"好的，以后不再推荐《{song['title']}》。" + nxt.message)


def play_mv(ctx: Ctx) -> Outcome:
    """带歌名时先播这首歌再打开 MV。"""
    if not ctx.arg("Song"):
        return toggle("media.mv_open", True, "开始播放 MV")(ctx)
    out = play(Ctx(ctx.state, ctx.tool, "Music.Play", {"Song": ctx.arg("Song")}, ctx.seat, ctx.limits))
    if not st.is_ok(out.status):
        return out
    return Outcome(out.status, {**out.updates, "media.mv_open": True}, out.data, out.message.rstrip("。") + "，已打开 MV。")


def play_favorite(ctx: Ctx) -> Outcome:
    favorites = list(ctx.state.get("media.favorites") or [])
    if not favorites:
        out = _start(ctx, [_song(s) for s in library()[:5]], source="每日推荐")
        return Outcome(out.status, out.updates, out.data, "收藏列表还是空的，先给你播放每日推荐。" + out.message)
    known = {s["title"]: _song(s) for s in library()}
    queue = [known.get(f["title"], {**f, "album": "", "duration_s": 240}) for f in favorites]
    return _start(ctx, queue, source="收藏")


def favorite_list(ctx: Ctx) -> Outcome:
    favorites = list(ctx.state.get("media.favorites") or [])
    text = "、".join(f"《{f['title']}》" for f in favorites) if favorites else "收藏列表是空的"
    return ok(ctx, text, data={"favorites": favorites}, app="music")


def current_play(ctx: Ctx) -> Outcome:
    s = ctx.state
    if not s.get("media.title"):
        return ok(ctx, "现在没有在播放", app="music")
    text = f"正在播放{s.get('media.artist')}的《{s.get('media.title')}》" if s.get("media.kind") == "music" else f"正在播放《{s.get('media.title')}》"
    return ok(ctx, text, data=_now_playing(ctx), app="music")


def _play_other(ctx: Ctx, kind: str, title: str, artist: str = "", episode: int = 0, duration: int = 600) -> Outcome:
    updates = {"media.kind": kind, "media.playing": True, "media.title": title, "media.artist": artist, "media.album": "",
               "media.episode": episode, "media.progress_s": 0, "media.duration_s": duration, "media.queue": [], "media.index": 0}
    suffix = f"第{episode}集" if episode else ""
    return ok(ctx, f"正在播放《{title}》{suffix}", updates, {"title": title, "episode": episode}, app="music")


def play_video(ctx: Ctx) -> Outcome:
    name = str(ctx.arg("Resource") or "")
    if not name:
        video = videos()[1]  # 没说放什么：默认动画片
        name = video["title"]
    episode = cn_number(str(ctx.arg("Episode") or "").removeprefix("第").removesuffix("集")) or 1
    return _play_other(ctx, "video", name, episode=episode, duration=2400)


def play_news(ctx: Ctx) -> Outcome:
    category = str(ctx.arg("Category") or "要闻")
    return _play_other(ctx, "news", f"{category}新闻播报", "新闻电台", duration=900)


def play_podcast(ctx: Ctx) -> Outcome:
    return _play_other(ctx, "podcast", str(ctx.arg("Resource") or "每日播客"), "播客", duration=1800)


HANDLERS: dict[tuple[str, str], Handler] = {
    ("media_play", "Music.Play"): play,
    ("media_play", "Music.PlayAlbum"): play_album,
    ("media_play", "Music.Open"): open_app,
    ("media_play", "Music.Close"): close,
    ("media_play", "Music.OpenLyrics"): toggle("media.lyrics_open", True, "已打开歌词"),
    ("media_play", "Music.CloseLyrics"): toggle("media.lyrics_open", False, "已关闭歌词"),
    ("media_play", "Music.PlayMV"): play_mv,
    ("media_play", "Music.PlayCurrentMV"): toggle("media.mv_open", True, "开始播放 MV"),
    ("media_play", "Music.CloseMV"): toggle("media.mv_open", False, "已关闭 MV"),
    ("media_play", "Music.Dislike"): dislike,
    ("media_play", "Music.PlayFavorite"): play_favorite,
    ("media_play", "News.Play"): play_news,
    ("media_play", "Video.Play"): play_video,
    ("media_play", "Podcast.Play"): play_podcast,
    ("media_control", "GlobalCtrl.Pause"): pause,
    ("media_control", "GlobalCtrl.Resume"): resume,
    ("media_control", "GlobalCtrl.Next"): step(1),
    ("media_control", "GlobalCtrl.Previous"): step(-1),
    ("media_control", "GlobalCtrl.FastForward"): seek(1),
    ("media_control", "GlobalCtrl.FastBackward"): seek(-1),
    ("media_control", "GlobalCtrl.SetPlayMode"): set_mode,
    ("media_control", "GlobalCtrl.AddFavorite"): add_favorite,
    ("media_control", "GlobalCtrl.OpenFavoriteList"): favorite_list,
    ("media_control", "GlobalCtrl.CurrentPlay"): current_play,
}
