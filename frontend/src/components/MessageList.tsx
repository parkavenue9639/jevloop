import { useEffect, useRef } from "react";
import { useT } from "../i18n";
import { laneErrorMessage, lanePhase } from "../lane";
import type { StreamData } from "../stream";
import type { ChatApi } from "../chat";
import { ActivityGroup } from "./ActivityGroup";
import { PairedTurn, UserBubble } from "./PairedTurn";

/** One conversation turn. A paired run renders as a single experiment card
 *  with both lanes under the user's goal; a single-lane run stays an
 *  ordinary chat card with just the Jev lane panel. */
function Turn({ runId, chat }: { runId: string; chat: ChatApi }) {
  const data: StreamData = chat.streamOf(runId);
  if (data.lanes.baseline != null) return <PairedTurn runId={runId} chat={chat} />;
  const goal = data.params ? String(data.params.goal ?? "") : "";
  const state = data.lanes.jev;
  const phase = lanePhase("jev", state, data.errors, data.done);
  const isActive = runId === chat.activeRunId && chat.canControl;
  return (
    <div className="flex flex-col gap-3">
      {goal && <UserBubble goal={goal} />}
      <ActivityGroup
        lane="jev"
        state={state}
        phase={phase}
        error={laneErrorMessage("jev", data)}
        historical={!isActive}
        onContinue={isActive ? chat.continueRun : undefined}
        onAbort={isActive ? chat.abortRun : undefined}
      />
    </div>
  );
}

/** The scrolling chat transcript. Appends bottom-up and follows the tail
 *  automatically, except while the user has scrolled up to read — then it
 *  stays put until they return near the bottom. Widens when the session
 *  contains paired turns so the two lanes have room to sit side by side. */
export function MessageList({ chat }: { chat: ChatApi }) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    if (chat.playback.status !== "idle" && chat.playback.position === 0) {
      pinned.current = true;
      if (ref.current) ref.current.scrollTop = 0;
    }
  }, [chat.playback.status, chat.playback.position]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  // Observe both event-driven content growth and split-pane resizing. Do not
  // steal the scroll position when the user has moved up to read history.
  useEffect(() => {
    const el = ref.current;
    const body = content.current;
    if (!el || !body) return;
    const follow = () => { if (pinned.current) el.scrollTop = el.scrollHeight; };
    const observer = new ResizeObserver(follow);
    observer.observe(el);
    observer.observe(body);
    follow();
    return () => observer.disconnect();
  }, []);

  const paired = chat.turns.some((id) => chat.streamOf(id).lanes.baseline != null);

  return (
    <div ref={ref} onScroll={onScroll} data-testid="conversation-scroll" className="min-h-0 flex-1 overflow-y-auto">
      <div ref={content} className={`mx-auto flex w-full flex-col gap-5 px-4 py-6 ${paired ? "max-w-5xl" : "max-w-3xl"}`}>
        {chat.turns.map((runId) => (
          <Turn key={runId} runId={runId} chat={chat} />
        ))}

        {chat.pendingGoal && (
          <div className="flex flex-col gap-3">
            <UserBubble goal={chat.pendingGoal} />
            <ActivityGroup lane="jev" state={undefined} phase="running" />
          </div>
        )}

        {!chat.turns.length && !chat.pendingGoal && (
          <div className="rounded-xl border border-dashed border-line p-8 text-center text-sm leading-relaxed text-ink2">
            {t("welcome")}
          </div>
        )}
      </div>
    </div>
  );
}
