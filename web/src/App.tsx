import { useCallback, useEffect, useState } from "react";
import { api, type PoolModel, type Run } from "./api";
import { useTheme } from "./theme";
import Sidebar from "./components/Sidebar";
import NewQuestion from "./components/NewQuestion";
import RunPage from "./components/RunPage";

/** Hash routing keeps the bundle dependency-free: #/ — new question, #/runs/<id> — a run. */
function useRoute(): [string | null, (id: string | null) => void] {
  const parse = () => location.hash.match(/^#\/runs\/([\w-]+)/)?.[1] ?? null;
  const [id, setId] = useState<string | null>(parse);
  useEffect(() => {
    const on = () => setId(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [id, (next) => (location.hash = next ? `#/runs/${next}` : "#/")];
}

export default function App() {
  const [runId, go] = useRoute();
  const [theme, setTheme] = useTheme();
  const [runs, setRuns] = useState<Run[]>([]);
  const [pool, setPool] = useState<PoolModel[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.runs().then(setRuns).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    refresh();
    api.models().then(setPool).catch((e) => setError(String(e)));
    const t = setInterval(refresh, 5000); // runs started from MCP/CLI appear on their own
    return () => clearInterval(t);
  }, [refresh]);

  const submit = async (question: string, models: string[]) => {
    const run = await api.createRun({ question, models: models.length ? models : undefined });
    setRuns((r) => [run, ...r]);
    go(run.id);
  };

  return (
    <div className="app">
      <Sidebar runs={runs} active={runId} pool={pool} theme={theme} setTheme={setTheme} onSelect={go} />
      <main className="main">
        {error && <div className="err" style={{ margin: 12 }}>Сервер недоступен: {error}</div>}
        {runId ? (
          <RunPage key={runId} runId={runId} pool={pool} onSubmit={submit} onChanged={refresh} />
        ) : (
          <NewQuestion pool={pool} onSubmit={submit} />
        )}
      </main>
    </div>
  );
}
