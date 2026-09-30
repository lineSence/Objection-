import type { Mode, RunEvent, Usage, Verdict } from "./api";

export interface Answer { modelId: string; text: string; position: string; usage: Usage; seq: number; findings?: any[]; overall?: string }
export interface ModelError { modelId: string; error: string; seq: number }
export interface Critique {
  modelId: string; seq: number;
  position?: string; changed?: boolean; reason?: string | null;
  objections?: { target: string; text: string }[]; supports?: { target: string; text: string }[];
  votes?: { id: string; vote: "confirm" | "refute" | "unsure"; reason: string }[];
}

export interface Phase {
  key: string;
  status: "pending" | "running" | "done";
  answers: Answer[];
  errors: ModelError[];
  critiques: Critique[];
  judge?: string;
  finished?: Record<string, any>;
}

export interface RunState {
  phases: Phase[];
  verdict: Verdict | null;
  failed: string | null;
  finished: boolean;
  agreeing: string[];
}

export interface PhaseInfo { key: string; title: string; hint: string; planned?: boolean }

export function phasesFor(mode: Mode): PhaseInfo[] {
  if (mode === "review")
    return [
      { key: "independent", title: "Независимые ревью", hint: "Каждая модель ревьюит материал сама, без чужих замечаний" },
      { key: "analyze", title: "Объединение находок", hint: "Дубликаты сводятся в одну находку, авторы скрыты" },
      { key: "critique", title: "Перекрёстная проверка", hint: "Каждая модель подтверждает или опровергает каждую находку — Objection!" },
      { key: "verify", title: "Проверка фактов", hint: "Появится в M3: запуск тестов, песочница", planned: true },
      { key: "synthesize", title: "Вердикт", hint: "Считается кодом: pass / fail / uncertain по порогу серьёзности" },
    ];
  return [
    { key: "independent", title: "Независимые ответы", hint: "Модели отвечают параллельно, не видя ответов друг друга" },
    { key: "critique", title: "Перекрёстная критика", hint: "Каждая модель видит 2 чужих анонимных ответа и возражает" },
    { key: "verify", title: "Проверка фактов", hint: "Появится в M3: поиск, python-песочница, тесты", planned: true },
    { key: "synthesize", title: "Анализ и синтез", hint: "Председатель сводит позиции, сохраняя разногласия" },
  ];
}

export function reduce(mode: Mode, events: RunEvent[]): RunState {
  const phases = new Map<string, Phase>(
    phasesFor(mode).map((p) => [p.key, { key: p.key, status: "pending", answers: [], errors: [], critiques: [] }]),
  );
  const st: RunState = { phases: [], verdict: null, failed: null, finished: false, agreeing: [] };
  for (const e of events) {
    const ph = e.phase ? phases.get(e.phase) : undefined;
    switch (e.type) {
      case "phase_started":
        if (ph) { ph.status = "running"; ph.judge = e.data.judge; }
        break;
      case "answer":
        ph?.answers.push({ modelId: e.model_id!, text: e.data.text, position: e.data.position, usage: e.data.usage, seq: e.seq, findings: e.data.findings, overall: e.data.overall });
        break;
      case "model_error":
        ph?.errors.push({ modelId: e.model_id!, error: e.data.error, seq: e.seq });
        break;
      case "critique":
        ph?.critiques.push({ modelId: e.model_id!, seq: e.seq, ...e.data });
        break;
      case "phase_finished":
        if (ph) { ph.status = "done"; ph.finished = e.data; if (e.data.judge) ph.judge = e.data.judge; }
        if (e.data.agreeing) st.agreeing = e.data.agreeing;
        break;
      case "verdict":
        st.verdict = e.data as Verdict;
        if (mode === "review") { const s = phases.get("synthesize"); if (s) s.status = "done"; }
        break;
      case "run_failed":
        st.failed = e.data.error;
        st.finished = true;
        break;
      case "run_finished":
        st.finished = true;
    }
  }
  st.phases = [...phases.values()];
  return st;
}

export const phase = (st: RunState, key: string) => st.phases.find((p) => p.key === key)!;
export const letter = (models: string[], id: string) => String.fromCharCode(65 + Math.max(0, models.indexOf(id)));
export const avatarClass = (models: string[], id: string) => `av m${(Math.max(0, models.indexOf(id)) % 5) + 1}`;
export const money = (v: number) => `$${v.toFixed(v < 0.01 ? 4 : 3)}`;

export const SEVERITY: Record<string, [string, string]> = {
  critical: ["r", "критично"], high: ["r", "высокая"], medium: ["o", "средняя"], low: ["n", "низкая"], info: ["n", "инфо"],
};
export const FSTATUS: Record<string, [string, string]> = {
  confirmed: ["g", "подтверждено"], disputed: ["o", "спорно"], rejected: ["n", "отклонено"],
};
export const RVERDICT: Record<string, [string, string]> = {
  pass: ["g", "pass"], fail: ["r", "fail"], uncertain: ["o", "uncertain"],
};
export const SOURCE: Record<string, string> = { web: "Web", cli: "CLI", opencode: "OpenCode", cline: "Cline", mcp: "MCP" };
