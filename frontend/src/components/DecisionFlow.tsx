import { useEffect, useId, useRef, useState } from "react";
import { candidateView, observedRoute, type CandidateQuestion } from "../candidateView";
import { describeQuestion, describeOption, type OriginalRoute } from "../candidateSemantics";
import { horizontalCurve, flowLayout, contextWires, NODE_WIDTH, NODE_HALF_HEIGHT, OPTION_HALF_WIDTH, type Point, type BranchOption } from "../flowLayout";
import type { DecisionFrame, Step } from "../types";
import type { loopView } from "../loopView";
import { useLang } from "../i18n";

type View = ReturnType<typeof loopView>;
const short = (text: string, length = 17) => Array.from(text).length > length ? `${Array.from(text).slice(0, length - 1).join("")}…` : text;

function Wire({ d, tone = "neutral", selected = false, moving = false, dashed = false }: {
  d: string; tone?: string; selected?: boolean; moving?: boolean; dashed?: boolean;
}) {
  return <g className={`flow-wire tone-${tone} ${selected ? "is-selected" : ""} ${moving ? "is-moving" : ""} ${dashed ? "is-dashed" : ""}`}>
    <path d={d} /><path d={d} className="flow-packet" />
  </g>;
}

function Node({ at, title, hint, tone, active = false, observed = false, width = NODE_WIDTH }: {
  at: Point; title: string; hint: string; tone: string; active?: boolean; observed?: boolean; width?: number;
}) {
  return <g transform={`translate(${at.x} ${at.y})`} className={`flow-svg-node tone-${tone} ${active ? "is-active" : ""} ${observed ? "is-observed" : ""}`}>
    <title>{title} · {hint}</title>
    <g className="flow-node-visual">
      <rect className="flow-halo" x={-width / 2 - 3} y={-NODE_HALF_HEIGHT - 3} width={width + 6} height={NODE_HALF_HEIGHT * 2 + 6} rx="13" />
      <rect className="flow-node-body" x={-width / 2} y={-NODE_HALF_HEIGHT} width={width} height={NODE_HALF_HEIGHT * 2} rx="9" />
      <text textAnchor="middle" y="-3" className="flow-title">{title}</text>
      <text textAnchor="middle" y="16" className="flow-hint">{hint}</text>
    </g>
    <circle cx={-width / 2} r="2.5" className="flow-port" />
    <circle cx={width / 2} r="2.5" className="flow-port" />
  </g>;
}

