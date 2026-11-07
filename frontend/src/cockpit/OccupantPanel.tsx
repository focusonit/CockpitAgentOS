import { getPath, SEATS, type Seat, type VehicleState } from "../lib/protocol";

interface OccupantPanelProps {
  state: VehicleState | null;
  speaker: Seat;
  onSpeaker: (seat: Seat) => void;
  setState: (updates: Record<string, unknown>) => void;
}

/** 乘员面板：每个座位有没有人、是谁；点“说话”把对话的音区切到这个座位。 */
export function OccupantPanel({ state, speaker, onSpeaker, setState }: OccupantPanelProps) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2 font-medium">乘员与音区</header>
      <div className="grid grid-cols-2 gap-2 p-3">
        {SEATS.map((seat) => {
          const present = getPath(state, `occupants.${seat}.present`) === true;
          const who = String(getPath(state, `occupants.${seat}.who`) ?? "");
          const active = seat === speaker;
          return (
            <div
              key={seat}
              className={"rounded px-2 py-1.5 text-xs " + (active ? "bg-cloud-bg ring-1 ring-cloud" : "bg-bg")}
            >
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">{seat}</span>
                <label className="flex items-center gap-1 text-muted">
                  <input
                    type="checkbox"
                    checked={present}
                    onChange={(e) => setState({ [`occupants.${seat}.present`]: e.target.checked })}
                  />
                  有人
                </label>
              </div>
              <div className="flex gap-1">
                <input
                  aria-label={`${seat}乘员`}
                  defaultValue={who}
                  key={who}
                  disabled={!present}
                  placeholder="谁"
                  maxLength={10}
                  onBlur={(e) => e.target.value !== who && setState({ [`occupants.${seat}.who`]: e.target.value.trim() })}
                  className="min-w-0 flex-1 rounded border border-line bg-panel px-1 disabled:opacity-50"
                />
                <button
                  disabled={!present}
                  onClick={() => onSpeaker(seat)}
                  className={"rounded px-1.5 disabled:opacity-40 " + (active ? "bg-cloud text-white" : "bg-panel")}
                >
                  {active ? "说话中" : "说话"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
