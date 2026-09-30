import { useState } from "react";
import type { Mode, NewRun, PoolModel } from "../api";

const MODES: [Mode, string, string][] = [
  ["auto", "Авто", "Роутер выбирает самый дешёвый подходящий протокол"],
  ["deliberate", "Вопрос", "Независимые ответы → критика → синтез с особым мнением"],
  ["verify", "Проверка", "Короткие ответы, взвешенное голосование, критика только при расхождении"],
  ["quick", "Быстро", "Две модели; если не согласны — эскалация на весь совет"],
  ["code", "Код", "Каждая модель пишет решение, ваши тесты выбирают победителя"],
  ["review", "Ревью", "Diff, план или файл: находки и перекрёстная проверка"],
];

const DEFAULT_REVIEW = "Найди реальные проблемы: баги, безопасность, сломанная логика, пропущенные граничные случаи.";

export default function Composer(props: { pool: PoolModel[]; onSubmit: (b: NewRun) => Promise<void>; autoFocus?: boolean }) {
  const { pool, onSubmit, autoFocus } = props;
  const [mode, setMode] = useState<Mode>("auto");
  const [fc, setFc] = useState<"auto" | "on" | "off">("auto");
  const [tests, setTests] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [file, setFile] = useState("");
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
      const models = picked.length ? picked : undefined;
      await onSubmit(mode === "review"
        ? { mode, question: q.trim() || DEFAULT_REVIEW, target, target_kind: kind, models }
        : mode === "code"
          ? { mode, question: q.trim(), models, tests_cmd: tests.trim() || undefined, workdir: workdir.trim() || undefined, solution_path: file.trim() || undefined }
          : { mode, question: q.trim(), models, ...(fc === "auto" ? {} : { check_facts: fc === "on" }) });
      setQ(""); setTarget("");
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const onKey = (e: React.KeyboardEvent) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); };

  return (
    <div className="composer">
      <div className="ctl">
        <div className="seg" role="radiogroup" aria-label="Пресет">
          {MODES.map(([k, l, t]) => (
            <button key={k} className={mode === k ? "on" : ""} title={t} onClick={() => setMode(k)}>{l}</button>
          ))}
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
      {mode === "code" && (
        <>
          <div className="fields">
            <input className="inp mono" value={tests} onChange={(e) => setTests(e.target.value)} placeholder="Команда тестов, напр. pytest -q" />
            <input className="inp mono" value={workdir} onChange={(e) => setWorkdir(e.target.value)} placeholder="Папка проекта (абсолютный путь)" />
            <input className="inp mono" value={file} onChange={(e) => setFile(e.target.value)} placeholder="Файл решения, напр. src/util.py" />
          </div>
          <div className="warn">Код моделей запускается на этом компьютере во временной копии папки проекта, без песочницы (песочница — M3). Используйте только с доверенными моделями.</div>
        </>
      )}
      <textarea
        autoFocus={autoFocus && mode !== "review"} value={q} onKeyDown={onKey}
        placeholder={mode === "review" ? `На что смотреть (необязательно). По умолчанию: ${DEFAULT_REVIEW}` : mode === "code" ? "Задача: что написать или исправить…" : mode === "verify" ? "Утверждение или вопрос с коротким ответом…" : "Спросите совет…"}
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
        {mode !== "review" && mode !== "code" && (
          <div className="seg sm" role="radiogroup" aria-label="Фактчек" title="Проверка утверждений веб-поиском/кодом">
            {([["auto", "фактчек: авто"], ["on", "вкл"], ["off", "выкл"]] as const).map(([k, l]) => (
              <button key={k} className={fc === k ? "on" : ""} onClick={() => setFc(k)}>{l}</button>
            ))}
          </div>
        )}
        <span className="tag n">режим: {MODES.find((m) => m[0] === mode)?.[1]}</span>
        <button className="btn p" disabled={!ready || busy} onClick={send}>{busy ? "Отправка…" : "Отправить"}</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}