function Fan({ question, start, end, options, label, onOption, pending, original, focusLevel, returned }: {
  question: CandidateQuestion; start: Point; end: Point; options: BranchOption[]; label: Point;
  onOption: (option: BranchOption) => void; pending: boolean;
  original: OriginalRoute; focusLevel: number; returned: boolean;
}) {
  const { lang } = useLang();
  const zh = lang === "zh";
  const semantics = describeQuestion(question, original, zh);
  const muted = (focusLevel > 0 && focusLevel < 4 && focusLevel !== semantics.level) || (focusLevel === 4 && !question.consumed)
    || (focusLevel === 0 && returned && !question.consumed);
  const title = semantics.subject ? `${zh ? "③ 纳入" : "③ Include"} ${semantics.subject}?` : semantics.title;
  const optionLabel = (option: BranchOption) => option.key === "LLM_PARAMETERS" ? (zh ? "LLM 补全参数" : "LLM parameters")
    : option.key === "DEFAULT_ARGUMENTS" ? (zh ? "展示的默认参数" : "Default arguments")
      : semantics.role === "phase" ? option.key
        : semantics.role === "target_mode" || semantics.role === "target_member" ? `${option.key} · ${describeOption(option.key, semantics.role, zh).split(" · ")[0]}` : option.key;
  return <g className={`flow-fan fan-level-${semantics.level} ${muted ? "is-muted" : ""}`}>
    <rect className="fan-panel" x={start.x - 2} y={label.y - 23} width={end.x - start.x + 4}
      height={Math.max(42, (options.at(-1)?.y ?? label.y) - label.y + 39)} rx="7" />
    <text x={label.x} y={label.y - 9} textAnchor="middle" className="flow-fan-label"><title>{`${semantics.title}\n${semantics.explanation}\n${question.key}`}</title>{short(title, 26)}</text>
    <text x={label.x} y={label.y + 5} textAnchor="middle" className="flow-fan-condition"><title>{semantics.condition}</title>{short(semantics.condition, 32)}</text>
    {!options.length && <Wire d={horizontalCurve(start, end)} dashed />}
    {options.map((option) => <g key={option.key}>
      <Wire d={horizontalCurve(start, { x: option.x - OPTION_HALF_WIDTH, y: option.y })} tone="jev" selected={option.selected} />
      <Wire d={horizontalCurve({ x: option.x + OPTION_HALF_WIDTH, y: option.y }, end)} tone="jev" selected={option.selected} />
    </g>)}
    <circle cx={start.x} cy={start.y} r="3" className="flow-junction" />
    <circle cx={end.x} cy={end.y} r="3" className={`flow-junction ${question.selected ? "is-selected" : ""}`} />
    {options.map((option) => <g key={option.key} transform={`translate(${option.x} ${option.y})`}
      className={`flow-option ${option.selected ? "is-selected" : ""} ${option.hidden ? "is-more" : ""}`}
      role="button" tabIndex={0} aria-label={`${semantics.condition}: ${option.key} · ${describeOption(option.key, semantics.role, zh)}${option.selected ? (zh ? "，已选中" : ", selected") : ""}`}
      onClick={() => onOption(option)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOption(option); } }}>
      <title>{`${option.key}\n${describeOption(option.key, semantics.role, zh)}\n${semantics.condition}\n${option.detail}`}</title>
      <rect className="option-halo" x="-85" y="-16" width="170" height="32" rx="16" />
      <rect className="option-body" x={-OPTION_HALF_WIDTH} y="-13" width={OPTION_HALF_WIDTH * 2} height="26" rx="13" />
      <svg x="-75" y="-12" width="122" height="24"><text x="0" y="17" className="option-title">{short(optionLabel(option), 17)}</text></svg>
      <text x="76" textAnchor="end" y="5" className="option-score">{question.deterministic ? (zh ? "固" : "FIX") : option.probability != null ? `${Math.round(option.probability * 100)}` : pending ? "" : "—"}{option.selected ? "✓" : ""}</text>
    </g>)}
  </g>;
}

