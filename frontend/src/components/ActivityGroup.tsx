import type { DecisionProvider, Lane, LanePhase, LaneState } from "../types";
import { useLang, useT } from "../i18n";
import { liveDecision } from "../candidateView";
import { modelName } from "../diagramModel";
import { useLiveElapsedMs } from "../hooks";
import { escalationStats, LANE_INFO } from "../lane";
import { ActivityRow } from "./ActivityRow";
import { PhaseChip, Spinner } from "./StatusBits";

const LANE_ICON: Record<Lane, string> = { jev: "⚡", baseline: "🤖" };

/** One lane of a run rendered as a self-contained panel: a header with the
 *  lane's identity, live phase and continue/abort controls; the streaming
 *  activity lines; an error banner when the lane failed; the lane's final
 *  answer once it lands; and — once final metrics land — a one-line footer
 *  summarising the lane's cost profile. Used full-width for single-lane
 *  turns and as one half of a paired turn's side-by-side lanes. */
export function ActivityGroup({ lane, state, phase, error, onContinue, onAbort, compact = false, historical = false, model = "jev" }: {
  lane: Lane;
  state: LaneState | undefined;
  phase: LanePhase;
  error?: string;
  onContinue?: () => void;
  onAbort?: () => void;
  compact?: boolean;
  historical?: boolean;
  model?: DecisionProvider;
}) {
  const t = useT();
  const { lang } = useLang();
  const name = modelName(model);
  const milestones = state?.decisionFrame ? liveDecision(state.decisionFrame, lang === "zh", model) : [];
  const live = phase === "running" || phase === "awaiting";
  const elapsedMs = useLiveElapsedMs(state?.metrics?.elapsed_ms ?? null, live && !historical);
  const metrics = state?.metrics ?? null;
  const esc = escalationStats(metrics);
  const decisionSteps = lane === "jev"
    ? metrics?.routing?.jev_steps ?? state?.steps.length ?? 0
    : metrics?.routing?.plain_steps ?? state?.steps.length ?? 0;
  const directSteps = lane === "jev"
    ? metrics?.routing?.direct_jev_steps ?? Math.max(0, decisionSteps - esc.count)
    : 0;
  const activity = state?.activity;
  const activityLabel = activity
    ? t(`activity_${activity.stage}`, {
      lane: lane === "jev" ? `${name}Loop` : t(LANE_INFO[lane].titleKey),
      operation: activity.operation ?? "",
    })
    : null;

  return (
    <section
      data-testid="activity-group"
      data-lane={lane}
      className="soft-lift min-w-0 rounded-2xl border border-line bg-surface"
    >
      <header className={`flex flex-wrap items-center gap-2 border-b border-line ${compact ? "px-3 py-2" : "px-3.5 py-2.5"}`}>
        <span aria-hidden className="text-base leading-none">{LANE_ICON[lane]}</span>
        <h3 className={`font-bold ${compact ? "text-xs" : "text-sm"}`}>{lane === "jev" ? `${name}Loop` : t(LANE_INFO[lane].titleKey)}</h3>
        <span className="hidden text-[11px] text-ink2 sm:inline">{lane === "jev" ? t("jevLaneSub", { model: name }) : t(LANE_INFO[lane].subKey)}</span>
        <PhaseChip phase={phase} />
        {elapsedMs != null && (
          <span className="num rounded-full border border-line px-2 py-0.5 text-xs font-semibold text-ink">
            {(elapsedMs / 1000).toFixed(1)}s
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {phase === "awaiting" && onContinue && (
            <button
              onClick={onContinue}
              className="rounded-xl bg-accent px-2.5 py-1 text-xs font-semibold text-white"
            >
              {t("continueBtn")}
            </button>
          )}
          {live && onAbort && (
            <button
              onClick={onAbort}
              className="rounded-xl border border-critical/60 px-2.5 py-1 text-xs font-semibold text-critical"
            >
              {t("abort")}
            </button>
          )}
        </span>
      </header>

      <div className="flex flex-col gap-1 px-2 py-2">
        {state?.steps.length ? (
          state.steps.map((step, i) => (
            <ActivityRow key={i} index={i + 1} step={step} lane={lane} compact={compact} modelLabel={name} />
          ))
        ) : !activityLabel ? (
          <p className="px-2 py-4 text-center text-xs text-ink2">
            {phase === "running" || phase === "awaiting" ? t("waitingFirstStep") : t("waitingLane")}
          </p>
        ) : null}
        {!!milestones.length && <div className="conversation-events" data-testid="live-decision-events">
          <span className="eyebrow">{lang === "zh" ? "当前步骤 · 已接收事件" : "CURRENT STEP / RECEIVED EVENTS"}</span>
          {milestones.map((line, index) => <p key={index}>{line}</p>)}
        </div>}
        {live && activityLabel && (
          <div className="flex items-center gap-2 rounded-xl border border-accent/30 bg-accent/5 px-3 py-2 text-xs text-ink">
            <Spinner className="text-accent" />
            <span>{activityLabel}</span>
          </div>
        )}
      </div>

      {!!state?.unknownAcknowledgements && (
        <div className="mx-2 mb-2 rounded-xl border border-warn/60 bg-warn/5 p-2.5 text-xs text-ink">
          {t("unknownAcknowledged", { n: state.unknownAcknowledgements })}
        </div>
      )}

      {error && (
        <div className="mx-2 mb-2 rounded-xl border border-critical/50 bg-critical/5 p-2.5 text-xs text-ink">
          <span className="font-semibold text-critical">{t("errorLabel")}</span> — {error}
        </div>
      )}

      {state?.answer && (
        <div data-testid="lane-answer" className="mx-2 mb-2 rounded-xl border border-accent/30 bg-accent/5 px-3 py-2.5">
          <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-accent">
            {t("answer")}
          </span>
          <div className="max-h-72 overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-ink">
            {state.answer}
          </div>
        </div>
      )}
      {(phase === "done" || phase === "error") && state && !state.answer && (
        <p className="mx-2 mb-2 text-xs italic text-ink2">{t("statusNoAnswer")}</p>
      )}

      {(phase === "done" || phase === "error") && metrics && (
        <details data-testid="run-metrics" className="group border-t border-line">
          <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3.5 py-2 text-xs text-ink2">
            <span className="font-semibold text-ink">{t("runSummary")}</span>
            <span>
              {lane === "jev"
                ? t("metricSummary", {
                  steps: decisionSteps,
                  direct: directSteps,
                  reviewed: esc.count,
                  time: `${(metrics.elapsed_ms / 1000).toFixed(1)}s`,
                  model: name,
                })
                : t("baselineMetricSummary", {
                  steps: decisionSteps,
                  time: `${(metrics.elapsed_ms / 1000).toFixed(1)}s`,
                })}
            </span>
          </summary>
          <div className="grid gap-2 border-t border-line px-3.5 py-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
            {lane === "jev" && (
              <MetricItem label={t("metricJevCalls", { model: name })} value={`${metrics.jev.calls}`} />
            )}
            <MetricItem label={t("metricLlmCalls")} value={`${metrics.helper.calls}`} />
            {lane === "jev" && (
              <MetricItem label={t("metricReviewCalls")} value={`${esc.count}`} />
            )}
            {lane === "jev" && (
              <MetricItem
                label={t("metricJevTokens", { model: name })}
                value={`${metrics.jev.input_tokens} ↑ / ${metrics.jev.output_tokens} ↓`}
              />
            )}
            <MetricItem
              label={t("metricLlmTokens")}
              value={`${metrics.helper.input_tokens} ↑ / ${metrics.helper.output_tokens} ↓`}
            />
            <MetricItem label={t("metricCacheHit")} value={`${metrics.helper.cache_hit_tokens}`} />
            <MetricItem label={t("metricCost")} value={`$${metrics.est_cost_usd}`} />
          </div>
        </details>
      )}
    </section>
  );
}

function MetricItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface2 px-2.5 py-2">
      <span className="block text-[10px] uppercase tracking-wide text-ink2">{label}</span>
      <strong className="num mt-0.5 block text-xs text-ink">{value}</strong>
    </div>
  );
}
