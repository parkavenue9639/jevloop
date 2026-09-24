export interface Escalation {
  from: { action: string; confidence: number; probabilities?: Record<string, number> };
  to: { action: string; target: string | string[] | null };
  agreed: boolean;
}

export interface Decision {
  operation: string;
  escalated?: boolean;
  phase?: "INSPECT" | "ACT" | "VERIFY" | "RESPOND" | string | null;
  phase_confidence?: number | null;
  phase_probabilities?: Record<string, number>;
  operation_confidence?: number | null;
  turn?: number;
  confidence: number | null;
  operation_probabilities?: Record<string, number>;
  target: string | string[] | null;
  target_label?: string | null;
  target_confidence?: number | null;
  target_probabilities_labeled?: { key: string; label: string; p: number }[];
  latency_ms: number | null;
  usage?: { input_tokens?: number; output_tokens?: number };
}

export interface Outcome {
  status: string;
  action?: string;
  dry_run?: boolean;
  created?: string;
  reason?: string;
  answer?: string;
  /** the LLM-authored content of this tool call (ledger view: what it "wrote") */
  text?: string;
  helper?: { latency_ms?: number; model?: string } | null;
}

export interface ModelCall {
  kind: "jev_decision" | "plain_decision" | "arbitration" | "authoring" | string;
  model: string;
  latency_ms?: number | null;
  usage?: Record<string, unknown>;
  request?: unknown;
  response?: unknown;
}

export interface Step {
  intent_id?: string;
  decision: Decision;
  escalation?: Escalation;
  outcome?: Outcome;
  denied?: string;
  aborted?: boolean;
  request?: unknown;
  model_calls?: ModelCall[];
  final?: { final: string; steps: number; writes: number; denials: string[] } & Record<string, unknown>;
}

export interface Metrics {
  elapsed_ms: number;
  jev: {
    calls: number;
    median_ms: number | null;
    p95_ms: number | null;
    max_ms: number | null;
    input_tokens: number;
    output_tokens: number;
    est_cost_usd: number;
  };
  helper: {
    calls: number;
    median_ms: number | null;
    total_ms: number;
    input_tokens: number;
    output_tokens: number;
    cache_hit_tokens: number;
    cache_miss_tokens?: number;
    cache_unknown_tokens?: number;
    cache_miss_reported_calls?: number;
    cache_miss_derived_calls?: number;
    by_kind?: Record<string, {
      calls: number;
      input_tokens: number;
      output_tokens: number;
      cache_hit_tokens: number;
      cache_miss_tokens?: number;
      cache_unknown_tokens?: number;
      est_cost_usd: number;
    }>;
    est_cost_usd: number;
  };
  routing?: {
    decision_steps: number;
    jev_steps: number;
    direct_jev_steps: number;
    llm_assisted_jev_steps: number;
    llm_avoidance_rate: number | null;
    plain_steps: number;
  };
  est_cost_usd: number;
  pricing?: {
    jev_input_per_mtok: number;
    llm_input_per_mtok: number;
    llm_output_per_mtok: number;
    llm_cache_hit_per_mtok: number;
  };
  cache?: {
    policy: string;
    scope_hash: string | null;
  };
  lark: {
    calls: number;
    executed_ms: number;
    avg_ms: number | null;
    dry_run_calls: number;
    failed: number;
  };
  denials: string[];
  escalations: EscalationStats & {
    detail: { from: { action: string; confidence: number };
              to: { action: string; target: string | string[] | null };
              agreed: boolean }[];
  } | null;
}

export type Lane = "jev" | "baseline";
export type RunProfile = "single_live" | "single_shadow" | "paired_shadow";


/** Coarse per-lane status derived from the stream (not sent by the server). */
export type LanePhase = "idle" | "running" | "awaiting" | "done" | "error";
export type LaneActivityStage =
  | "starting"
  | "deciding"
  | "authoring"
  | "preparing"
  | "executing";

export interface LaneActivity {
  stage: LaneActivityStage;
  operation?: string | null;
}

export interface LaneError {
  lane: Lane | null;
  message: string;
}

export interface EscalationStats {
  count: number;
  upheld: number;
  overridden: number;
}

export interface RunSummary {
  run_id: string;
  session_id?: string;
  created_at: string | null;
  goal: string;
  compare: boolean;
  live: boolean;
  demo: boolean;
  finished: boolean;
  error: boolean;
}

export type RunEvent =
  | { type: "meta"; params: Record<string, unknown>; created_at: string }
  | { type: "sandbox_ready"; image_id: string; lanes: Lane[] }
  | { type: "attempt_started"; lane?: Lane; attempt_id: string; step: number }
  | { type: "jev_request"; lane?: Lane; attempt_id: string; questions: Record<string, unknown> }
  | { type: "jev_response"; lane?: Lane; attempt_id: string; response: Record<string, unknown> }
  | { type: "llm_started"; lane?: Lane; attempt_id: string; kind: string; operation: string; reason?: string }
  | { type: "llm_completed"; lane?: Lane; attempt_id: string; kind: string; operation: string; reason?: string; status: "returned" | "failed" }
  | { type: "decision_ready"; lane?: Lane; attempt_id: string; operation: string; needs_authoring: boolean; binding_mode?: string | null; escalated?: boolean }
  | { type: "intent"; lane?: Lane; operation: string; target?: string | null }
  | { type: "dispatch_started"; lane?: Lane; operation: string }
  | { type: "observation"; lane?: Lane; operation?: string | null }
  | { type: "unknown_acknowledged"; lane?: Lane; count: number; policy: string }
  | { type: "step"; lane?: Lane; step: Step }
  | { type: "metrics"; lane?: Lane; metrics: Metrics }
  | { type: "awaiting_continue"; lane?: Lane; step_index: number }
  | { type: "final"; lane?: Lane; final: { answer: string | null }; metrics: Metrics }
  | { type: "error"; lane?: Lane; message: string }
  | { type: "done"; lane?: Lane };

export interface LaneState {
  /** Ephemeral observation of this attempt, not another transcript. */
  decisionFrame?: DecisionFrame;
  steps: Step[];
  metrics: Metrics | null;
  awaiting: boolean;
  answer: string | null;
  /** authoritative final event received for this lane */
  finished: boolean;
  activity: LaneActivity | null;
  unknownAcknowledgements: number;
}

export interface DecisionFrame {
  llm?: { kind: string; operation: string; reason?: string; status: "running" | "returned" | "failed" };
  committed?: boolean;
  attemptId: string;
  step: number;
  questions?: Record<string, unknown>;
  response?: Record<string, unknown>;
  finalOperation?: string;
  finalBinding?: string | null;
  needsAuthoring?: boolean;
  escalated?: boolean;
}

export interface RunParams {
  goal: string;
  session_id?: string;
  profile: RunProfile;
  step_pause: boolean;
  escalate_threshold: number;
  ambiguity_gate: number | null;
  answer_progress_floor: number | null;
  max_steps: number;
  max_writes: number;
  sandbox_network: boolean;
  min_confidence: number;
  allow_recipients: string[];
}
