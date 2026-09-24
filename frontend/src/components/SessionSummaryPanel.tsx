import { useState } from "react";
import type { SessionComparison } from "../comparison";
import { modelName } from "../diagramModel";
import { useT } from "../i18n";
import { CompareTable } from "./CompareTable";
import { EscalationStatsBlock } from "./PairedTurn";

/** Live session-wide aggregate across all paired turns. Rendered as a compact
 *  one-line strip — progress plus each lane's running time/cost totals — that
 *  expands on demand into the full comparison table and adjudication stats.
 *  The collapsed strip keeps updating live, so the panel never competes with
 *  the chat for vertical space. */
export function SessionSummaryPanel({ comparison }: { comparison: SessionComparison }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const name = modelName(comparison.model);
  const jm = comparison.jev.metrics;
  const bm = comparison.baseline.metrics;
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      data-testid="session-summary-panel"
      className="group rounded-xl border border-accent/50 bg-accent/5"
    >
      <summary className="flex cursor-pointer select-none flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2 text-sm font-bold">
        <span>{t("sessionSummaryTitle")}</span>
        <span className="num text-xs font-medium text-ink2">
          {t("sessionSummaryProgress", {
            done: comparison.completedTurns,
            total: comparison.turns,
          })}
        </span>
        {jm && bm && (
          <span className="flex flex-wrap gap-1.5 text-[11px] font-medium text-ink2">
            <span className="num rounded-full bg-surface px-2 py-0.5">
              {t("sessionLaneSummary", {
                lane: `${name}Loop`,
                time: `${(jm.elapsed_ms / 1000).toFixed(1)}s`,
                cost: jm.est_cost_usd,
              })}
            </span>
            <span className="num rounded-full bg-surface px-2 py-0.5">
              {t("sessionLaneSummary", {
                lane: t("baseLane"),
                time: `${(bm.elapsed_ms / 1000).toFixed(1)}s`,
                cost: bm.est_cost_usd,
              })}
            </span>
          </span>
        )}
        <span className="ml-auto inline-block text-ink2 transition-transform group-open:rotate-90">▸</span>
      </summary>
      <div className="flex flex-col gap-3 px-3.5 pb-3.5">
        <div className="overflow-x-auto">
          <CompareTable
            jev={comparison.jev}
            baseline={comparison.baseline}
            phases={comparison.phases}
            model={comparison.model}
          />
        </div>
        <EscalationStatsBlock
          states={{ jev: comparison.jev, baseline: comparison.baseline }}
          titleKey="sessionEscalationsTitle"
          model={comparison.model}
        />
      </div>
    </details>
  );
}
