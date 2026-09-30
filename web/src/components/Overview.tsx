import { useEffect, useState } from "react";
import { api, type ModelCheck, type PoolModel, type Run, type Stats } from "../api";
import { go } from "../App";
import { MODE_LABEL, RVERDICT, SOURCE, money } from "../model";

const STATUS: Record<string, [string, string]> = {
  queued: ["n", "в очереди"], running: ["o", "идёт"], done: ["g", "готово"], failed: ["r", "ошибка"],
};

function ago(ts: string) {
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
  return new Date(ts).toLocaleDateString("ru-RU");
}

function outcome(r: Run) {
  const v = r.verdict;
  if (r.status === "failed") return <span className="tag r">ошибка</span>;
  if (!v) return <span className={`tag ${STATUS[r.status][0]}`}>{STATUS[r.status][1]}</span>;
  if (r.mode === "review" && v.verdict) {
    const [t, l] = RVERDICT[v.verdict];
    const n = v.findings.filter((f) => f.status === "confirmed").length;
    return <><span className={`tag ${t}`}>{l}</span> <span className="muted">{n} подтв.</span></>;
  }
  if (r.mode === "code") {
    const t = v.verdict === "pass" ? "g" : v.verdict === "fail" ? "r" : "o";
    return <span className={`tag ${t}`}>{v.verdict === "pass" ? "тесты ✓" : v.verdict === "fail" ? "тесты ×" : v.verdict ?? "выбор председателя"}</span>;
  }
  if (r.mode === "verify" || r.mode === "quick")
    return <><span className={`tag ${v.verdict === "uncertain" ? "o" : "g"}`} title={v.answer}>{v.answer.slice(0, 18)}{v.answer.length > 18 ? "…" : ""} · {v.agreement ?? "—"}</span>
      {v.stopped_early ? <span className="muted"> ранн. стоп</span> : v.escalated_to ? <span className="muted"> эскал.</span> : null}</>;
  return <span className={`tag ${v.disputed.length ? "o" : "g"}`}>{v.agreement ?? "—"}{v.disputed.length ? ` · ${v.disputed.length} спорн.` : ""}</span>;
}

export default function Overview({ runs, pool }: { runs: Run[]; pool: PoolModel[] }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [checks, setChecks] = useState<Record<string, ModelCheck>>({});
  const [checking, setChecking] = useState(false);

  useEffect(() => { api.stats(7).then(setStats).catch(() => setStats(null)); }, [runs.length]);

  const check = async () => {
    setChecking(true);
    try { setChecks(Object.fromEntries((await api.checkModels()).map((c) => [c.id, c]))); } finally { setChecking(false); }
  };

  const s = stats;
  const src = s ? Object.entries(s.by_source).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${SOURCE[k] ?? k} ${v}`).join(" · ") : "";
  const rv = s?.review_verdicts ?? {};
  const maxDay = Math.max(0.000001, ...(s?.cost_by_day ?? []).map((d) => d.cost_usd));

  return (
    <>
      <div className="topbar">
        <h3>Обзор</h3>
        <span className="muted" style={{ fontSize: 13 }}>последние 7 дней</span>
        <span style={{ marginLeft: "auto" }} />
        <button className="btn p sm" onClick={() => go({ page: "new" })}>Новый запуск</button>
      </div>
      <div className="scroll">
        <div className="ov">
          <div className="kpis">
            <div className="card kpi"><div className="lbl">Запуски</div><div className="v">{s?.runs ?? "—"}</div><div className="s">{src || "пока пусто"}</div></div>
            <div className="card kpi"><div className="lbl">Расходы</div><div className="v">{s ? money(s.cost_usd) : "—"}</div><div className="s">в среднем {s ? money(s.avg_cost_usd) : "—"} за запуск</div></div>
            <div className="card kpi"><div className="lbl">Вердикты ревью</div>
              <div className="v" style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                <span style={{ color: "var(--gT)" }}>{rv.pass ?? 0}</span><span className="muted">/</span>
                <span style={{ color: "var(--rT)" }}>{rv.fail ?? 0}</span><span className="muted">/</span>
                <span style={{ color: "var(--oT)" }}>{rv.uncertain ?? 0}</span>
              </div>
              <div className="s">pass / fail / uncertain</div></div>
            <div className="card kpi"><div className="lbl">Экономия</div><div className="v" style={{ color: "var(--gT)" }}>{s ? `≈${money(s.saved_usd ?? 0)}` : "—"}</div>
              <div className="s">{s ? `${s.early_stops ?? 0} ранних остановок · ${s.escalations ?? 0} эскалаций${s.disputed_share != null ? ` · спорных ${Math.round(s.disputed_share * 100)}%` : ""}` : "—"}</div></div>
          </div>

          <div className="ovgrid">
            <div className="card" style={{ overflow: "hidden" }}>
              <div style={{ padding: "12px 12px 4px" }} className="lbl">Последние запуски</div>
              {runs.length === 0 ? <div className="empty">Запусков пока нет. Задайте вопрос или вызовите council_review из OpenCode/Cline.</div> : (
                <table className="runs">
                  <thead><tr><th>Запрос</th><th>Режим</th><th>Источник</th><th>Итог</th><th>Цена</th><th>Когда</th></tr></thead>
                  <tbody>
                    {runs.slice(0, 12).map((r) => (
                      <tr key={r.id} onClick={() => go({ page: "run", id: r.id })}>
                        <td className="q" title={r.question}>{r.question}</td>
                        <td><span className="tag b" title={r.route_reason ?? undefined}>{r.requested_mode === "auto" ? "авто→" : ""}{MODE_LABEL[r.mode] ?? r.mode}</span></td>
                        <td>{SOURCE[r.source] ?? r.source}</td>
                        <td>{outcome(r)}{r.cached && <span className="tag n" style={{ marginLeft: 4 }}>кэш</span>}</td>
                        <td>{money(r.cost_usd)}</td>
                        <td className="muted">{ago(r.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div className="card" style={{ padding: "12px 14px" }}>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <div className="lbl">Пул моделей</div>
                  <button className="link" style={{ marginLeft: "auto" }} disabled={checking} onClick={check}>{checking ? "Проверяю…" : "Проверить"}</button>
                </div>
                {pool.map((m) => {
                  const c = checks[m.id];
                  return (
                    <div key={m.id} className="mrow">
                      <span title={m.model}>{m.id}{m.local && <span className="tag n" style={{ marginLeft: 6 }}>локальная</span>}</span>
                      <span className="muted" style={{ fontSize: 12 }}>{c ? `${c.latency_s.toFixed(1)} с` : m.model.split("/")[0]}</span>
                      {!m.enabled ? <span className="tag n">выкл.</span> : c ? <span className={`tag ${c.ok ? "g" : "r"}`} title={c.detail}>{c.ok ? "ок" : "нет"}</span> : <span className="tag n">—</span>}
                    </div>
                  );
                })}
              </div>
              <div className="card" style={{ padding: "12px 14px" }}>
                <div className="lbl">Расходы по дням</div>
                <div className="spark">
                  {(s?.cost_by_day ?? []).map((d, i, a) => (
                    <i key={d.date} className={i === a.length - 1 ? "h" : ""} title={`${d.date}: ${money(d.cost_usd)}`} style={{ height: `${Math.max(3, (d.cost_usd / maxDay) * 100)}%` }} />
                  ))}
                </div>
                <div className="row muted" style={{ fontSize: 11, marginTop: 4 }}>
                  <span>{s?.cost_by_day[0]?.date.slice(5)}</span><span>сегодня</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
