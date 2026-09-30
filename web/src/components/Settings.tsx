import { useEffect, useMemo, useState } from "react";
import { settingsApi, type CatalogEntry, type Defaults, type ModelSpec, type ProbeResult, type Provider, type Settings as S } from "../api";

const isMock = (m: ModelSpec) => m.model.startsWith("mock/");
const slug = (s: string) => s.toLowerCase().replace(/[:@]/g, "-").replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "model";

function providerOf(m: ModelSpec, providers: Provider[]): Provider {
  const prefix = m.model.split("/", 1)[0];
  if (prefix === "openai" && m.api_base) return providers.find((p) => p.id === "openai_compatible")!;
  if (prefix === "ollama") return providers.find((p) => p.id === "ollama")!;
  return providers.find((p) => p.prefix === prefix) ?? providers.find((p) => p.id === "openai_compatible")!;
}

function Probe({ r }: { r: ProbeResult | "busy" | undefined }) {
  if (!r) return null;
  if (r === "busy") return <span className="tag n"><span className="spin" style={{ width: 10, height: 10 }} />проверяю</span>;
  return r.ok
    ? <span className="tag g" title={r.reply ?? ""}>работает · {r.latency_s} с</span>
    : <span className="tag r" title={r.detail}>ошибка</span>;
}

export default function Settings({ onPoolChanged }: { onPoolChanged: () => void }) {
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ index: number | null; spec: ModelSpec } | null>(null);
  const [probes, setProbes] = useState<Record<string, ProbeResult | "busy">>({});

  useEffect(() => { settingsApi.get().then(setS).catch((e) => setErr(String(e))); }, []);

  const apply = async (p: Promise<S>, msg: string) => {
    setErr(null);
    try { setS(await p); onPoolChanged(); setNote(msg); setTimeout(() => setNote(null), 2500); return true; }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); return false; }
  };

  if (!s) return <div className="empty">{err ? `Не удалось загрузить настройки: ${err}` : <span className="spin" style={{ display: "inline-block" }} />}</div>;

  const models = s.models;
  const saveModels = (next: ModelSpec[], msg = "Пул моделей сохранён") => apply(settingsApi.saveModels(next), msg);
  const test = async (m: ModelSpec) => {
    setProbes((p) => ({ ...p, [m.id]: "busy" }));
    const r = await settingsApi.test(m).catch((e) => ({ ok: false, detail: String(e), latency_s: 0, reply: null, cost_usd: 0 }));
    setProbes((p) => ({ ...p, [m.id]: r }));
  };
  const onlyMock = models.length > 0 && models.every(isMock);

  return (
    <>
      <div className="topbar">
        <h3>Настройки</h3>
        <span className="muted" style={{ fontSize: 13 }}>модели через LiteLLM, ключи, совет по умолчанию</span>
        {note && <span className="tag g" style={{ marginLeft: "auto" }}>{note}</span>}
      </div>
      <div className="scroll">
        <div className="settings">
          {err && <div className="err"><b>Ошибка:</b> {err}</div>}
          {onlyMock && (
            <div className="card banner">
              <div><b>Сейчас работает демо-пул из mock-моделей.</b> Они отвечают заглушками без интернета. Добавьте настоящие модели и ключи ниже.</div>
            </div>
          )}

          <section className="card sec">
            <div className="sec-h">
              <h2>Модели</h2>
              <span className="muted">Любая модель из LiteLLM: облачные API, OpenRouter, Ollama, LM Studio, LiteLLM Proxy, свой OpenAI-совместимый сервер.</span>
            </div>
            <table className="mtable">
              <thead><tr><th style={{ width: 36 }}>Вкл.</th><th>ID</th><th>Модель LiteLLM</th><th>Статус</th><th /></tr></thead>
              <tbody>
                {models.map((m, i) => (
                  <tr key={m.id} className={m.enabled === false ? "off" : ""}>
                    <td><input type="checkbox" aria-label={`Включить ${m.id}`} checked={m.enabled !== false}
                      onChange={(e) => saveModels(models.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x)))} /></td>
                    <td><b>{m.id}</b></td>
                    <td className="mono small">{m.model}
                      {m.api_base && <div className="muted">{m.api_base}</div>}
                      <div className="tags">
                        {m.local && <span className="tag n">локальная</span>}
                        {isMock(m) && <span className="tag o">mock</span>}
                        {m.api_key_env && <span className="tag n">ключ: {m.api_key_env}</span>}
                      </div>
                    </td>
                    <td><Probe r={probes[m.id]} />
                      {probes[m.id] && probes[m.id] !== "busy" && !(probes[m.id] as ProbeResult).ok &&
                        <div className="err-small">{(probes[m.id] as ProbeResult).detail}</div>}</td>
                    <td><div className="acts">
                      <button className="link" onClick={() => test(m)}>Проверить</button>
                      <button className="link" onClick={() => setEditing({ index: i, spec: { ...m } })}>Изменить</button>
                      <button className="link danger" onClick={() => confirm(`Удалить модель ${m.id} из пула?`) && saveModels(models.filter((_, j) => j !== i), `Модель ${m.id} удалена`)}>Удалить</button>
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row-btns">
              <button className="btn p sm" onClick={() => setEditing({ index: null, spec: { id: "", model: "", enabled: true, params: {} } })}>+ Добавить модель</button>
              <button className="btn sm" onClick={() => models.filter((m) => m.enabled !== false).forEach(test)}>Проверить все</button>
              {models.some(isMock) && !onlyMock && (
                <button className="btn sm" onClick={() => saveModels(models.filter((m) => !isMock(m)), "Mock-модели убраны")}>Убрать mock-модели</button>
              )}
            </div>
            {editing && (
              <ModelEditor key={editing.index ?? "new"} settings={s} initial={editing.spec} isNew={editing.index === null}
                onCancel={() => setEditing(null)}
                onSave={async (spec, keys) => {
                  if (Object.keys(keys).length && !(await apply(settingsApi.saveKeys(keys), "Ключ сохранён"))) return;
                  const next = editing.index === null ? [...models, spec] : models.map((x, j) => (j === editing.index ? spec : x));
                  if (await saveModels(next, `Модель ${spec.id} сохранена`)) { setEditing(null); test(spec); }
                }} />
            )}
          </section>

          <KeysSection s={s} onSave={(values, msg) => apply(settingsApi.saveKeys(values), msg)} />
          <DefaultsSection s={s} onSave={(d) => apply(settingsApi.saveDefaults(d), "Настройки совета сохранены")} />

          <div className="muted small" style={{ padding: "0 4px 24px" }}>
            Конфиг: <span className="mono">{s.config_path}</span>{s.config_exists ? "" : " (будет создан при сохранении)"} · ключи: <span className="mono">{s.secrets_path}</span> (доступ только вашему пользователю).
            Web UI применяет изменения сразу; CLI и MCP-сервер подхватят их при следующем запуске.
          </div>
        </div>
      </div>
    </>
  );
}

