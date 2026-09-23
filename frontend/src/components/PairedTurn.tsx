import { useState } from "react";
import { useT } from "../i18n";
import { escalationStats, LANE_INFO, laneErrorMessage, lanePhase } from "../lane";
import type { Lane, LaneState } from "../types";
import type { ChatApi } from "../chat";
import { ActivityGroup } from "./ActivityGroup";
import { CompareTable } from "./CompareTable";

/** The user's goal for one turn, right-aligned like any chat bubble. */
export function UserBubble({ goal }: { goal: string }) {
  return (
    <div className="flex justify-end">
      <div
        data-testid="user-bubble"
        className="max-w-[85%] break-words whitespace-pre-wrap rounded-2xl bg-accent px-4 py-2.5 text-sm leading-relaxed text-white"
      >
        {goal}
      </div>
    </div>
  );
}

/** Compact adjudication stats for whichever lanes escalated at all. Shared by
 *  the per-turn comparison and the session aggregate; renders nothing when
 *  neither lane escalated. */
export function EscalationStatsBlock({ states, titleKey }: {
  states: Record<Lane, LaneState | undefined>;
  titleKey: string;
}) {
  const t = useT();
  const lanes = (["jev", "baseline"] as const).filter(
    (lane) => escalationStats(states[lane]?.metrics).count > 0,
  );
  if (!lanes.length) return null;
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <span className="text-xs font-semibold">{t(titleKey)}</span>
      <ul className="mt-1.5 flex flex-col gap-1 text-xs text-ink">
        {lanes.map((lane) => {
          const esc = escalationStats(states[lane]?.metrics);
          return (
            <li key={lane} className="num">
              <span className="font-medium">{t(LANE_INFO[lane].titleKey)}</span>
              {" — "}
              {t("escStatsLine", { n: esc.count, u: esc.upheld, o: esc.overridden })}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** One paired turn = one bounded experiment card under the user's goal: a
 *  collapsible per-turn comparison strip (metrics + adjudication) on top,
 *  then the two lane panels — Jev and the plain LLM baseline — side by side
 *  on desktop and stacked on mobile. Streams live while the turn is active;
 *  afterwards the comparison collapses and the card stays inspectable. */
export function PairedTurn({ runId, chat }: { runId: string; chat: ChatApi }) {
  const t = useT();
  const data = chat.streamOf(runId);
  const goal = data.params ? String(data.params.goal ?? "") : "";
  const jev = data.lanes.jev;
  const baseline = data.lanes.baseline;
  const phases = {
    jev: lanePhase("jev", jev, data.errors, data.done),
    baseline: lanePhase("baseline", baseline, data.errors, data.done),
  };
  const isActive = runId === chat.activeRunId;
  const [compareOpen, setCompareOpen] = useState(isActive);

  return (
    <div className="flex flex-col gap-3">
      {goal && <UserBubble goal={goal} />}
      <section
        data-testid="paired-turn"
        className="soft-lift flex min-w-0 flex-col gap-3 rounded-2xl border border-line bg-surface2/40 p-2.5 sm:p-3"
      >
        {jev && baseline && (
          <details
            open={compareOpen}
            onToggle={(event) => setCompareOpen(event.currentTarget.open)}
            data-testid="turn-compare"
            className="group rounded-xl border border-accent/40 bg-accent/5"
          >
            <summary className="cursor-pointer select-none px-3.5 py-2 text-sm font-bold">
              {t("turnCompareTitle")}
              <span className="ml-2 inline-block text-ink2 transition-transform group-open:rotate-90">▸</span>
            </summary>
            <div className="flex flex-col gap-3 px-2.5 pb-3 sm:px-3.5">
              <div className="overflow-x-auto">
                <CompareTable jev={jev} baseline={baseline} phases={phases} />
              </div>
              <EscalationStatsBlock states={{ jev, baseline }} titleKey="escStatsTitle" />
            </div>
          </details>
        )}

        <div className="grid grid-cols-1 items-start gap-3 md:grid-cols-2">
          <ActivityGroup
            lane="jev"
            state={jev}
            phase={phases.jev}
            error={laneErrorMessage("jev", data)}
            onContinue={isActive ? chat.continueRun : undefined}
            onAbort={isActive ? chat.abortRun : undefined}
            compact
          />
          <ActivityGroup
            lane="baseline"
            state={baseline}
            phase={phases.baseline}
            error={laneErrorMessage("baseline", data)}
            onContinue={isActive ? chat.continueRun : undefined}
            onAbort={isActive ? chat.abortRun : undefined}
            compact
          />
        </div>
      </section>
    </div>
  );
}
