import { useCallback, useEffect, useState } from "react";
import { api, type NewRun, type PoolModel, type Run } from "./api";
import { useTheme } from "./theme";
import Sidebar from "./components/Sidebar";
import NewQuestion from "./components/NewQuestion";
import RunPage from "./components/RunPage";
import Overview from "./components/Overview";

export type Route = { page: "overview" } | { page: "new" } | { page: "run"; id: string };

/** Hash routing keeps the bundle dependency-free: #/ overview, #/new, #/runs/<id>. */
function parse(): Route {
  const run = location.hash.match(/^#\/runs\/([\w-]+)/)?.[1];
  if (run) return { page: "run", id: run };
  return location.hash.startsWith("#/new") ? { page: "new" } : { page: "overview" };
}

export function go(r: Route) {
  location.hash = r.page === "run" ? `#/runs/${r.id}` : r.page === "new" ? "#/new" : "#/";
}

export default function App() {
  const [route, setRoute] = useState<Route>(parse);
  const [theme, setTheme] = useTheme();
  const [runs, setRuns] = useState<Run[]>([]);
  const [pool, setPool] = useState<PoolModel[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const refresh = useCallback(() => {
    api.runs().then((r) => { setRuns(r); setError(null); }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    refresh();
    api.models().then(setPool).catch((e) => setError(String(e)));
    const t = setInterval(refresh, 4000); // runs started from MCP/CLI appear on their own
    return () => clearInterval(t);
  }, [refresh]);

  const submit = async (body: NewRun) => {
    const run = await api.createRun(body);
    setRuns((r) => [run, ...r]);
    go({ page: "run", id: run.id });
  };

  return (
    <div className="app">
      <Sidebar runs={runs} route={route} pool={pool} theme={theme} setTheme={setTheme} />
      <main className="main">
        {error && <div className="err" style={{ margin: 12 }}>Сервер недоступен: {error}</div>}
        {route.page === "run" ? (
          <RunPage key={route.id} runId={route.id} pool={pool} onSubmit={submit} onChanged={refresh} />
        ) : route.page === "new" ? (
          <NewQuestion pool={pool} onSubmit={submit} />
        ) : (
          <Overview runs={runs} pool={pool} />
        )}
      </main>
    </div>
  );
}
