import { useEffect } from "react";
import { useT } from "../i18n";
import type { RunSummary } from "../types";
import { RunHistory } from "./RunHistory";
import { Icon } from "./Icon";

/** Collapsible left rail listing stored sessions; picking one replays the
 *  whole conversation (all its turns). Overlays the chat on small screens,
 *  docks into the layout from md up. */
export function HistoryDrawer({ open, onClose, runs, currentSessionId, onPickSession, disabled }: {
  open: boolean;
  onClose: () => void;
  runs: RunSummary[];
  currentSessionId: string | null;
  onPickSession: (sessionId: string) => void;
  disabled: boolean;
}) {
  const t = useT();
  // Escape dismisses the mobile overlay and returns focus to the header's
  // history toggle; the docked (md+) rail is part of the layout, not a dialog
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!window.matchMedia("(max-width: 767px)").matches) return;
      onClose();
      document.querySelector<HTMLButtonElement>('[data-testid="history-toggle"]')?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-10 bg-black/20 md:hidden" onClick={onClose} />
      <aside
        data-testid="history-drawer"
        className="history-rail fixed inset-y-0 left-0 z-20 flex w-72 flex-col border-r border-line bg-surface p-3 md:static md:z-auto md:w-56 md:shrink-0"
      >
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{t("historySection")}</h2>
          <button
            onClick={onClose}
            className="icon-button"
            title={t("historyBtn")}
            aria-label={t("historyBtn")}
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <RunHistory runs={runs} currentSessionId={currentSessionId} onPickSession={onPickSession} disabled={disabled} />
        </div>
      </aside>
    </>
  );
}
