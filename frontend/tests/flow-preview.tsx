import { useState } from "react";
import { createRoot } from "react-dom/client";
import { DecisionFlow } from "../src/components/DecisionFlow";
import { ExecutionWorkspace } from "../src/components/ExecutionWorkspace";
import { ChatInput } from "../src/components/ChatInput";
import type { ChatApi } from "../src/chat";
import { emptyPlayback } from "../src/playback";
import { I18nProvider, LangToggle } from "../src/i18n";
import { loopView } from "../src/loopView";
import { applyRunEvent, emptyStream } from "../src/stream";
import type { RunEvent, Step } from "../src/types";
import "../src/styles.css";
import "../src/console.css";

// Synthetic, in-memory fixtures only. No API client, SSE, model, or tool calls.
const attemptId = "offline-fixture-attempt-1";
const questions = {
  phase: { type: "choice", criteria: { INSPECT: "Inspect existing evidence", ACT: "Change the workspace" } },
  action__inspect: { type: "choice", criteria: { READ_FILE: "Read a file", LIST_FILES: "List a directory", BASH: "Run an inspection command" } },
  target__inspect__read_file: { type: "choice", criteria: {
    "a.py": { file: "a.py", arguments: '{"path":"a.py"}', source: "Earlier synthetic directory observation" },
    "b.py": { file: "b.py", arguments: '{"path":"b.py"}', source: "Earlier synthetic directory observation" },
    LLM_PARAMETERS: { binding: "Author all READ_FILE arguments from the transcript using the canonical tool schema" },
  } },
  action__act: { type: "choice", criteria: { WRITE_FILE: "Write a file", BASH: "Run a workspace command" } },
  target__act__write_file: { type: "choice", criteria: { LLM_PARAMETERS: { binding: "Author all WRITE_FILE arguments" } } },
};
const response = {
  operation: "READ_FILE", phase: "INSPECT", binding_mode: "llm_parameters",
  target: null, bound_arguments: {}, confidence: .91, latency_ms: null,
  phase_probabilities: { INSPECT: .95, ACT: .05 },
  operation_probabilities: { READ_FILE: .93, LIST_FILES: .05, BASH: .02 },
  target_probabilities: { "a.py": .04, "b.py": .05, LLM_PARAMETERS: .91 },
  consumed_heads: [
    { role: "phase", head: "phase", selected: "INSPECT", confidence: .95, probabilities: { INSPECT: .95, ACT: .05 } },
    { role: "action", head: "action__inspect", selected: "READ_FILE", confidence: .93, probabilities: { READ_FILE: .93, LIST_FILES: .05, BASH: .02 } },
    { role: "target", head: "target__inspect__read_file", selected: "LLM_PARAMETERS", confidence: .91, probabilities: { "a.py": .04, "b.py": .05, LLM_PARAMETERS: .91 } },
  ],
};
const recordedStep: Step = {
  intent_id: "offline-fixture-intent-1",
  // decision is the accepted routing snapshot, not the later authored arguments.
  decision: { ...response, escalated: false },
  model_calls: [
    { kind: "jev_decision", model: "offline-fixture-jev", request: { questions }, response },
    { kind: "parameter_authoring", model: "offline-fixture-llm", response: {
      role: "assistant", content: null,
      tool_calls: [{ id: "offline-fixture-call-1", type: "function", function: {
        name: "READ_FILE", arguments: '{"path":"a.py","offset":0,"limit":100}',
      } }],
    } },
  ],
  outcome: { status: "ready", action: "READ_FILE", reason: "Synthetic read result; no file was read." },
};
const stages: { label: string; note: string; events: RunEvent[] }[] = [
  { label: "Start attempt", note: "No candidates have arrived yet.", events: [
    { type: "attempt_started", attempt_id: attemptId, step: 1 },
  ] },
  { label: "Show request", note: "Jev is waiting. All submitted heads are visible; no pick or probabilities exist yet.", events: [
    { type: "jev_request", attempt_id: attemptId, questions },
  ] },
  { label: "Jev response", note: "Original Jev pick: READ_FILE + LLM_PARAMETERS. No LLM call or transcript commit yet.", events: [
    { type: "jev_response", attempt_id: attemptId, response },
  ] },
  { label: "LLM start", note: "decision_ready precedes the actual parameter_authoring call. The LLM independently restores transcript context.", events: [
    { type: "decision_ready", attempt_id: attemptId, operation: "READ_FILE", needs_authoring: true, binding_mode: "llm_parameters", escalated: false },
    { type: "llm_started", attempt_id: attemptId, kind: "parameter_authoring", operation: "READ_FILE" },
  ] },
  { label: "LLM returned", note: "A helper return is not an accepted intent or executed tool; commit is still absent.", events: [
    { type: "llm_completed", attempt_id: attemptId, kind: "parameter_authoring", operation: "READ_FILE", status: "returned" },
  ] },
  { label: "Intent", note: "Accepted authored arguments have been committed and checkpointed before dispatch. No tool result yet.", events: [
    { type: "intent", operation: "READ_FILE", target: "a.py" },
  ] },
  { label: "Recorded step", note: "This button batches synthetic dispatch_started → observation → step. The recorded read result appends separately; the run is not declared complete.", events: [
    { type: "dispatch_started", operation: "READ_FILE" },
    { type: "observation", operation: "READ_FILE" },
    { type: "step", step: recordedStep },
  ] },
];

