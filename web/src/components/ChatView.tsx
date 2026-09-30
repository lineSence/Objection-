import { useState } from "react";
import type { NewRun, PoolModel, Run } from "../api";
import { avatarClass, letter, phase, type Answer, type Critique, type RunState } from "../model";
import Composer from "./Composer";
import Inspector from "./Inspector";
import { FindingCard, VerdictBanner } from "./Findings";

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
  const ind = phase(state, "independent");
  const crit = phase(state, "critique");
  const ana = review ? phase(state, "analyze") : null;
  const syn = phase(state, "synthesize");
  const waiting = run.models.filter((m) => !ind.answers.some((a) => a.modelId === m) && !ind.errors.some((e) => e.modelId === m));
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
          <div className="grp">
            <div className="lbl">Раунд 1 · {review ? "независимые ревью" : "независимые ответы"}</div>
            {ind.answers.map((a) => <AnswerItem key={a.seq} run={run} a={a} />)}
            {ind.errors.map((e) => <div key={e.modelId} className="err"><b>{e.modelId}:</b> {e.error}</div>)}
            {!state.finished && waiting.map((m) => (
              <div key={m} className="pending"><span className="spin" />{m} {review ? "ревьюит" : "думает"}…</div>
            ))}
          </div>
          {ana?.status === "running" && <div className="pending"><span className="spin" />{ana.judge ?? "Председатель"} объединяет дубликаты…</div>}
          {!review && (crit.status !== "pending") && (
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
          {!review && syn.status === "running" && <div className="pending"><span className="spin" />Председатель ({syn.judge}) сводит позиции…</div>}
          {v && review && (
            <>
              <VerdictBanner run={{ ...run, verdict: v }} />
              {v.answer && <div className="txt">{v.answer}</div>}
              {(v.findings ?? []).map((f) => <FindingCard key={f.id} run={run} f={f} />)}
            </>
          )}
          {v && !review && (
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
