import { useState, useRef, useEffect } from "react";
import { Sun, Moon, Monitor, Lock, LockOpen } from "lucide-react";
import { useTheme } from "@/lib/themes";
import { setAuthToken, getStoredAuthToken } from "@/lib/api";
import { DESIGN_NAMES, type DesignVariant, type ThemeMode } from "@/lib/types";

export function Topbar({ version }: { version?: string }) {
  const { design, theme, setDesign, setTheme } = useTheme();

  return (
    <div className="topbar">
      <div className="brand">
        <div className="brand-icon">
          <svg viewBox="0 0 24 24">
            <path d="M19 8H5c-1.66 0-3 1.34-3 3v6h4v4h12v-4h4v-6c0-1.66-1.34-3-3-3zm-3 11H8v-5h8v5zm3-7c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm-1-9H6v4h12V3z" />
          </svg>
        </div>
        <span className="brand-title">Printer Keepalive</span>
        {version && <span className="brand-version">v{version}</span>}
      </div>

      <div className="topbar-right">
        <select
          className="design-switcher"
          value={design}
          onChange={(e) => setDesign(e.target.value as DesignVariant)}
        >
          {(Object.entries(DESIGN_NAMES) as [DesignVariant, string][]).map(
            ([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ),
          )}
        </select>
        <ThemeToggle theme={theme} setTheme={setTheme} />
        <AuthButton />
      </div>
    </div>
  );
}

function AuthButton() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState(getStoredAuthToken);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const hasToken = !!token;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        className={`auth-toggle-btn${hasToken ? " has-token" : ""}`}
        onClick={() => setOpen(!open)}
        title={hasToken ? "Auth token set" : "Set auth token"}
      >
        {hasToken ? <Lock size={14} /> : <LockOpen size={14} />}
      </button>
      {open && (
        <div className="auth-dropdown pop-in">
          <label>Auth Token</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Paste token here…"
          />
          <div className="btn-row" style={{ justifyContent: "flex-end" }}>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => { setAuthToken(token); setOpen(false); }}
            >
              Save
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => { setToken(""); setAuthToken(""); setOpen(false); }}
            >
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ThemeToggle({
  theme,
  setTheme,
}: {
  theme: ThemeMode;
  setTheme: (t: ThemeMode) => void;
}) {
  const buttons: { mode: ThemeMode; icon: typeof Sun; label: string }[] = [
    { mode: "light", icon: Sun, label: "\u2600" },
    { mode: "dark", icon: Moon, label: "\u263E" },
    { mode: "system", icon: Monitor, label: "\u2699" },
  ];

  return (
    <div className="theme-toggle">
      {buttons.map(({ mode, label }) => (
        <button
          key={mode}
          className={`theme-btn${theme === mode ? " active" : ""}`}
          onClick={() => setTheme(mode)}
          title={mode}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
