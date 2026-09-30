import { useEffect, useMemo, useState } from "react";
import { api, streamRun, type PoolModel, type Run, type RunEvent } from "../api";
import { reduce } from "../model";
import ChatView from "./ChatView";
import TraceView from "./TraceView";

type Tab = "chat" | "trace";
const STATUS: Record<string, [string, string]> = {
  queued: ["n", "в очереди"], running: ["o", "выполняется"], done: ["g", "готово"], failed: ["r", "ошибка"],
};

export default function RunPage(props: {
  runId: string; pool: PoolModel[];
  onSubmit: (q: string, models: string[]) => Promise<void>; onChanged: () => void;
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
  const state = useMemo(() => reduce(events), [events]);

  if (err) return <div className="empty">Запуск не найден: {err}</div>;
  if (!run) return <div className="empty"><span className="spin" style={{ display: "inline-block" }} /></div>;

  const status = state.failed ? "failed" : state.finished ? "done" : run.status === "queued" && events.length ? "running" : run.status;
  const [tone, label] = STATUS[status];

  return (
    <>
      <div className="topbar">
        <h3 title={run.question}>{run.question}</h3>
        <span className="tag b">{run.mode}</span>
        <span className={`tag ${tone}`}>{label}</span>
        {run.source !== "web" && <span className="tag n">источник: {run.source}</span>}
        <nav className="tabs" aria-label="Вид запуска">
          <button className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>Чат</button>
          <button className={tab === "trace" ? "on" : ""} onClick={() => setTab("trace")}>Ход запуска</button>
          <button disabled title="Появится в M1">Спор</button>
          <button disabled title="Появится в M3">Отчёт</button>
        </nav>
      </div>
      {tab === "chat" ? (
        <ChatView run={run} state={state} pool={pool} onSubmit={onSubmit} />
      ) : (
        <TraceView run={run} state={state} events={events} />
      )}
    </>
  );
}
