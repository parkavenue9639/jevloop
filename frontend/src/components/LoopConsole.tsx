import { useState } from "react";
import type { ChatApi } from "../chat";
import { diagramModel, modelName } from "../diagramModel";
import { useDecisionChoice } from "../decisionChoice";
import type { Lane, LaneState } from "../types";
import type { StreamData } from "../stream";
import { useLang } from "../i18n";
import { laneErrorMessage } from "../lane";
import { formatCost, formatTime, loopView } from "../loopView";
import { LoopDiagram } from "./LoopDiagram";
import { ActivityRow } from "./ActivityRow";
import { costComplete, costText } from "../cost";

function Meter({ title, jev, baseline, format, label, jevText, baselineText, comparable = true }: {
  title: string; jev?: number | null; baseline?: number | null; format: (value: number | null | undefined) => string; label: string;
  jevText?: string; baselineText?: string; comparable?: boolean;
}) {
  const max = Math.max(jev ?? 0, baseline ?? 0, Number.EPSILON);
  return <div className="console-meter">
    <div className="meter-title">{title}</div>
    {([[`${label}Loop`, jev, "jev"], ["LLM-only", baseline, "llm"]] as const).map(([name, value, tone]) => (
      <div className={`meter-row tone-${tone} ${comparable ? "" : "is-incomplete"}`} key={name}>
        <span>{name}</span><div className="meter-track">{comparable && <i style={{ width: `${((value ?? 0) / max) * 100}%` }} />}</div><strong>{(tone === "jev" ? jevText : baselineText) ?? format(value)}</strong>
      </div>
    ))}
  </div>;
}

function Telemetry({ state, decisionLabel }: { state?: LaneState; decisionLabel: string }) {
  const { lang } = useLang();
  const m = state?.metrics;
  const routing = m?.routing;
  const hit = m && m.helper.input_tokens > 0 ? m.helper.cache_hit_tokens / m.helper.input_tokens : null;
  return <div className="telemetry-grid">
    <div><span>{lang === "zh" ? `${decisionLabel} 调用` : `${decisionLabel} calls`}</span><strong>{m?.jev.calls ?? "—"}</strong></div>
    <div><span>{lang === "zh" ? "LLM 调用" : "LLM calls"}</span><strong>{m?.helper.calls ?? "—"}</strong></div>
    <div><span>{lang === "zh" ? "无 LLM 步骤" : "LLM-free steps"}</span><strong>{routing ? `${routing.direct_jev_steps}/${routing.decision_steps}` : "—"}</strong></div>
    {routing?.visual_steps != null && <div><span>{lang === "zh" ? "视觉 LLM 步骤" : "Vision LLM steps"}</span><strong>{routing.visual_steps}</strong></div>}
    <div><span>{lang === "zh" ? "输入缓存命中" : "Input cache hit"}</span><strong>{hit == null ? "—" : `${(hit * 100).toFixed(1)}%`}</strong></div>
  </div>;
}

