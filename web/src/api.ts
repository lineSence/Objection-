export type RunStatus = "queued" | "running" | "done" | "failed";

export interface Usage { input_tokens: number; output_tokens: number; cost_usd: number; latency_s: number }

export interface Disputed { point: string; positions: Record<string, string> }

export interface Verdict {
  answer: string;
  confidence: number | null;
  agreement: string | null;
  consensus: string[];
  disputed: Disputed[];
  minority_report: string | null;
  assumptions: string[];
}

export interface Run {
  id: string;
  question: string;
  context: string | null;
  mode: string;
  source: string;
  status: RunStatus;
  models: string[];
  budget_usd: number;
  cost_usd: number;
  latency_s: number | null;
  verdict: Verdict | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export type EventType =
  | "run_started" | "phase_started" | "answer" | "model_error"
  | "phase_finished" | "verdict" | "run_finished" | "run_failed";

export interface RunEvent {
  run_id: string;
  seq: number;
  type: EventType;
  phase: string | null;
  model_id: string | null;
  data: Record<string, any>;
  ts: string;
}

export interface PoolModel { id: string; model: string; local: boolean; enabled: boolean }

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json() as Promise<T>;
}

export const api = {
  runs: () => fetch("/api/runs").then((r) => json<Run[]>(r)),
  run: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<Run>(r)),
  models: () => fetch("/api/models").then((r) => json<PoolModel[]>(r)),
  createRun: (body: { question: string; context?: string; models?: string[] }) =>
    fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, source: "web" }),
    }).then((r) => json<Run>(r)),
};

const TERMINAL: EventType[] = ["run_finished", "run_failed"];
const TYPES: EventType[] = ["run_started", "phase_started", "answer", "model_error", "phase_finished", "verdict", "run_finished", "run_failed"];

/** Subscribe to a run's events over SSE. Replays stored events first, then streams live ones. */
export function streamRun(id: string, onEvent: (e: RunEvent) => void): () => void {
  const es = new EventSource(`/api/runs/${id}/stream`);
  const handler = (msg: MessageEvent) => {
    const ev = JSON.parse(msg.data) as RunEvent;
    onEvent(ev);
    if (TERMINAL.includes(ev.type)) es.close();
  };
  TYPES.forEach((t) => es.addEventListener(t, handler as EventListener));
  return () => es.close();
}