function Preview() {
  const [stage, setStage] = useState(0);
  const [combined, setCombined] = useState(true);
  const data = stages.slice(0, stage).flatMap((item) => item.events).reduce(applyRunEvent, emptyStream());
  data.params = { goal: "Synthetic task: inspect a.py. No actual execution." };
  data.connection = "connected";
  const history = { ...emptyStream(), done: true, params: { goal: "Synthetic earlier turn for scroll testing" },
    lanes: { jev: { steps: Array.from({ length: 12 }, () => recordedStep), metrics: null, answer: "Synthetic earlier answer", awaiting: false, finished: true, activity: null, unknownAcknowledgements: 0 } } };
  const noop = () => {};
  const chat: ChatApi = { turns: ["fixture-history", "fixture-live"], activeRunId: "fixture-live", stream: data,
    streamOf: (id) => id === "fixture-history" ? history : data, running: stage > 0, canControl: stage > 0, starting: false, startError: null,
    pendingGoal: null, history: [], sessionId: "offline-fixture", replaying: false,
    playback: emptyPlayback(), canPlayback: false, historyComplete: true, startPlayback: noop, playbackAction: noop,
    newSession: noop, send: noop, replay: noop, replaySession: noop, continueRun: noop, abortRun: noop };
  const lane = data.lanes.jev;
  const view = loopView(lane, { live: stage > 0 && stage < stages.length, connected: true, done: false, error: false });
  const current = stages[stage - 1];
  return <main className="fixture-page">
    <style>{`
      .fixture-page { max-width: 1440px; margin: 0 auto; padding: 0 20px 40px; }
      .fixture-controls { position: sticky; top: 0; z-index: 30; background: var(--color-surface); border-bottom: 1px solid var(--color-line); padding: 16px 0; }
      .fixture-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
      .fixture-title h1 { font: 600 17px var(--font-mono); color: var(--color-warn); }
      .fixture-description { margin: 8px 0 12px; color: var(--color-ink2); font-size: 12px; line-height: 1.6; }
      .fixture-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
      .fixture-buttons button[aria-current="step"] { color: var(--color-accent); border-color: var(--color-accent); }
      .fixture-status { min-height: 82px; padding: 18px 0; font-size: 13px; line-height: 1.7; }
      .fixture-status code { color: var(--color-ink2); font-size: 11px; }
      .fixture-raw { margin-top: 16px; color: var(--color-ink2); font-size: 12px; }
      .fixture-raw pre { overflow: auto; max-height: 320px; padding: 12px; background: var(--console-panel); }
      .fixture-workspace { height: 850px; display: flex; flex-direction: column; border: 1px solid var(--color-line); }
    `}</style>
    <header className="fixture-controls">
      <div className="fixture-title"><h1>OFFLINE FIXTURE / NO MODEL CALLS</h1><LangToggle /></div>
      <p className="fixture-description">Synthetic events and probabilities. No backend, SSE, model, tool execution, or saved run data. Click the next stage; nothing advances automatically. Earlier stages can be revisited.</p>
      <nav className="fixture-buttons" aria-label="Manual event stages">
        {stages.map((item, index) => <button key={item.label} className="console-button" type="button"
          disabled={index > stage} aria-current={stage === index + 1 ? "step" : undefined}
          onClick={() => setStage(index + 1)}>{index + 1}. {item.label}</button>)}
        <button className="console-button" type="button" onClick={() => setStage(0)}>Reset</button>
        <button className="console-button" type="button" aria-pressed={combined} onClick={() => setCombined(!combined)}>Combined workspace</button>
      </nav>
    </header>
    <section className="fixture-status" aria-live="polite">
      <p><strong>Stage {stage} / {stages.length}: {current?.label ?? "Ready"}</strong> — {current?.note ?? "Click Start attempt to begin the offline simulation."}</p>
      <code>Events: {current?.events.map((event) => event.type).join(" → ") ?? "none"}</code>
    </section>
    {combined ? <div className="fixture-workspace"><ExecutionWorkspace chat={chat} composer={<ChatInput disabled={false} starting={false} error={null} onSend={noop} />} /></div> : <section className="console-panel" aria-label="Actual DecisionFlow component with synthetic inputs">
      <DecisionFlow key={lane?.decisionFrame?.attemptId ?? (view.step ? "recorded" : "ready")} view={view} frame={lane?.decisionFrame} step={view.step} />
    </section>}
    <details className="fixture-raw"><summary>Inspect current synthetic state</summary>
      <pre>{JSON.stringify(lane?.decisionFrame ?? view.step ?? {}, null, 2)}</pre>
    </details>
  </main>;
}

createRoot(document.getElementById("root")!).render(<I18nProvider><Preview /></I18nProvider>);