function Inspector({ data, live, replaying, scope, sceneKey }: { data: StreamData; live: boolean; replaying: boolean; scope: string; sceneKey: string }) {
  const { lang } = useLang();
  const [selection, setSelection] = useState<{ scope: string; lane: Lane; index: number } | null>(null);
  const selected = !replaying && selection?.scope === scope ? selection : null;
  const setSelected = (value: { lane: Lane; index: number } | null) => setSelection(value ? { ...value, scope } : null);
  const [inspectOpen, setInspectOpen] = useState(false);
  const jev = data.lanes.jev;
  const base = data.lanes.baseline;
  const decision = diagramModel(data.params, useDecisionChoice().provider);
  const decisionLabel = modelName(decision);
  // Reserve the baseline lane from metadata, before its first event arrives.
  const paired = data.params?.profile === "paired_shadow" || !!base;
  const connected = replaying || data.connection === "connected";
  const selectedStep = selected ? data.lanes[selected.lane]?.steps[selected.index] : undefined;
  const lanes = paired ? (["jev", "baseline"] as const) : (["jev"] as const);
  const rows = lanes.flatMap((lane) => (data.lanes[lane]?.steps ?? []).map((step, index) => ({ lane, step, index }))).sort((a, b) => b.index - a.index || a.lane.localeCompare(b.lane));
  const metricScope = lang === "zh" ? "所选轮次 · 最新指标快照" : "SELECTED TURN · LATEST METRICS";
  const operation = selectedStep?.decision.operation ?? (jev?.decisionFrame
    ? jev.decisionFrame.finalOperation ?? (typeof jev.decisionFrame.response?.operation === "string" ? jev.decisionFrame.response.operation : undefined)
    : jev?.activity?.operation ?? jev?.steps.at(-1)?.decision.operation);
  return <>
    <div className="console-grid">
      <section className="console-panel topology-panel">
        <div className="panel-heading"><span className="eyebrow">{lang === "zh" ? "执行拓扑" : "EXECUTION TOPOLOGY"}</span><span className="topology-legend"><i className="tone-jev" />{decisionLabel}<i className="tone-llm" />LLM<i className="tone-tool" />{lang === "zh" ? "内核" : "Kernel"}</span></div>
        <div className="graph-step-picker"><label htmlFor="jev-iteration">{lang === "zh" ? `${decisionLabel} 循环步骤` : `${decisionLabel.toUpperCase()} ITERATION`}</label><select id="jev-iteration" disabled={replaying || !jev?.steps.length} aria-label={lang === "zh" ? `${decisionLabel} 循环步骤` : `${decisionLabel} iteration`}
          value={selected?.lane === "jev" ? selected.index : selected ? "baseline" : ""} onChange={(event) => { setSelected(event.target.value === "" ? null : { lane: "jev", index: Number(event.target.value) }); setInspectOpen(false); }}>
          <option value="">{lang === "zh" ? "跟随最新" : "Follow latest"}</option>
          {selected?.lane === "baseline" && <option value="baseline" disabled>{lang === "zh" ? "回看基线步骤" : "Inspecting baseline step"} {selected.index + 1}</option>}
          {jev?.steps.map((step, index) => <option value={index} key={index}>{String(index + 1).padStart(2, "0")} · {step.decision.operation}</option>)}
        </select>{selected && <button className="console-text-button" onClick={() => { setSelected(null); setInspectOpen(false); }}>{lang === "zh" ? "跟随最新" : "Follow latest"}</button>}</div>
        <div className={`diagram-grid ${paired ? "is-paired" : ""}`}>
          {lanes.map((lane) => <LoopDiagram key={lane} state={data.lanes[lane]} baseline={lane === "baseline"}
            model={decision}
            sceneKey={sceneKey}
            live={live && !selected} connected={connected} done={data.done} error={!!laneErrorMessage(lane, data)}
            step={selected?.lane === lane ? selectedStep : undefined} />)}
        </div>
        <div className="topology-note">{lang === "zh" ? "动效仅表示已收到的阶段事件；结束不等于任务验证通过。" : "Motion follows observed stages. An ended run is not a verified successful task."}</div>
      </section>

      <details className="console-panel telemetry-disclosure"><summary>{lang === "zh" ? "展开遥测与指标" : "Telemetry & metrics"}</summary><aside className="telemetry-panel">
        <div className="panel-heading"><span className="eyebrow">{lang === "zh" ? "双路遥测" : "RUN TELEMETRY"}</span><span className="telemetry-mark">{base ? "A / B" : "A"}</span></div>
        <div className="telemetry-intro"><span>{metricScope}</span><h3>{lang === "zh" ? "观察每一次决策。" : "Every decision, visible."}</h3></div>
        <Meter title={lang === "zh" ? "累计耗时" : "Elapsed time"} jev={jev?.metrics?.elapsed_ms} baseline={base?.metrics?.elapsed_ms} format={formatTime} label={decisionLabel} />
        <Meter title={lang === "zh" ? "预估成本 / USD" : "Estimated cost / USD"} jev={jev?.metrics?.est_cost_usd} baseline={base?.metrics?.est_cost_usd} format={formatCost} label={decisionLabel}
          comparable={costComplete(jev?.metrics) && costComplete(base?.metrics)}
          jevText={costText(jev?.metrics?.est_cost_usd, costComplete(jev?.metrics), lang === "zh", 5)}
          baselineText={costText(base?.metrics?.est_cost_usd, costComplete(base?.metrics), lang === "zh", 5)} />
        <Meter title={lang === "zh" ? "已记录步骤" : "Recorded steps"} jev={jev ? jev.steps.length : null} baseline={base ? base.steps.length : null} format={(n) => n == null ? "—" : String(n)} label={decisionLabel} />
        <div className="telemetry-label">JEVLOOP / {lang === "zh" ? "路由与缓存" : "ROUTING & CACHE"}</div>
        <Telemetry state={jev} decisionLabel={decisionLabel} />
        <div className="telemetry-caveat">{lang === "zh" ? "直通仅表示未调用 LLM，不代表工具成功。比较质量请查看两路输出。" : "LLM-free does not mean successful execution. Compare both outputs to judge quality."}</div>
      </aside></details>
    </div>

    <details className="console-panel decision-panel"><summary>{lang === "zh" ? "展开决策记录" : "Decision trace"} · {rows.length}</summary>
      <div className="panel-heading"><span className="eyebrow">{lang === "zh" ? "决策记录" : "DECISION TRACE"}</span><span className="trace-count">{rows.length} {lang === "zh" ? "条已记录步骤" : "recorded steps"}</span>
        {selected && <button className="console-text-button" onClick={() => { setSelected(null); setInspectOpen(false); }}>{lang === "zh" ? "回到最新状态" : "Follow latest"}</button>}</div>
      <div className="trace-layout">
        <div className="trace-list" role="group" aria-label={lang === "zh" ? "已记录决策" : "Recorded decisions"}>
          {!rows.length && <div className="trace-empty">{lang === "zh" ? "发送目标或选择历史会话，查看真实决策路径。" : "Send a goal or select a session to inspect real decision paths."}</div>}
          {rows.map(({ lane, step, index }) => {
            const route = loopView(undefined, { live: false, connected: false, done: false, error: false, baseline: lane === "baseline", step });
            const routeLabel = step.denied ? (lang === "zh" ? "拦截" : "BLOCKED") : step.aborted ? (lang === "zh" ? "中止" : "ABORTED") : lane === "baseline" ? "LLM" : route.assisted ? `${decisionLabel.toUpperCase()} + LLM` : route.direct ? (lang === "zh" ? "无 LLM" : "NO LLM") : "—";
            const chosen = selected?.lane === lane && selected.index === index;
            return <button key={`${lane}-${index}`} disabled={replaying} className={`trace-row ${chosen ? "is-selected" : ""}`} aria-pressed={chosen}
              onClick={() => { setSelected({ lane, index }); setInspectOpen(true); }}>
              <span className={`trace-lane ${lane}`}>{lane === "jev" ? decisionLabel.toUpperCase() : "LLM"}</span>
              <span className="trace-index">{String(index + 1).padStart(2, "0")}</span>
              <strong title={step.decision.operation}>{step.decision.operation}</strong>
              <span className={`trace-route ${step.denied || step.aborted ? "is-blocked" : ""}`}>{routeLabel}</span>
              <span className="trace-latency" title={lang === "zh" ? "决策延迟，非步骤总耗时" : "Decision latency, not total step time"}>{formatTime(step.decision.latency_ms)}</span>
            </button>;
          })}
        </div>
        <div className="trace-inspector">
          <span className="eyebrow">{selected ? (lang === "zh" ? "所选步骤" : "SELECTED STEP") : (lang === "zh" ? "最近操作" : "LATEST OPERATION")}</span>
          <h3>{operation ?? "—"}</h3>
          {selectedStep ? <>
            <p>{selectedStep.decision.target_label ?? (Array.isArray(selectedStep.decision.target) ? selectedStep.decision.target.join(", ") : selectedStep.decision.target) ?? (lang === "zh" ? "无独立目标" : "No separate target")}</p>
            <dl><div><dt>{lang === "zh" ? "路径置信度" : "Path confidence"}</dt><dd>{selectedStep.decision.confidence == null ? "—" : `${(selectedStep.decision.confidence * 100).toFixed(1)}%`}</dd></div>
              <div><dt>{lang === "zh" ? "结果状态" : "Outcome status"}</dt><dd>{selectedStep.denied ? "DENIED" : selectedStep.aborted ? "ABORTED" : selectedStep.outcome?.status ?? "—"}</dd></div></dl>
            <button className="console-text-button" onClick={() => setInspectOpen((value) => !value)}>{lang === "zh" ? "展开 / 收起原始详情" : "Toggle step details"}<span aria-hidden="true">↗</span></button>
          </> : <p>{lang === "zh" ? "点选左侧记录检查路由、目标与执行结果。记录按步骤序号排列，两路不共享时间轴。" : "Select a recorded step to inspect its route, target and outcome. Rows are ordered by step index, not a shared lane clock."}</p>}
          {data.errors.map((error, index) => <p key={index} className="console-error">{error.lane ?? "stream"}: {error.message}</p>)}
        </div>
      </div>
      {selectedStep && inspectOpen && <div className="console-step-detail"><ActivityRow step={selectedStep} index={selected!.index + 1} lane={selected!.lane} modelLabel={decisionLabel} /></div>}
    </details>
  </>;
}