function ModelEditor(props: {
  settings: S; initial: ModelSpec; isNew: boolean;
  onCancel: () => void; onSave: (spec: ModelSpec, keys: Record<string, string>) => Promise<void>;
}) {
  const { settings, initial, isNew, onCancel, onSave } = props;
  const providers = settings.providers;
  const [prov, setProv] = useState<Provider>(() => (initial.model ? providerOf(initial, providers) : providers[0]));
  const [name, setName] = useState(() => (initial.model.includes("/") ? initial.model.slice(initial.model.indexOf("/") + 1) : ""));
  const [id, setId] = useState(initial.id);
  const [idTouched, setIdTouched] = useState(!isNew);
  const [apiBase, setApiBase] = useState(initial.api_base ?? "");
  const [key, setKey] = useState("");
  const [timeout, setTimeoutS] = useState(initial.timeout_s?.toString() ?? "");
  const [maxPar, setMaxPar] = useState(initial.max_parallel?.toString() ?? "");
  const [temp, setTemp] = useState(initial.params?.temperature?.toString() ?? "");
  const [maxTok, setMaxTok] = useState(initial.params?.max_tokens?.toString() ?? "");
  const [extra, setExtra] = useState(() => {
    const { temperature, max_tokens, ...rest } = initial.params ?? {};
    return Object.keys(rest).length ? JSON.stringify(rest, null, 1) : "";
  });
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [found, setFound] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | "discover" | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setCatalog([]); setFound(null);
    if (prov.catalog) settingsApi.catalog(prov.catalog).then(setCatalog).catch(() => setCatalog([]));
  }, [prov]);

  const pickProvider = (p: Provider) => {
    setProv(p); setProbe(null);
    setApiBase(p.api_base ?? "");
    if (isNew) setName("");
  };

  const keyEnv = prov.custom_key ? (initial.api_key_env || (id ? `OBJECTION_KEY_${id.toUpperCase().replace(/[^A-Z0-9]/g, "_")}` : null)) : prov.env;
  const keyStatus = settings.keys.find((k) => k.env === keyEnv);
  const needKey = !!prov.env && !prov.optional_key && !keyStatus?.set && !key;
  const entry = catalog.find((c) => c.name === name);
  const suggestions = found ?? catalog.map((c) => c.name);

  const build = (): ModelSpec | string => {
    const n = name.trim();
    if (!n) return "Укажите модель";
    const mid = (id.trim() || slug(n.split("/").pop()!));
    if (!/^[\w.-]{1,40}$/.test(mid)) return "ID: латиница, цифры, - _ . (до 40 символов)";
    if (isNew && settings.models.some((m) => m.id === mid)) return `Модель с ID «${mid}» уже есть`;
    let params: Record<string, any> = {};
    if (extra.trim()) {
      try { params = JSON.parse(extra); } catch { return "Доп. параметры: неверный JSON"; }
    }
    if (temp) params.temperature = Number(temp);
    if (maxTok) params.max_tokens = Number(maxTok);
    return {
      id: mid, model: `${prov.prefix}/${n}`, enabled: initial.enabled ?? true,
      api_base: apiBase.trim() || null,
      // custom servers keep a per-model key; a hand-written api_key_env on a standard provider is preserved
      api_key_env: prov.custom_key ? (key || initial.api_key_env ? keyEnv : null)
        : !isNew && initial.api_key_env && providerOf(initial, providers).id === prov.id ? initial.api_key_env : null,
      timeout_s: timeout ? Number(timeout) : null, max_parallel: maxPar ? Number(maxPar) : null, params,
    };
  };

  const run = async (what: "test" | "save") => {
    const spec = build();
    if (typeof spec === "string") { setErr(spec); return; }
    setErr(null); setBusy(what);
    try {
      if (what === "test") {
        const probeSpec = prov.custom_key && key ? { ...spec, api_key_env: null } : spec;
        const r = await settingsApi.test(prov.custom_key ? probeSpec : spec, key || undefined);
        setProbe(r);
      } else {
        await onSave(spec, key && keyEnv ? { [keyEnv]: key } : {});
      }
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };

  const discover = async () => {
    setBusy("discover"); setErr(null);
    try {
      const list = await settingsApi.discover(prov.id, apiBase, keyEnv, key || undefined);
      setFound(list);
      if (!list.length) setErr("Сервер ответил, но моделей не нашлось");
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };

  return (
    <div className="editor">
      <div className="sec-h"><h3>{isNew ? "Новая модель" : `Изменить ${initial.id}`}</h3></div>
      <div className="grid2">
        <label className="field">
          <span>Провайдер</span>
          <select className="inp" value={prov.id} onChange={(e) => pickProvider(providers.find((p) => p.id === e.target.value)!)}>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>
        {(prov.api_base !== undefined || apiBase) && (
          <label className="field">
            <span>Адрес сервера (api_base)</span>
            <input className="inp mono" value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder={prov.api_base} />
          </label>
        )}
        {(prov.env || prov.custom_key) && (
          <label className="field">
            <span>API-ключ {prov.optional_key || prov.custom_key ? "(если нужен)" : ""}</span>
            <input className="inp" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
              placeholder={keyStatus?.set ? `задан ${keyStatus.masked} — оставьте пустым` : keyEnv ?? ""} />
            <small className="muted">{keyEnv ? <>Сохранится в <span className="mono">{keyEnv}</span>{prov.custom_key ? " только для этой модели" : " — общий для всех моделей провайдера"}.</> : "Укажите ID модели."}</small>
          </label>
        )}
        <label className="field">
          <span>Модель {found ? `(найдено ${found.length})` : catalog.length ? `(${catalog.length} в каталоге LiteLLM)` : ""}</span>
          <div className="inrow">
            <span className="prefix mono">{prov.prefix}/</span>
            <input className="inp mono" list="model-suggest" value={name} placeholder={prov.discover ? "имя модели на сервере" : "начните вводить…"}
              onChange={(e) => { setName(e.target.value); setProbe(null); if (!idTouched) setId(slug(e.target.value.split("/").pop() ?? "")); }} />
            {prov.discover && <button className="btn sm" disabled={!apiBase || busy === "discover"} onClick={discover}>{busy === "discover" ? "Ищу…" : "Найти модели"}</button>}
          </div>
          <datalist id="model-suggest">{suggestions.map((n) => <option key={n} value={n} />)}</datalist>
          {entry && (entry.input_per_mtok != null || entry.context) && (
            <small className="muted">
              {entry.input_per_mtok != null && `$${entry.input_per_mtok} / $${entry.output_per_mtok} за 1M токенов (вход/выход)`}
              {entry.context ? ` · контекст ${Math.round(entry.context / 1000)}K` : ""}
            </small>
          )}
        </label>
        <label className="field">
          <span>ID в пуле</span>
          <input className="inp" value={id} disabled={!isNew} placeholder="например, claude" onChange={(e) => { setId(e.target.value); setIdTouched(true); }} />
          <small className="muted">Короткое имя для логов и статистики{isNew ? "" : " (не меняется)"}.</small>
        </label>
      </div>
      <details className="adv">
        <summary>Дополнительно: таймаут, параллелизм, параметры генерации</summary>
        <div className="grid4">
          <label className="field"><span>Таймаут, с</span><input className="inp" type="number" min={1} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} placeholder={String(settings.defaults.timeout_s)} /></label>
          <label className="field"><span>Параллельных запросов</span><input className="inp" type="number" min={1} value={maxPar} onChange={(e) => setMaxPar(e.target.value)} placeholder={prov.local ? "1 для локальных" : "без лимита"} /></label>
          <label className="field"><span>temperature</span><input className="inp" type="number" step="0.1" min={0} max={2} value={temp} onChange={(e) => setTemp(e.target.value)} placeholder="по умолчанию" /></label>
          <label className="field"><span>max_tokens</span><input className="inp" type="number" min={1} value={maxTok} onChange={(e) => setMaxTok(e.target.value)} placeholder="по умолчанию" /></label>
        </div>
        <label className="field">
          <span>Другие параметры LiteLLM (JSON)</span>
          <textarea className="inp mono" rows={2} value={extra} onChange={(e) => setExtra(e.target.value)} placeholder='{"reasoning_effort": "low"}' />
        </label>
      </details>
      {needKey && <div className="hint">Для {prov.label} нужен ключ <span className="mono">{prov.env}</span> — введите его выше или в разделе «Ключи API».</div>}
      {err && <div className="err">{err}</div>}
      {probe && (probe.ok
        ? <div className="okbox">Модель ответила за {probe.latency_s} с: «{probe.reply}»{probe.cost_usd ? ` · $${probe.cost_usd.toFixed(6)}` : ""}</div>
        : <div className="err"><b>Не работает:</b> {probe.detail}</div>)}
      <div className="row-btns">
        <button className="btn sm" disabled={!!busy} onClick={() => run("test")}>{busy === "test" ? "Проверяю…" : "Проверить"}</button>
        <button className="btn p sm" disabled={!!busy} onClick={() => run("save")}>{busy === "save" ? "Сохраняю…" : "Сохранить"}</button>
        <button className="btn sm" onClick={onCancel}>Отмена</button>
      </div>
    </div>
  );
}

