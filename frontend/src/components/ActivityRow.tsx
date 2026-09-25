import { ProbBars } from "./ProbBars";
import { Badge, TurnContent } from "./TurnContent";
import type { Lane, Step } from "../types";
import { useT } from "../i18n";

function fmtTokens(n?: number): string {
  if (!n) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function confidenceTone(value: number): "good" | "accent" | "warn" {
  if (value >= 0.8) return "good";
  if (value >= 0.5) return "accent";
  return "warn";
}

/** One tool/activity line inside a chat turn: icon + operation + target + the
 *  few badges that matter at a glance (confidence, jev/LLM latency, escalation,
 *  denial, dry-run/live, created link). The line itself is the toggle; the
 *  probability distributions, escalation's original distribution, denial detail
 *  and the raw request live behind it. Native <details> so it works with zero
 *  state (and in server rendering). */
export function ActivityRow({ index, step, lane, compact = false, modelLabel = "Jev" }: {
  index: number;
  step: Step;
  lane?: Lane;
  modelLabel?: string;
  compact?: boolean;
}) {
  const t = useT();
  const d = step.decision;
  if (!d) return null;

  const operationLabel = OP_LABEL_KEYS[d.operation]
    ? t(OP_LABEL_KEYS[d.operation])
    : d.operation;
  const phaseRows = Object.entries(d.phase_probabilities ?? {}).map(([key, value]) => ({
    key, label: t(PHASE_LABEL_KEYS[key] ?? key), value,
  }));
  const opRows = Object.entries(d.operation_probabilities ?? {}).map(([key, value]) => ({
    key, label: OP_LABEL_KEYS[key] ? t(OP_LABEL_KEYS[key]) : key, value,
  }));
  const targetRows = d.target_probabilities_labeled?.map((r) => ({ key: r.key, label: r.label, value: r.p })) ?? [];
  const esc = step.escalation;
  const escProbRows = esc?.from.probabilities
    ? Object.entries(esc.from.probabilities).map(([key, value]) => ({
      key, label: OP_LABEL_KEYS[key] ? t(OP_LABEL_KEYS[key]) : key, value,
    }))
    : [];
  const modelCalls = step.model_calls ?? [];

  const row = (
    <>
      <span className="num shrink-0 text-ink2">{t("stepLabel", { n: index })}</span>
      <span aria-hidden className="shrink-0">
        {d.operation === "LLM_TURN" ? "🤖" : OP_ICONS[d.operation] ?? "⚡"}
      </span>
      <Badge tone="accent">{operationLabel}</Badge>
      {d.target_label ? (
        <span className={`max-w-40 truncate text-ink ${compact ? "max-w-32" : ""}`} title={d.target_label}>
          → {d.target_label}
        </span>
      ) : null}
      {d.confidence != null && (
        <Badge tone={confidenceTone(d.confidence)} title={t("routeConfidenceHelp")}>
          {t("routeConfidence")} {(d.confidence * 100).toFixed(0)}%
        </Badge>
      )}
      {esc ? (
        <Badge tone="warn" title={t("escalated")}>{t("llmReviewed")}</Badge>
      ) : null}
      {step.denied && <Badge tone="critical" title={step.denied}>{t("denied")}</Badge>}
      {step.aborted && <Badge tone="warn" title={t("aborted")}>⏹</Badge>}
      {step.outcome?.dry_run === false && <Badge tone="good">{t("liveExecShort")}</Badge>}
      {step.outcome?.dry_run === true && <Badge tone="neutral">{t("dryRun")}</Badge>}
      {step.outcome?.created && (
        <a className="text-accent underline decoration-dotted" href={step.outcome.created} target="_blank" rel="noreferrer">
          {t("created")}
        </a>
      )}
      <span className="ml-auto flex items-center gap-2 text-ink2">
        {d.latency_ms != null && (
          <span className="num" title={t("jevDecisionTime", { model: modelLabel })}>
            {lane === "jev" ? modelLabel : "LLM"} {fmtMs(d.latency_ms)}
          </span>
        )}
        {step.outcome?.helper?.latency_ms != null && (
          <span className="num" title={t("llmAuthoringTime")}>
            {t("llmGenerated")} {fmtMs(step.outcome.helper.latency_ms)}
          </span>
        )}
      </span>
    </>
  );

  const expandable =
    opRows.length + targetRows.length + escProbRows.length > 0
    || modelCalls.length > 0 || !!step.denied || !!step.request || !!step.outcome;

  const expansion = (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl border border-line bg-surface2/50 p-2.5 text-xs text-ink2">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {d.phase && (
            <span>
              {t("selectedPhase")}{" "}
              <strong className="text-ink">{t(PHASE_LABEL_KEYS[d.phase] ?? d.phase)}</strong>
            </span>
          )}
          {d.confidence != null && (
            <span>
              {t("routeConfidence")}{" "}
              <strong className="num text-ink">{(d.confidence * 100).toFixed(0)}%</strong>
            </span>
          )}
          {(d.usage?.input_tokens != null || d.usage?.output_tokens != null) && (
            <span className="num">
              {t("jevTokenDetail", {
                input: fmtTokens(d.usage.input_tokens),
                output: fmtTokens(d.usage.output_tokens),
                model: modelLabel,
              })}
            </span>
          )}
        </div>
        {d.confidence != null && (
          <p className="mt-1 text-[11px] leading-relaxed">{t("routeConfidenceHelp")}</p>
        )}
      </div>
      {phaseRows.length > 0 && (
        <div>
          <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink2">
            {t("phaseDist")}
          </h4>
          <ProbBars rows={phaseRows} highlight={d.phase} />
        </div>
      )}
      {(opRows.length > 0 || targetRows.length > 0) && (
        <div className="grid gap-4 md:grid-cols-2">
          {opRows.length > 0 && (
            <div>
              <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink2">{t("actionDist")}</h4>
              <ProbBars rows={opRows} highlight={d.operation} />
            </div>
          )}
          {targetRows.length > 0 && (
            <div>
              <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink2">{t("tgtDist")}</h4>
              <ProbBars rows={targetRows} highlight={typeof d.target === "string" ? d.target : null} />
            </div>
          )}
        </div>
      )}
      {escProbRows.length > 0 && (
        <div>
          <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink2">{t("originalActionDist")}</h4>
          <ProbBars rows={escProbRows} highlight={esc?.from.action ?? null} />
        </div>
      )}
      {step.outcome && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink2">
          {step.outcome.status && (
            <span className="rounded bg-surface2 px-1.5 py-0.5 text-[11px] uppercase tracking-wide">{step.outcome.status}</span>
          )}
          {step.outcome.action && <code className="rounded bg-surface2 px-1.5 py-0.5">{step.outcome.action}</code>}
          {step.outcome.helper?.model && (
            <span className="num">{t("llmBadge")} {step.outcome.helper.model}</span>
          )}
        </div>
      )}
      {step.denied && (
        <div className="rounded-xl border border-critical/50 bg-critical/5 p-2.5 text-xs text-ink">
          <span className="font-semibold text-critical">{t("denied")}</span> — {step.denied}
        </div>
      )}
      {step.aborted && (
        <div className="rounded-xl border border-warn/60 bg-warn/5 p-2.5 text-xs text-ink">{t("aborted")}</div>
      )}
      {modelCalls.length > 0 && (
        <div className="flex flex-col gap-2" data-testid="model-calls">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-ink2">
            {t("modelCalls")}
          </h4>
          {modelCalls.map((call, callIndex) => (
            <details
              key={`${call.kind}-${callIndex}`}
              data-testid="model-call"
              className="rounded-xl border border-line bg-surface"
            >
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2 text-xs">
                <Badge tone={call.kind === "jev_decision" ? "accent" : "neutral"}>
                  {t(`modelCallKind_${call.kind}`, { model: modelLabel })}
                </Badge>
                <span className="font-semibold text-ink">{call.model}</span>
                {call.latency_ms != null && (
                  <span className="num text-ink2">{call.latency_ms}ms</span>
                )}
                <span className="ml-auto text-ink2">▸</span>
              </summary>
              <div className="grid gap-3 border-t border-line p-3 lg:grid-cols-2">
                <div className="min-w-0">
                  <h5 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink2">
                    {t("modelInput")}
                  </h5>
                  <pre className="max-h-80 overflow-auto rounded-lg bg-surface2 p-2 text-[10px] leading-relaxed text-ink">
                    {JSON.stringify(call.request, null, 2)}
                  </pre>
                </div>
                <div className="min-w-0">
                  <h5 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink2">
                    {t("modelOutput")}
                  </h5>
                  <pre className="max-h-80 overflow-auto rounded-lg bg-surface2 p-2 text-[10px] leading-relaxed text-ink">
                    {JSON.stringify(call.response, null, 2)}
                  </pre>
                </div>
              </div>
            </details>
          ))}
        </div>
      )}
      {step.request && modelCalls.length === 0 ? (
        <details>
          <summary className="cursor-pointer text-xs text-ink2 select-none">{t("rawReq", { model: modelLabel })}</summary>
          <pre className="mt-2 max-h-72 overflow-auto rounded-xl bg-surface2 p-3 text-[11px] leading-relaxed text-ink2">
            {JSON.stringify(step.request, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  );

  const pad = compact ? "px-2.5 py-1.5" : "px-3 py-2";

  return (
    <article className={`rounded-xl border border-line bg-surface ${compact ? "text-[11px]" : "text-xs"}`}>
      {expandable ? (
        <details className="group">
          <summary
            className={`flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 ${pad}`}
            title={t("expandStep")}
          >
            {row}
            <span className="text-ink2 transition-transform group-open:rotate-90">▸</span>
          </summary>
          <div className={`border-t border-line px-3 py-3 ${compact ? "text-xs" : ""}`}>{expansion}</div>
        </details>
      ) : (
        <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${pad}`}>{row}</div>
      )}
      {(step.outcome?.text ?? step.outcome?.answer) && (
        <TurnContent op={d.operation} text={(step.outcome?.text ?? step.outcome?.answer)!} />
      )}
      {esc && (
        <div
          data-testid="escalation-banner"
          className={`mx-3 mb-2 rounded-xl border p-2 text-[11px] leading-relaxed text-ink ${
            esc.agreed ? "border-good/60 bg-good/5" : "border-warn/60 bg-warn/5"
          }`}
        >
          <span className="font-semibold">⚖ {t("llmReviewed")}</span>
          <span> — {t("reviewReason", { model: modelLabel })} </span>
          <code className="rounded bg-surface2 px-1 py-px">{esc.from.action}</code>
          {esc.from.confidence != null && ` ${(esc.from.confidence * 100).toFixed(0)}%`}
          <span> {t("reviewResult")} </span>
          <code className="rounded bg-surface2 px-1 py-px">{esc.to.action}</code>
          {esc.to.target && (
            <span> → {Array.isArray(esc.to.target) ? esc.to.target.join(", ") : esc.to.target}</span>
          )}
          <span> · {esc.agreed ? t("escUpheld") : t("escOverridden")}</span>
        </div>
      )}
    </article>
  );
}

/** Rough per-operation glyphs for the activity line. */
const OP_ICONS: Record<string, string> = {
  LIST_CHATS: "💬",
  SEARCH_CHATS: "🔍",
  OPEN_CHAT: "📖",
  OPEN_DOC: "📄",
  SEARCH_DOCS: "🗂",
  SEND_MESSAGE: "✉️",
  REPLY_MESSAGE: "↩️",
  WRITE_FILE: "📝",
  LIST_FILES: "🗂",
  BASH: "⌨️",
  ANSWER: "💬",
  DONE: "✅",
  BLOCKED: "🚫",
};

const OP_LABEL_KEYS: Record<string, string> = {
  LIST_CHATS: "operation_LIST_CHATS",
  SEARCH_CHATS: "operation_SEARCH_CHATS",
  OPEN_CHAT: "operation_OPEN_CHAT",
  OPEN_DOC: "operation_OPEN_DOC",
  SEARCH_DOCS: "operation_SEARCH_DOCS",
  SEND_MESSAGE: "operation_SEND_MESSAGE",
  REPLY_MESSAGE: "operation_REPLY_MESSAGE",
  WRITE_FILE: "operation_WRITE_FILE",
  READ_FILE: "operation_READ_FILE",
  LIST_FILES: "operation_LIST_FILES",
  BASH: "operation_BASH",
  ANSWER: "operation_ANSWER",
  DONE: "operation_DONE",
  BLOCKED: "operation_BLOCKED",
};

const PHASE_LABEL_KEYS: Record<string, string> = {
  INSPECT: "phase_INSPECT",
  ACT: "phase_ACT",
  VERIFY: "phase_VERIFY",
  RESPOND: "phase_RESPOND",
};
