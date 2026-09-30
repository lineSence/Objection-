import type { PoolModel, Run } from "../api";
import type { ThemeMode } from "../theme";

const STATUS_COLOR: Record<string, string> = { done: "var(--g)", failed: "var(--r)", running: "var(--blue)", queued: "var(--t2)" };
const THEMES: [ThemeMode, string][] = [["auto", "Авто"], ["light", "Светлая"], ["dark", "Тёмная"]];

function groups(runs: Run[]) {
  const today = new Date().toDateString();
  const t: Run[] = [], earlier: Run[] = [];
  runs.forEach((r) => (new Date(r.created_at).toDateString() === today ? t : earlier).push(r));
  return [["Сегодня", t], ["Раньше", earlier]] as const;
}

export default function Sidebar(props: {
  runs: Run[]; active: string | null; pool: PoolModel[];
  theme: ThemeMode; setTheme: (m: ThemeMode) => void; onSelect: (id: string | null) => void;
}) {
  const { runs, active, pool, theme, setTheme, onSelect } = props;
  const enabled = pool.filter((m) => m.enabled).length;
  return (
    <aside className="nav">
      <div className="logo" style={{ padding: "4px 10px 12px" }}>Objection<b>!</b></div>
      <button className="btn p" style={{ marginBottom: 12 }} onClick={() => onSelect(null)}>Новый вопрос</button>
      <div className="runs">
        {runs.length === 0 && <div className="ni muted">Запусков пока нет</div>}
        {groups(runs).map(([title, items]) =>
          items.length ? (
            <div key={title}>
              <div className="lbl" style={{ padding: "8px 10px 4px" }}>{title}</div>
              {items.map((r) => (
                <button key={r.id} className={`ni ${r.id === active ? "a" : ""}`} onClick={() => onSelect(r.id)} title={r.question}>
                  <span className="dot" style={{ background: STATUS_COLOR[r.status] }} />
                  {r.question}
                </button>
              ))}
            </div>
          ) : null,
        )}
      </div>
      <div className="foot">
        <div className="ni muted" title={pool.map((m) => m.id).join(", ")}>Пул моделей · {enabled} активны</div>
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
