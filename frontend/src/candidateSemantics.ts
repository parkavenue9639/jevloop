import type { CandidateQuestion } from "./candidateView";

export interface OriginalRoute {
  phase: string | null;
  operation: string | null;
}

export interface QuestionSemantics {
  level: 1 | 2 | 3;
  role: string;
  title: string;
  condition: string;
  explanation: string;
  phase: string | null;
  operation: string | null;
  subject: string | null;
  /** Recorded use, not a predicted/argmax branch. */
  active: boolean;
}

const phases = new Set(["INSPECT", "ACT", "VERIFY", "RESPOND"]);
const roles = new Set(["phase", "action", "target", "target_mode", "target_member", "target_fallback"]);

/** Membership head suffixes are compiler indices, NOT target identities. */
function memberSubject(question: CandidateQuestion): string | null {
  const detail = question.options.find((option) => option.key === "include")?.detail;
  if (!detail) return null;
  const match = /^Include offered target (.+)\.$/.exec(detail);
  if (!match) return detail;
  const quoted = match[1];
  return /^(['"]).*\1$/.test(quoted) ? quoted.slice(1, -1) : quoted;
}

/** Decode only compiler-owned head structure. A branch being offered does not
 * mean it was selected. Legacy names retain unknown ownership, and constants
 * use only the caller's ORIGINAL Jev route, never an arbitration override. */
export function describeQuestion(
  question: CandidateQuestion, original: OriginalRoute, zh: boolean,
): QuestionSemantics {
  const parts = question.key.split("__");
  const headRole = parts[0] === "include" ? "target_member" : parts[0];
  const parsed = roles.has(headRole) && (headRole === "phase" ? parts.length === 1
    : headRole === "action" ? parts.length === 2
    : headRole === "target_member" ? parts.length === 4 : parts.length === 3);
  const role = roles.has(question.role) ? question.role : parsed ? headRole : "unknown";
  let phase: string | null = parsed && phases.has(parts[1]?.toUpperCase()) ? parts[1].toUpperCase() : null;
  let operation: string | null = parsed && phase && parts.length >= 3 ? parts[2].toUpperCase() : null;
  if (question.deterministic && question.consumed) {
    if (role !== "phase") phase ??= original.phase;
    if (!["phase", "action"].includes(role)) operation ??= original.operation;
  }
  const level = role === "phase" ? 1 : role === "action" ? 2 : 3;
  const subject = role === "target_member" ? memberSubject(question) : null;
  const titles: Record<string, [string, string]> = {
    phase: ["① 目的 · 下一步做什么", "① Purpose · what comes next"],
    action: ["② 工具 · 如何执行", "② Tool · how to act"],
    target: ["③ 参数 · 从哪里获得", "③ Arguments · where from"],
    target_fallback: ["③ 参数 · 多选为空后的回退", "③ Arguments · empty-batch fallback"],
    target_mode: ["③ 参数模式 · 单项或批量", "③ Binding mode · single or batch"],
    target_member: ["③ 批量成员 · 是否纳入", "③ Batch member · include or skip"],
  };
  const explanations: Record<string, [string, string]> = {
    phase: ["先选检查、执行、验证或回答的目的；这不是工具调用。", "Choose inspect, act, verify or respond; this is not a tool call."],
    action: ["在该目的下选一个工具；参数由下一层决定。", "Choose one tool for this purpose; the next layer supplies arguments."],
    target: ["选可执行的已知参数、默认参数，或交给 LLM 生成完整参数。", "Choose offered executable arguments, defaults, or LLM-authored complete arguments."],
    target_fallback: ["批量成员全部跳过时，使用单项参数选择结果。", "If every batch member is skipped, use the single-binding result."],
    target_mode: ["one 使用单项参数选择；many 进一步检查各候选是否纳入同一次调用。", "one uses the single binding; many checks membership for one bounded call."],
    target_member: ["仅决定这个候选是否加入批量参数；不代表又选了一个工具。", "Decide whether this candidate joins the batch, not another tool selection."],
  };
  const index = zh ? 0 : 1;
  let condition = role === "phase" ? (zh ? "本轮根选择" : "Root choice")
    : phase ? `${zh ? "仅当" : "Only if"} ${phase}${operation ? ` → ${operation}` : ""}`
      : (zh ? "历史记录未提供所属分支" : "Owning branch absent from historical evidence");
  if (role === "target_member") condition += zh ? " · 批量模式 many" : " · batch mode many";
  if (role === "target_fallback") condition += zh ? " · many 且全部 skip" : " · many with all members skipped";
  return {
    level, role, phase, operation, subject, condition,
    title: titles[role]?.[index] ?? question.key,
    explanation: explanations[role]?.[index] ?? (zh ? "保留原始记录；未知选择含义。" : "Original evidence retained; choice semantics unknown."),
    active: question.consumed && question.selected !== null,
  };
}

/** Short option glosses. Keep original keys/details visible alongside these. */
export function describeOption(optionKey: string, role: string, zh: boolean): string {
  const index = zh ? 0 : 1;
  if (optionKey === "R" && role === "deterministic") return zh ? "规则确定 · 非模型概率" : "Rule-defined · not a model probability";
  const phaseLabels: Record<string, [string, string]> = {
    INSPECT: ["检查 · 获取证据", "Inspect · gather evidence"],
    ACT: ["执行 · 推进任务", "Act · advance the task"],
    VERIFY: ["验证 · 检查结果", "Verify · check outcomes"],
    RESPOND: ["回答 · 向用户交付", "Respond · report to the user"],
  };
  if (role === "phase") return phaseLabels[optionKey]?.[index] ?? optionKey;
  if (["target", "target_fallback", "target_member", "include"].includes(role)) {
    if (optionKey === "LLM_PARAMETERS") return zh ? "LLM 生成完整参数 · 不换工具" : "LLM authors all arguments · same tool";
    if (optionKey === "DEFAULT_ARGUMENTS") return zh ? "执行展示的默认参数" : "Use the displayed default arguments";
  }
  if (role === "target_mode") {
    if (optionKey === "one") return zh ? "单项 · 使用参数候选的选择" : "Single · use the binding choice";
    if (optionKey === "many") return zh ? "批量 · 逐项选择纳入成员" : "Batch · choose members individually";
  }
  if (role === "target_member" || role === "include") {
    if (optionKey === "include") return zh ? "纳入这次批量调用" : "Include in this batch call";
    if (optionKey === "skip") return zh ? "不纳入这次批量调用" : "Exclude from this batch call";
  }
  return optionKey;
}
