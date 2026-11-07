import { getPath, SEATS, type Seat, type VehicleState } from "../lib/protocol";

interface CarTopViewProps {
  state: VehicleState | null;
  changed: Set<string>;
  speaker: Seat;
  onSpeaker: (seat: Seat) => void;
}

// 俯视图坐标（viewBox 0 0 240 420，车头朝上）
const SEAT_BOX: Record<Seat, { x: number; y: number }> = {
  主驾: { x: 58, y: 150 },
  副驾: { x: 132, y: 150 },
  左后排: { x: 58, y: 250 },
  右后排: { x: 132, y: 250 },
};
const SEAT_W = 50;
const SEAT_H = 62;
// 车窗：沿车身侧边的线段，左侧 x=30，右侧 x=210
const WINDOW_LINE: Record<Seat, { x: number; y1: number; y2: number }> = {
  主驾: { x: 30, y1: 140, y2: 215 },
  副驾: { x: 210, y1: 140, y2: 215 },
  左后排: { x: 30, y1: 235, y2: 310 },
  右后排: { x: 210, y1: 235, y2: 310 },
};
const HEAT_FILL = ["var(--bg)", "#f6d7b8", "#efa76f", "#e0702e"];

const num = (state: VehicleState | null, path: string) => Number(getPath(state, path) ?? 0);

function hot(changed: Set<string>, prefix: string) {
  for (const p of changed) if (p === prefix || p.startsWith(prefix + ".")) return true;
  return false;
}

