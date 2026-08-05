import type { SupplyLevel } from "@/lib/types";

export function InkLevelBar({ supply }: { supply: SupplyLevel }) {
  const pct = Math.max(0, Math.min(100, supply.level));
  const color = supply.color || inferColor(supply.name);

  return (
    <div className="ink-bar-wrap">
      <span className="ink-bar-label">{supply.name}</span>
      <div className="ink-bar-track">
        <div
          className="ink-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="ink-bar-pct">{pct}%</span>
    </div>
  );
}

export function InkLevelBars({ supplies }: { supplies: SupplyLevel[] }) {
  if (!supplies?.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {supplies.map((s, i) => (
        <InkLevelBar key={i} supply={s} />
      ))}
    </div>
  );
}

function inferColor(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("cyan")) return "#00bcd4";
  if (n.includes("magenta")) return "#e91e63";
  if (n.includes("yellow")) return "#ffc107";
  if (n.includes("black") || n.includes("key")) return "#37474f";
  if (n.includes("photo")) return "#78909c";
  if (n.includes("red")) return "#f44336";
  if (n.includes("blue")) return "#2196f3";
  if (n.includes("green")) return "#4caf50";
  if (n.includes("orange")) return "#ff9800";
  return "#9e9e9e";
}
