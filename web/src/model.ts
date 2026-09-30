import type { Mode, Run, RunEvent, Usage, Verdict } from "./api";

export interface Answer {
  modelId: string; text: string; position: string; usage: Usage; seq: number; findings?: any[]; overall?: string;
  answer?: string; confidence?: number | null; filename?: string; code?: string; round?: number;
}
export interface TestResult { modelId: string; seq: number; passed: boolean; exit_code: number; output: string; duration_s: number; round: number; filename: string }
export interface Route { mode: string; reason: string; by: string; escalated: boolean; seq: number }
export interface ModelError { modelId: string; error: string; seq: number }
export interface Critique {
  modelId: string; seq: number;
  position?: string; changed?: boolean; reason?: string | null;
  objections?: { target: string; text: string }[]; supports?: { target: string; text: string }[];
  votes?: { id: string; vote: "confirm" | "refute" | "unsure"; reason: string }[];
}

export interface Phase {
  key: string;
  status: "pending" | "running" | "done" | "skipped";
  answers: Answer[];
  tests: TestResult[];
  skipReason?: string;
  started?: Record<string, any>;
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
  routes: Route[];
}

export interface PhaseInfo { key: string; title: string; hint: string; planned?: boolean }

const ROUTE: PhaseInfo = { key: "route", title: "Маршрутизация", hint: "Авто-режим: правило или самая дешёвая модель выбирает протокол" };

export function phasesFor(mode: Mode, auto = false): PhaseInfo[] {
  const pre = auto ? [ROUTE] : [];
  if (mode === "review")
    return [...pre,
      { key: "independent", title: "Независимые ревью", hint: "Каждая модель ревьюит материал сама, без чужих замечаний" },
      { key: "analyze", title: "Объединение находок", hint: "Дубликаты сводятся в одну находку, авторы скрыты" },
      { key: "critique", title: "Перекрёстная проверка", hint: "Каждая модель подтверждает или опровергает каждую находку — Objection!" },
      { key: "synthesize", title: "Вердикт", hint: "Считается кодом: pass / fail / uncertain по порогу серьёзности" },
    ];
  if (mode === "verify" || mode === "quick")
    return [...pre,
      { key: "independent", title: mode === "quick" ? "Два независимых ответа" : "Независимые ответы",
        hint: mode === "quick" ? "Две модели разных провайдеров дают короткий канонический ответ" : "Каждая модель даёт короткий канонический ответ и уверенность" },
      { key: "vote", title: "Голосование", hint: "Ответы нормализуются и взвешиваются: вес модели × уверенность" },
      { key: "critique", title: mode === "quick" ? "Эскалация и критика" : "Анти-конформная критика",
        hint: "Только при расхождении: все видят чужие ответы анонимно и ищут ошибки; менять ответ — только с доводом" },
      { key: "final", title: "Итоговое голосование", hint: "Повторный подсчёт после критики; вердикт считает код, не модель" },
    ];
  if (mode === "code")
    return [...pre,
      { key: "independent", title: "Кандидаты", hint: "Каждая модель пишет файл целиком, не видя других решений" },
      { key: "test", title: "Тесты", hint: "Команда тестов запускается на каждом кандидате во временной копии проекта" },
      { key: "fix", title: "Исправление", hint: "Если все упали — один раунд исправлений по логам тестов" },
      { key: "retest", title: "Повторные тесты", hint: "Тесты на исправленных версиях" },
      { key: "synthesize", title: "Выбор председателя", hint: "Только без команды тестов: председатель выбирает лучший вариант" },
    ];
  return [...pre,
    { key: "independent", title: "Независимые ответы", hint: "Модели отвечают параллельно, не видя ответов друг друга" },
    { key: "critique", title: "Перекрёстная критика", hint: "Каждая модель видит 2 чужих анонимных ответа и возражает" },
    { key: "synthesize", title: "Анализ и синтез", hint: "Председатель сводит позиции, сохраняя разногласия" },
  ];
}

/** Resolved mode: the stored run says "auto" until it finishes, the first `route` event knows earlier. */
export function effectiveMode(run: Run, events: RunEvent[]): Mode {
  if (run.mode !== "auto") return run.mode;
  const r = events.find((e) => e.type === "route" && !e.data.escalated);
  return (r?.data.mode as Mode) ?? "auto";
}

export function reduce(mode: Mode, events: RunEvent[], auto = false): RunState {
  const phases = new Map<string, Phase>(
    phasesFor(mode, auto).map((p) => [p.key, { key: p.key, status: "pending", answers: [], errors: [], critiques: [], tests: [] }]),
  );
  const st: RunState = { phases: [], verdict: null, failed: null, finished: false, agreeing: [], routes: [] };
  for (const e of events) {
    const ph = e.phase ? phases.get(e.phase) : undefined;
    switch (e.type) {
      case "phase_started":
        if (ph) { ph.status = e.data.skipped ? "skipped" : "running"; ph.judge = e.data.judge; ph.skipReason = e.data.reason; ph.started = e.data; }
        break;
      case "answer":
        ph?.answers.push({ modelId: e.model_id!, text: e.data.text, position: e.data.position, usage: e.data.usage, seq: e.seq, findings: e.data.findings, overall: e.data.overall,
          answer: e.data.answer, confidence: e.data.confidence, filename: e.data.filename, code: e.data.code, round: e.data.round });
        break;
      case "route": {
        st.routes.push({ mode: e.data.mode, reason: e.data.reason, by: e.data.by, escalated: !!e.data.escalated, seq: e.seq });
        const r = phases.get("route");
        if (r && !e.data.escalated) { r.status = "done"; r.finished = { ...e.data }; }
        break;
      }
      case "test_result":
        ph?.tests.push({ modelId: e.model_id!, seq: e.seq, ...(e.data as any) });
        break;
      case "model_error":
        ph?.errors.push({ modelId: e.model_id!, error: e.data.error, seq: e.seq });
        break;
      case "critique":
        ph?.critiques.push({ modelId: e.model_id!, seq: e.seq, ...e.data });
        break;
      case "phase_finished":
        if (ph) { ph.status = e.data.skipped ? "skipped" : "done"; ph.finished = e.data; if (e.data.judge) ph.judge = e.data.judge; }
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

const EMPTY: Phase = { key: "", status: "pending", answers: [], errors: [], critiques: [], tests: [] };
export const phase = (st: RunState, key: string): Phase => st.phases.find((p) => p.key === key) ?? { ...EMPTY, key };
export const MODE_LABEL: Record<string, string> = {
  auto: "авто", deliberate: "вопрос", review: "ревью", verify: "проверка", quick: "быстро", code: "код",
};
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
