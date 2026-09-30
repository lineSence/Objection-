import { useState } from "react";
import type { NewRun, PoolModel, Run } from "../api";
import { avatarClass, letter, phase, type Answer, type Critique, type RunState } from "../model";
import Composer from "./Composer";
import Inspector from "./Inspector";
import { FindingCard, VerdictBanner } from "./Findings";
import { ClaimsBlock, FactNotes } from "./FactCheck";
import { CandidateItem, RouteNote, SavedNote, ShortAnswer, SolutionFinal, VoteBars, VoteFinal } from "./M2Parts";

function AnswerItem({ run, a }: { run: Run; a: Answer }) {
  const [open, setOpen] = useState(false);
  const review = run.mode === "review";
  const text = review ? (a.findings ?? []).map((f: any) => `• [${f.severity}] ${f.title}${f.location ? ` (${f.location})` : ""}`).join("\n") : a.text;
  const rest = review ? text : text.slice(text.indexOf(a.position) + a.position.length).trim();
  const long = rest.length > 420;
  return (
    <div className="m">
      <span className={avatarClass(run.models, a.modelId)}>{letter(run.models, a.modelId)}</span>
      <div className="body2">
        <div className="who">{a.modelId}
          {review && a.overall && <span className={`tag ${a.overall === "fail" ? "r" : a.overall === "pass" ? "g" : "o"}`}>{a.overall}</span>}
          {review && <span className="tag n">{a.findings?.length ?? 0} находок</span>}
        </div>
        <div className="txt" style={{ fontWeight: 600 }}>{a.position}</div>
        {rest && <div className={`txt ${long && !open ? "clip" : ""}`} style={review ? { fontSize: 13 } : undefined}>{rest}</div>}
        {long && <button className="link" onClick={() => setOpen(!open)}>{open ? "Свернуть" : "Показать полностью"}</button>}
      </div>
    </div>
  );
}

function CritiqueItem({ run, c }: { run: Run; c: Critique }) {
  return (
    <div className="m">
      <span className={avatarClass(run.models, c.modelId)}>{letter(run.models, c.modelId)}</span>
      <div className="body2">
        <div className="who">{c.modelId}
          {c.changed ? <span className="tag o">сменил позицию</span> : <span className="tag n">держит позицию</span>}
        </div>
        <div className="txt" style={{ fontWeight: 600 }}>{c.position}</div>
        {c.changed && c.reason && <div className="txt muted" style={{ fontSize: 13 }}>Почему: {c.reason}</div>}
        {(c.objections ?? []).map((o, i) => (
          <div key={i} className="obj"><b>Objection! → {o.target}:</b> {o.text}</div>
        ))}
        {(c.supports ?? []).map((o, i) => (
          <div key={i} className="sup"><b>Согласен с {o.target}:</b> {o.text}</div>
        ))}
      </div>
    </div>
  );
}

