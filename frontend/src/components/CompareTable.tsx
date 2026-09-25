import type { DecisionProvider, LanePhase, LaneState, Metrics } from "../types";
import { modelName } from "../diagramModel";
import { useT } from "../i18n";

interface Cell {
  text: string;
  /** numeric value when comparable; lower is better */
  value?: number | null;
}

interface Row {
  labelKey: string;
  jev: Cell;
  baseline: Cell;
}

export interface LanePhases {
  jev: LanePhase;
  baseline?: LanePhase;
}

function fmtMs(ms: number | null): string {
  return ms == null ? "–" : `${ms}ms`;
}

function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

type MetricsLike = Metrics | null | undefined;
type T = ReturnType<typeof useT>;

function outcomeText(t: T, phase: LanePhase | undefined, state: LaneState | undefined): string {
  switch (phase) {
    case "error": return t("laneError");
    case undefined:
    case "idle": return t("laneIdle");
    case "awaiting": return t("laneAwaiting");
    case "done": return state?.answer ? t("statusAnswered") : t("statusNoAnswer");
    default: return t("laneRunning");
  }
}

function rowsFor(t: T, jev: LaneState, baseline: LaneState | undefined, phases: LanePhases, name: string): Row[] {
  const j: MetricsLike = jev.metrics;
  const b: MetricsLike = baseline?.metrics;
  const cell = (text: string, value?: number | null): Cell => ({ text, value });
  const split = (metrics: MetricsLike) => {
    if (!metrics) return "–";
    const roleTotal = metrics.jev.est_cost_usd + metrics.helper.est_cost_usd;
    if (roleTotal <= 0) return "–";
    const jevShare = metrics.jev.est_cost_usd / roleTotal * 100;
    const llmShare = metrics.helper.est_cost_usd / roleTotal * 100;
    return `${name} ${jevShare.toFixed(1)}% · LLM ${llmShare.toFixed(1)}%`;
  };
  const llmCalls = (metrics: MetricsLike) => {
    if (!metrics) return "–";
    const parts = Object.entries(metrics.helper.by_kind ?? {})
      .filter(([, value]) => value.calls > 0)
      .map(([kind, value]) => `${value.calls} ${kind}`);
    return parts.length ? parts.join(" + ") : `${metrics.helper.calls} llm`;
  };
  const directPass = (metrics: MetricsLike) => {
    const routing = metrics?.routing;
    if (!routing || routing.jev_steps === 0) return "–";
    const rate = routing.llm_avoidance_rate ?? 0;
    return `${routing.direct_jev_steps}/${routing.jev_steps} · ${(rate * 100).toFixed(1)}%`;
  };
  return [
    {
      labelKey: "rowTotalTime",
      jev: cell(j ? `${(j.elapsed_ms / 1000).toFixed(1)}s` : "–", j?.elapsed_ms),
      baseline: cell(b ? `${(b.elapsed_ms / 1000).toFixed(1)}s` : "–", b?.elapsed_ms),
    },
    {
      labelKey: "rowModelCalls",
      jev: cell(j ? `${j.jev.calls} ${name.toLowerCase()} + ${llmCalls(j)}` : "–",
                j ? j.jev.calls + j.helper.calls : null),
      baseline: cell(b ? llmCalls(b) : "–", b?.helper.calls ?? null),
    },
    {
      labelKey: "rowDirectPass",
      jev: cell(directPass(j)),
      baseline: cell("–"),
    },
    {
      labelKey: "rowDecisionMedian",
      jev: cell(j ? fmtMs(j.jev.median_ms) : "–", j?.jev.median_ms),
      baseline: cell(b ? fmtMs(b.helper.median_ms) : "–", b?.helper.median_ms),
    },
    {
      labelKey: "rowJevTokens",
      jev: cell(j ? `${fmtTokens(j.jev.input_tokens)}↑ / ${fmtTokens(j.jev.output_tokens)}↓` : "–",
                j ? j.jev.input_tokens + j.jev.output_tokens : null),
      baseline: cell("–", null),
    },
    {
      labelKey: "rowLlmTokens",
      jev: cell(j ? `${fmtTokens(j.helper.input_tokens)}↑ / ${fmtTokens(j.helper.output_tokens)}↓ · hit ${fmtTokens(j.helper.cache_hit_tokens)} / miss ${fmtTokens(j.helper.cache_miss_tokens ?? Math.max(0, j.helper.input_tokens - j.helper.cache_hit_tokens))}${j.helper.cache_unknown_tokens ? ` / unknown ${fmtTokens(j.helper.cache_unknown_tokens)}` : ""}` : "–",
                j ? j.helper.input_tokens + j.helper.output_tokens : null),
      baseline: cell(b ? `${fmtTokens(b.helper.input_tokens)}↑ / ${fmtTokens(b.helper.output_tokens)}↓ · hit ${fmtTokens(b.helper.cache_hit_tokens)} / miss ${fmtTokens(b.helper.cache_miss_tokens ?? Math.max(0, b.helper.input_tokens - b.helper.cache_hit_tokens))}${b.helper.cache_unknown_tokens ? ` / unknown ${fmtTokens(b.helper.cache_unknown_tokens)}` : ""}` : "–",
                     b ? b.helper.input_tokens + b.helper.output_tokens : null),
    },
    {
      labelKey: "rowTokens",
      jev: cell(j ? `${fmtTokens(j.jev.input_tokens + j.helper.input_tokens)}↑ / ${fmtTokens(j.jev.output_tokens + j.helper.output_tokens)}↓` : "–",
                j ? j.jev.input_tokens + j.helper.input_tokens + j.jev.output_tokens + j.helper.output_tokens : null),
      baseline: cell(b ? `${fmtTokens(b.helper.input_tokens)}↑ / ${fmtTokens(b.helper.output_tokens)}↓` : "–",
                     b ? b.helper.input_tokens + b.helper.output_tokens : null),
    },
    {
      labelKey: "rowJevCost",
      jev: cell(j ? `$${j.jev.est_cost_usd.toFixed(6)}` : "–", j?.jev.est_cost_usd),
      baseline: cell("–", null),
    },
    {
      labelKey: "rowLlmCost",
      jev: cell(j ? `$${j.helper.est_cost_usd.toFixed(6)}` : "–", j?.helper.est_cost_usd),
      baseline: cell(b ? `$${b.helper.est_cost_usd.toFixed(6)}` : "–",
                     b?.helper.est_cost_usd),
    },
    {
      labelKey: "rowCostShare",
      jev: cell(split(j)),
      baseline: cell(b ? `${name} 0.0% · LLM 100.0%` : "–"),
    },
    {
      labelKey: "rowCost",
      jev: cell(j ? `$${j.est_cost_usd.toFixed(6)}` : "–", j?.est_cost_usd),
      baseline: cell(b ? `$${b.est_cost_usd.toFixed(6)}` : "–", b?.est_cost_usd),
    },
    {
      labelKey: "rowLark",
      jev: cell(j ? String(j.lark.calls) : "–", j?.lark.calls),
      baseline: cell(b ? String(b.lark.calls) : "–", b?.lark.calls),
    },
    {
      labelKey: "rowSteps",
      jev: cell(String(jev.steps.length), jev.steps.length),
      baseline: cell(baseline ? String(baseline.steps.length) : "–", baseline?.steps.length ?? null),
    },
    {
      labelKey: "rowStatus",
      jev: cell(outcomeText(t, phases.jev, jev)),
      baseline: cell(outcomeText(t, phases.baseline, baseline)),
    },
  ];
}