function KeysSection({ s, onSave }: { s: S; onSave: (v: Record<string, string | null>, msg: string) => Promise<boolean> }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const labels = useMemo(() => Object.fromEntries(s.providers.filter((p) => p.env).map((p) => [p.env!, p.label])), [s.providers]);
  const used = new Set(s.models.map((m) => providerOf(m, s.providers).env));
  const [all, setAll] = useState(false);
  const main = s.keys.filter((k) => used.has(k.env) || k.set);
  const keys = all || main.length === 0 ? [...s.keys].sort((a, b) => Number(used.has(b.env) || b.set) - Number(used.has(a.env) || a.set)) : main;
  return (
    <section className="card sec">
      <div className="sec-h">
        <h2>Ключи API</h2>
        <span className="muted">Хранятся локально в отдельном файле и никогда не показываются целиком. Ключ из окружения тоже подхватывается.</span>
      </div>
      <div className="keys">
        {keys.map((k) => (
          <div key={k.env} className="krow">
            <div><b>{labels[k.env] ?? (s.models.filter((m) => m.api_key_env === k.env).map((m) => `Модель ${m.id}`).join(", ") || "Своя модель")}</b><div className="mono muted small">{k.env}</div></div>
            <div>{k.set ? <span className="tag g">{k.source === "env" ? "из окружения" : "задан"} {k.masked}</span> : <span className="tag n">не задан</span>}
              {used.has(k.env) && !k.set && <span className="tag o" style={{ marginLeft: 4 }}>нужен для пула</span>}</div>
            <input className="inp" type="password" autoComplete="off" placeholder={k.set ? "новый ключ" : "вставьте ключ"} value={draft[k.env] ?? ""}
              onChange={(e) => setDraft({ ...draft, [k.env]: e.target.value })} />
            <div className="acts">
              <button className="btn sm" disabled={!draft[k.env]} onClick={async () => { if (await onSave({ [k.env]: draft[k.env] }, `Ключ ${k.env} сохранён`)) setDraft({ ...draft, [k.env]: "" }); }}>Сохранить</button>
              {k.source === "file" && <button className="link danger" onClick={() => onSave({ [k.env]: null }, `Ключ ${k.env} удалён`)}>Удалить</button>}
            </div>
          </div>
        ))}
      </div>
      {main.length > 0 && main.length < s.keys.length && (
        <button className="link" style={{ alignSelf: "flex-start" }} onClick={() => setAll(!all)}>
          {all ? "Показать только нужные" : `Другие провайдеры (${s.keys.length - main.length})`}
        </button>
      )}
    </section>
  );
}

