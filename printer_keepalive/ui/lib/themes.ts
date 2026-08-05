import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import { createElement } from "react";
import type { DesignVariant, ThemeMode } from "./types";

interface ThemeContextValue {
  design: DesignVariant;
  theme: ThemeMode;
  resolvedTheme: "light" | "dark";
  setDesign: (d: DesignVariant) => void;
  setTheme: (t: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function getSystemTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") return getSystemTheme();
  return mode;
}

function loadDesign(): DesignVariant {
  const stored = localStorage.getItem("pk_design") as DesignVariant | null;
  if (stored && ["v1", "v2", "v3", "v4", "v5"].includes(stored)) return stored;
  // Check cookie fallback
  const match = document.cookie.match(/pk_design=(\w+)/);
  if (match && ["v1", "v2", "v3", "v4", "v5"].includes(match[1])) {
    return match[1] as DesignVariant;
  }
  return "v1";
}

function loadTheme(): ThemeMode {
  const stored = localStorage.getItem("pk-theme") as ThemeMode | null;
  if (stored && ["light", "dark", "system"].includes(stored)) return stored;
  return "system";
}

function setDesignCookie(design: DesignVariant) {
  document.cookie = `pk_design=${design};path=/;max-age=31536000;SameSite=Lax`;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [design, setDesignState] = useState<DesignVariant>(loadDesign);
  const [theme, setThemeState] = useState<ThemeMode>(loadTheme);
  const [resolvedTheme, setResolvedTheme] = useState<"light" | "dark">(() =>
    resolveTheme(loadTheme()),
  );

  const setDesign = useCallback((d: DesignVariant) => {
    setDesignState(d);
    localStorage.setItem("pk_design", d);
    setDesignCookie(d);
  }, []);

  const setTheme = useCallback((t: ThemeMode) => {
    setThemeState(t);
    localStorage.setItem("pk-theme", t);
    setResolvedTheme(resolveTheme(t));
  }, []);

  // Apply attributes to <html>
  useEffect(() => {
    document.documentElement.setAttribute("data-design", design);
  }, [design]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolvedTheme);
  }, [resolvedTheme]);

  // Listen for system theme changes
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => setResolvedTheme(getSystemTheme());
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);

  return createElement(
    ThemeContext.Provider,
    { value: { design, theme, resolvedTheme, setDesign, setTheme } },
    children,
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
