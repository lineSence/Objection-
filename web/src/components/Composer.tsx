import { useState } from "react";
import type { Mode, NewRun, PoolModel } from "../api";

const DEFAULT_REVIEW = "Найди реальные проблемы: баги, безопасность, сломанная логика, пропущенные граничные случаи.";

export default function Composer(props: { pool: PoolModel[]; onSubmit: (b: NewRun) => Promise<void>; autoFocus?: boolean }) {
  const { pool, onSubmit, autoFocus } = props;
  const [mode, setMode] = useState<Mode>("deliberate");
  const [q, setQ] = useState("");
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState("diff");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const ready = mode === "review" ? target.trim().length > 0 : q.trim().length > 0;
  const send = async () => {
    if (!ready || busy) return;
    setBusy(true); setErr(null);
    try {
      await onSubmit(mode === "review"
        ? { mode, question: q.trim() || DEFAULT_REVIEW, target, target_kind: kind, models: picked.length ? picked : undefined }
        : { mode, question: q.trim(), models: picked.length ? picked : undefined });
      setQ(""); setTarget("");
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const onKey = (e: React.KeyboardEvent) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); };

  return (
    <div className="composer">
      <div className="ctl">
        <div className="seg" role="radiogroup" aria-label="Пресет">
          <button className={mode === "deliberate" ? "on" : ""} onClick={() => setMode("deliberate")}>Вопрос</button>
          <button className={mode === "review" ? "on" : ""} onClick={() => setMode("review")}>Ревью</button>
        </div>
        {mode === "review" && (
          <div className="seg" role="radiogroup" aria-label="Тип материала">
            {[["diff", "diff"], ["plan", "план"], ["file", "файл"], ["text", "текст"]].map(([k, l]) => (
              <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{l}</button>
            ))}
          </div>
        )}
      </div>
      {mode === "review" && (
        <textarea className="mono target" autoFocus={autoFocus} value={target} onKeyDown={onKey}
          placeholder="Вставьте diff, план или содержимое файла…" onChange={(e) => setTarget(e.target.value)} />
      )}
      <textarea
        autoFocus={autoFocus && mode === "deliberate"} value={q} onKeyDown={onKey}
        placeholder={mode === "review" ? `На что смотреть (необязательно). По умолчанию: ${DEFAULT_REVIEW}` : "Спросите совет…"}
        onChange={(e) => setQ(e.target.value)} style={mode === "review" ? { minHeight: 24 } : undefined}
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
        <span className="tag n">пресет: {mode}</span>
        <button className="btn p" disabled={!ready || busy} onClick={send}>{busy ? "Отправка…" : "Отправить"}</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}