export default function ChatView(props: { run: Run; state: RunState; pool: PoolModel[]; onSubmit: (b: NewRun) => Promise<void> }) {
  const { run, state, pool, onSubmit } = props;
  const review = run.mode === "review";
  const voting = run.mode === "verify" || run.mode === "quick";
  const code = run.mode === "code";
  const ind = phase(state, "independent");
  const vote = phase(state, "vote");
  const fin = phase(state, "final");
  const tests = [...phase(state, "test").tests, ...phase(state, "retest").tests];
  const fixp = phase(state, "fix");
  const testOf = (a: Answer) => tests.find((t) => t.modelId === a.modelId && t.round === (a.round ?? 1));
  const crit = phase(state, "critique");
  const ana = review ? phase(state, "analyze") : null;
  const syn = phase(state, "synthesize");
  const asked: string[] = ind.status === "running" ? (ind.started?.models ?? run.models) : [];
  const waiting = asked.filter((m) => !ind.answers.some((a) => a.modelId === m) && !ind.errors.some((e) => e.modelId === m));
  const v = state.verdict;
  const [showTarget, setShowTarget] = useState(false);

  return (
    <div className="body chat">
      <div className="scroll" style={{ display: "flex", flexDirection: "column" }}>
        <div className="msgs" style={{ flex: 1 }}>
          <div className="u">{run.question}{run.context ? `\n\n${run.context}` : ""}</div>
          {review && run.target && (
            <div>
              <button className="link" onClick={() => setShowTarget(!showTarget)}>
                {showTarget ? "Скрыть" : "Показать"} материал ({run.target_kind}, {run.target.split("\n").length} строк)
              </button>
              {showTarget && <div className="target-view" style={{ marginTop: 6 }}>{run.target}</div>}
            </div>
          )}
          {code && run.tests_cmd && <div className="muted" style={{ fontSize: 12 }}>Тесты: <span className="mono">{run.tests_cmd}</span>{run.workdir ? <> в <span className="mono">{run.workdir}</span></> : null}</div>}
          <RouteNote routes={state.routes.filter((r) => !r.escalated)} />
          {run.mode === "auto" && !state.failed && <div className="pending"><span className="spin" />Выбираю режим…</div>}
          <div className="grp">
            <div className="lbl">Раунд 1 · {review ? "независимые ревью" : code ? "кандидаты" : "независимые ответы"}</div>
            {ind.answers.map((a) => voting ? <ShortAnswer key={a.seq} run={run} a={a} />
              : code ? <CandidateItem key={a.seq} run={run} a={a} test={testOf(a)} /> : <AnswerItem key={a.seq} run={run} a={a} />)}
            {ind.errors.map((e) => <div key={e.modelId} className="err"><b>{e.modelId}:</b> {e.error}</div>)}
            {!state.finished && waiting.map((m) => (
              <div key={m} className="pending"><span className="spin" />{m} {review ? "ревьюит" : code ? "пишет код" : "думает"}…</div>
            ))}
            {code && phase(state, "test").status === "running" && <div className="pending"><span className="spin" />Запускаю тесты на каждом кандидате…</div>}
          </div>
          {voting && vote.finished?.votes && (
            <div className="grp">
              <div className="lbl">Голосование</div>
              <VoteBars run={run} votes={vote.finished.votes} />
            </div>
          )}
          {voting && state.verdict && <SavedNote v={state.verdict} />}
          <RouteNote routes={state.routes.filter((r) => r.escalated)} />
          {code && fixp.status !== "pending" && (
            <div className="grp">
              <div className="lbl">Раунд 2 · исправление по логам тестов</div>
              {fixp.answers.map((a) => <CandidateItem key={a.seq} run={run} a={a} test={testOf(a)} />)}
              {fixp.errors.map((e) => <div key={e.seq} className="err"><b>{e.modelId}:</b> {e.error}</div>)}
              {(fixp.status === "running" || phase(state, "retest").status === "running") && <div className="pending"><span className="spin" />Модели исправляют решения…</div>}
            </div>
          )}
          {ana?.status === "running" && <div className="pending"><span className="spin" />{ana.judge ?? "Председатель"} объединяет дубликаты…</div>}
          {!review && !code && crit.status !== "pending" && crit.status !== "skipped" && (
            <div className="grp">
              <div className="lbl">Раунд 2 · перекрёстная критика</div>
              {crit.critiques.map((c) => <CritiqueItem key={c.seq} run={run} c={c} />)}
              {crit.errors.map((e) => <div key={e.seq} className="err"><b>{e.modelId}:</b> {e.error}</div>)}
              {crit.status === "running" && <div className="pending"><span className="spin" />Модели читают чужие ответы и возражают…</div>}
            </div>
          )}
          {review && crit.status !== "pending" && (
            <div className="grp">
              <div className="lbl">Раунд 2 · перекрёстная проверка находок</div>
              {crit.critiques.map((c) => {
                const n = (k: string) => (c.votes ?? []).filter((x) => x.vote === k).length;
                return (
                  <div key={c.seq} className="m">
                    <span className={avatarClass(run.models, c.modelId)}>{letter(run.models, c.modelId)}</span>
                    <div className="body2">
                      <div className="who">{c.modelId}</div>
                      <div className="votes">
                        <span className="tag g">подтвердил {n("confirm")}</span>
                        <span className="tag r">опроверг {n("refute")}</span>
                        <span className="tag n">не уверен {n("unsure")}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
              {crit.status === "running" && <div className="pending"><span className="spin" />Каждая модель проверяет каждую находку…</div>}
            </div>
          )}
          {voting && fin.finished?.votes && !state.verdict && <VoteBars run={run} votes={fin.finished.votes} title="Итоговое голосование" />}
          {!review && syn.status === "running" && <div className="pending"><span className="spin" />Председатель ({syn.judge}) {code ? "выбирает решение" : "сводит позиции"}…</div>}
          {v && run.budget_exhausted && <div className="note o"><b>Бюджет исчерпан:</b> часть вызовов пропущена, результат частичный.</div>}
          {v && review && (
            <>
              <VerdictBanner run={{ ...run, verdict: v }} />
              {v.answer && <div className="txt">{v.answer}</div>}
              {(v.findings ?? []).map((f) => <FindingCard key={f.id} run={run} f={f} />)}
            </>
          )}
          {!code && <ClaimsBlock run={run} state={state} />}
          {v && !review && !code && <FactNotes v={v} />}
          {v && voting && <VoteFinal run={run} v={v} />}
          {v && code && <SolutionFinal run={run} v={v} />}
          {v && !review && !voting && !code && (
            <div className="final">
              <div className="who" style={{ fontSize: 14 }}>
                <span className="logo" style={{ fontSize: 14 }}>Objection<b>!</b></span> Итог совета
                {v.agreement && <span className="tag g">согласие {v.agreement}</span>}
              </div>
              <div className="txt" style={{ marginTop: 8 }}>{v.answer}</div>
              {v.minority_report && (
                <div className="txt muted" style={{ marginTop: 8, fontSize: 13 }}><b>Особое мнение:</b> {v.minority_report}</div>
              )}
            </div>
          )}
          {state.failed && <div className="err"><b>Запуск завершился ошибкой:</b> {state.failed}</div>}
        </div>
        <div style={{ padding: "0 32px 20px", maxWidth: 920, width: "100%", margin: "0 auto" }}>
          <Composer pool={pool} onSubmit={onSubmit} />
        </div>
      </div>
      <Inspector run={run} state={state} />
    </div>
  );
}
