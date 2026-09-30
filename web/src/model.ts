import type { RunEvent, Usage, Verdict } from "./api";

export interface Answer { modelId: string; text: string; position: string; usage: Usage; seq: number }
export interface ModelError { modelId: string; error: string }

export interface Phase {
  key: string;
  status: "pending" | "running" | "done";
  answers: Answer[];
  errors: ModelError[];
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

export const PHASES: { key: string; title: string; hint: string }[] = [
  { key: "independent", title: "Независимые ответы", hint: "Модели отвечают параллельно, не видя ответов друг друга" },
  { key: "critique", title: "Перекрёстная критика", hint: "Появится в M1: один анонимный раунд Objection!" },
  { key: "verify", title: "Проверка фактов", hint: "Появится в M3: поиск, python-песочница, тесты" },
  { key: "synthesize", title: "Анализ и синтез", hint: "Председатель сводит позиции, сохраняя разногласия" },
];

export function reduce(events: RunEvent[]): RunState {
  const phases = new Map<string, Phase>(
    PHASES.map((p) => [p.key, { key: p.key, status: "pending", answers: [], errors: [] }]),
  );
  const st: RunState = { phases: [], verdict: null, failed: null, finished: false, agreeing: [] };
  for (const e of events) {
    const ph = e.phase ? phases.get(e.phase) : undefined;
    switch (e.type) {
      case "phase_started":
        if (ph) { ph.status = "running"; ph.judge = e.data.judge; }
        break;
      case "answer":
        ph?.answers.push({ modelId: e.model_id!, text: e.data.text, position: e.data.position, usage: e.data.usage, seq: e.seq });
        break;
      case "model_error":
        ph?.errors.push({ modelId: e.model_id!, error: e.data.error });
        break;
      case "phase_finished":
        if (ph) { ph.status = "done"; ph.finished = e.data; if (e.data.judge) ph.judge = e.data.judge; }
        if (e.data.agreeing) st.agreeing = e.data.agreeing;
        break;
      case "verdict":
        st.verdict = e.data as Verdict;
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

export const letter = (models: string[], id: string) => String.fromCharCode(65 + Math.max(0, models.indexOf(id)));
export const avatarClass = (models: string[], id: string) => `av m${(Math.max(0, models.indexOf(id)) % 5) + 1}`;
export const money = (v: number) => `$${v.toFixed(v < 0.01 ? 4 : 3)}`;
