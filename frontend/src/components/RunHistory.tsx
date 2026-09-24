import { useT } from "../i18n";
import type { RunSummary } from "../types";

interface SessionGroup {
  sessionId: string;
  runs: RunSummary[];           // oldest first
  title: string;                // oldest turn's goal (the conversation topic)
  createdAt: string | null;     // newest turn's time (drives recency sort)
  compare: boolean;
  live: boolean;
  error: boolean;
  unfinished: boolean;
}

interface Props {
  runs: RunSummary[];           // newest first from the server
  currentSessionId: string | null;
  onPickSession: (sessionId: string) => void;
  /** block replays while a live run is streaming */
  disabled?: boolean;
}

function flag(label: string, tone: "accent" | "critical" | "neutral") {
  const tones = {
    accent: "border-accent/40 text-accent",
    critical: "border-critical/50 text-critical",
    neutral: "border-line text-ink2",
  } as const;
  return (
    <span className={`rounded border px-1 py-px text-[10px] font-semibold ${tones[tone]}`}>{label}</span>
  );
}

/** History grouped by session: one entry per conversation, showing its turn
 *  count; picking one replays the WHOLE session as a single conversation. */
export function RunHistory({ runs, currentSessionId, onPickSession, disabled = false }: Props) {
  const t = useT();
  const groups = new Map<string, SessionGroup>();
  for (const run of runs) {  // newest-first input; keep order for sorting later
    const key = run.session_id ?? run.run_id;
    const group = groups.get(key) ?? {
      sessionId: key, runs: [], title: "", createdAt: run.created_at,
      compare: false, live: false, error: false, unfinished: false,
    };
    group.runs.push(run);
    // input is newest-first, so the last write is the oldest turn's goal;
    // createdAt keeps the seed value (newest) for the recency sort below
    group.title = run.goal;
    group.compare = group.compare || run.compare;
    group.live = group.live || run.live;
    group.error = group.error || run.error;
    group.unfinished = group.unfinished || !run.finished;
    groups.set(key, group);
  }
  const sessions = [...groups.values()]
    .sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
  if (!sessions.length) {
    return <p className="text-xs text-ink2">{t("historyEmpty")}</p>;
  }
  return (
    <ul className="flex flex-col gap-1">
      {sessions.map((session) => (
        <li key={session.sessionId}>
          <button
            onClick={() => onPickSession(session.sessionId)}
            disabled={disabled}
            className={`w-full rounded-xl border px-2 py-1.5 text-left text-xs transition-colors disabled:opacity-40 ${
              session.sessionId === currentSessionId
                ? "border-accent bg-accent/10"
                : "border-line hover:border-accent/50"
            }`}
          >
            <div className="flex items-center gap-1">
              <span className="num text-[10px] text-ink2">
                {(session.createdAt ?? "").replace("T", " ").slice(5, 16)}
              </span>
              {session.runs.length > 1 && (
                <span className="num rounded border border-accent/40 px-1 py-px text-[10px] font-semibold text-accent">
                  {session.runs.length}{t("turnsUnit")}
                </span>
              )}
              {session.compare && flag("vs", "accent")}
              {session.live && flag("live", "critical")}
              {session.unfinished && flag("…", "neutral")}
              {session.error && flag("!", "critical")}
            </div>
            <div className="mt-0.5 truncate text-ink" title={session.title}>{session.title}</div>
          </button>
        </li>
      ))}
    </ul>
  );
}
