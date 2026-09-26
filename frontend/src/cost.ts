import type { Metrics } from "./types";

/** Missing flags keep historical semantics. Explicit incomplete costs are
 * known subtotals, never comparable totals or evidence of free inference. */
export function costComplete(metrics: Metrics | null | undefined): boolean {
  return metrics?.cost_complete !== false && metrics?.helper.cost_complete !== false;
}

export function costText(cost: number | null | undefined, complete: boolean, zh: boolean, digits = 6): string {
  if (cost == null) return "—";
  if (!complete && cost <= 0) return zh ? "未知（价格未配置）" : "Unknown (pricing not configured)";
  const amount = `$${cost.toFixed(digits)}`;
  return complete ? amount : `${amount} ${zh ? "（部分费用；价格未配置）" : "(partial cost; pricing not configured)"}`;
}
