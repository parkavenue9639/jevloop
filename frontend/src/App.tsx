import { useState } from "react";
import { LangToggle, useT } from "./i18n";
import { ThemeToggle } from "./components/ThemeToggle";
import { useChat } from "./chat";
import { ChatView } from "./components/ChatView";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { aggregateSessionComparison } from "./comparison";
import { SessionSummaryPanel } from "./components/SessionSummaryPanel";

/** Layout and orchestration only: header + history rail + the compact live
 *  session aggregate strip + the chat column. Paired turns render both lanes
 *  inline in the transcript (see PairedTurn), so there is no side rail.
 *  All conversation state lives in useChat. */
function App() {
  const t = useT();
  const chat = useChat();
  const [historyOpen, setHistoryOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= 768,
  );

  const jevLane = chat.stream.lanes.jev;
  const baselineLane = chat.stream.lanes.baseline;
  const anyAwaiting = !!(jevLane?.awaiting || baselineLane?.awaiting);

  const sessionComparison = aggregateSessionComparison(
    chat.turns.map((runId) => chat.streamOf(runId)),
  );
  const showSummary = sessionComparison != null;

  const statusLabel = chat.starting
    ? t("starting")
    : chat.stream.done
      ? t("statusFinished")
      : chat.stream.errors.length
        ? t("statusError")
        : anyAwaiting
          ? t("statusAwaiting")
          : chat.running
            ? t("statusRunning")
            : chat.activeRunId
              ? t("statusIdle")
              : t("statusReady");

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-line bg-surface px-4 py-2.5">
        <button
          onClick={() => setHistoryOpen((o) => !o)}
          className={`rounded-full border px-2 py-1 text-xs font-semibold transition-colors ${
            historyOpen ? "border-accent text-accent" : "border-line text-ink2 hover:text-ink"
          }`}
          title={t("historyBtn")}
        >
          🕘 {t("historyBtn")}
        </button>
        <h1 className="text-base font-semibold tracking-tight text-ink">
          JevLoop <span className="script-accent text-lg text-accent">by Jev</span>
        </h1>
        <span className="num hidden border border-line px-2 py-0.5 text-xs font-medium text-ink2 sm:inline">
          {statusLabel}
        </span>
        <button
          onClick={chat.newSession}
          disabled={chat.running || chat.replaying}
          className="rounded-full border border-line px-2 py-1 text-xs font-semibold text-ink2 transition-colors hover:text-ink disabled:opacity-40"
          title={t("newSession")}
        >
          ✚ {t("newSession")}
        </button>
        <ThemeToggle />
        <LangToggle />
      </header>

      <div className="relative flex min-h-0 flex-1">
        <HistoryDrawer
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          runs={chat.history}
          currentSessionId={chat.sessionId}
          onPickSession={(sid) => void chat.replaySession(sid)}
          disabled={chat.running || chat.replaying}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          {showSummary && (
            <div className="shrink-0 border-b border-line px-4 py-2">
              <div className="mx-auto max-w-5xl">
                <SessionSummaryPanel comparison={sessionComparison} />
              </div>
            </div>
          )}

          <ChatView chat={chat} />
        </main>
      </div>
    </div>
  );
}

export default App;
