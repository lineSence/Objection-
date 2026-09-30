import { useState } from "react";
import type { PoolModel, Run } from "../api";
import { avatarClass, letter, type Answer, type RunState } from "../model";
import Composer from "./Composer";
import Inspector from "./Inspector";

function AnswerItem({ run, a }: { run: Run; a: Answer }) {
  const [open, setOpen] = useState(false);
  const long = a.text.length > 420;
  const rest = a.text.slice(a.text.indexOf(a.position) + a.position.length).trim();
  return (
    <div className="m">
      <span className={avatarClass(run.models, a.modelId)}>{letter(run.models, a.modelId)}</span>
      <div className="body2">
        <div className="who">{a.modelId}</div>
        <div className="txt" style={{ fontWeight: 600 }}>{a.position}</div>
        {rest && <div className={`txt ${long && !open ? "clip" : ""}`}>{rest}</div>}
        {long && <button className="link" onClick={() => setOpen(!open)}>{open ? "Свернуть" : "Показать полностью"}</button>}
      </div>
    </div>
  );
}

export default function ChatView(props: { run: Run; state: RunState; pool: PoolModel[]; onSubmit: (q: string, m: string[]) => Promise<void> }) {
  const { run, state, pool, onSubmit } = props;
  const ind = state.phases.find((p) => p.key === "independent")!;
  const syn = state.phases.find((p) => p.key === "synthesize")!;
  const waiting = run.models.filter((m) => !ind.answers.some((a) => a.modelId === m) && !ind.errors.some((e) => e.modelId === m));
  const v = state.verdict;

  return (
    <div className="body chat">
      <div className="scroll" style={{ display: "flex", flexDirection: "column" }}>
        <div className="msgs" style={{ flex: 1 }}>
          <div className="u">{run.question}{run.context ? `\n\n${run.context}` : ""}</div>
          <div className="grp">
            <div className="lbl">Раунд 1 · независимые ответы</div>
            {ind.answers.map((a) => <AnswerItem key={a.seq} run={run} a={a} />)}
            {ind.errors.map((e) => <div key={e.modelId} className="err"><b>{e.modelId}:</b> {e.error}</div>)}
            {!state.finished && waiting.map((m) => (
              <div key={m} className="pending"><span className="spin" />{m} думает…</div>
            ))}
          </div>
          {syn.status === "running" && <div className="pending"><span className="spin" />Председатель ({syn.judge}) сводит позиции…</div>}
          {v && (
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
