import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { control, fetchRuns, fetchSessionRuns, loadRecordedRun, startRun, useRunStream } from "./api";
import type { RunParams, RunSummary } from "./types";
import { emptyStream, reduceEvents } from "./stream";
import type { StreamData } from "./stream";
import { emptyPlayback, playbackReducer, replayDelay, replayTimeline } from "./playback";
import type { PlaybackAction, PlaybackState } from "./playback";

export interface ChatApi {
  turns: string[];
  activeRunId: string | null;
  stream: StreamData;
  streamOf: (runId: string) => StreamData;
  running: boolean;
  canControl: boolean;
  starting: boolean;
  startError: string | null;
  pendingGoal: string | null;
  history: RunSummary[];
  sessionId: string | null;
  /** Loading a saved session/recording, not animated playback. */
  replaying: boolean;
  playback: PlaybackState;
  canPlayback: boolean;
  historyComplete: boolean;
  startPlayback: () => void;
  playbackAction: (action: PlaybackAction) => void;
  newSession: () => void;
  send: (params: RunParams) => void;
  replay: (runId: string) => void;
  replaySession: (sessionId: string) => void;
  continueRun: () => void;
  abortRun: () => void;
}

/** Durable run snapshots stay separate from the disposable playback cursor.
 * Both panels receive the same event prefix; neither controls a separate clock. */