/** One coordinate system owns the loop: no CSS-sized gaps between wires. */
export function DecisionFlow({ view, frame, step, sceneKey = "default", initialViewportWidth = 0 }: { view: View; frame?: DecisionFrame; step?: Step; sceneKey?: string; initialViewportWidth?: number }) {
  const { lang } = useLang();
  const id = useId().replaceAll(":", "");
  const [overview, setOverview] = useState(false);
  const [focusLevel, setFocusLevel] = useState(0);
  const viewport = useRef<HTMLDivElement>(null);
  // Server-rendered documentation can select the same responsive layout before
  // ResizeObserver exists. Interactive callers still measure their actual pane.
  const [availableWidth, setAvailableWidth] = useState(initialViewportWidth);
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setAvailableWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const source = frame?.attemptId ?? step;
  const [detail, setDetail] = useState<{ key: string; detail: string; selected: boolean; source: typeof source } | null>(null);
  const choices = candidateView(frame ? undefined : step, frame);
  const original = { phase: choices.originalPhase, operation: choices.originalOperation };
  const pending = !!frame && !frame.response;
  const questions = choices.submitted;
  const graph = flowLayout(questions, availableWidth);
  // Candidate growth may extend the scroll area; subsequent small pools do not
  // collapse it mid-session. A different session/viewport gets its own reserve.
  const reserve = useRef({ sceneKey, width: graph.width, height: graph.height });
  if (reserve.current.sceneKey !== sceneKey || reserve.current.width !== graph.width) {
    reserve.current = { sceneKey, width: graph.width, height: graph.height };
  } else reserve.current.height = Math.max(reserve.current.height, graph.height);
  graph.height = reserve.current.height;
  const contextPaths = contextWires(graph);
  const { transcript: t, jevProjection: jp, llmProjection: lp, jev: j, llm: l, commit: c, kernel: k, record: r, exit: e } = graph;
  const moving = view.status === "live";
  const jevActive = moving && view.active === "decision" && !frame?.response;
  const llmActive = moving && view.active === "authoring";
  const { assisted, direct } = frame ? observedRoute(frame) : view;
  const returned = !!frame?.response || !!choices.originalOperation;
  const committed = frame ? !!frame.committed : !!step?.intent_id && step.decision.operation !== "BLOCKED";
  const recorded = !frame && !!step?.outcome && !["RUN_LIMIT", "RESTORE"].includes(step?.decision.operation ?? "");
  const llmKind = frame?.llm?.kind ?? step?.model_calls?.find((call) => call.kind !== "jev_decision")?.kind;
  const llmLabel = llmKind === "arbitration" ? (lang === "zh" ? "LLM 复核 / 兜底" : "LLM review / fallback") : (lang === "zh" ? "LLM 补参 / 生成" : "LLM authoring");
  const changed = choices.originalOperation !== choices.finalOperation && choices.finalOperation != null;
  const emptyLabel = pending ? (lang === "zh" ? "等待本轮候选" : "Awaiting this request") : (lang === "zh" ? "未记录候选" : "No candidate record");
  return <div className={`decision-flow ${moving && frame?.response ? "has-live-response" : ""}`} data-testid="decision-flow">
    <div className="flow-toolbar"><span>{frame ? `STEP ${String(frame.step).padStart(2, "0")} · ${pending ? "REQUEST" : "RESPONSE"}` : (lang === "zh" ? "已记录选择路径" : "RECORDED PATH")}</span>
      <span>{questions.length} {lang === "zh" ? "组分支" : "HEADS"} · {questions.reduce((sum, question) => sum + question.options.length, 0)} {lang === "zh" ? "项候选" : "OPTIONS"}</span>
      <button className="flow-zoom" aria-pressed={overview} onClick={() => setOverview((before) => !before)}>{overview ? (lang === "zh" ? "恢复可读字号" : "Readable size") : (lang === "zh" ? "全图概览" : "Fit overview")}</button>
    </div>
    <div className="choice-explainer" aria-label={lang === "zh" ? "Jev 选择的逻辑层级" : "Jev decision hierarchy"}>
      {[
        { level: 1, label: lang === "zh" ? "目的：下一步做什么" : "Purpose: what next?", value: choices.originalPhase ? describeOption(choices.originalPhase, "phase", lang === "zh") : (lang === "zh" ? "等待目的选择" : "Awaiting purpose") },
        { level: 2, label: lang === "zh" ? "工具：用哪个操作" : "Tool: which operation?", value: choices.originalOperation ?? (lang === "zh" ? "取决于所选目的" : "Depends on purpose") },
        { level: 3, label: lang === "zh" ? "参数：怎样调用工具" : "Arguments: how to call it?", value: choices.originalBinding === "llm_parameters" ? (lang === "zh" ? "LLM 补全全部参数，不换工具" : "LLM authors all arguments, same tool") : choices.originalBinding === "defaults" ? (lang === "zh" ? "使用固定默认参数" : "Use fixed defaults") : choices.originalBinding ? (lang === "zh" ? "使用所选完整参数" : "Use selected complete arguments") : (lang === "zh" ? "取决于所选工具" : "Depends on the selected tool") },
      ].map((item) => <button className={`choice-level level-${item.level}`} key={item.level} aria-pressed={focusLevel === item.level}
        onClick={() => setFocusLevel(focusLevel === item.level ? 0 : item.level)} title={lang === "zh" ? "点击强调该层级的候选" : "Emphasize candidates at this level"}>
        <span>{item.level} · {item.label}</span><strong title={item.value}>{item.value}</strong></button>)}
    </div>
    <div className="choice-legend"><span>{lang === "zh" ? "一次并行请求，按 ①→②→③ 消费路径；不是三次模型调用。灰色分支本轮未生效。" : "One parallel request; consume ① → ② → ③, not three model calls. Dim branches are unused."}</span>
      <button className="console-text-button" onClick={() => setFocusLevel(0)} aria-pressed={focusLevel === 0}>{lang === "zh" ? "全部层级" : "All levels"}</button>
      <button className="console-text-button" onClick={() => setFocusLevel(focusLevel === 4 ? 0 : 4)} disabled={!returned} aria-pressed={focusLevel === 4}>{lang === "zh" ? "强调生效路径" : "Emphasize consumed path"}</button>
    </div>
    <div className={`flow-handoff ${llmActive ? "is-active" : ""}`} aria-live="polite">
      <span>Jev</span><strong>{choices.originalOperation ?? (lang === "zh" ? "等待选择" : "Awaiting choice")}</strong>
      {assisted ? <><b aria-hidden="true">→</b><span className="handoff-llm">{llmLabel}</span><small>{frame?.llm?.status === "running" ? (lang === "zh" ? "正在调用" : "IN FLIGHT") : frame?.llm?.status === "returned" ? (lang === "zh" ? "已返回，待校验" : "RETURNED / VALIDATE") : frame?.llm?.status === "failed" ? (lang === "zh" ? "调用失败" : "CALL FAILED") : frame ? (lang === "zh" ? "等待调用" : "AWAITING CALL") : (lang === "zh" ? "历史记录" : "RECORDED")}</small></>
        : direct ? <><b aria-hidden="true">→</b><span>{lang === "zh" ? "直通执行" : "DIRECT EXECUTION"}</span></> : null}
    </div>
    <div className="flow-canvas-hint">{overview ? (lang === "zh" ? "概览模式 · 可返回可读字号查看细节" : "Overview · return to readable size for details") : (lang === "zh" ? "可读字号 · 横向滚动查看完整流程 · 点击候选查看参数" : "Readable size · scroll horizontally for the full loop · select a candidate for arguments")}</div>
    <div ref={viewport} className="flow-svg-viewport" tabIndex={0} aria-label={lang === "zh" ? "横向流程画布，可滚动" : "Horizontal flow canvas, scrollable"}>
      <svg viewBox={`0 0 ${graph.width} ${graph.height}`} style={{ minWidth: overview ? 0 : graph.width * .9, maxWidth: graph.width, height: "auto", maxHeight: "none", aspectRatio: `${graph.width} / ${graph.height}` }} className="decision-circuit" role="group" aria-labelledby={`${id}-title ${id}-desc`}>
        <title id={`${id}-title`}>{lang === "zh" ? "Jev 候选分支与 transcript 闭环" : "Jev candidate branches and transcript loop"}</title>
        <desc id={`${id}-desc`}>{lang === "zh" ? "每次模型调用从同一 transcript 独立恢复。选中候选与线路高亮。已接受进展在工具执行前写入，执行结果再次追加。原始概率是观测日志，不是 transcript 记录。" : "Each model call restores its own context from one transcript. Selected candidates and wires are highlighted. Accepted progress commits before dispatch; results append again. Raw probabilities are telemetry, not transcript records."}</desc>
        <defs><pattern id={`${id}-grid`} width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".7" fill="currentColor" opacity=".15" /></pattern></defs>
        <rect width={graph.width} height={graph.height} fill={`url(#${id}-grid)`} />
        <Wire d={`M${t.x - 160} ${t.y} H${jp.x} V${jp.y - NODE_HALF_HEIGHT}`} tone="jev" selected={returned} moving={jevActive} />
        <Wire d={contextPaths.transcriptToLlm} tone="llm" selected={!!assisted} moving={llmActive} />
        <Wire d={horizontalCurve({ x: jp.x + NODE_WIDTH / 2, y: jp.y }, { x: j.x - 27, y: j.y })} tone="jev" selected={returned} moving={jevActive} />
        <Wire d={contextPaths.contextToLlm} tone="llm" selected={!!assisted} moving={llmActive} />
        <text x={lp.x} y={lp.y - 43} textAnchor="middle" className="flow-annotation">{lang === "zh" ? "独立恢复 LLM 上下文" : "RESTORE LLM CONTEXT"}</text>
        {graph.groups.map((group) => <g key={`wires-${group.question.key}`}>
          <Wire d={`M${j.x + 27} ${j.y} H302 V${graph.requestBusY} H${group.start.x} V${group.start.y}`} tone="jev" selected={returned && group.question.consumed} moving={jevActive} />
          {group.question.consumed && <Wire d={`M${group.end.x} ${group.end.y} H${group.end.x + 8} V${graph.selectionBusY} H${graph.poolRight + 18} V${e.y} H${e.x - NODE_WIDTH / 2}`} tone="jev" selected />}
        </g>)}
        {graph.groups.map((group) => <Fan key={group.question.key} {...group} pending={pending} original={original} focusLevel={focusLevel} returned={returned}
          onOption={(option) => {
            const meaning = describeQuestion(group.question, original, lang === "zh");
            setDetail({ source, key: option.key, detail: `${meaning.title}\n${meaning.condition}\n${meaning.explanation}\n${describeOption(option.key, meaning.role, lang === "zh")}\n\n${option.detail}`, selected: option.selected });
          }} />)}
        {!questions.length && <text x={graph.poolLeft + 100} y="290" className="flow-annotation">{emptyLabel}</text>}
        <Wire d={horizontalCurve({ x: e.x + NODE_WIDTH / 2, y: e.y }, c)} tone="jev" selected={direct} moving={moving && view.active === "kernel" && direct} />
        <Wire d={`M${e.x} ${e.y + NODE_HALF_HEIGHT} V${l.y - NODE_HALF_HEIGHT}`} tone="llm" selected={!!assisted} moving={llmActive} />
        <Wire d={`M${l.x + NODE_WIDTH / 2} ${l.y} H${c.x} V${c.y}`} tone="llm" selected={!!assisted} moving={moving && view.active === "kernel" && !!assisted} />
        <text x={c.x - 24} y={c.y - 15} textAnchor="end" className={`flow-annotation ${direct ? "accent-jev" : ""}`}>{lang === "zh" ? "直通" : "DIRECT"}</text>
        <text x={l.x - 14} y={l.y - 64} textAnchor="end" className="flow-route-label">{llmKind === "arbitration" ? (lang === "zh" ? "交接 → 复核" : "HANDOFF → REVIEW") : (lang === "zh" ? "交接 → 补参" : "HANDOFF → PARAMETERS")}</text>
        <Wire d={horizontalCurve(c, { x: k.x - NODE_WIDTH / 2, y: k.y })} tone="tool" selected={!!choices.finalOperation} moving={moving && view.active === "kernel"} />
        <Wire d={`M${k.x} ${k.y + NODE_HALF_HEIGHT} V${r.y - 22}`} tone="evidence" selected={recorded} />
        <Wire d={contextPaths.acceptedProgress} tone="evidence" selected={committed} dashed />
        <text x={c.x - 16} y="28" textAnchor="end" className="flow-annotation accent-evidence">{lang === "zh" ? "接受进展 → 写回" : "ACCEPTED PROGRESS → APPEND"}</text>
        <Wire d={`M${r.x} ${r.y + 22} V${graph.height - 24} H16 V${t.y} H${t.x - 160}`} tone="evidence" selected={recorded} />
        <text x={graph.width / 2} y={graph.height - 34} textAnchor="middle" className="flow-annotation accent-evidence">{lang === "zh" ? "执行 / 拒绝证据 → 追加至 transcript → 下一轮恢复" : "EXECUTION / REFUSAL EVIDENCE → APPEND TO TRANSCRIPT → NEXT ITERATION"}</text>
        <Node at={t} title="Transcript" hint={lang === "zh" ? "唯一持久记录 · 每次调用独立恢复" : "ONE LEDGER · RESTORE EACH CALL"} tone="evidence" width={320} observed />
        <Node at={jp} title={lang === "zh" ? "Jev 上下文" : "Jev context"} hint={lang === "zh" ? "动态状态 + 候选" : "STATE + CANDIDATES"} tone="jev" active={jevActive} />
        <Node at={lp} title={lang === "zh" ? "LLM 上下文" : "LLM context"} hint={lang === "zh" ? "稳定消息 + schema" : "MESSAGES + SCHEMAS"} tone="llm" active={llmActive} />
        <g transform={`translate(${j.x} ${j.y})`} className={`jev-orbit ${jevActive ? "is-active" : ""}`}><circle r="33" className="orbit-ring" /><circle r="27" className="orbit-core" /><text textAnchor="middle" y="5">Jev</text></g>
        <text x={graph.poolLeft + 20} y={graph.requestBusY - 18} className="flow-annotation">{lang === "zh" ? "一次请求 · 完整候选池 · 高亮已消费路径" : "ONE REQUEST · FULL POOL · CONSUMED PATH"}</text>
        <Node at={e} title={short(choices.originalOperation ?? (lang === "zh" ? "等待 Jev" : "Awaiting Jev"), 19)} hint={lang === "zh" ? "Jev 原始选择" : "JEV ORIGINAL PICK"} tone="jev" observed={returned} />
        <Node at={l} title={llmLabel} hint={llmActive ? (lang === "zh" ? "正在调用" : "CALL IN FLIGHT") : (lang === "zh" ? "独立恢复上下文" : "RESTORED CONTEXT")} tone="llm" active={llmActive} observed={!!assisted} />
        <circle cx={c.x} cy={c.y} r="5" className="commit-junction" /><text x={c.x} y={c.y + 24} textAnchor="middle" className="flow-annotation">{lang === "zh" ? "接受 / 记录" : "ACCEPT / RECORD"}</text>
        <Node at={k} title={short(choices.finalOperation ?? (frame ? "…" : step?.decision.operation ?? "Execution"), 19)} hint={changed ? (lang === "zh" ? "复核后 · 校验执行" : "REVIEWED · EXECUTE") : "VALIDATE / EXECUTE"} tone={!frame && view.blocked ? "critical" : "tool"} active={moving && view.active === "kernel"} />
        <g transform={`translate(${r.x} ${r.y})`} className="record-capsule"><rect x="-108" y="-22" width="216" height="44" rx="22" /><text textAnchor="middle" y="5">{lang === "zh" ? "记录执行 / 拒绝证据" : "Record evidence"}</text></g>
      </svg>
    </div>
    {detail && detail.source === source && <div className="flow-detail" role="region" aria-label={lang === "zh" ? "候选详情" : "Candidate detail"}><div><strong>{detail.selected ? "✓ " : ""}{detail.key}</strong><button onClick={() => setDetail(null)} aria-label={lang === "zh" ? "关闭候选详情" : "Close candidate detail"}>×</button></div><pre>{detail.detail}</pre></div>}
    <p className="flow-caption">{lang === "zh" ? "数值为概率百分比；固 = 规则唯一确定，未单独询问模型。one/many 是单项/批量参数；include/skip 仅决定批量成员。选中不代表已执行。" : "Scores are percent; FIX = rule-defined, no separate model choice. one/many = single/batch arguments; include/skip = batch membership. Selected does not mean executed."}</p>
  </div>;
}
