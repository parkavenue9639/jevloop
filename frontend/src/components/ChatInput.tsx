import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { useDecisionChoice } from "../decisionChoice";
import type { RunParams } from "../types";
import { useT } from "../i18n";
import { Icon } from "./Icon";

const field =
  "w-full resize-y rounded-xl bg-transparent px-1.5 py-1 text-sm text-ink outline-none placeholder:text-ink2";
/** fixed 3-line label block: long locale strings stay fully visible (no
 *  truncation — tooltips don't exist on touch), and because every label box
 *  is the same height the inputs beneath them stay row-aligned. */
const label = "block min-h-12 text-[11px] font-semibold uppercase leading-4 tracking-wide text-ink2";

/** Numeric-field parse: empty or non-finite input degrades to null. Null is
 *  legal ("off") only for the gate fields; every other field falls back to
 *  its default in send(), so the request body always carries finite numbers. */
const num = (raw: string): number | null => {
  if (raw.trim() === "") return null;
  const v = Number(raw);
  return Number.isFinite(v) ? v : null;
};

/** Bottom composer: textarea (Enter sends, Shift+Enter is a newline), a
 *  prominent compare-run checkbox and a settings popover carrying the run
 *  parameters that used to live in the sidebar form. Disabled while a run is
 *  live; field values persist across turns. */
