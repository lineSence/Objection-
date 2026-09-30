import { useState } from "react";
import type { Run, RunEvent } from "../api";
import { phasesFor, avatarClass, letter, money, type RunState } from "../model";

export default function TraceView({ run, state, events, auto }: { run: Run; state: RunState; events: RunEvent[]; auto?: boolean }) {
  const [sel, setSel] = useState<number | null>(null);
  const selected = events.find((e) => e.seq === sel) ?? events.find((e) => e.type === "verdict") ?? null;

  return (
    <div className="body trace">
      <div className="scroll">
        <div className="steps">
          <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
            run {run.id} · {run.models.length} модели · бюджет {money(run.budget_usd)}
          </div>
          {phasesFor(run.mode, auto).map((p, i) => {
            const ph = state.phases.find((x) => x.key === p.key)!;
            const planned = !!p.planned;
            const failedHere = state.failed && ph.status === "running";
            const ic = failedHere ? "fail" : ph.status;
            const phaseEvents = events.filter((e) => e.phase === p.key && ["answer", "model_error", "critique", "test_result", "route", "tool_call", "claim_checked"].includes(e.type));
            const skipped = ph.status === "skipped";
            return (
              <div key={p.key} className={`st ${planned || skipped ? "off" : ""}`}>
                <span className={`ic ${ic}`}>{ph.status === "running" && !failedHere ? <span className="spin" style={{ borderTopColor: "#fff" }} /> : i + 1}</span>
                <div style={{ minWidth: 0 }}>
                  <div className="t">{p.title}{ph.judge ? ` · председатель ${ph.judge}` : ""}</div>
                  <div className="d">{skipped ? `Пропущено: ${SKIP[ph.skipReason ?? ""] ?? ph.skipReason ?? ""}` : p.hint}</div>
                  {phaseEvents.length > 0 && (
                    <div className="sub">
                      {phaseEvents.map((e) => (
                        <button key={e.seq} className={`sb ${sel === e.seq ? "sel" : ""} ${e.type === "model_error" ? "bad" : ""}`} onClick={() => setSel(e.seq)}>
                          {e.model_id && run.models.includes(e.model_id) && <span className={`${avatarClass(run.models, e.model_id)} xs`}>{letter(run.models, e.model_id)}</span>}
                          <span>{label(e)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {p.key === "vote" || p.key === "final" ? events.filter((e) => e.phase === p.key && e.type === "phase_finished" && e.data.votes).map((e) => (
                    <div key={e.seq} className="sub">
                      <button className={`sb ${sel === e.seq ? "sel" : ""}`} onClick={() => setSel(e.seq)}>
                        <span>{e.data.unanimous ? "единогласно" : `согласие ${e.data.agreement}`} · {e.data.votes.map((v: any) => `${v.answer} (${v.weight})`).join(" · ")}</span>
                      </button>
                    </div>
                  )) : null}
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

const SKIP: Record<string, string> = {
  unanimous: "все ответили одинаково — ранняя остановка", "two models agreed": "две модели согласились — эскалация не нужна",
  "budget exhausted": "бюджет исчерпан", "single answer": "ответила только одна модель",
  "no more models to escalate to": "в совете нет других моделей",
  "fact-checking is off": "фактчек выключен для этого запуска",
};

function label(e: RunEvent): string {
  if (e.type === "model_error") return `${e.model_id} · ${e.data.budget ? "пропущен (бюджет)" : "ошибка"}`;
  if (e.type === "route") return e.data.escalated ? `эскалация → ${e.data.mode}` : `→ ${e.data.mode} · ${e.data.by}`;
  if (e.type === "tool_call") return e.data.tool === "search" ? `поиск · ${e.data.ok ? `${e.data.results?.length ?? 0} рез.` : "ошибка"}${e.data.flagged ? " · ⚠" : ""}` : `Python · ${e.data.ok ? "ok" : `код ${e.data.exit_code}`}`;
  if (e.type === "claim_checked") return `${e.data.id} · ${({ supported: "подтверждено", refuted: "опровергнуто", unverified: "не проверено" } as Record<string, string>)[e.data.status] ?? e.data.status}`;
  if (e.type === "test_result") return `${e.model_id} · ${e.data.passed ? "тесты прошли" : "тесты упали"} · ${e.data.duration_s} с`;
  if (e.type === "critique") return `${e.model_id} · ${e.data.votes ? `${e.data.votes.length} голосов` : e.data.changed ? "сменил позицию" : "критика"}`;
  return `${e.model_id} · ${e.data.usage?.output_tokens ?? 0} ток.`;
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
      {ev.type === "answer" && ev.data.answer && <div className="txt" style={{ fontWeight: 600 }}>{ev.data.answer}{ev.data.confidence != null ? ` · уверенность ${ev.data.confidence}` : ""}</div>}
      {ev.type === "answer" && <div className="txt" style={{ fontSize: 13 }}>{ev.data.text}</div>}
      {ev.type === "answer" && ev.data.code && <pre className="json">{ev.data.code}</pre>}
      {ev.type === "tool_call" && <div className="txt mono" style={{ fontSize: 12 }}>{ev.data.tool}: {String(ev.data.input ?? "").slice(0, 400)}</div>}
      {ev.type === "tool_call" && ev.data.error && <div className="err">{ev.data.error}</div>}
      {ev.type === "tool_call" && (ev.data.results ?? []).map((r: any, i: number) => <div key={i} className="src"><a href={r.url} target="_blank" rel="noreferrer noopener">{r.title || r.url}</a><div style={{ fontSize: 12 }}>{r.snippet}</div></div>)}
      {ev.type === "tool_call" && ev.data.output && <pre className="json">{ev.data.output}</pre>}
      {ev.type === "claim_checked" && <div className="txt" style={{ fontSize: 13 }}><b>{ev.data.status}</b>{ev.data.evidence ? ` — ${ev.data.evidence}` : ""}</div>}
      {ev.type === "route" && <div className="txt" style={{ fontSize: 13 }}><b>{ev.data.escalated ? "Эскалация" : "Режим"}: {ev.data.mode}</b> — {ev.data.reason}</div>}
      {ev.type === "test_result" && (
        <>
          <div className={ev.data.passed ? "sup" : "obj"}><b>{ev.data.passed ? "PASS" : `FAIL (код ${ev.data.exit_code})`}</b> · {ev.data.filename} · раунд {ev.data.round}</div>
          <pre className="json">{ev.data.output || "(нет вывода)"}</pre>
        </>
      )}
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
