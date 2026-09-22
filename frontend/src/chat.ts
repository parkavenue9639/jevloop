import { useCallback, useEffect, useRef, useState } from "react";
import { control, fetchRuns, loadFinishedRun, startRun, useRunStream } from "./api";
import type { RunParams, RunSummary } from "./types";
import { emptyStream } from "./stream";
import type { StreamData } from "./stream";

/** The conversation model: an ordered list of turns (one turn = one run).
 *  The active turn renders from the live SSE stream; retired turns render
 *  from immutable snapshots taken at the moment the conversation moved on,
 *  so earlier exchanges stay visible above the latest one, chat-style. */
export interface ChatApi {
  turns: string[];
  activeRunId: string | null;
  /** live stream of the active run */
  stream: StreamData;
  /** stream data for any turn — live for the active one, snapshot otherwise */
  streamOf: (runId: string) => StreamData;
  running: boolean;
  starting: boolean;
  startError: string | null;
  /** local echo of the just-sent goal, until the run's meta event confirms it */
  pendingGoal: string | null;
  history: RunSummary[];
  sessionId: string | null;
  replaying: boolean;
  newSession: () => void;
  send: (params: RunParams) => void;
  replay: (runId: string) => void;
  replaySession: (sessionId: string) => void;
  continueRun: () => void;
  abortRun: () => void;
}

export function useChat(): ChatApi {
  const [turns, setTurns] = useState<string[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Record<string, StreamData>>({});
  const [pendingGoal, setPendingGoal] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [replaying, setReplaying] = useState(false);
  const replayingRef = useRef(false);
  const [activeLive, setActiveLive] = useState(false);
  const liveStream = useRunStream(activeLive ? activeRunId : null);
  const stream = activeRunId
    ? activeLive ? liveStream : snapshots[activeRunId] ?? emptyStream()
    : emptyStream();

  // latest-value refs so event handlers can snapshot without stale closures
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
  const bootstrappedRef = useRef(false);

  // the run's own meta event is the authoritative goal — drop the local echo
  useEffect(() => {
    if (stream.params) setPendingGoal(null);
  }, [stream.params]);

  const adoptSession = useCallback(async (
    targetSession: string,
    sourceRuns: RunSummary[],
  ) => {
    const runs = sourceRuns
      .filter((run) => (run.session_id ?? run.run_id) === targetSession)
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
    if (!runs.length) return;
    const loaded = await Promise.all(runs.map((run) => loadFinishedRun(run.run_id)));
    const nextSnapshots: Record<string, StreamData> = {};
    runs.forEach((run, index) => { nextSnapshots[run.run_id] = loaded[index]; });
    setSnapshots(nextSnapshots);
    setTurns(runs.map((run) => run.run_id));
    activeLiveRef.current = false;
    setActiveLive(false);
    setActiveRunId(runs[runs.length - 1].run_id);
    setPendingGoal(null);
    setStartError(null);
    sessionRef.current = targetSession;
    setSessionId(targetSession);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchRuns().then(async (runs) => {
      if (cancelled) return;
      setHistory(runs);
      if (!bootstrappedRef.current) {
        bootstrappedRef.current = true;
        const latest = runs[0];
        if (latest && !activeRef.current) {
          const target = latest.session_id ?? latest.run_id;
          setReplaying(true);
          replayingRef.current = true;
          try {
            await adoptSession(target, runs);
          } finally {
            if (!cancelled) setReplaying(false);
            replayingRef.current = false;
          }
        }
      }
    }).catch((error) => setStartError(String(error)));
    return () => { cancelled = true; };
  }, [activeRunId, stream.done, adoptSession]);

  const send = useCallback(async (params: RunParams) => {
    if (!params.goal.trim()) return;
    if (replayingRef.current) return;
    const prev = activeRef.current;
    if (prev && activeLiveRef.current && !streamRef.current.done) return;
    setStartError(null);
    setPendingGoal(params.goal);
    setStarting(true);
    try {
      const { run_id: runId, session_id: sid } = await startRun({
        ...params, session_id: sessionRef.current ?? undefined,
      });
      sessionRef.current = sid;
      setSessionId(sid);
      if (prev && prev !== runId) {
        setSnapshots((s) => ({ ...s, [prev]: streamRef.current }));
      }
      setTurns((t) => (t.includes(runId) ? t : [...t, runId]));
      activeLiveRef.current = true;
      setActiveLive(true);
      setActiveRunId(runId);
    } catch (error) {
      setStartError(String(error));
      setPendingGoal(null);
    } finally {
      setStarting(false);
    }
  }, []);

  /** Replaying history loads a run's stored JSON events and adopts the whole
   * session without opening a second live SSE subscription. */
  const replay = useCallback((runId: string) => {
    if (activeRef.current && activeLiveRef.current && !streamRef.current.done) return;
    const run = historyRef.current.find((r) => r.run_id === runId);
    replaySession(run?.session_id ?? runId);
  }, []);

  /** Load and adopt a whole session; the next send continues its ledger. */
  const replaySession = useCallback(async (targetSession: string) => {
    if (activeRef.current && activeLiveRef.current && !streamRef.current.done) return;
    replayingRef.current = true;
    setReplaying(true);
    try {
      await adoptSession(targetSession, historyRef.current);
    } catch (error) {
      setStartError(String(error));
    } finally {
      setReplaying(false);
      replayingRef.current = false;
    }
  }, [adoptSession]);
  const newSession = useCallback(() => {
    if (activeRef.current && activeLiveRef.current && !streamRef.current.done) return;
    setSnapshots({});
    setTurns([]);
    setActiveRunId(null);
    activeLiveRef.current = false;
    setActiveLive(false);
    setPendingGoal(null);
    setStartError(null);
    sessionRef.current = null;
    setSessionId(null);
  }, []);



  const continueRun = useCallback(() => {
    if (activeRef.current) void control(activeRef.current, "continue");
  }, []);

  const abortRun = useCallback(() => {
    if (activeRef.current) void control(activeRef.current, "abort");
  }, []);

  const running = activeLive && !!activeRunId && !stream.done && stream.errors.length === 0;

  const streamOf = (runId: string): StreamData =>
    runId === activeRunId ? stream : snapshots[runId] ?? emptyStream();

  return {
    turns, activeRunId, stream, streamOf, running, starting, startError,
    pendingGoal, history, sessionId, replaying, newSession, send, replay,
    replaySession, continueRun, abortRun,
  };
}
