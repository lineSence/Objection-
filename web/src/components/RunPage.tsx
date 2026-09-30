import { useEffect, useMemo, useState } from "react";
import { api, streamRun, type NewRun, type PoolModel, type Run, type RunEvent } from "../api";
import { MODE_LABEL, effectiveMode, reduce } from "../model";
import ChatView from "./ChatView";
import TraceView from "./TraceView";
import DebateView from "./DebateView";

type Tab = "chat" | "trace" | "debate";
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
        <nav className="tabs" aria-label="Вид запуска">
          <button className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>Чат</button>
          <button className={tab === "debate" ? "on" : ""} onClick={() => setTab("debate")}>Спор</button>
          <button className={tab === "trace" ? "on" : ""} onClick={() => setTab("trace")}>Ход запуска</button>
          <button disabled title="Появится в M3">Отчёт</button>
        </nav>
      </div>
      {tab === "chat" ? (
        <ChatView run={view} state={state} pool={pool} onSubmit={onSubmit} />
      ) : tab === "debate" ? (
        <DebateView run={view} state={state} />
      ) : (
        <TraceView run={view} auto={auto} state={state} events={events} />
      )}
    </>
  );
}
