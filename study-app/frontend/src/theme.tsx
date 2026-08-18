import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

/** User-selectable theme mode; "system" tracks the OS preference live. */
export type ThemeMode = "light" | "dark" | "system";

const THEME_KEY = "study_app_theme";

const MODES: ThemeMode[] = ["light", "dark", "system"];

function readStoredMode(): ThemeMode {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v && (MODES as string[]).includes(v) ? (v as ThemeMode) : "system";
  } catch {
    return "system";
  }
}

function systemPrefersDark(): boolean {
  return (
    typeof matchMedia === "function" &&
    matchMedia("(prefers-color-scheme: dark)").matches
  );
}

function resolve(mode: ThemeMode): "light" | "dark" {
  if (mode !== "system") return mode;
  return systemPrefersDark() ? "dark" : "light";
}

interface ThemeContextValue {
  mode: ThemeMode;
  resolved: "light" | "dark";
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: "system",
  resolved: "light",
  setMode: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode);
  const [resolved, setResolved] = useState<"light" | "dark">(() =>
    resolve(readStoredMode()),
  );

  const apply = useCallback((next: "light" | "dark") => {
    document.documentElement.classList.toggle("dark", next === "dark");
    setResolved(next);
  }, []);

  // OS preference changes re-resolve "system" mode live.
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (readStoredMode() === "system") apply(resolve("system"));
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [apply]);

  // The no-flash script in index.html applies the class before React loads;
  // this keeps state in sync even if something else changed it.
  useEffect(() => {
    apply(resolve(mode));
  }, [mode, apply]);

  const setMode = useCallback(
    (next: ThemeMode) => {
      setModeState(next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        // private mode — theme just won't persist
      }
      apply(resolve(next));
    },
    [apply],
  );

  return (
    <ThemeContext.Provider value={{ mode, resolved, setMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
