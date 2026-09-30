import { useState } from "react";
import type { PoolModel } from "../api";

export default function Composer(props: { pool: PoolModel[]; onSubmit: (q: string, models: string[]) => Promise<void>; autoFocus?: boolean }) {
  const { pool, onSubmit, autoFocus } = props;
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const send = async () => {
    if (!q.trim() || busy) return;
    setBusy(true); setErr(null);
    try { await onSubmit(q.trim(), picked); setQ(""); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <div className="composer">
      <textarea
        autoFocus={autoFocus} value={q} placeholder="Спросите совет или вставьте diff…"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
      />
      <div className="ctl">
        <div className="ctl" style={{ flex: 1, minWidth: 0 }}>
        <span className="muted" style={{ fontSize: 12 }}>Совет:</span>
        {pool.filter((m) => m.enabled).map((m) => (
          <button key={m.id} className={`chip ${picked.includes(m.id) ? "on" : ""}`} onClick={() => toggle(m.id)} title={m.model}>
            {m.id}{m.local ? " · локал." : ""}
          </button>
        ))}
        {!picked.length && <span className="muted" style={{ fontSize: 12 }}>по умолчанию из конфига</span>}
        </div>
        <span className="tag n">пресет: deliberate</span>
        <button className="btn p" disabled={!q.trim() || busy} onClick={send}>{busy ? "Отправка…" : "Отправить"}</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}
