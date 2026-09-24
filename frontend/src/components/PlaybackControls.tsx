import type { ChatApi } from "../chat";
import { useLang } from "../i18n";
import { Icon } from "./Icon";

export function PlaybackControls({ chat }: { chat: ChatApi }) {
  const { lang } = useLang();
  const zh = lang === "zh";
  const { playback: p, playbackAction: act } = chat;
  const active = p.status !== "idle";
  if (!chat.turns.length && !active) return null;
  const total = p.timeline.frames.length;
  return <section className={`playback-bar ${active ? "is-active" : ""}`} aria-label={zh ? "历史会话回放" : "Session playback"} data-testid="playback-controls">
    <div className="playback-actions">
      <span className="eyebrow">{zh ? "历史回放" : "SESSION REPLAY"}</span>
      {!active ? <button className="console-button" onClick={chat.startPlayback} disabled={!chat.canPlayback}
        title={zh ? "从第一轮开始同步回放两个窗口；仅支持已结束的会话" : "Replay both panels from the first turn; completed sessions only"}>
        <Icon name="play" />{chat.historyComplete ? (zh ? "回放整个会话" : "Replay session") : (zh ? "回放已加载记录" : "Replay loaded turns")}
      </button> : <>
        <button className="console-button" onClick={() => act({ type: p.status === "playing" ? "pause" : "play" })}>
          <Icon name={p.status === "playing" ? "pause" : "play"} />
          {p.status === "playing" ? (zh ? "暂停回放" : "Pause replay") : p.status === "ended" ? (zh ? "再次播放" : "Play again") : (zh ? "继续回放" : "Resume replay")}
        </button>
        <button className="console-button" onClick={() => act({ type: "restart" })}><Icon name="history" />{zh ? "从头播放" : "Restart"}</button>
        <button className="icon-button" onClick={() => act({ type: "seek", position: p.position + 1 })} disabled={p.position >= total}
          title={zh ? "下一个事件（暂停）" : "Next event (paused)"} aria-label={zh ? "下一个事件" : "Next event"}><Icon name="next" /></button>
        <label className="playback-speed">{zh ? "速度" : "Speed"}<select aria-label={zh ? "回放速度" : "Replay speed"} value={p.speed}
          onChange={(e) => act({ type: "speed", speed: Number(e.target.value) })}>
          {[0.5, 1, 2, 4].map((speed) => <option value={speed} key={speed}>{speed}×</option>)}
        </select></label>
        <div className="playback-progress"><input type="range" min={0} max={total} value={p.position}
          aria-label={zh ? "回放事件进度" : "Replay event position"}
          aria-valuetext={`${p.position} / ${total}`}
          onChange={(e) => act({ type: "seek", position: Number(e.target.value) })} />
          <span className="num">{p.position} / {total}</span></div>
        <span className="playback-turn">{zh ? "轮次" : "Turn"} {p.turns.length} / {p.timeline.runIds.length}</span>
        <button className="console-button playback-exit" onClick={() => act({ type: "exit" })}><Icon name="close" />{zh ? "退出回放" : "Exit replay"}</button>
      </>}
    </div>
    <p className="playback-note">{active
      ? (zh ? "按已记录事件同步展示 · 演示节奏，非原始耗时 · 不调用模型或工具" : "Synchronized recorded events · presentation pace, not original timing · no model or tool calls")
      : (zh ? "流程图与对话同步重现；回放不会重新执行任务。" : "Replay the flow and conversation together, without executing the task again.")}
      {active && p.timeline.partialTelemetry && <span> {zh ? "部分旧记录缺少中间事件，仅展示已保存的阶段。" : "Some older runs lack intermediate events; only recorded stages are shown."}</span>}
      {!chat.historyComplete && <span> {zh ? "后端尚未支持完整会话查询，仅回放已加载轮次；重启更新后的后端可加载完整会话。" : "Older server: only loaded turns are available. Restart the updated backend to load complete sessions."}</span>}
    </p>
  </section>;
}
