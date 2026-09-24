import type { DecisionFrame, Step } from "./types";

export interface CandidateOption {
  key: string;
  detail: string;
  probability: number | null;
  selected: boolean;
}
export interface CandidateQuestion {
  key: string;
  role: string;
  selected: string | null;
  confidence: number | null;
  deterministic: boolean;
  consumed: boolean;
  options: CandidateOption[];
}
const record = (value: unknown): Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const probability = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
const str = (value: unknown): string | null => typeof value === "string" ? value : null;
const detail = (value: unknown): string => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2);

/** Presentation only: these milestones are not additional transcript records. */
export function liveDecision(frame: DecisionFrame, zh: boolean) {
  const candidates = candidateView(undefined, frame);
  const lines: string[] = [];
  if (frame.questions) {
    const requested = candidateView(undefined, { ...frame, response: undefined }).submitted;
    const count = requested.reduce((sum, question) => sum + question.options.length, 0);
    lines.push(zh ? `已发送 ${requested.length} 组、${count} 个候选给 Jev` : `Sent ${requested.length} groups / ${count} candidates to Jev`);
  }
  if (frame.response) {
    const selected = candidates.cards.filter((card) => card.consumed && card.selected).map((card) => card.selected).join(" → ");
    lines.push(zh ? `Jev 已返回${selected ? `：${selected}` : ""}` : `Jev returned${selected ? `: ${selected}` : ""}`);
  }
  if (frame.llm) {
    const kind = frame.llm.kind === "arbitration" ? (zh ? "LLM 复核" : "LLM review") : frame.llm.kind === "parameter_authoring" ? (zh ? "LLM 补参" : "LLM parameters") : (zh ? "LLM 撰写" : "LLM authoring");
    const status = frame.llm.status === "running" ? (zh ? "已开始" : "started") : frame.llm.status === "failed" ? (zh ? "失败" : "failed") : (zh ? "已返回，尚不代表已提交" : "returned; not yet a commit");
    lines.push(`${kind} · ${frame.llm.operation} · ${status}`);
  }
  if (frame.committed) lines.push(zh ? "调用已提交至 transcript，执行结果另行记录" : "Call committed to transcript; execution result recorded separately");
  return lines;
}

/** Old decision_ready events did not reveal arbitration: absence is unknown. */
export function observedRoute(frame: DecisionFrame) {
  const assisted = !!(frame.llm || frame.needsAuthoring || frame.escalated || frame.finalBinding === "arbitrated");
  const direct = !!frame.finalOperation && frame.escalated === false && frame.needsAuthoring === false
    && !frame.llm && typeof frame.finalBinding === "string" && frame.finalBinding !== "arbitrated";
  return { assisted, direct };
}

/** Read the ORIGINAL Jev response, never label an arbitration override as its
 * selection. Do not reconstruct a pick from argmax or synthesize probabilities. */
