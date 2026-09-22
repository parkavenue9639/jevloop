import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { RunParams } from "../types";
import { useT } from "../i18n";

const field =
  "w-full resize-y rounded-xl bg-transparent px-1.5 py-1 text-sm text-ink outline-none placeholder:text-ink2";
const label = "text-[11px] font-semibold uppercase tracking-wide text-ink2";

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
  const [compare, setCompare] = useState(false);
  const [live, setLive] = useState(false);
  const [sandboxNetwork, setSandboxNetwork] = useState(true);
  const [stepPause, setStepPause] = useState(false);
  const [threshold, setThreshold] = useState(0.5);
  const [ambiguityGate, setAmbiguityGate] = useState<number | null>(0.4);
  const [progressFloor, setProgressFloor] = useState<number | null>(null);
  const [maxSteps, setMaxSteps] = useState(0);
  const [maxWrites, setMaxWrites] = useState(0);
  const [minConfidence, setMinConfidence] = useState(0.6);
  const [recipients, setRecipients] = useState("陆冲");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);

  // close the popover on outside click
  useEffect(() => {
    if (!settingsOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) setSettingsOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [settingsOpen]);

  const canSend = !disabled && !starting && !!goal.trim();

  const send = () => {
    if (!canSend) return;
    onSend({
      goal: goal.trim(),
      profile: compare ? "paired_shadow" : live ? "single_live" : "single_shadow",
      step_pause: stepPause,
      escalate_threshold: threshold,
      ambiguity_gate: ambiguityGate,
      answer_progress_floor: progressFloor,
      max_steps: maxSteps,
      max_writes: maxWrites,
      sandbox_network: sandboxNetwork,
      min_confidence: minConfidence,
      allow_recipients: recipients.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    });
    setGoal(""); // the pending-goal echo takes over in the transcript
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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
    <form onSubmit={onSubmit} className="shrink-0 border-t border-line bg-surface">
      <div className="mx-auto max-w-3xl px-4 py-3">
        {error && <p className="mb-2 text-xs text-critical">{error}</p>}

        <div className="mb-2 flex items-center gap-2">
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

          <div className="relative ml-auto" ref={popRef}>
            <button
              type="button"
              onClick={() => setSettingsOpen((o) => !o)}
              disabled={disabled}
              className="rounded-xl border border-line px-3 py-1 text-sm text-ink2 transition-colors hover:text-ink disabled:opacity-50"
            >
              ⚙ {t("settings")}
            </button>
            {settingsOpen && (
              <div className="absolute bottom-full right-0 z-30 mb-2 w-80 rounded-xl border border-line bg-surface p-3 shadow-lg">
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
                    <label className={label} htmlFor="chat-threshold">{t("threshold")}</label>
                    <input id="chat-threshold" type="number" step={0.05} min={0} max={1} className={`${field} num mt-1`}
                      value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-ambiguity">{t("ambiguityGate")}</label>
                    <input id="chat-ambiguity" type="number" step={0.05} min={0} max={1}
                      className={`${field} num mt-1`} value={ambiguityGate ?? ""}
                      placeholder={t("off")}
                      onChange={(e) => setAmbiguityGate(
                        e.target.value === "" ? null : Number(e.target.value))}
                      disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-progress">{t("progressFloor")}</label>
                    <input id="chat-progress" type="number" step={0.5} min={0} max={3}
                      className={`${field} num mt-1`} value={progressFloor ?? ""}
                      placeholder={t("off")}
                      onChange={(e) => setProgressFloor(
                        e.target.value === "" ? null : Number(e.target.value))}
                      disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-steps">{t("maxSteps")}</label>
                    <input id="chat-steps" type="number" min={0} className={`${field} num mt-1`}
                      value={maxSteps} onChange={(e) => setMaxSteps(Number(e.target.value))} disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-writes">{t("maxWrites")}</label>
                    <input id="chat-writes" type="number" min={0} className={`${field} num mt-1`}
                      value={maxWrites} onChange={(e) => setMaxWrites(Number(e.target.value))} disabled={disabled} />
                  </div>
                  <div>
                    <label className={label} htmlFor="chat-conf">{t("minConf")}</label>
                    <input id="chat-conf" type="number" step={0.05} min={0} max={1} className={`${field} num mt-1`}
                      value={minConfidence} onChange={(e) => setMinConfidence(Number(e.target.value))} disabled={disabled} />
                  </div>
                </div>
                <div className="mt-2">
                  <label className={label} htmlFor="chat-recipients">{t("recipients")}</label>
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
