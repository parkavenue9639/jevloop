import { useEffect, useRef, useState } from "react";
import type { RunEvent, RunParams, RunSummary } from "./types";
import { applyRunEvent, emptyStream, reduceEvents } from "./stream";
import type { StreamData } from "./stream";

export async function startRun(params: RunParams): Promise<{ run_id: string; session_id: string }> {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText);
  return await res.json();
}

export async function control(runId: string, action: string): Promise<void> {
  await fetch(`/api/run/${runId}/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const res = await fetch("/api/runs");
  if (!res.ok) return [];
  const { runs } = await res.json();
  return runs;
}

/** Load a finished run through a bounded JSON replay endpoint. Historical
 * runs can contain large model-call payloads; fetch avoids EventSource's
 * reconnect/error races and never resolves to a silently partial transcript. */
export async function loadFinishedRun(runId: string): Promise<StreamData> {
  const res = await fetch(`/api/run/${runId}`);
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText);
  const payload = await res.json() as {
    events: Array<{ seq: number; event: RunEvent }>;
  };
  return reduceEvents(payload.events.map((entry) => entry.event));
}

export type RunStream = StreamData;

/** Subscribe to a run's SSE stream; deduplicates on the server's sequence ids
 *  so an EventSource auto-reconnect replays without duplicating steps. Event
 *  folding lives in the pure `applyRunEvent` reducer (see stream.ts). */
export function useRunStream(runId: string | null): RunStream {
  const [data, setData] = useState<StreamData>(emptyStream);
  const lastSeq = useRef(0);

  useEffect(() => {
    if (!runId) return;
    setData(emptyStream());
    lastSeq.current = 0;

    const source = new EventSource(`/api/run/${runId}/events`);
    source.onmessage = (event: MessageEvent<string>) => {
      const seq = Number(event.lastEventId ?? 0);
      if (seq && seq <= lastSeq.current) return;
      lastSeq.current = seq;
      let payload: RunEvent;
      try {
        payload = JSON.parse(event.data) as RunEvent;
      } catch {
        return; // malformed frame: skip, the seq gap is reclaimed on reconnect
      }
      setData((prev) => applyRunEvent(prev, payload));
      if (payload.type === "done") source.close();
    };
    source.onerror = () => {
      // the server closes the stream when the run finishes; done is set by the event
    };
    return () => source.close();
  }, [runId]);

  return data;
}