function DefaultsSection({ s, onSave }: { s: S; onSave: (d: Defaults) => Promise<boolean> }) {
  const [d, setD] = useState<Defaults>(s.defaults);
  useEffect(() => setD(s.defaults), [s.defaults]);
  const enabled = s.models.filter((m) => m.enabled !== false);
  const pinned = d.council.pinned;
  const auto = enabled.slice(0, d.council.size).map((m) => m.id);
  const toggle = (id: string) => setD({ ...d, council: { ...d.council, pinned: pinned.includes(id) ? pinned.filter((x) => x !== id) : [...pinned, id] } });
  const dirty = JSON.stringify(d) !== JSON.stringify(s.defaults);
  return (
    <section className="card sec">
      <div className="sec-h">
        <h2>Совет по умолчанию</h2>
        <span className="muted">Кого спрашивать, если в запросе не выбраны модели.</span>
      </div>
      <div className="field">
        <span>Состав</span>
        <div className="chips">
          {enabled.map((m) => {
            const on = pinned.length ? pinned.includes(m.id) : auto.includes(m.id);
            return <button key={m.id} className={`chip ${on ? "on" : ""}`} onClick={() => toggle(m.id)}>{m.id}</button>;
          })}
        </div>
        <small className="muted">{pinned.length ? `Закреплено: ${pinned.length}. Снимите все, чтобы брать первые N включённых.` : `Не закреплено — берутся первые ${d.council.size} включённые модели. Нажмите, чтобы закрепить.`}</small>
      </div>
      <div className="grid4">
        <label className="field"><span>Размер (без закрепления)</span><input className="inp" type="number" min={1} max={9} value={d.council.size} onChange={(e) => setD({ ...d, council: { ...d.council, size: Number(e.target.value) } })} /></label>
        <label className="field"><span>Председатель</span>
          <select className="inp" value={d.judge} onChange={(e) => setD({ ...d, judge: e.target.value })}>
            <option value="auto">авто (модель вне совета)</option>
            {s.models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
          </select></label>
        <label className="field"><span>Бюджет на запуск, $</span><input className="inp" type="number" step="0.05" min={0.01} value={d.budget_usd} onChange={(e) => setD({ ...d, budget_usd: Number(e.target.value) })} /></label>
        <label className="field"><span>Таймаут модели, с</span><input className="inp" type="number" min={5} value={d.timeout_s} onChange={(e) => setD({ ...d, timeout_s: Number(e.target.value) })} /></label>
      </div>
      <div className="row-btns">
        <button className="btn p sm" disabled={!dirty} onClick={() => onSave(d)}>Сохранить</button>
        {dirty && <button className="btn sm" onClick={() => setD(s.defaults)}>Отменить изменения</button>}
      </div>
    </section>
  );
}
