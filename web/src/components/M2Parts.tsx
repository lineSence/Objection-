import { useState } from "react";
import type { Run, Verdict, Vote } from "../api";
import { MODE_LABEL, avatarClass, letter, money, type Answer, type Route, type TestResult } from "../model";

export function RouteNote({ routes }: { routes: Route[] }) {
  if (!routes.length) return null;
  return (
    <>
      {routes.map((r) => (
        <div key={r.seq} className={`note ${r.escalated ? "o" : "b"}`}>
          <b>{r.escalated ? "Эскалация" : "Авто-режим"} → {MODE_LABEL[r.mode] ?? r.mode}.</b>{" "}
          {r.reason}{!r.escalated && r.by && r.by !== "rule" && r.by !== "heuristic" ? ` (решила ${r.by})` : r.by === "rule" ? " (правило)" : ""}
        </div>
      ))}
    </>
  );
}

export function VoteBars({ run, votes, title }: { run: Run; votes: Vote[]; title?: string }) {
  const total = votes.reduce((s, v) => s + v.weight, 0) || 1;
  return (
    <div className="votebox">
      {title && <div className="lbl">{title}</div>}
      {votes.map((v, i) => (
        <div key={i} className="vrow">
          <div className="vhead">
            <b>{v.answer || "—"}</b>
            <span className="muted">{v.weight.toFixed(2)} · {Math.round((v.weight / total) * 100)}%</span>
          </div>
          <div className="bar"><i style={{ width: `${(v.weight / total) * 100}%`, background: i === 0 ? undefined : "var(--o)" }} /></div>
          <div className="vmodels">
            {v.models.map((m) => (
              <span key={m} className="vm"><span className={`${avatarClass(run.models, m)} xs`}>{letter(run.models, m)}</span>{m}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function ShortAnswer({ run, a }: { run: Run; a: Answer }) {
  return (
    <div className="m">
      <span className={avatarClass(run.models, a.modelId)}>{letter(run.models, a.modelId)}</span>
      <div className="body2">
        <div className="who">{a.modelId}
          {a.confidence != null && <span className="tag n">уверенность {a.confidence.toFixed(2)}</span>}
        </div>
        <div className="txt" style={{ fontWeight: 700, fontSize: 16 }}>{a.answer ?? a.position}</div>
        {a.text && <div className="txt muted" style={{ fontSize: 13 }}>{a.text}</div>}
      </div>
    </div>
  );
}

export function SavedNote({ v }: { v: Verdict }) {
  if (!v.stopped_early) return null;
  return (
    <div className="note g">
      <b>Ранняя остановка:</b> модели ответили одинаково, раунд критики не понадобился
      {v.saved_usd_est ? ` — сэкономлено ≈${money(v.saved_usd_est)}` : ""}.
    </div>
  );
}

export function VoteFinal({ run, v }: { run: Run; v: Verdict }) {
  return (
    <div className="final">
      <div className="who" style={{ fontSize: 14 }}>
        <span className="logo" style={{ fontSize: 14 }}>Objection<b>!</b></span> Итог голосования
        {v.agreement && <span className={`tag ${v.verdict === "uncertain" ? "o" : "g"}`}>согласие {v.agreement}</span>}
        {v.verdict === "uncertain" && <span className="tag o">неуверенно</span>}
        {v.escalated_to && <span className="tag o">после эскалации</span>}
      </div>
      <div className="txt" style={{ marginTop: 8, fontSize: 20, fontWeight: 800 }}>{v.answer}</div>
      {v.votes?.[0]?.reasoning && <div className="txt" style={{ marginTop: 6, fontSize: 13 }}>{v.votes[0].reasoning}</div>}
      {v.votes && v.votes.length > 1 && <VoteBars run={run} votes={v.votes} />}
      {v.minority_report && (
        <div className="txt muted" style={{ marginTop: 8, fontSize: 13 }}><b>Особое мнение:</b> {v.minority_report}</div>
      )}
    </div>
  );
}

export function CodeBlock({ code, name }: { code: string; name?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="codeblock">
      <div className="codehead">
        <span className="mono">{name}</span>
        <button className="link" onClick={() => navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); })}>
          {copied ? "Скопировано" : "Копировать"}
        </button>
      </div>
      <pre className="mono">{code}</pre>
    </div>
  );
}

export function TestTag({ t }: { t?: TestResult }) {
  if (!t) return null;
  return <span className={`tag ${t.passed ? "g" : "r"}`} title={t.output}>{t.passed ? "тесты ✓" : `тесты × (код ${t.exit_code})`} · {t.duration_s} с</span>;
}

export function CandidateItem({ run, a, test }: { run: Run; a: Answer; test?: TestResult }) {
  const [open, setOpen] = useState(false);
  const [log, setLog] = useState(false);
  return (
    <div className="m">
      <span className={avatarClass(run.models, a.modelId)}>{letter(run.models, a.modelId)}</span>
      <div className="body2">
        <div className="who">{a.modelId}
          <span className="tag n mono">{a.filename}</span>
          {a.round === 2 && <span className="tag o">исправление</span>}
          <TestTag t={test} />
        </div>
        {a.text && <div className="txt" style={{ fontSize: 13 }}>{a.text}</div>}
        <div style={{ display: "flex", gap: 12 }}>
          <button className="link" onClick={() => setOpen(!open)}>{open ? "Скрыть код" : `Код (${(a.code ?? "").split("\n").length} строк)`}</button>
          {test && !test.passed && <button className="link" onClick={() => setLog(!log)}>{log ? "Скрыть лог" : "Лог тестов"}</button>}
        </div>
        {open && a.code && <CodeBlock code={a.code} name={a.filename} />}
        {log && test && <pre className="json" style={{ maxHeight: 260 }}>{test.output || "(нет вывода)"}</pre>}
      </div>
    </div>
  );
}

export function SolutionFinal({ run, v }: { run: Run; v: Verdict }) {
  const s = v.solution;
  if (!s) return null;
  const tone = v.verdict === "pass" ? "g" : v.verdict === "fail" ? "r" : "o";
  return (
    <div className="final">
      <div className="who" style={{ fontSize: 14 }}>
        <span className="logo" style={{ fontSize: 14 }}>Objection<b>!</b></span> Решение
        {v.verdict && <span className={`tag ${tone}`}>{v.verdict === "pass" ? "тесты проходят" : v.verdict === "fail" ? "тесты не проходят" : "неуверенно"}</span>}
        {v.agreement && run.tests_cmd && <span className="tag n">прошли {v.agreement} моделей</span>}
        <span className="tag n">автор {s.model_id}</span>
      </div>
      <div className="txt" style={{ marginTop: 8 }}>{v.answer}</div>
      <CodeBlock code={s.code} name={s.filename} />
      {s.passed === false && s.output && <pre className="json" style={{ maxHeight: 220 }}>{s.output}</pre>}
    </div>
  );
}