export function CarTopView({ state, changed, speaker, onSpeaker }: CarTopViewProps) {
  const ambientOn = getPath(state, "lights.ambient.on") === true;
  const sunroof = num(state, "sunroof");
  const backdoor = num(state, "backdoor");
  const locked = getPath(state, "doors_locked") === true;
  const folded = getPath(state, "mirrors_folded") === true;
  const ring = (path: string) => (hot(changed, path) ? "var(--veh)" : "var(--line)");

  return (
    <svg
      viewBox="0 0 240 420"
      className="mx-auto h-full max-h-[420px] w-full"
      role="img"
      aria-label="车辆俯视图：四个座椅的加热、通风与温度，车窗开度、侧窗遮阳帘（琥珀虚线，越长拉得越多）、乘员和说话人（蓝色虚框，点座位切换音区），天窗、尾门、门锁和后视镜状态"
    >
      {/* 车身 */}
      <rect
        x={30}
        y={40}
        width={180}
        height={340}
        rx={60}
        fill="var(--panel)"
        stroke={ambientOn ? "var(--cloud)" : "var(--fg)"}
        strokeWidth={ambientOn ? 4 : 2}
      />
      <text x={120} y={30} textAnchor="middle" fontSize={11} fill="var(--muted)">
        车头
      </text>
      {/* 前挡和后挡 */}
      <path d="M58 110 Q120 85 182 110" fill="none" stroke="var(--muted)" strokeWidth={2} />
      <path d="M58 330 Q120 350 182 330" fill="none" stroke="var(--muted)" strokeWidth={2} />

      {/* 后视镜：折叠时贴近车身 */}
      {[
        { x: folded ? 22 : 8, side: "left" },
        { x: folded ? 210 : 214, side: "right" },
      ].map((m) => (
        <rect key={m.side} x={m.x} y={120} width={folded ? 8 : 18} height={12} rx={3} fill="var(--muted)" stroke={ring("mirrors_folded")} />
      ))}

      {/* 天窗：开度越大，透明区域越大 */}
      <rect x={80} y={205} width={80} height={36} rx={6} fill="var(--bg)" stroke={ring("sunroof")} strokeWidth={1.5} />
      <rect x={80} y={205} width={(80 * sunroof) / 100} height={36} rx={6} fill="var(--cloud-bg)" />
      <text x={120} y={227} textAnchor="middle" fontSize={10} fill="var(--muted)">
        天窗 {sunroof}%
      </text>

      {/* 座椅 */}
      {SEATS.map((seat) => {
        const box = SEAT_BOX[seat];
        const heat = Math.min(3, num(state, `seats.${seat}.heat`));
        const vent = num(state, `seats.${seat}.vent`);
        const massage = num(state, `seats.${seat}.massage`);
        const temp = getPath(state, `ac.temp.${seat}`);
        const changedSeat = hot(changed, `seats.${seat}`) || hot(changed, `ac.temp.${seat}`);
        const present = getPath(state, `occupants.${seat}.present`) !== false;
        const who = String(getPath(state, `occupants.${seat}.who`) ?? "");
        const speaking = seat === speaker;
        return (
          <g
            key={seat}
            opacity={present ? 1 : 0.45}
            className="cursor-pointer"
            onClick={() => present && onSpeaker(seat)}
            role="button"
            aria-label={`${seat}${present ? who || "有人" : "空座"}${speaking ? "，正在说话" : ""}`}
          >
            {speaking && (
              <rect x={box.x - 4} y={box.y - 4} width={SEAT_W + 8} height={SEAT_H + 8} rx={13} fill="none" stroke="var(--cloud)" strokeWidth={2} strokeDasharray="4 3" />
            )}
            <rect
              x={box.x}
              y={box.y}
              width={SEAT_W}
              height={SEAT_H}
              rx={10}
              fill={HEAT_FILL[heat]}
              stroke={changedSeat ? "var(--veh)" : "var(--muted)"}
              strokeWidth={changedSeat ? 2.5 : 1}
            />
            <text x={box.x + SEAT_W / 2} y={box.y + 18} textAnchor="middle" fontSize={10} fill="var(--fg)">
              {present ? who || seat : "空座"}
            </text>
            <text x={box.x + SEAT_W / 2} y={box.y + 36} textAnchor="middle" fontSize={13} fontWeight={600} fill="var(--fg)">
              {temp === undefined ? "—" : `${String(temp)}℃`}
            </text>
            <text x={box.x + SEAT_W / 2} y={box.y + 53} textAnchor="middle" fontSize={9} fill="var(--muted)">
              {[heat ? `热${heat}` : "", vent ? `风${vent}` : "", massage ? `摩${massage}` : ""].filter(Boolean).join(" ") || "—"}
            </text>
          </g>
        );
      })}

      {/* 车窗：绿色线段长度表示开度 */}
      {SEATS.map((seat) => {
        const w = WINDOW_LINE[seat];
        const pct = num(state, `windows.${seat}`);
        const open = ((w.y2 - w.y1) * pct) / 100;
        const shade = num(state, `sunshades.${seat}`);
        return (
          <g key={`w-${seat}`}>
            <line x1={w.x} y1={w.y1} x2={w.x} y2={w.y2} stroke="var(--line)" strokeWidth={5} strokeLinecap="round" />
            {shade < 100 && (
              <line
                x1={w.x < 120 ? w.x + 7 : w.x - 7}
                y1={w.y1}
                x2={w.x < 120 ? w.x + 7 : w.x - 7}
                y2={w.y1 + ((w.y2 - w.y1) * (100 - shade)) / 100}
                stroke="var(--safe)"
                strokeWidth={3}
                strokeDasharray="3 2"
              />
            )}
            {pct > 0 && (
              <line x1={w.x} y1={w.y1} x2={w.x} y2={w.y1 + open} stroke="var(--veh)" strokeWidth={5} strokeLinecap="round" />
            )}
            {hot(changed, `windows.${seat}`) && (
              <text x={w.x < 120 ? w.x - 4 : w.x + 4} y={w.y1 - 4} textAnchor={w.x < 120 ? "end" : "start"} fontSize={9} fill="var(--veh)">
                {pct}%
              </text>
            )}
          </g>
        );
      })}

      {/* 尾门 */}
      <rect x={75} y={380 + (backdoor ? 4 : 0)} width={90} height={8} rx={3} fill={backdoor ? "var(--safe)" : "var(--line)"} stroke={ring("backdoor")} />
      <text x={120} y={408} textAnchor="middle" fontSize={10} fill="var(--muted)">
        尾门 {backdoor}% · {locked ? "已上锁" : "未上锁"}
      </text>
    </svg>
  );
}
