export type RunStatus = "queued" | "running" | "done" | "failed";

export interface Usage { input_tokens: number; output_tokens: number; cost_usd: number; latency_s: number }

export interface Disputed { point: string; positions: Record<string, string> }

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type ReviewVerdict = "pass" | "fail" | "uncertain";
export type Mode = "deliberate" | "review";

export interface Finding {
  id: string;
  title: string;
  severity: Severity;
  location: string | null;
  detail: string;
  suggestion: string | null;
  reported_by: string[];
  confirmed_by: string[];
  refuted_by: { model_id: string; reason: string }[];
  status: "confirmed" | "disputed" | "rejected";
}

export interface Verdict {
  answer: string;
  verdict: ReviewVerdict | null;
  findings: Finding[];
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
  mode: Mode;
  source: string;
  target: string | null;
  target_kind: string;
  fail_on: Severity;
  cached: boolean;
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
  | "run_started" | "phase_started" | "answer" | "model_error" | "critique"
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
  if (!res.ok) {
    const text = await res.text();
    let msg = text || res.statusText;
    try {
      const d = JSON.parse(text).detail;
      msg = Array.isArray(d) ? d.map((e: any) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`).join("; ") : String(d ?? msg);
    } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export interface Stats {
  days: number;
  runs: number;
  by_source: Record<string, number>;
  by_mode: Record<string, number>;
  by_status: Record<string, number>;
  cost_usd: number;
  avg_cost_usd: number;
  review_verdicts: Record<string, number>;
  disputed_share: number | null;
  cost_by_day: { date: string; cost_usd: number }[];
}

export interface ModelCheck { id: string; ok: boolean; detail: string; latency_s: number }

export interface NewRun {
  question: string;
  mode?: Mode;
  target?: string;
  target_kind?: string;
  context?: string;
  models?: string[];
}

export interface ModelSpec {
  id: string; model: string; api_base?: string | null; api_key_env?: string | null;
  timeout_s?: number | null; max_parallel?: number | null; params?: Record<string, any>; enabled?: boolean; local?: boolean;
}
export interface Provider {
  id: string; label: string; prefix: string; env: string | null; catalog?: string; local?: boolean;
  optional_key?: boolean; custom_key?: boolean; api_base?: string; discover?: "ollama" | "openai";
}
export interface KeyStatus { env: string; set: boolean; source: "file" | "env" | null; masked: string | null }
export interface Defaults {
  mode: string; council: { size: number; selection: string; pinned: string[] }; judge: string;
  budget_usd: number; timeout_s: number; anonymize: boolean; max_critique_rounds: number;
}
export interface Settings {
  config_path: string; config_exists: boolean; secrets_path: string;
  models: ModelSpec[]; defaults: Defaults; providers: Provider[]; keys: KeyStatus[];
}
export interface ProbeResult { ok: boolean; detail: string; latency_s: number; reply: string | null; cost_usd: number }
export interface CatalogEntry { name: string; input_per_mtok: number | null; output_per_mtok: number | null; context: number | null }

const send = <T,>(method: string, url: string, body: unknown) =>
  fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => json<T>(r));

export const settingsApi = {
  get: () => fetch("/api/settings").then((r) => json<Settings>(r)),
  saveModels: (models: ModelSpec[]) => send<Settings>("PUT", "/api/settings/models", models.map(({ local, ...m }) => m)),
  saveDefaults: (d: Defaults) => send<Settings>("PUT", "/api/settings/defaults", d),
  saveKeys: (values: Record<string, string | null>) => send<Settings>("PUT", "/api/settings/keys", { values }),
  test: (spec: ModelSpec, api_key?: string) => send<ProbeResult>("POST", "/api/settings/test", { spec: (({ local, ...m }) => m)(spec), api_key: api_key || null }),
  catalog: (provider: string) => fetch(`/api/settings/catalog?provider=${encodeURIComponent(provider)}`).then((r) => json<CatalogEntry[]>(r)),
  discover: (provider: string, api_base: string, api_key_env?: string | null, api_key?: string) =>
    send<string[]>("POST", "/api/settings/discover", { provider, api_base, api_key_env: api_key_env || null, api_key: api_key || null }),
};

export const api = {
  stats: (days = 7) => fetch(`/api/stats?days=${days}`).then((r) => json<Stats>(r)),
  checkModels: () => fetch("/api/models/check", { method: "POST" }).then((r) => json<ModelCheck[]>(r)),
  runs: () => fetch("/api/runs").then((r) => json<Run[]>(r)),
  run: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<Run>(r)),
  models: () => fetch("/api/models").then((r) => json<PoolModel[]>(r)),
  createRun: (body: NewRun) =>
    fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, source: "web" }),
    }).then((r) => json<Run>(r)),
};

const TERMINAL: EventType[] = ["run_finished", "run_failed"];
const TYPES: EventType[] = ["run_started", "phase_started", "answer", "model_error", "critique", "phase_finished", "verdict", "run_finished", "run_failed"];

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
