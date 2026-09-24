import { memo, useId } from "react";
import { modelName } from "../diagramModel";
import { useLang } from "../i18n";
import type { DecisionProvider, LaneState, Step } from "../types";
import { loopView, type LoopNode } from "../loopView";
import { DecisionFlow } from "./DecisionFlow";

type View = ReturnType<typeof loopView>;
const nodes: { id: LoopNode; x: number; y: number; en: string; zh: string; hint: string; tone: string }[] = [
  { id: "state", x: 230, y: 45, en: "Own transcript", zh: "独立 transcript", hint: "RESTORE LLM CONTEXT", tone: "neutral" },
  { id: "decision", x: 230, y: 139, en: "Jev-led routing", zh: "Jev 决策路由", hint: "ROUTE / OPTIONAL REVIEW", tone: "jev" },
  { id: "binding", x: 114, y: 239, en: "LLM-free route", zh: "无 LLM 路径", hint: "NO LLM CALL", tone: "jev" },
  { id: "authoring", x: 346, y: 239, en: "LLM assistance", zh: "LLM 辅助", hint: "PARAMETERS / REVIEW", tone: "llm" },
  { id: "kernel", x: 230, y: 339, en: "Execution kernel", zh: "执行内核", hint: "VALIDATE / DISPATCH", tone: "tool" },
  { id: "evidence", x: 230, y: 439, en: "Recorded evidence", zh: "记录执行证据", hint: "APPEND TO TRANSCRIPT", tone: "evidence" },
];

const Circuit = memo(function Circuit({ view, baseline, id, model }: { view: View; baseline: boolean; id: string; model: DecisionProvider }) {
  const { lang } = useLang();
  const name = modelName(model);
  const nodeName = (key: LoopNode) => baseline && key === "decision" ? (lang === "zh" ? "LLM 决策" : "LLM decision") : key === "decision" ? (lang === "zh" ? `${name} 决策路由` : `${name}-led routing`) : nodes.find((node) => node.id === key)?.[lang];
  const edges: { from: LoopNode; to: LoopNode; d: string }[] = [
    { from: "state", to: "decision", d: "M230 74 V110" },
    ...(baseline ? [{ from: "decision" as const, to: "kernel" as const, d: "M230 168 V310" }] : [
      { from: "decision" as const, to: "binding" as const, d: "M230 168 C230 193 114 185 114 210" },
      { from: "decision" as const, to: "authoring" as const, d: "M230 168 C230 193 346 185 346 210" },
      { from: "binding" as const, to: "kernel" as const, d: "M114 268 C114 293 230 285 230 310" },
      { from: "authoring" as const, to: "kernel" as const, d: "M346 268 C346 293 230 285 230 310" },
    ]),
    { from: "kernel", to: "evidence", d: "M230 368 V410" },
    { from: "evidence", to: "state", d: "M148 439 H32 Q20 439 20 427 V57 Q20 45 32 45 H148" },
  ];
  return <svg viewBox="0 0 460 494" className="loop-circuit" role="img"
    aria-labelledby={`${id}-title ${id}-description`}>
    <title id={`${id}-title`}>{lang === "zh" ? (baseline ? "LLM 基线执行流程" : `${name} 与 LLM 协作执行流程`) : baseline ? "LLM-only execution flow" : `${name} and LLM execution flow`}</title>
    <desc id={`${id}-description`}>{lang === "zh" ? "活动节点" : "Active node"}: {view.active ? nodeName(view.active) : "—"}. {lang === "zh" ? "记录路径" : "Recorded path"}: {view.route.map(nodeName).join(" → ") || "—"}.</desc>
    <defs>
      <pattern id={`${id}-grid`} width="20" height="20" patternUnits="userSpaceOnUse">
        <circle cx="1" cy="1" r="0.65" fill="currentColor" opacity="0.17" />
      </pattern>
    </defs>
    <rect width="460" height="494" fill={`url(#${id}-grid)`} />
    <text x="30" y="490" className="circuit-caption">{lang === "zh" ? "证据持续沉淀 / 下一轮循环" : "EVIDENCE PERSISTS / NEXT ITERATION"}</text>
    {edges.map((edge) => {
      const known = view.route.includes(edge.from) && view.route.includes(edge.to);
      // Animate only an observed active stage, not a simulated end-to-end run.
      const moving = view.active === edge.to && (edge.to !== "kernel" || baseline);
      return <g key={`${edge.from}-${edge.to}`} className={`circuit-edge ${known ? "is-recorded" : ""} ${moving ? "is-moving" : ""}`}>
        <path d={edge.d} />
        {moving && <path d={edge.d} className="signal-packet" />}
      </g>;
    })}
    {baseline && <text x="244" y="239" className="circuit-caption">{lang === "zh" ? "决策 + 参数生成" : "DECIDE + GENERATE"}</text>}
    {nodes.filter((node) => !baseline || !["binding", "authoring"].includes(node.id)).map((node) => {
      const active = view.active === node.id;
      const recorded = view.route.includes(node.id);
      const isDecision = node.id === "decision";
      const tone = baseline && isDecision ? "llm" : node.tone;
      const title = baseline && isDecision ? (lang === "zh" ? "LLM 决策" : "LLM decision") : isDecision ? (lang === "zh" ? `${name} 决策路由` : `${name}-led routing`) : node[lang];
      return <g key={node.id} transform={`translate(${node.x - 82} ${node.y - 29})`}
        className={`circuit-node tone-${tone} ${active ? "is-active" : ""} ${recorded ? "is-recorded" : ""} ${node.id === "kernel" && view.blocked ? "is-blocked" : ""}`}>
        <rect className="node-glow" x="-3" y="-3" width="170" height="64" rx="8" />
        <rect className="node-surface" width="164" height="58" rx="5" />
        <path className="node-corner" d="M0 13 V5 Q0 0 5 0 H20 M144 58 H159 Q164 58 164 53 V45" />
        <circle className="node-indicator" cx="14" cy="19" r="3" />
        <text x="25" y="23" className="node-title">{title}</text>
        <text x="13" y="43" className="node-hint">{baseline && isDecision ? "TOOL + ARGUMENTS" : node.hint}</text>
        <circle cx="82" cy="58" r="3" className="node-port" />
      </g>;
    })}
  </svg>;
}, (a, b) => a.baseline === b.baseline && a.id === b.id && a.model === b.model
  && a.view.status === b.view.status && a.view.active === b.view.active
  && a.view.blocked === b.view.blocked && a.view.route.join() === b.view.route.join());

