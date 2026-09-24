import type { DecisionProvider } from "./types";

/** The diagram follows the run that is on screen. Before a run exists, it
 * follows the composer's current choice. Older runs have no field and stay Jev. */
export function diagramModel(
  params: Record<string, unknown> | null | undefined,
  selected: DecisionProvider,
): DecisionProvider {
  const recorded = params?.decision_provider;
  if (recorded === "laya" || recorded === "jev") return recorded;
  if (params) return "jev";
  return selected;
}

export function modelName(model: DecisionProvider): "Jev" | "Laya" {
  return model === "laya" ? "Laya" : "Jev";
}
