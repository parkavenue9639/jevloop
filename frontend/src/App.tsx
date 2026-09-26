import { useEffect, useState } from "react";
import { LangToggle, useLang, useT } from "./i18n";
import { ThemeToggle } from "./components/ThemeToggle";
import { useChat } from "./chat";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { ExecutionWorkspace } from "./components/ExecutionWorkspace";
import { ChatInput } from "./components/ChatInput";
import { Icon } from "./components/Icon";
import { PlaybackControls } from "./components/PlaybackControls";

/** One stream drives both the graph and conversation; one persistent composer. */
function App() {
  const t = useT();
  const chat = useChat();
  const { lang } = useLang();
  const playbackActive = chat.playback.status !== "idle";
  const locked = chat.running || chat.canControl || chat.replaying || chat.starting || playbackActive;
  const [historyOpen, setHistoryOpen] = useState(() => {
    try {
      const stored =
        typeof localStorage === "undefined" ? null : localStorage.getItem("jevloop.historyOpen");
      if (stored != null) return stored === "1";
    } catch {
      // storage unavailable — fall through to the viewport default
    }
    return typeof window !== "undefined" && window.innerWidth >= 768;
  });
  useEffect(() => {
    try {
      localStorage.setItem("jevloop.historyOpen", historyOpen ? "1" : "0");
    } catch {
      // best effort — the preference simply won't survive a reload
    }
  }, [historyOpen]);

  const jevLane = chat.stream.lanes.jev;
  const baselineLane = chat.stream.lanes.baseline;
  const anyAwaiting = !!(jevLane?.awaiting || baselineLane?.awaiting);

  const statusLabel = playbackActive
    ? (chat.playback.status === "playing" ? (lang === "zh" ? "历史回放中" : "Replaying history")
      : chat.playback.status === "paused" ? (lang === "zh" ? "回放已暂停" : "Replay paused")
        : (lang === "zh" ? "回放结束" : "Replay ended"))
    : chat.replaying ? (lang === "zh" ? "加载历史记录" : "Loading history") : chat.starting
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
    <div className="app-shell flex h-dvh flex-col">
      <header className="app-header">
        <button
          data-testid="history-toggle"
          onClick={() => setHistoryOpen((o) => !o)}
          className={`icon-button ${historyOpen ? "is-active" : ""}`}
          title={t("historyBtn")}
          aria-label={t("historyBtn")}
          aria-expanded={historyOpen}
        >
          <Icon name="history" />
        </button>
        <h1 className="app-brand"><span className="brand-mark"><Icon name="loop" /></span>JevLoop<span className="brand-subtitle">/ CONSOLE</span></h1>
        <span className="header-status">
          {statusLabel}
        </span>
        <button
          onClick={chat.newSession}
          disabled={locked}
          className="console-button new-session-button"
          title={t("newSession")}
          aria-label={t("newSession")}
        >
          <Icon name="plus" /><span className="hidden sm:inline">{t("newSession")}</span>
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
          disabled={locked}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          <PlaybackControls chat={chat} />
          <ExecutionWorkspace chat={chat} composer={<ChatInput scope={chat.composerScope} disabled={locked} starting={chat.starting} error={chat.startError} onSend={(params) => void chat.send(params)} />} />
        </main>
      </div>
    </div>
  );
}

export default App;
