import { useState } from "react";
import type { Run, RunEvent } from "../api";
import { phasesFor, avatarClass, letter, money, type RunState } from "../model";

export default function TraceView({ run, state, events }: { run: Run; state: RunState; events: RunEvent[] }) {
  const [sel, setSel] = useState<number | null>(null);
  const selected = events.find((e) => e.seq === sel) ?? events.find((e) => e.type === "verdict") ?? null;

  return (
    <div className="body trace">
      <div className="scroll">
        <div className="steps">
          <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
            run {run.id} · {run.models.length} модели · бюджет {money(run.budget_usd)}
          </div>
          {phasesFor(run.mode).map((p, i) => {
            const ph = state.phases.find((x) => x.key === p.key)!;
            const planned = !!p.planned;
            const failedHere = state.failed && ph.status === "running";
            const ic = failedHere ? "fail" : ph.status;
            const phaseEvents = events.filter((e) => e.phase === p.key && (e.type === "answer" || e.type === "model_error" || e.type === "critique"));
            return (
              <div key={p.key} className={`st ${planned ? "off" : ""}`}>
                <span className={`ic ${ic}`}>{ph.status === "running" && !failedHere ? <span className="spin" style={{ borderTopColor: "#fff" }} /> : i + 1}</span>
                <div style={{ minWidth: 0 }}>
                  <div className="t">{p.title}{ph.judge ? ` · председатель ${ph.judge}` : ""}</div>
                  <div className="d">{p.hint}</div>
                  {phaseEvents.length > 0 && (
                    <div className="sub">
                      {phaseEvents.map((e) => (
                        <button key={e.seq} className={`sb ${sel === e.seq ? "sel" : ""} ${e.type === "model_error" ? "bad" : ""}`} onClick={() => setSel(e.seq)}>
                          <span className={`${avatarClass(run.models, e.model_id!)} xs`}>{letter(run.models, e.model_id!)}</span>
                          <span>{e.model_id} · {e.type === "model_error" ? "ошибка" : e.type === "critique" ? (e.data.votes ? `${e.data.votes.length} голосов` : e.data.changed ? "сменил позицию" : "критика") : `${e.data.usage.output_tokens} ток.`}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {p.key === "analyze" && ph.status === "done" && (
                    <div className="sub">
                      {events.filter((e) => e.phase === "analyze" && e.type === "phase_finished").map((e) => (
                        <button key={e.seq} className={`sb ${sel === e.seq ? "sel" : ""}`} onClick={() => setSel(e.seq)}>
                          <span>{e.data.from ? `${e.data.from} → ${e.data.groups} находок` : "Объединение пропущено"}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {p.key === "synthesize" && ph.status === "done" && (
                    <div className="sub">
                      {events.filter((e) => e.phase === "synthesize" && e.type === "phase_finished").map((e) => (
                        <button key={e.seq} className={`sb ${sel === e.seq ? "sel" : ""}`} onClick={() => setSel(e.seq)}><span>Ответ председателя</span></button>
                      ))}
                      {events.filter((e) => e.type === "verdict").map((e) => (
                        <button key={e.seq} className={`sb ${sel === e.seq ? "sel" : ""}`} onClick={() => setSel(e.seq)}><span>Вердикт (JSON)</span></button>
                      ))}
                    </div>
                  )}
                </div>
                <span className="tm">{ph.finished?.latency_s != null ? `${ph.finished.latency_s} с` : ph.status === "running" ? "…" : "—"}</span>
              </div>
            );
          })}
          {state.failed && <div className="err" style={{ marginTop: 12 }}>{state.failed}</div>}
        </div>
      </div>
      <aside className="side scroll">
        {selected ? <Detail run={run} ev={selected} /> : <div className="muted">Выберите шаг слева, чтобы увидеть детали.</div>}
      </aside>
    </div>
  );
}

function Detail({ run, ev }: { run: Run; ev: RunEvent }) {
  const usage = ev.data.usage;
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {ev.model_id && <span className={avatarClass(run.models, ev.model_id)}>{letter(run.models, ev.model_id)}</span>}
        <b>{ev.model_id ?? "Совет"}</b>
        <span className="tag n">{ev.type}</span>
      </div>
      {ev.type === "answer" && <div className="txt" style={{ fontSize: 13 }}>{ev.data.text}</div>}
      {ev.type === "model_error" && <div className="err">{ev.data.error}</div>}
      {ev.type === "critique" && ev.data.position && <div className="txt" style={{ fontSize: 13, fontWeight: 600 }}>{ev.data.position}</div>}
      {ev.type === "critique" && (ev.data.objections ?? []).map((o: any, i: number) => <div key={i} className="obj"><b>Objection! → {o.target}:</b> {o.text}</div>)}
      {ev.type === "critique" && (ev.data.votes ?? []).map((v: any, i: number) => (
        <div key={i} className={v.vote === "refute" ? "obj" : v.vote === "confirm" ? "sup" : "cl"}><b>{v.id} · {v.vote}:</b> {v.reason}</div>
      ))}
      {usage && (
        <div>
          <div className="lbl" style={{ marginBottom: 4 }}>Токены и стоимость</div>
          <div className="row"><span className="muted">вход / выход</span><span>{usage.input_tokens} / {usage.output_tokens}</span></div>
          <div className="row"><span className="muted">время</span><span>{usage.latency_s} с</span></div>
          <div className="row"><span className="muted">стоимость</span><span>{money(usage.cost_usd)}</span></div>
        </div>
      )}
      <div className="lbl">Событие #{ev.seq}</div>
      <pre className="json">{JSON.stringify(ev.data, null, 2)}</pre>
    </>
  );
}