export function LoopConsole({ chat }: { chat: ChatApi }) {
  const { lang } = useLang();
  const [pickedRun, setPickedRun] = useState<string | null>(null);
  const replaying = chat.playback.status !== "idle";
  const runId = !replaying && pickedRun && chat.turns.includes(pickedRun) ? pickedRun : chat.activeRunId;
  const data = runId ? chat.streamOf(runId) : chat.stream;
  const live = runId === chat.activeRunId && (replaying || !!data.connection) && !data.done;
  const goal = (!pickedRun ? chat.pendingGoal : null) ?? String(data.params?.goal ?? "");
  return <div className="console-scroll" data-testid="loop-console">
    <div className="console-content">
      <div className="console-heading">
        <span className="eyebrow">{replaying ? (lang === "zh" ? "历史流程回放" : "RECORDED FLOW REPLAY") : (lang === "zh" ? "实时执行流程" : "LIVE EXECUTION FLOW")}</span>
        <p className="workspace-goal" title={goal}>{goal || (lang === "zh" ? "输入目标开始运行" : "Send a goal to begin")}</p>
        <label className="turn-picker"><span>{lang === "zh" ? "轮次" : "TURN"}</span><select disabled={replaying} value={!replaying && pickedRun && chat.turns.includes(pickedRun) ? pickedRun : ""} onChange={(event) => setPickedRun(event.target.value || null)} aria-label={lang === "zh" ? "查看轮次" : "Inspect turn"}>
          <option value="">{lang === "zh" ? "跟随最新" : "Follow latest"}</option>
          {chat.turns.map((id, index) => <option key={id} value={id}>{String(index + 1).padStart(2, "0")} · {String(chat.streamOf(id).params?.goal ?? id).slice(0, 45)}</option>)}
        </select></label>
      </div>
      <Inspector scope={runId ?? "empty"} sceneKey={chat.sessionId ?? "new-session"} data={data} live={live} replaying={replaying} />
      {live && !replaying && <div className="console-pause">
        {(data.lanes.jev?.awaiting || data.lanes.baseline?.awaiting) && <><span>{lang === "zh" ? "执行已暂停，等待你的操作。" : "Execution paused, awaiting your action."}</span><button className="console-button" onClick={chat.continueRun}>{lang === "zh" ? "继续" : "Continue"}</button></>}
        <button className="console-button" onClick={chat.abortRun}>{lang === "zh" ? "中止运行" : "Abort run"}</button>
      </div>}
    </div>
  </div>;
}
