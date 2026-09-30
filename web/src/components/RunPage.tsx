import { useEffect, useMemo, useState } from "react";
import { api, streamRun, type NewRun, type PoolModel, type Run, type RunEvent } from "../api";
import { MODE_LABEL, effectiveMode, reduce } from "../model";
import ChatView from "./ChatView";
import TraceView from "./TraceView";
import DebateView from "./DebateView";
import ReportView from "./FactCheck";
import { go } from "../App";

type Tab = "chat" | "trace" | "debate" | "report";
const STATUS: Record<string, [string, string]> = {
  queued: ["n", "в очереди"], running: ["o", "выполняется"], done: ["g", "готово"], failed: ["r", "ошибка"],
};

export default function RunPage(props: {
  runId: string; pool: PoolModel[];
  onSubmit: (b: NewRun) => Promise<void>; onChanged: () => void;
}) {
  const { runId, pool, onSubmit, onChanged } = props;
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tab, setTab] = useState<Tab>(() => (sessionStorage.getItem("objection.tab") as Tab) || "chat");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.run(runId).then(setRun).catch((e) => setErr(String(e)));
    const stop = streamRun(runId, (ev) => {
      setEvents((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev].sort((a, b) => a.seq - b.seq)));
      if (ev.type === "run_finished" || ev.type === "run_failed") {
        api.run(runId).then(setRun);
        onChanged();
      }
    });
    return stop;
  }, [runId, onChanged]);

  useEffect(() => sessionStorage.setItem("objection.tab", tab), [tab]);
  const mode = run ? effectiveMode(run, events) : "deliberate";
  const auto = run?.requested_mode === "auto";
  const state = useMemo(() => reduce(mode, events, auto), [mode, events, auto]);
  const view = useMemo(() => (run ? { ...run, mode } : null), [run, mode]);

  if (err) return <div className="empty">Запуск не найден: {err}</div>;
  if (!run || !view) return <div className="empty"><span className="spin" style={{ display: "inline-block" }} /></div>;

  const status = state.failed ? "failed" : state.finished ? "done" : run.status === "queued" && events.length ? "running" : run.status;
  const [tone, label] = STATUS[status];

  return (
    <>
      <div className="topbar">
        <h3 title={run.question}>{run.mode === "review" ? `Ревью: ${run.question}` : run.question}</h3>
        <span className="tag b" title={run.route_reason ?? state.routes[0]?.reason ?? undefined}>
          {auto ? "авто → " : ""}{mode === "review" ? `ревью · ${run.target_kind}` : MODE_LABEL[mode] ?? mode}
          {state.verdict?.escalated_to ? ` → ${MODE_LABEL[state.verdict.escalated_to]}` : ""}
        </span>
        <span className={`tag ${tone}`}>{label}</span>
        {run.cached && <span className="tag n" title="Тот же запрос уже отвечен — результат взят из кэша">из кэша</span>}
        {run.source !== "web" && <span className="tag n">источник: {run.source}</span>}
        {(run.excluded_models?.length ?? 0) > 0 && (
          <span className="tag o" title={run.excluded_models!.map((e) => `${e.id}: ${e.reason}`).join("\n")}>
            исключены: {run.excluded_models!.map((e) => e.id).join(", ")}
          </span>
        )}
        <nav className="tabs" aria-label="Вид запуска">
          <button className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>Чат</button>
          <button className={tab === "debate" ? "on" : ""} onClick={() => setTab("debate")}>Спор</button>
          <button className={tab === "trace" ? "on" : ""} onClick={() => setTab("trace")}>Ход запуска</button>
          <button className={tab === "report" ? "on" : ""} onClick={() => setTab("report")}>Отчёт</button>
        </nav>
        {state.finished && run.status === "done" && <LabelButtons run={run} onLabel={(r) => { setRun(r); onChanged(); }} />}
        <button className="btn sm danger" disabled={!state.finished && !state.failed} title={state.finished ? "Удалить запуск и его историю" : "Удалить можно после завершения"}
          onClick={async () => {
            if (!confirm("Удалить этот запуск вместе с ходом и результатом? Отменить нельзя.")) return;
            try { await api.deleteRun(run.id); onChanged(); go({ page: "overview" }); } catch (e) { alert(String(e)); }
          }}>Удалить</button>
      </div>
      {tab === "chat" ? (
        <ChatView run={view} state={state} pool={pool} onSubmit={onSubmit} />
      ) : tab === "report" ? (
        <ReportView run={view} state={state} />
      ) : tab === "debate" ? (
        <DebateView run={view} state={state} />
      ) : (
        <TraceView run={view} auto={auto} state={state} events={events} />
      )}
    </>
  );
}

/** Human judgement of the final answer: labelled runs become your own eval set (`objection eval run --suite mine`). */
function LabelButtons({ run, onLabel }: { run: Run; onLabel: (r: Run) => void }) {
  const [busy, setBusy] = useState(false);
  const set = async (label: "correct" | "wrong" | null) => {
    let expected: string | undefined;
    if (label === "wrong" && run.mode !== "review" && run.mode !== "code") {
      expected = prompt("Какой ответ правильный? (необязательно — поможет оценить каждую модель)") ?? undefined;
    }
    setBusy(true);
    try { onLabel(await api.label(run.id, run.label === label ? null : label, expected)); }
    catch (e) { alert(String(e)); }
    finally { setBusy(false); }
  };
  return (
    <span className="label-btns" title={run.expected ? `правильный ответ: ${run.expected}` : "Оценка итога: попадёт в ваш набор для eval"}>
      <button className={`btn sm ${run.label === "correct" ? "on g" : ""}`} disabled={busy} aria-pressed={run.label === "correct"}
        onClick={() => set("correct")}>👍 верно</button>
      <button className={`btn sm ${run.label === "wrong" ? "on r" : ""}`} disabled={busy} aria-pressed={run.label === "wrong"}
        onClick={() => set("wrong")}>👎 неверно</button>
    </span>
  );
}
