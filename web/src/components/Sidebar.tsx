import { api, type PoolModel, type Run } from "../api";
import type { ThemeMode } from "../theme";
import { go, type Route } from "../App";

const STATUS_COLOR: Record<string, string> = { done: "var(--g)", failed: "var(--r)", running: "var(--blue)", queued: "var(--t2)" };
const THEMES: [ThemeMode, string][] = [["auto", "Авто"], ["light", "Светлая"], ["dark", "Тёмная"]];

function groups(runs: Run[]) {
  const today = new Date().toDateString();
  const t: Run[] = [], earlier: Run[] = [];
  runs.forEach((r) => (new Date(r.created_at).toDateString() === today ? t : earlier).push(r));
  return [["Сегодня", t], ["Раньше", earlier]] as const;
}

export default function Sidebar(props: {
  runs: Run[]; route: Route; pool: PoolModel[];
  theme: ThemeMode; setTheme: (m: ThemeMode) => void; onDeleted: () => void;
}) {
  const { runs, route, pool, theme, setTheme, onDeleted } = props;
  const del = async (e: React.MouseEvent, r: Run) => {
    e.stopPropagation();
    if (!confirm(`Удалить запуск «${r.question.slice(0, 60)}»? Отменить нельзя.`)) return;
    try { await api.deleteRun(r.id); if (r.id === active) go({ page: "overview" }); onDeleted(); } catch (err) { alert(String(err)); }
  };
  const active = route.page === "run" ? route.id : null;
  const enabled = pool.filter((m) => m.enabled).length;
  return (
    <aside className="nav">
      <div className="logo" style={{ padding: "4px 10px 12px" }}>Objection<b>!</b></div>
      <button className="btn p" style={{ marginBottom: 8 }} onClick={() => go({ page: "new" })}>Новый вопрос</button>
      <button className={`ni ${route.page === "overview" ? "a" : ""}`} onClick={() => go({ page: "overview" })}>Обзор</button>
      <button className={`ni ${route.page === "settings" ? "a" : ""}`} onClick={() => go({ page: "settings" })}>Настройки</button>
      <div className="runs">
        {runs.length === 0 && <div className="ni muted">Запусков пока нет</div>}
        {groups(runs).map(([title, items]) =>
          items.length ? (
            <div key={title}>
              <div className="lbl" style={{ padding: "8px 10px 4px" }}>{title}</div>
              {items.map((r) => (
                <div key={r.id} role="button" tabIndex={0} className={`ni run-item ${r.id === active ? "a" : ""}`} title={r.question}
                  onClick={() => go({ page: "run", id: r.id })} onKeyDown={(e) => e.key === "Enter" && go({ page: "run", id: r.id })}>
                  <span className="dot" style={{ background: STATUS_COLOR[r.status] }} />
                  <span className="q">{r.mode === "review" ? "Ревью: " : r.mode === "code" ? "Код: " : r.mode === "verify" ? "Проверка: " : ""}{r.question}</span>
                  {(r.status === "done" || r.status === "failed") && <button className="x" aria-label="Удалить запуск" title="Удалить" onClick={(e) => del(e, r)}>×</button>}
                </div>
              ))}
            </div>
          ) : null,
        )}
      </div>
      <div className="foot">
        <button className="ni muted" title={pool.map((m) => m.id).join(", ")} onClick={() => go({ page: "settings" })}>Пул моделей · {enabled} активны</button>
        <div style={{ padding: "0 6px" }}>
          <div className="seg" role="radiogroup" aria-label="Тема">
            {THEMES.map(([k, label]) => (
              <button key={k} role="radio" aria-checked={theme === k} className={theme === k ? "on" : ""} onClick={() => setTheme(k)}>{label}</button>
            ))}
          </div>
        </div>
      </div>
    </aside>
  );
}
