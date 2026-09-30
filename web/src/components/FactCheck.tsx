import { useState } from "react";
import type { Claim, Run } from "../api";
import { CLAIM, avatarClass, letter, phase, type RunState } from "../model";
import { CodeBlock } from "./M2Parts";
import { FindingCard, VerdictBanner } from "./Findings";

const METHOD: Record<string, string> = { search: "поиск", python: "Python", repo: "репозиторий", none: "без инструмента" };
const SKIP: Record<string, string> = {
  "fact-checking is off": "фактчек выключен для этого запуска", "budget exhausted": "бюджет исчерпан",
};

export function ClaimRow({ run, c, open: initial = false }: { run: Run; c: Claim & { pending?: boolean }; open?: boolean }) {
  const [open, setOpen] = useState(initial);
  const [tone, label] = CLAIM[c.status];
  return (
    <div className={`claim ${c.status}`} id={`claim-${c.id}`}>
      <div className="claim-h" onClick={() => setOpen(!open)}>
        <span className="mono muted">{c.id}</span>
        {c.pending ? <span className="tag n"><span className="spin" style={{ width: 10, height: 10 }} />проверяю</span>
          : <span className={`tag ${tone}`}>{label}</span>}
        <span className="tag n">{METHOD[c.method] ?? c.method}</span>
        {c.flagged && <span className="tag o" title="В найденных страницах есть текст, похожий на prompt injection; он передан моделям только как данные">подозрительный источник</span>}
        <span className="claim-t">{c.text}</span>
      </div>
      {open && (
        <div className="claim-b">
          {c.evidence && <div className="txt" style={{ fontSize: 13 }}><b>Доказательство:</b> {c.evidence}</div>}
          {c.authors.length > 0 && (
            <div className="muted" style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>утверждали:
              {c.authors.map((m) => <span key={m} className="vm"><span className={`${avatarClass(run.models, m)} xs`}>{letter(run.models, m)}</span>{m}</span>)}
            </div>
          )}
          {Object.keys(c.judges).length > 0 && (
            <div className="muted" style={{ fontSize: 12 }}>судьи: {Object.entries(c.judges).map(([m, s]) => `${m} — ${CLAIM[s]?.[1] ?? s}`).join(" · ")}</div>
          )}
          {c.sources.map((s, i) => s.url.startsWith("repo://") ? (
            <div key={i} className="src">
              <b className="mono" style={{ fontSize: 12 }}>{s.title}</b>
              {s.snippet && <pre className="json" style={{ maxHeight: 160, margin: "4px 0 0" }}>{s.snippet}</pre>}
            </div>
          ) : (
            <div key={i} className="src">
              <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a>
              <div className="muted mono" style={{ fontSize: 11 }}>{s.url}</div>
              {s.snippet && <div style={{ fontSize: 12 }}>{s.snippet}</div>}
            </div>
          ))}
          {c.code && <CodeBlock code={c.code} name="проверочный скрипт (песочница)" />}
          {c.output && <pre className="json" style={{ maxHeight: 160 }}>{c.output}</pre>}
        </div>
      )}
    </div>
  );
}

export function ClaimsBlock({ run, state }: { run: Run; state: RunState }) {
  const ph = phase(state, "check");
  if (ph.status === "pending") return null;
  if (ph.status === "skipped") return <div className="muted" style={{ fontSize: 12 }}>Проверка фактов: {SKIP[ph.skipReason ?? ""] ?? ph.skipReason}</div>;
  return (
    <div className="grp">
      <div className="lbl">Проверка фактов</div>
      {state.claims.map((c) => <ClaimRow key={c.id} run={run} c={c} />)}
      {ph.status === "running" && !state.claims.length && <div className="pending"><span className="spin" />Выделяю проверяемые утверждения…</div>}
      {ph.status === "done" && !state.claims.length && <div className="muted" style={{ fontSize: 13 }}>Проверяемых утверждений не нашлось.</div>}
      {ph.errors.map((e) => <div key={e.seq} className="err"><b>{e.modelId}:</b> {e.error}</div>)}
    </div>
  );
}

export function FactNotes({ v }: { v: { revised?: boolean; fact_override?: string | null } }) {
  return (
    <>
      {v.fact_override && <div className="note o"><b>Факты перевесили голоса:</b> {v.fact_override}.</div>}
      {v.revised && <div className="note o"><b>Ответ исправлен после фактчека:</b> опровергнутые утверждения убраны или поправлены. Исходный вариант — в «Отчёте».</div>}
    </>
  );
}