export function ChatInput({ disabled, starting, error, onSend }: {
  disabled: boolean;
  starting: boolean;
  error: string | null;
  onSend: (params: RunParams) => void;
}) {
  const t = useT();
  const [goal, setGoal] = useState("");
  const { provider, setProvider: chooseProvider } = useDecisionChoice();
  const [compare, setCompare] = useState(false);
  const [live, setLive] = useState(false);
  const [sandboxNetwork, setSandboxNetwork] = useState(true);
  const [stepPause, setStepPause] = useState(false);
  const [threshold, setThreshold] = useState<number | null>(0.5);
  const [ambiguityGate, setAmbiguityGate] = useState<number | null>(0.4);
  const [progressFloor, setProgressFloor] = useState<number | null>(null);
  const [maxSteps, setMaxSteps] = useState<number | null>(0);
  const [maxWrites, setMaxWrites] = useState<number | null>(0);
  const [minConfidence, setMinConfidence] = useState<number | null>(0.6);
  const [recipients, setRecipients] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const settingsBtnRef = useRef<HTMLButtonElement>(null);

  // close the popover on outside click; Escape closes it and returns focus
  // to the trigger so keyboard users are not stranded
  useEffect(() => {
    if (!settingsOpen) return;
    const close = (refocus: boolean) => {
      setSettingsOpen(false);
      if (refocus) settingsBtnRef.current?.focus();
    };
    const onDoc = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) close(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [settingsOpen]);

  const canSend = !disabled && !starting && !!goal.trim();

  const send = () => {
    if (!canSend) return;
    onSend({
      goal: goal.trim(),
      profile: compare ? "paired_shadow" : live ? "single_live" : "single_shadow",
      step_pause: stepPause,
      escalate_threshold: threshold ?? 0.5,
      ambiguity_gate: ambiguityGate,
      answer_progress_floor: progressFloor,
      max_steps: maxSteps ?? 0,
      max_writes: maxWrites ?? 0,
      sandbox_network: sandboxNetwork,
      min_confidence: minConfidence ?? 0.6,
      allow_recipients: recipients.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      decision_provider: provider,
    });
    setGoal(""); // the pending-goal echo takes over in the transcript
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send();
  };

  return (
    <form onSubmit={onSubmit} className="chat-composer shrink-0 border-t border-line bg-surface">
      <div className="composer-inner mx-auto px-4 py-3">
        {error && <p className="mb-2 text-xs text-critical">{error}</p>}
        {provider === "laya" && (
          <p className="mb-2 text-xs text-ink2" data-testid="decision-laya-note">{t("decisionLayaNote")}</p>
        )}

        <div className="mb-2 flex flex-wrap items-center gap-2">
          <label
            className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1 text-sm font-semibold transition-colors ${
              compare ? "border-accent bg-accent/10 text-accent" : "border-line text-ink2 hover:text-ink"
            } ${disabled ? "pointer-events-none opacity-50" : ""}`}
            title={t("compareNote")}
          >
            <input
              type="checkbox"
              className="accent-(--color-accent)"
              checked={compare}
              onChange={(e) => {
                setCompare(e.target.checked);
                if (e.target.checked) setLive(false);
              }}
              disabled={disabled}
            />
            {t("compareToggle")}
          </label>

          <div
            className="flex overflow-hidden rounded-xl border border-line"
            role="radiogroup"
            aria-label={t("decisionProvider")}
            data-testid="decision-provider"
          >
            {(["jev", "laya"] as const).map((id) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={provider === id}
                data-testid={`decision-${id}`}
                disabled={disabled}
                title={id === "laya" ? t("decisionLayaHelp") : undefined}
                onClick={() => chooseProvider(id)}
                className={`px-3 py-1 text-sm font-semibold transition-colors ${
                  provider === id ? "bg-accent/15 text-accent" : "text-ink2 hover:text-ink"
                } disabled:opacity-50`}
              >
                {id === "jev" ? t("decisionJev") : t("decisionLaya")}
              </button>
            ))}
          </div>

          <div className="relative ml-auto" ref={popRef}>
            <button
              type="button"
              ref={settingsBtnRef}
              onClick={() => setSettingsOpen((o) => !o)}
              disabled={disabled}
              className="console-button"
              aria-expanded={settingsOpen}
            >
              <Icon name="settings" /> {t("settings")}
            </button>
            {settingsOpen && (
              <div className="absolute bottom-full right-0 z-30 mb-2 w-80 max-w-[calc(100cqw-2rem)] max-h-[calc(100dvh-12rem)] overflow-y-auto rounded-xl border border-line bg-surface p-3 shadow-lg">
                <div className="flex flex-col gap-1.5">
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)}
                      disabled={disabled || compare} />
                    <span className={live ? "font-semibold text-critical" : ""}>{t("live")}</span>
                  </label>
                  {live && <p className="text-xs text-critical">{t("liveWarn")}</p>}
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input type="checkbox" checked={stepPause} onChange={(e) => setStepPause(e.target.checked)} disabled={disabled} />
                    {t("stepPause")}
                  </label>
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={sandboxNetwork}
                      onChange={(e) => setSandboxNetwork(e.target.checked)}
                      disabled={disabled}
                    />
                    {t("sandboxNetwork")}
                  </label>
                  <p className="text-xs text-ink2">{t("sandboxNetworkNote")}</p>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <div>
                    <label className={label} htmlFor="chat-threshold" title={t("threshold")}>{t("threshold")}</label>
                    <input id="chat-threshold" type="number" step={0.05} min={0} max={1} className={`${field} num mt-1`}
                      value={threshold ?? ""} placeholder="0.5"
                      onChange={(e) => setThreshold(num(e.target.value))} disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-ambiguity" title={t("ambiguityGate")}>{t("ambiguityGate")}</label>
                    <input id="chat-ambiguity" type="number" step={0.05} min={0} max={1}
                      className={`${field} num mt-1`} value={ambiguityGate ?? ""}
                      placeholder={t("off")}
                      onChange={(e) => setAmbiguityGate(num(e.target.value))}
                      disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-progress" title={t("progressFloor")}>{t("progressFloor")}</label>
                    <input id="chat-progress" type="number" step={0.5} min={0} max={3}
                      className={`${field} num mt-1`} value={progressFloor ?? ""}
                      placeholder={t("off")}
                      onChange={(e) => setProgressFloor(num(e.target.value))}
                      disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-steps" title={t("maxSteps")}>{t("maxSteps")}</label>
                    <input id="chat-steps" type="number" min={0} className={`${field} num mt-1`}
                      value={maxSteps ?? ""} placeholder="0"
                      onChange={(e) => setMaxSteps(num(e.target.value))} disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-writes" title={t("maxWrites")}>{t("maxWrites")}</label>
                    <input id="chat-writes" type="number" min={0} className={`${field} num mt-1`}
                      value={maxWrites ?? ""} placeholder="0"
                      onChange={(e) => setMaxWrites(num(e.target.value))} disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-conf" title={t("minConf")}>{t("minConf")}</label>
                    <input id="chat-conf" type="number" step={0.05} min={0} max={1} className={`${field} num mt-1`}
                      value={minConfidence ?? ""} placeholder="0.6"
                      onChange={(e) => setMinConfidence(num(e.target.value))} disabled={disabled} />
                  </div>
                </div>
                <div className="mt-2">
                  <label className={label} htmlFor="chat-recipients" title={t("recipients")}>{t("recipients")}</label>
                  <input id="chat-recipients" className={`${field} mt-1`} value={recipients}
                    onChange={(e) => setRecipients(e.target.value)} disabled={disabled}
                    placeholder={t("recipientsPh")} />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="soft-lift flex items-end gap-2 rounded-2xl border border-line bg-surface2 px-3 py-2.5">
          <textarea
            data-testid="chat-input"
            aria-label={t("chatPlaceholder")}
            rows={2}
            className={`${field} max-h-48 resize-y`}
            placeholder={t("chatPlaceholder")}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={disabled || starting}
          />
          <button
            type="submit"
            disabled={!canSend}
            className="cta-signature shrink-0 bg-ink px-5 py-2.5 text-sm font-semibold text-surface transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {starting ? t("starting") : t("send")}
          </button>
        </div>
      </div>
    </form>
  );
}
