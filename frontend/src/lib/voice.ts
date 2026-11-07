/** M7 语音：麦克风采集（16k 单声道 int16，每 20ms 一包）和下行音频播放（pcm_s16le 24k）。 */

const IN_RATE = 16000;
const OUT_RATE = 24000;
const FRAME_SAMPLES = 320; // 20ms @ 16k

// AudioWorklet：把 float32 转成 int16，攒够 20ms 发回主线程。
const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(${FRAME_SAMPLES}); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const v = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice().buffer, []); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

export interface Mic {
  stop: () => void;
}

/** 打开麦克风（回声消除、降噪、自动增益），每 20ms 回调一包 PCM。 */
export async function startMic(onFrame: (pcm: ArrayBuffer) => void): Promise<Mic> {
  // 必须在点击的同一时刻（第一个 await 之前）建 AudioContext：等麦克风授权框可能要好几秒，
  // 之后再建会因为“用户手势”过期而一直是 suspended，一帧音频都不出。
  const ctx = new AudioContext({ sampleRate: IN_RATE });
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    void ctx.close();
    throw err;
  }
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  try {
    await ctx.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => onFrame(e.data);
  // 接到一个静音输出：有的浏览器不处理没连到 destination 的节点
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(node);
  node.connect(mute).connect(ctx.destination);
  if (ctx.state !== "running") await ctx.resume();
  return {
    stop: () => {
      node.port.onmessage = null;
      source.disconnect();
      node.disconnect();
      mute.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
    },
  };
}

/** 按顺序排队播放 PCM 片段；打断时 flush() 立刻停。 */
export class PcmPlayer {
  private ctx: AudioContext | null = null;
  private nextAt = 0;
  private sources = new Set<AudioBufferSourceNode>();
  onIdle: (() => void) | null = null;

  private context(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate: OUT_RATE });
    if (this.ctx.state === "suspended") void this.ctx.resume();
    return this.ctx;
  }

  /** 在用户点击里调用一次，解锁浏览器的自动播放限制。 */
  unlock(): void {
    this.context();
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  play(data: ArrayBuffer): void {
    const samples = new Int16Array(data, 0, Math.floor(data.byteLength / 2));
    if (!samples.length) return;
    const ctx = this.context();
    const buffer = ctx.createBuffer(1, samples.length, OUT_RATE);
    const ch = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 0x8000;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    this.nextAt = Math.max(this.nextAt, ctx.currentTime + 0.03);
    src.start(this.nextAt);
    this.nextAt += buffer.duration;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (!this.sources.size) this.onIdle?.();
    };
  }

  flush(): void {
    for (const src of this.sources) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* 还没开始播的片段 stop 会抛错，忽略 */
      }
    }
    this.sources.clear();
    this.nextAt = 0;
  }

  close(): void {
    this.flush();
    void this.ctx?.close();
    this.ctx = null;
  }
}
