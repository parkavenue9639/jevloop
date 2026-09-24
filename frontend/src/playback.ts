import type { RunEvent } from "./types";
import { applyRunEvent, emptyStream } from "./stream.ts";
import type { StreamData } from "./stream";

export interface RecordedRun {
  runId: string;
  events: Array<{ seq: number; event: RunEvent }>;
}
export interface ReplayFrame { runId: string; seq: number; event: RunEvent }
export interface ReplayTimeline {
  frames: ReplayFrame[];
  runIds: string[];
  /** Older records lack the intermediate model-call events. Do not invent them. */
  partialTelemetry: boolean;
}
export type PlaybackStatus = "idle" | "playing" | "paused" | "ended";
export interface PlaybackState {
  status: PlaybackStatus;
  timeline: ReplayTimeline;
  position: number;
  speed: number;
  generation: number;
  turns: string[];
  activeRunId: string | null;
  streams: Record<string, StreamData>;
}
export type PlaybackAction =
  | { type: "start"; timeline: ReplayTimeline }
  | { type: "tick"; generation: number; position: number }
  | { type: "pause" | "play" | "restart" | "exit" }
  | { type: "seek"; position: number }
  | { type: "speed"; speed: number };

export const emptyPlayback = (): PlaybackState => ({
  status: "idle", timeline: { frames: [], runIds: [], partialTelemetry: false },
  position: 0, speed: 1, generation: 0, turns: [], activeRunId: null, streams: {},
});

/** Runs are supplied oldest first; each run has ONE interleaved lane sequence. */
export function replayTimeline(runs: RecordedRun[]): ReplayTimeline {
  const frames: ReplayFrame[] = [];
  if (new Set(runs.map((run) => run.runId)).size !== runs.length) throw new Error("Duplicate replay run");
  for (const run of runs) {
    const events = [...run.events].sort((a, b) => a.seq - b.seq);
    if (!events.length || events.at(-1)?.event.type !== "done") {
      throw new Error("Only completed event recordings can be replayed");
    }
    events.forEach((entry, index) => {
      if (entry.seq !== index + 1) throw new Error("Incomplete or duplicate replay event sequence");
      frames.push({ runId: run.runId, ...entry });
    });
  }
  return {
    frames, runIds: runs.map((run) => run.runId),
    partialTelemetry: runs.some((run) => !run.events.some(({ event }) => event.type === "jev_request")),
  };
}

function advance(state: PlaybackState): PlaybackState {
  const frame = state.timeline.frames[state.position];
  if (!frame) return { ...state, status: "ended" };
  const position = state.position + 1;
  return {
    ...state, position, activeRunId: frame.runId,
    status: position === state.timeline.frames.length ? "ended" : state.status,
    turns: state.turns.includes(frame.runId) ? state.turns : [...state.turns, frame.runId],
    streams: { ...state.streams, [frame.runId]: applyRunEvent(state.streams[frame.runId] ?? emptyStream(), frame.event) },
  };
}

function rewind(state: PlaybackState, position: number, status: PlaybackStatus): PlaybackState {
  let next: PlaybackState = { ...state, position: 0, turns: [], activeRunId: null, streams: {},
    generation: state.generation + 1, status };
  const target = Math.max(0, Math.min(state.timeline.frames.length, Math.floor(position)));
  while (next.position < target) next = advance(next);
  return next;
}

export function playbackReducer(state: PlaybackState, action: PlaybackAction): PlaybackState {
  switch (action.type) {
    case "start":
      return rewind({ ...state, timeline: action.timeline }, 0, action.timeline.frames.length ? "playing" : "idle");
    case "tick":
      return state.status === "playing" && action.generation === state.generation && action.position === state.position
        ? advance(state) : state;
    case "pause":
      return state.status === "playing" ? { ...state, status: "paused", generation: state.generation + 1 } : state;
    case "play":
      return state.status === "ended" ? rewind(state, 0, "playing")
        : state.status === "paused" ? { ...state, status: "playing", generation: state.generation + 1 } : state;
    case "restart":
      return state.status === "idle" ? state : rewind(state, 0, "playing");
    case "seek":
      return state.status === "idle" || !Number.isFinite(action.position) ? state : rewind(state, action.position, "paused");
    case "speed":
      return [0.5, 1, 2, 4].includes(action.speed) ? { ...state, speed: action.speed, generation: state.generation + 1 } : state;
    case "exit":
      return { ...emptyPlayback(), speed: state.speed, generation: state.generation + 1 };
  }
}

/** Presentation dwell AFTER an event, not an estimate of original latency. */
export function replayDelay(event: RunEvent | undefined, speed: number): number {
  const duration = !event ? 100 : event.type === "meta" || event.type === "final" ? 1100
    : ["jev_request", "jev_response", "llm_started", "step"].includes(event.type) ? 850
      : ["metrics", "observation", "done"].includes(event.type) ? 100 : 450;
  return duration / speed;
}