function diffText(t: T, row: Row): { text: string; winner: "jev" | "baseline" | null } {
  const { jev, baseline } = row;
  if (jev.value == null || baseline.value == null || baseline.value === 0) {
    return { text: "–", winner: null };
  }
  if (jev.value === baseline.value) return { text: t("same"), winner: null };
  const pct = Math.abs(1 - jev.value / baseline.value) * 100;
  const p = `${pct.toFixed(pct >= 10 ? 0 : 1)}%`;
  if (jev.value < baseline.value) {
    const ratio = baseline.value / jev.value;
    const suffix = ratio >= 1.5 ? ` (${ratio.toFixed(1)}×)` : "";
    return { text: t(row.labelKey === "rowTotalTime" || row.labelKey === "rowDecisionMedian" ? "faster" : "fewer", { p }) + suffix, winner: "jev" };
  }
  return { text: t(row.labelKey === "rowTotalTime" || row.labelKey === "rowDecisionMedian" ? "slower" : "more", { p }), winner: "baseline" };
}

/** Lane metrics side by side with a Δ column. Used live during a run and inside
 *  the post-run summary (final metrics are already applied to the lane states).
 *  Without a baseline lane the baseline/Δ columns collapse — the single-lane
 *  degenerate case of the same table. */
export function CompareTable({ jev, baseline, phases, model = "jev" }: {
  jev: LaneState;
  baseline: LaneState | undefined;
  phases: LanePhases;
  model?: DecisionProvider;
}) {
  const t = useT();
  const name = modelName(model);
  const rows = rowsFor(t, jev, baseline, phases, name);
  const showBaseline = baseline != null;
  const jt = jev.metrics?.elapsed_ms;
  const bt = baseline?.metrics?.elapsed_ms;
  const cachePolicy = jev.metrics?.cache;
  const baselineCache = baseline?.metrics?.cache;
  const pricing = jev.metrics?.pricing;
  const headline =
    jt != null && bt != null && jt > 0 && bt > 0
      ? jt <= bt
        ? `⚡ ${t("faster", { p: `${(((bt - jt) / bt) * 100).toFixed(0)}%` })}`
        : `🐢 ${t("slower", { p: `${(((jt - bt) / bt) * 100).toFixed(0)}%` })}`
      : null;

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-sm font-semibold">{t("compareTitle")}</h2>
        {headline && (
          <span className={`rounded-full border px-2 py-0.5 text-xs font-bold ${
            headline.startsWith("⚡")
              ? "border-good/60 bg-good/10 text-good"
              : "border-critical/50 bg-critical/5 text-critical"
          }`}>
            {name}Loop {headline}
          </span>
        )}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink2">
            <th className="pb-2 pr-4 font-semibold">{t("colMetric")}</th>
            <th className="pb-2 pr-4 font-semibold">{t("colJev", { model: name })}</th>
            {showBaseline && <th className="pb-2 pr-4 font-semibold">{t("colBaseline")}</th>}
            {showBaseline && <th className="pb-2 font-semibold">{t("colDiff")}</th>}
          </tr>
        </thead>
        <tbody className="num">
          {rows.map((row) => {
            const { text, winner } = showBaseline ? diffText(t, row) : { text: "–", winner: null };
            const jevWin = winner === "jev";
            const baseWin = winner === "baseline";
            return (
              <tr key={row.labelKey} className="border-b border-line/60 last:border-0">
                <td className="py-1.5 pr-4 text-ink2">{t(row.labelKey, { model: name })}</td>
                <td className={`py-1.5 pr-4 font-semibold ${jevWin ? "rounded bg-good/10 text-good" : "text-ink"}`}>
                  {row.jev.text}
                </td>
                {showBaseline && (
                  <td className={`py-1.5 pr-4 ${baseWin ? "rounded bg-good/10 font-semibold text-good" : "text-ink"}`}>
                    {row.baseline.text}
                  </td>
                )}
                {showBaseline && (
                  <td className={`py-1.5 text-xs ${winner ? "text-ink" : "text-ink2"}`}>{text}</td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-ink2">{t("compareNote")}</p>
      {pricing && (
        <p className="mt-1 text-[10px] text-ink2">
          {t("pricingNote", {
            j: pricing.jev_input_per_mtok,
            i: pricing.llm_input_per_mtok,
            c: pricing.llm_cache_hit_per_mtok,
            o: pricing.llm_output_per_mtok,
            model: name,
          })}
        </p>
      )}
      {cachePolicy && (
        <p className="mt-1 text-[10px] text-ink2">
          {t("cachePolicyNote", {
            policy: cachePolicy.policy,
            jev: cachePolicy.scope_hash?.slice(0, 8) ?? "shared",
            baseline: baselineCache?.scope_hash?.slice(0, 8) ?? "shared",
            model: name,
          })}
        </p>
      )}
    </section>
  );
}
