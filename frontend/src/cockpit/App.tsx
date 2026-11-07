import { useEffect, useState } from "react";
import type { Seat } from "../lib/protocol";
import { useAppsData } from "../lib/useAppsData";
import { useCockpitSocket } from "../lib/useCockpitSocket";
import { ChatPanel } from "./ChatPanel";
import { DebugPanel } from "./DebugPanel";
import { DemoPlayer } from "./DemoPlayer";
import { moodOf } from "./DoubaoAvatar";
import { OccupantPanel } from "./OccupantPanel";
import { ScenarioBar } from "./ScenarioBar";
import { ScreenDock } from "./ScreenDock";
import { TaskPanel } from "./TaskPanel";
import { TracePanel } from "./TracePanel";
import { VehiclePanel } from "./VehiclePanel";

export function App() {
  const { status, state, lastDiff, messages, traces, lastInvoke, brain, warning, confirmPending, busy, send, say, invoke, touch, event, advanceClock, clearMessages,
    voiceAvailable, voiceState, voiceText, voiceLevel, startVoice, stopVoice, setVoiceSeat } = useCockpitSocket();
  const { data } = useAppsData();
  const [seat, setSeat] = useState<Seat>("主驾");
  const setState = (updates: Record<string, unknown>) => send({ type: "set_state", updates });
  const voiceOn = voiceState !== "off";
  // 语音开着时，换说话人（音区）同步给语音桥
  useEffect(() => {
    if (voiceOn) setVoiceSeat(seat);
  }, [seat, voiceOn, setVoiceSeat]);

  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <ScenarioBar state={state} status={status} send={send} />
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(280px,1fr)_minmax(420px,1.6fr)_minmax(260px,1fr)]">
        <ChatPanel
          messages={messages}
          status={status}
          brain={brain}
          warning={warning}
          onSay={say}
          confirmPending={confirmPending}
          onEvent={event}
          seat={seat}
          onSeat={setSeat}
          voiceAvailable={voiceAvailable}
          voiceState={voiceState}
          voiceText={voiceText}
          voiceLevel={voiceLevel}
          onVoiceToggle={() => (voiceOn ? stopVoice() : void startVoice(seat))}
        />
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <VehiclePanel state={state} lastDiff={lastDiff} speaker={seat} onSpeaker={setSeat} />
          <OccupantPanel state={state} speaker={seat} onSpeaker={setSeat} setState={setState} />
          <ScreenDock state={state} data={data} diff={lastDiff} touch={touch} confirmPending={confirmPending} onEvent={event} mood={moodOf(messages, busy, confirmPending, voiceState)} />
        </div>
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <DemoPlayer busy={busy} send={send} say={say} onSeat={setSeat} clearMessages={clearMessages} />
          <DebugPanel lastInvoke={lastInvoke} onInvoke={invoke} />
          <TracePanel traces={traces} />
          <TaskPanel
            state={state}
            onAdvance={advanceClock}
            onCancel={(t) => touch("task_master", { task: `取消${t.action}的任务` })}
          />
        </div>
      </main>
    </div>
  );
}
