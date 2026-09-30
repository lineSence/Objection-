import { useEffect, useState } from "react";

export type ThemeMode = "auto" | "light" | "dark";
const KEY = "objection.theme";

/** Three modes (D-010): auto follows prefers-color-scheme; the choice is kept in localStorage. */
export function useTheme(): [ThemeMode, (m: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(() => (localStorage.getItem(KEY) as ThemeMode) || "auto");
  useEffect(() => {
    const root = document.documentElement;
    if (mode === "auto") {
      delete root.dataset.theme;
      localStorage.removeItem(KEY);
    } else {
      root.dataset.theme = mode;
      localStorage.setItem(KEY, mode);
    }
  }, [mode]);
  return [mode, setMode];
}