/** Split `text` into plain parts and highlighted claim quotes (first occurrence of each quote). */
function highlight(text: string, claims: Claim[], onPick: (id: string) => void) {
  const marks: { start: number; end: number; c: Claim }[] = [];
  for (const c of claims) {
    if (!c.quote) continue;
    const start = text.indexOf(c.quote);
    if (start < 0 || marks.some((m) => start < m.end && start + c.quote!.length > m.start)) continue;
    marks.push({ start, end: start + c.quote.length, c });
  }
  marks.sort((a, b) => a.start - b.start);
  const out: (string | React.ReactNode)[] = [];
  let pos = 0;
  for (const m of marks) {
    out.push(text.slice(pos, m.start));
    out.push(<mark key={m.c.id} className={`hl ${m.c.status}`} title={`${m.c.id}: ${CLAIM[m.c.status][1]}`} onClick={() => onPick(m.c.id)}>{text.slice(m.start, m.end)}<sup>{m.c.id}</sup></mark>);
    pos = m.end;
  }
  out.push(text.slice(pos));
  return out;
}

export default function ReportView({ run, state }: { run: Run; state: RunState }) {
  const v = state.verdict;
  const [showOrig, setShowOrig] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  if (!v) return <div className="empty">{state.failed ? `Запуск завершился ошибкой: ${state.failed}` : "Отчёт появится, когда совет закончит…"}</div>;
  const claims = state.claims;
  const n = (s: string) => claims.filter((c) => c.status === s).length;
  const text = showOrig && v.original_answer ? v.original_answer : v.answer;
  const pick = (id: string) => { setPicked(id); document.getElementById(`claim-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); };
  return (
    <div className="body" style={{ display: "block" }}>
      <div className="scroll" style={{ height: "100%" }}>
        <div className="report">
          <h2 style={{ margin: 0 }}>{run.mode === "review" ? `Ревью: ${run.question}` : run.question}</h2>
          <div className="muted" style={{ fontSize: 13 }}>run {run.id} · {run.models.join(", ")}{v.agreement ? ` · согласие ${v.agreement}` : ""}</div>
          {run.mode === "review" && <VerdictBanner run={{ ...run, verdict: v }} />}
          <FactNotes v={v} />
          <div className="card rep-answer">
            <div className="lbl" style={{ display: "flex", justifyContent: "space-between" }}>
              <span>{showOrig ? "Исходный ответ (до фактчека)" : "Итог"}</span>
              {v.original_answer && <button className="link" onClick={() => setShowOrig(!showOrig)}>{showOrig ? "Показать исправленный" : "Показать исходный"}</button>}
            </div>
            <div className="txt" style={{ fontSize: 15, marginTop: 6 }}>{highlight(text, claims, pick)}</div>
            {v.minority_report && <div className="txt muted" style={{ fontSize: 13, marginTop: 8 }}><b>Особое мнение:</b> {v.minority_report}</div>}
          </div>
          {v.solution && <CodeBlock code={v.solution.code} name={v.solution.filename} />}
          {(v.findings ?? []).length > 0 && (v.findings ?? []).map((f) => <FindingCard key={f.id} run={run} f={f} />)}
          <div className="lbl" style={{ marginTop: 8 }}>
            Утверждения · <span style={{ color: "var(--gT)" }}>{n("supported")} подтверждено</span> · <span style={{ color: "var(--rT)" }}>{n("refuted")} опровергнуто</span> · <span style={{ color: "var(--oT)" }}>{n("unverified")} не проверено</span>
          </div>
          {claims.length ? claims.map((c) => <ClaimRow key={c.id + (picked === c.id ? "-o" : "")} run={run} c={c} open={picked === c.id} />)
            : <div className="muted">{phase(state, "check").status === "skipped" ? `Фактчек не выполнялся: ${SKIP[phase(state, "check").skipReason ?? ""] ?? phase(state, "check").skipReason}.` : run.mode === "code" ? "В режиме «Код» факты проверяют тесты." : run.mode === "review" ? "В ревью находки проверяет перекрёстная проверка; фактчек утверждений для ревью — позже." : "Проверяемых утверждений нет."}</div>}
          <div className="muted" style={{ fontSize: 12 }}>
            Подсветка: <mark className="hl supported">подтверждено</mark> <mark className="hl refuted">опровергнуто</mark> <mark className="hl unverified">не проверено</mark>. Нажмите на подсветку, чтобы увидеть доказательства.
          </div>
        </div>
      </div>
    </div>
  );
}
