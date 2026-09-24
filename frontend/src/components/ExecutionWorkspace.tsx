import { useRef, useState, type ReactNode } from "react";
import type { ChatApi } from "../chat";
import { useLang } from "../i18n";
import { aggregateSessionComparison } from "../comparison";
import { LoopConsole } from "./LoopConsole";
import { ChatView } from "./ChatView";
import { SessionSummaryPanel } from "./SessionSummaryPanel";

/** Layout only: both panels remain mounted and subscribe to the same ChatApi. */
export function ExecutionWorkspace({ chat, composer }: { chat: ChatApi; composer?: ReactNode }) {
  const { lang } = useLang();
  const root = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(72);
  const comparison = aggregateSessionComparison(chat.turns.map((id) => chat.streamOf(id)));
  const resize = (value: number) => setSplit(Math.max(45, Math.min(80, value)));
  const tracks = `minmax(0, ${split}fr) 12px minmax(0, ${100 - split}fr)`;
  return <div ref={root} className={`execution-workspace ${chat.playback.status === "paused" ? "playback-paused" : ""}`} data-testid="execution-workspace" data-layout="columns"
    style={{ gridTemplateColumns: tracks, gridTemplateRows: "minmax(0, 1fr)" }}>
    <section id="workspace-flow" className="workspace-flow" aria-label={lang === "zh" ? "实时流程图" : "Execution flow"}>
      <LoopConsole chat={chat} />
    </section>
    <div role="separator" tabIndex={0} aria-orientation="vertical" aria-controls="workspace-flow"
      aria-label={lang === "zh" ? "调整流程图与对话比例" : "Resize flow and conversation"}
      aria-valuemin={45} aria-valuemax={80} aria-valuenow={split}
      className="workspace-divider" title={lang === "zh" ? "拖动调整宽度；左右键微调，Home / End 调至边界" : "Drag to resize; left/right keys adjust, Home / End for limits"}
      onPointerDown={(event) => { event.preventDefault(); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={(event) => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
        const rect = root.current?.getBoundingClientRect();
        if (!rect) return;
        const size = rect.width;
        const offset = event.clientX - rect.left;
        if (size > 12) resize(Math.round((offset - 6) / (size - 12) * 100));
      }}
      onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onKeyDown={(event) => {
        const decrease = "ArrowLeft";
        const increase = "ArrowRight";
        if (![decrease, increase, "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        resize(event.key === "Home" ? 45 : event.key === "End" ? 80 : split + (event.key === decrease ? -5 : 5));
      }}><span /></div>
    <section className="workspace-conversation" aria-label={lang === "zh" ? "对话与实时事件" : "Conversation and live events"}>
      <div className="conversation-heading">
        <span className="eyebrow">{lang === "zh" ? "对话与实时事件" : "CONVERSATION / LIVE EVENTS"}</span>
        <span>{chat.stream.connection === "reconnecting" || chat.stream.connection === "connecting"
          ? (lang === "zh" ? "连接中 · 等待实时事件" : "Connecting · awaiting live events")
          : `${chat.turns.length} ${lang === "zh" ? "轮对话 · 独立滚动" : "turns · independent scroll"}`}</span>
      </div>
      {comparison && <details className="workspace-summary"><summary>{lang === "zh" ? "会话对比汇总" : "Session comparison"}</summary>
        <div><SessionSummaryPanel comparison={comparison} /></div>
      </details>}
      <ChatView chat={chat} />
      {composer}
    </section>
  </div>;
}
