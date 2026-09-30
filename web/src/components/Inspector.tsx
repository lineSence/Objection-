import type { Run } from "../api";
import { RVERDICT, avatarClass, letter, money, phase, type RunState } from "../model";

function toMarkdown(run: Run): string {
  const v = run.verdict;
  if (!v) return `# ${run.question}\n\n(нет итога)`;
  const list = (title: string, xs: string[]) => (xs.length ? `\n## ${title}\n${xs.map((x) => `- ${x}`).join("\n")}\n` : "");
  if (run.mode === "review")
    return [
      `# Ревью: ${run.question}`, "", `**Вердикт: ${v.verdict ?? "—"}** (порог ${run.fail_on}) · ${money(run.cost_usd)} · run ${run.id}`, "", v.answer, "",
      ...v.findings.map((f) => [
        `## ${f.id} [${f.severity}] ${f.title} — ${f.status}`,
        f.location ? `*${f.location}*` : "", f.detail, f.suggestion ? `**Предложение:** ${f.suggestion}` : "",
        `Нашли: ${f.reported_by.join(", ")} · подтвердили: ${f.confirmed_by.join(", ") || "—"}`,
        ...f.refuted_by.map((o) => `> Objection! ${o.model_id}: ${o.reason}`), "",
      ].filter(Boolean).join("\n\n")),
    ].join("\n");
  return [
    `# ${run.question}`, "", `**Итог совета.** ${v.answer}`, "",
    `Согласие: ${v.agreement ?? "—"} · уверенность: ${v.confidence ?? "—"} · ${money(run.cost_usd)} · run ${run.id}`,
    list("Консенсус", v.consensus),
    list("Спорные пункты", v.disputed.map((d) => d.point)),
    v.minority_report ? `\n## Особое мнение\n${v.minority_report}\n` : "",
    list("Допущения", v.assumptions),
  ].join("\n");
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

export default function Inspector({ run, state }: { run: Run; state: RunState }) {
  const v = state.verdict;
  const ind = phase(state, "independent");
  const review = run.mode === "review";
  const fs = v?.findings ?? [];
  const count = (st: string) => fs.filter((f) => f.status === st).length;
  const [agree, total] = (v?.agreement ?? "").split("/").map(Number);
  const ratio = total ? agree / total : 0;
  const totalCost = run.status === "done" || run.status === "failed" ? run.cost_usd
    : state.phases.reduce((s, p) => s + p.answers.reduce((x, a) => x + a.usage.cost_usd, 0) + p.critiques.reduce((x, c: any) => x + (c.usage?.cost_usd ?? 0), 0), 0);

  return (
    <aside className="insp scroll">
      {review ? (
        <div>
          <div className="lbl">Вердикт</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 6 }}>
            <span style={{ fontSize: 28, fontWeight: 800, textTransform: "uppercase", color: v?.verdict ? `var(--${RVERDICT[v.verdict][0]}T)` : undefined }}>{v?.verdict ?? "—"}</span>
            <span className="muted">порог {run.fail_on}</span>
          </div>
          <div className="row"><span><span className="dot" style={{ background: "var(--g)" }} />подтверждено</span><b>{count("confirmed")}</b></div>
          <div className="row"><span><span className="dot" style={{ background: "var(--o)" }} />спорно</span><b>{count("disputed")}</b></div>
          <div className="row"><span><span className="dot" style={{ background: "var(--t2)" }} />отклонено</span><b>{count("rejected")}</b></div>
        </div>
      ) : (
      <div>
        <div className="lbl">Согласие</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 6 }}>
          <span style={{ fontSize: 28, fontWeight: 700 }}>{v?.agreement?.replace("/", " / ") ?? "—"}</span>
          {v?.confidence != null && <span className="muted">уверенность {v.confidence.toFixed(2)}</span>}
        </div>
        <div className="bar" style={{ marginTop: 8 }}><i style={{ width: `${ratio * 100}%` }} /></div>
      </div>
      )}

      {v && v.disputed.length > 0 && (
        <div>
          <div className="lbl">Спорные пункты</div>
          {v.disputed.map((d, i) => (
            <div key={i} className="cl"><span className="dot" style={{ background: "var(--o)" }} />{d.point}</div>
          ))}
        </div>
      )}
      {v && v.consensus.length > 0 && (
        <div>
          <div className="lbl">Консенсус</div>
          {v.consensus.map((c, i) => (
            <div key={i} className="cl"><span className="dot" style={{ background: "var(--g)" }} />{c}</div>
          ))}
        </div>
      )}
      <div>
        <div className="lbl">Проверка фактов</div>
        <div className="cl muted">Появится в M3 (Verifier)</div>
      </div>

      <div>
        <div className="lbl">Модели</div>
        {run.models.map((m) => {
          const a = ind.answers.find((x) => x.modelId === m);
          const e = ind.errors.find((x) => x.modelId === m);
          return (
            <div key={m} className="row">
              <span><span className={`${avatarClass(run.models, m)} xs`}>{letter(run.models, m)}</span>{m}
                {state.agreeing.includes(m) && <span className="tag g">за итог</span>}</span>
              <span className="muted">{a ? `${money(a.usage.cost_usd)} · ${a.usage.latency_s.toFixed(1)} с` : e ? <span className="tag r">ошибка</span> : "…"}</span>
            </div>
          );
        })}
      </div>

      <div style={{ borderTop: "1px solid var(--b)", paddingTop: 12 }}>
        <div className="row"><span className="muted">Итого</span><b>{money(totalCost)}{run.latency_s != null ? ` · ${run.latency_s} с` : ""}</b></div>
        <div className="row"><span className="muted">Бюджет</span><span>{money(run.budget_usd)}</span></div>
        <div className="row">
          <span className="muted">Экспорт</span>
          <span style={{ display: "flex", gap: 10 }}>
            <button className="link" onClick={() => download(`objection-${run.id}.json`, JSON.stringify(run, null, 2), "application/json")}>JSON</button>
            <button className="link" disabled={!v} onClick={() => download(`objection-${run.id}.md`, toMarkdown(run), "text/markdown")}>Markdown</button>
          </span>
        </div>
      </div>
    </aside>
  );
}
