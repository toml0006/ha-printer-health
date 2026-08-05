import { Link, useRouterState } from "@tanstack/react-router";

const tabs = [
  { to: "/", label: "Dashboard", dot: "#00bcd4" },
  { to: "/printers", label: "Printers", dot: "#e91e63" },
  { to: "/discovery", label: "Discovery", dot: "#ffc107" },
  { to: "/cards", label: "Cards", dot: "#8b5cf6" },
  { to: "/templates", label: "Templates", dot: "#f97316" },
  { to: "/config", label: "Config", dot: "#263238" },
  { to: "/help", label: "Help", dot: "#06b6d4" },
] as const;

export function TabNav() {
  const { location } = useRouterState();

  return (
    <nav className="tabs">
      {tabs.map(({ to, label, dot }) => {
        const active =
          to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);
        return (
          <Link
            key={to}
            to={to}
            className={`tab-btn${active ? " active" : ""}`}
          >
            <span className="tab-dot" style={{ background: dot }} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