export function useChat(): ChatApi {
  const [turns, setTurns] = useState<string[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Record<string, StreamData>>({});
  const [pendingGoal, setPendingGoal] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [historyComplete, setHistoryComplete] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [replaying, setReplaying] = useState(false);
  const [activeLive, setActiveLive] = useState(false);
  const [playback, playbackAction] = useReducer(playbackReducer, undefined, emptyPlayback);
  const liveStream = useRunStream(activeLive ? activeRunId : null);
  const stream = activeRunId
    ? activeLive ? liveStream : snapshots[activeRunId] ?? emptyStream()
    : emptyStream();

  const playbackRef = useRef(playback);
  playbackRef.current = playback;
  const streamRef = useRef(stream);
  streamRef.current = stream;
  const activeRef = useRef(activeRunId);
  activeRef.current = activeRunId;
  const activeLiveRef = useRef(activeLive);
  activeLiveRef.current = activeLive;
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;
  const historyRef = useRef(history);
  historyRef.current = history;
  const replayingRef = useRef(false);
  const startingRef = useRef(false);
  const bootstrappedRef = useRef(false);
  const loadEpoch = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; loadEpoch.current += 1; };
  }, []);

  useEffect(() => {
    if (playback.status !== "playing") return;
    const timer = window.setTimeout(() => playbackAction({ type: "tick",
      generation: playback.generation, position: playback.position }),
    replayDelay(playback.timeline.frames[playback.position - 1]?.event, playback.speed));
    return () => window.clearTimeout(timer);
  }, [playback.status, playback.generation, playback.position, playback.timeline, playback.speed]);

  useEffect(() => { if (stream.params) setPendingGoal(null); }, [stream.params]);

  const busy = useCallback(() => startingRef.current || replayingRef.current
    || playbackRef.current.status !== "idle"
    || (activeLiveRef.current && !!activeRef.current && !streamRef.current.done), []);

  const adoptSession = useCallback(async (targetSession: string, sourceRuns: RunSummary[]) => {
    if (busy()) return;
    const epoch = ++loadEpoch.current;
    replayingRef.current = true;
    setReplaying(true);
    try {
      const completeRuns = await fetchSessionRuns(targetSession);
      // API history is newest first; reverse first to preserve timestamp ties.
      const runs = [...(completeRuns ?? sourceRuns)].reverse()
        .filter((run) => (run.session_id ?? run.run_id) === targetSession)
        .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
      if (!runs.length) return;
      const loaded = await Promise.all(runs.map((run) => loadRecordedRun(run.run_id)));
      if (!mounted.current || epoch !== loadEpoch.current) return;
      const nextSnapshots: Record<string, StreamData> = {};
      loaded.forEach((run) => { nextSnapshots[run.runId] = reduceEvents(run.events.map(({ event }) => event)); });
      setSnapshots(nextSnapshots);
      setHistoryComplete(completeRuns !== null);
      setTurns(runs.map((run) => run.run_id));
      activeLiveRef.current = false;
      setActiveLive(false);
      activeRef.current = runs[runs.length - 1].run_id;
      setActiveRunId(activeRef.current);
      setPendingGoal(null);
      setStartError(null);
      sessionRef.current = targetSession;
      setSessionId(targetSession);
    } catch (error) {
      if (mounted.current && epoch === loadEpoch.current) setStartError(String(error));
    } finally {
      if (mounted.current && epoch === loadEpoch.current) {
        setReplaying(false);
        replayingRef.current = false;
      }
    }
  }, [busy]);

  useEffect(() => {
    let cancelled = false;
    fetchRuns().then((runs) => {
      if (cancelled) return;
      setHistory(runs);
      if (!bootstrappedRef.current) {
        bootstrappedRef.current = true;
        const latest = runs[0];
        if (latest && !activeRef.current) void adoptSession(latest.session_id ?? latest.run_id, runs);
      }
    }).catch((error) => { if (!cancelled) setStartError(String(error)); });
    return () => { cancelled = true; };
  }, [activeRunId, stream.done, adoptSession]);

  const send = useCallback(async (params: RunParams) => {
    if (!params.goal.trim() || busy()) return;
    bootstrappedRef.current = true;
    const prev = activeRef.current;
    startingRef.current = true;
    setStartError(null);
    setPendingGoal(params.goal);
    setStarting(true);
    try {
      const { run_id: runId, session_id: sid } = await startRun({
        ...params, session_id: sessionRef.current ?? undefined,
      });
      if (!mounted.current) return;
      sessionRef.current = sid;
      setSessionId(sid);
      if (prev && prev !== runId) setSnapshots((s) => ({ ...s, [prev]: streamRef.current }));
      setTurns((t) => t.includes(runId) ? t : [...t, runId]);
      activeLiveRef.current = true;
      setActiveLive(true);
      activeRef.current = runId;
      setActiveRunId(runId);
    } catch (error) {
      if (mounted.current) { setStartError(String(error)); setPendingGoal(null); }
    } finally {
      startingRef.current = false;
      if (mounted.current) setStarting(false);
    }
  }, [busy]);

  const replaySession = useCallback((targetSession: string) => {
    if (busy()) return;
    bootstrappedRef.current = true;
    void adoptSession(targetSession, historyRef.current);
  }, [adoptSession, busy]);
  const replay = useCallback((runId: string) => {
    const run = historyRef.current.find((r) => r.run_id === runId);
    replaySession(run?.session_id ?? runId);
  }, [replaySession]);

  const newSession = useCallback(() => {
    if (busy()) return;
    bootstrappedRef.current = true;
    loadEpoch.current += 1;
    setSnapshots({}); setTurns([]); setActiveRunId(null);
    setHistoryComplete(true);
    activeRef.current = null;
    activeLiveRef.current = false;
    setActiveLive(false); setPendingGoal(null); setStartError(null);
    sessionRef.current = null;
    setSessionId(null);
  }, [busy]);

  const running = activeLive && !!activeRunId && !stream.done && stream.errors.length === 0;
  const canControl = activeLive && !!activeRunId && !stream.done && !replaying && !starting && playback.status === "idle";
  const canPlayback = !running && !starting && !replaying && playback.status === "idle" && turns.length > 0
    && turns.every((id) => (id === activeRunId ? stream : snapshots[id])?.done);
  const startPlayback = useCallback(async () => {
    if (!canPlayback || busy()) return;
    const epoch = ++loadEpoch.current;
    replayingRef.current = true;
    setReplaying(true); setStartError(null);
    try {
      const loaded = await Promise.all(turns.map(loadRecordedRun));
      if (!mounted.current || epoch !== loadEpoch.current) return;
      playbackAction({ type: "start", timeline: replayTimeline(loaded) });
    } catch (error) {
      if (mounted.current && epoch === loadEpoch.current) setStartError(String(error));
    } finally {
      if (mounted.current && epoch === loadEpoch.current) {
        replayingRef.current = false;
        setReplaying(false);
      }
    }
  }, [turns, canPlayback, busy]);

  // Defense in depth: historical callbacks can NEVER POST run controls.
  const runControl = useCallback((action: string) => {
    if (playbackRef.current.status !== "idle" || replayingRef.current || startingRef.current
      || !activeLiveRef.current || !activeRef.current || streamRef.current.done) return;
    void control(activeRef.current, action);
  }, []);
  const continueRun = useCallback(() => runControl("continue"), [runControl]);
  const abortRun = useCallback(() => runControl("abort"), [runControl]);

  const playingHistory = playback.status !== "idle";
  const visibleRunId = playingHistory ? playback.activeRunId : activeRunId;
  const streamOf = (runId: string): StreamData => playingHistory
    ? playback.streams[runId] ?? emptyStream()
    : runId === activeRunId ? stream : snapshots[runId] ?? emptyStream();
  return {
    turns: playingHistory ? playback.turns : turns, activeRunId: visibleRunId,
    stream: playingHistory ? (visibleRunId ? streamOf(visibleRunId) : emptyStream()) : stream,
    streamOf, running, canControl, starting, startError, pendingGoal, history, sessionId, replaying,
    playback, canPlayback, historyComplete, startPlayback, playbackAction,
    newSession, send, replay, replaySession, continueRun, abortRun,
  };
}