export function candidateView(step: Step | undefined, frame?: DecisionFrame) {
  const call = step?.model_calls?.find((item) => item.kind === "jev_decision");
  const original = record(call?.response);
  const recorded = record(step?.decision);
  const hasDecisionEvidence = recorded.confidence != null || recorded.latency_ms != null
    || recorded.consumed_heads != null || recorded.phase_probabilities != null;
  const fallback = hasDecisionEvidence && !step?.escalation && !step?.decision.escalated ? recorded : {};
  const response = frame ? record(frame.response) : Object.keys(original).length ? original : fallback;
  const request = frame ? { questions: frame.questions } : record(call?.request ?? step?.request);
  const questions = record(request.questions);
  const heads = (Array.isArray(response.consumed_heads) ? response.consumed_heads : []).map(record);
  const cards: CandidateQuestion[] = [];
  if (frame?.questions && !frame.response) {
    for (const [key, raw] of Object.entries(questions)) {
      const question = record(raw);
      if (question.type !== "choice") continue;
      cards.push({ key, role: key.split("__")[0], selected: null, confidence: null, deterministic: false, consumed: false,
        options: Object.entries(record(question.criteria)).map(([key, value]) => ({ key, detail: detail(value), probability: null, selected: false })) });
    }
  }
  const used = new Set<string>();
  for (const [index, head] of heads.entries()) {
    const key = str(head.head) ?? `constant-${index}`;
    used.add(key);
    const criteria = record(record(questions[key]).criteria);
    const probabilities = record(head.probabilities);
    const selected = str(head.selected);
    const keys = [...new Set([...Object.keys(criteria), ...Object.keys(probabilities), ...(selected ? [selected] : [])])];
    cards.push({
      key, role: str(head.role) ?? "choice", selected,
      confidence: probability(head.confidence), deterministic: head.deterministic === true, consumed: true,
      options: keys.map((key) => ({ key, detail: detail(criteria[key]), probability: head.deterministic === true ? null : probability(probabilities[key]), selected: key === selected })),
    });
  }
  // Older evidence has distributions but not a consumed-head manifest.
  if (!heads.length) {
    for (const [role, field, selected] of [
      ["phase", "phase_probabilities", str(response.phase)],
      ["action", "operation_probabilities", str(response.operation)],
      ["target", "target_probabilities", str(response.target)],
    ] as const) {
      const probabilities = record(response[field]);
      if (Object.keys(probabilities).length) cards.push({
        key: `legacy-${role}`, role, selected, confidence: null, deterministic: false, consumed: true,
        options: Object.entries(probabilities).map(([key, value]) => ({ key, detail: "", probability: probability(value), selected: key === selected })),
      });
    }
  }
  // A single binding can be an engineering constant, hence no question head.
  if (heads.length && !heads.some((head) => ["target", "target_member", "target_fallback"].includes(String(head.role)))) {
    const binding = response.binding_mode === "llm_parameters" ? "LLM_PARAMETERS" : response.binding_mode === "defaults" ? "DEFAULT_ARGUMENTS" : str(response.target);
    if (binding) cards.push({ key: "constant-binding", role: "target", selected: binding, confidence: null, deterministic: true, consumed: true,
      options: [{ key: binding, detail: detail(response.bound_arguments), probability: null, selected: true }] });
  }
  const unused: CandidateQuestion[] = (heads.length ? Object.entries(questions) : []).flatMap(([key, raw]) => {
    const question = record(raw);
    if (used.has(key) || question.type !== "choice") return [];
    return [{ key, role: "unconsumed", selected: null, confidence: null, deterministic: false, consumed: false,
      options: Object.entries(record(question.criteria)).map(([key, value]) => ({ key, detail: detail(value), probability: null, selected: false })) }];
  });
  return {
    cards, unused,
    // Stable request order survives the response: highlighting changes, the pool does not disappear.
    submitted: Object.keys(questions).flatMap((key) => {
      const known = [...cards, ...unused].find((question) => question.key === key);
      if (known) return [known];
      const question = record(questions[key]);
      return question.type === "choice" ? [{ key, role: key.split("__")[0], selected: null, confidence: null, deterministic: false, consumed: false,
        options: Object.entries(record(question.criteria)).map(([key, value]) => ({ key, detail: detail(value), probability: null, selected: false })) }] : [];
    }).concat(cards.filter((question) => !(question.key in questions))),
    originalOperation: str(response.operation) ?? step?.escalation?.from.action ?? null,
    originalPhase: str(response.phase) ?? cards.find((card) => card.role === "phase" && card.consumed)?.selected ?? null,
    finalOperation: frame ? frame.finalOperation ?? null : step?.decision.operation ?? null,
    originalBinding: str(response.binding_mode),
    finalBinding: frame ? frame.finalBinding ?? null : str(record(step?.decision).binding_mode),
    ambiguity: probability(response.ambiguity),
    progress: typeof record(response.progress).score === "number" ? record(response.progress).score as number : null,
  };
}
