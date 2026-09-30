import type { Run } from "../api";
import { SEVERITY, FSTATUS, avatarClass, letter, phase, type RunState } from "../model";
import { VerdictBanner } from "./Findings";

const MARK: Record<string, string> = { confirm: "✓", refute: "×", unsure: "?", none: "·" };

function ReviewMatrix({ run, state }: { run: Run; state: RunState }) {
  const v = state.verdict;
  const crit = phase(state, "critique");
  const findings = v?.findings ?? (phase(state, "analyze").finished?.findings as any[] | undefined) ?? [];
  const voteOf = (model: string, fid: string) => crit.critiques.find((c) => c.modelId === model)?.votes?.find((x) => x.id === fid);
  if (!findings.length)
    return <div className="empty">{state.finished ? "Находок нет — спорить не о чем." : "Матрица появится после объединения находок…"}</div>;
  return (
    <div className="ov">
      {v && <VerdictBanner run={{ ...run, verdict: v }} />}
      <div className="card" style={{ overflowX: "auto" }}>
        <table className="matrix">
          <thead>
            <tr>
              <th>Находка</th><th>Серьёзность</th>
              {run.models.map((m) => (
                <th key={m} style={{ textAlign: "center" }} title={m}><span className={`${avatarClass(run.models, m)} xs`}>{letter(run.models, m)}</span></th>
              ))}
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {findings.map((f: any) => (
              <tr key={f.id}>
                <td><b>{f.id}</b> {f.title}{f.location && <div className="muted" style={{ fontSize: 12 }}>{f.location}</div>}</td>
                <td><span className={`tag ${SEVERITY[f.severity]?.[0] ?? "n"}`}>{SEVERITY[f.severity]?.[1] ?? f.severity}</span></td>
                {run.models.map((m) => {
                  const vote = voteOf(m, f.id);
                  const k = vote?.vote ?? "none";
                  const author = f.reported_by?.includes(m);
                  return (
                    <td key={m} style={{ textAlign: "center" }}>
                      <span className={`cell ${k} ${author ? "author" : ""}`} title={`${m}${author ? " (нашёл)" : ""}: ${vote ? `${vote.vote} — ${vote.reason}` : "не голосовал"}`}>{MARK[k]}</span>
                    </td>
                  );
                })}
                <td>{f.status && <span className={`tag ${FSTATUS[f.status][0]}`}>{FSTATUS[f.status][1]}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="votes muted">
        <span className="cell confirm">✓</span> подтвердил <span className="cell refute">×</span> опроверг
        <span className="cell unsure">?</span> не уверен <span className="cell none author">·</span> рамка — автор находки.
        Подтверждение требует ≥2 голосов и перевеса над опровержениями.
      </div>
    </div>
  );
}

function CodeMatrix({ run, state }: { run: Run; state: RunState }) {
  const tests = [...phase(state, "test").tests, ...phase(state, "retest").tests];
  const cands = [...phase(state, "independent").answers, ...phase(state, "fix").answers];
  if (!cands.length) return <div className="empty">Матрица появится, когда модели пришлют решения…</div>;
  const cell = (m: string, round: number) => {
    const a = cands.find((c) => c.modelId === m && (c.round ?? 1) === round);
    const t = tests.find((x) => x.modelId === m && x.round === round);
    if (!a) return <span className="cell none">·</span>;
    if (!t) return <span className="cell unsure" title="не тестировалось">?</span>;
    return <span className={`cell ${t.passed ? "confirm" : "refute"}`} title={t.output}>{t.passed ? "✓" : "×"}</span>;
  };
  const winner = state.verdict?.solution;
  return (
    <div className="ov">
      <div className="card" style={{ overflowX: "auto" }}>
        <table className="matrix">
          <thead><tr><th>Модель</th><th>Файл</th><th style={{ textAlign: "center" }}>Раунд 1</th><th style={{ textAlign: "center" }}>Исправление</th><th>Итог</th></tr></thead>
          <tbody>
            {run.models.map((m) => {
              const a = cands.find((c) => c.modelId === m);
              return (
                <tr key={m}>
                  <td><span className={`${avatarClass(run.models, m)} xs`}>{letter(run.models, m)}</span> {m}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{a?.filename ?? "—"}</td>
                  <td style={{ textAlign: "center" }}>{cell(m, 1)}</td>
                  <td style={{ textAlign: "center" }}>{cell(m, 2)}</td>
                  <td>{winner?.model_id === m ? <span className="tag g">победитель</span> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="votes muted">
        <span className="cell confirm">✓</span> тесты прошли <span className="cell refute">×</span> упали
        <span className="cell unsure">?</span> без тестов. Решают тесты, а не мнение моделей; при нескольких прошедших — самое короткое решение первого раунда.
      </div>
    </div>
  );
}

function Arena({ run, state }: { run: Run; state: RunState }) {
  const ind = phase(state, "independent");
  const crit = phase(state, "critique");
  const models = run.models.filter((m) => ind.answers.some((a) => a.modelId === m) || ind.errors.some((e) => e.modelId === m));
  if (!models.length) return <div className="empty">Спор начнётся, когда модели ответят…</div>;
  return (
    <div className="arena">
      {models.map((m) => {
        const a = ind.answers.find((x) => x.modelId === m);
        const e = ind.errors.find((x) => x.modelId === m);
        const c = crit.critiques.find((x) => x.modelId === m);
        const received = crit.critiques.flatMap((x) => (x.objections ?? []).filter((o) => o.target === m).map((o) => ({ from: x.modelId, text: o.text })));
        const agree = state.agreeing.includes(m);
        return (
          <div key={m} className="card col">
            <div className="who" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className={avatarClass(run.models, m)}>{letter(run.models, m)}</span><b>{m}</b>
              {c?.changed && <span className="tag o">сменил позицию</span>}
              {agree && <span className="tag g">за итог</span>}
            </div>
            {e ? <div className="err">{e.error}</div> : (
              <>
                <div className="pos">{c?.position ?? a?.position}{a?.confidence != null && !c ? <span className="tag n" style={{ marginLeft: 6 }}>{a.confidence.toFixed(2)}</span> : null}</div>
                {c?.changed && c.reason && <div className="shift"><span className="dot" style={{ background: "var(--o)" }} />{c.reason}</div>}
                {(c?.objections ?? []).map((o, i) => <div key={i} className="obj"><b>Objection! → {o.target}:</b> {o.text}</div>)}
                {received.map((o, i) => <div key={`r${i}`} className="obj" style={{ borderLeftColor: "var(--o)", background: "var(--oS)" }}><b style={{ color: "var(--oT)" }}>{o.from} возражает:</b> {o.text}</div>)}
                {crit.status === "running" && !c && <div className="pending"><span className="spin" />критикует…</div>}
                <div className="r1"><b>Раунд 1:</b> {a?.position}</div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function DebateView({ run, state }: { run: Run; state: RunState }) {
  return (
    <div className="body" style={{ display: "block" }}>
      <div className="scroll" style={{ height: "100%" }}>
        {run.mode === "review" ? <ReviewMatrix run={run} state={state} /> : run.mode === "code" ? <CodeMatrix run={run} state={state} /> : (
          <>
            <Arena run={run} state={state} />
            {state.verdict && (
              <div style={{ padding: "0 24px 24px" }}>
                <div className="final">
                  <div className="who" style={{ fontSize: 14 }}><span className="logo" style={{ fontSize: 14 }}>Objection<b>!</b></span> Итог
                    {state.verdict.agreement && <span className="tag g">согласие {state.verdict.agreement}</span>}</div>
                  <div className="txt" style={{ marginTop: 8 }}>{state.verdict.answer}</div>
                  {state.verdict.disputed.map((d, i) => (
                    <div key={i} className="cl"><span className="dot" style={{ background: "var(--o)" }} />{d.point}</div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