function LoopDiagramView({ state, baseline = false, live, connected, done, error, step, sceneKey = "default", model = "jev" }: {
  state?: LaneState; baseline?: boolean; live: boolean; connected: boolean;
  done: boolean; error: boolean; step?: Step;
  sceneKey?: string; model?: DecisionProvider;
}) {
  const id = useId().replaceAll(":", "");
  const view = loopView(state, { baseline, live, connected, done, error, step });
  const frame = !step ? state?.decisionFrame : undefined;
  const frameOperation = frame?.finalOperation ?? (typeof frame?.response?.operation === "string" ? frame.response.operation : undefined);
  const { lang } = useLang();
  const name = modelName(model);
  const statuses: Record<View["status"], [string, string]> = {
    ready: ["READY", "就绪"], recorded: ["RECORDED", "历史记录"], live: ["LIVE", "实时"],
    waiting: ["WAITING", "等待事件"], paused: ["PAUSED", "已暂停"], error: ["ERROR", "出错"],
    ended: ["ENDED", "已结束"], disconnected: ["RECONNECTING", "重连中"],
  };
  return <section className={`diagram-lane ${baseline ? "baseline-lane" : "jev-lane"}`} data-testid={`loop-${baseline ? "baseline" : "jev"}`}>
    <div className="diagram-lane-heading">
      <div><span className="eyebrow">{baseline ? "LLM ONLY" : `${name.toUpperCase()} + LLM`}</span><h3>{baseline ? (lang === "zh" ? "基线循环" : "Baseline loop") : `${name}Loop`}</h3></div>
      <span className={`console-status status-${view.status}`}><i />{statuses[view.status][lang === "zh" ? 1 : 0]}</span>
    </div>
    {baseline ? <Circuit view={view} baseline={baseline} id={id} model={model} /> : <DecisionFlow sceneKey={sceneKey} view={view}
      frame={frame} step={view.step} model={model} />}
    <div className="lane-footnote"><span className="signal-dot" />{frame ? frameOperation ?? (lang === "zh" ? "本轮等待选择" : "Awaiting this attempt’s choice") : view.step?.decision.operation ?? (lang === "zh" ? "等待首个决策" : "Awaiting first decision")}</div>
  </section>;
}

// A lane untouched by a stream event keeps its state reference; skip its render.
export const LoopDiagram = memo(LoopDiagramView);
