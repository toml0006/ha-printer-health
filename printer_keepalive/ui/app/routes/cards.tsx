import { useState } from "react";
import { createRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { rootRoute } from "./__root";
import { useCards, usePrinters } from "@/lib/queries";
import { toast } from "@/components/toast";
import { PageTransition } from "@/lib/motion";

const CARD_STYLES = [
  { value: "full", label: "Full Dashboard" },
  { value: "compact", label: "Compact" },
  { value: "glance", label: "Glance" },
  { value: "status_only", label: "Status Only" },
  { value: "controls_only", label: "Controls Only" },
];

function CardsView() {
  const { printers } = usePrinters();
  const [style, setStyle] = useState("full");
  const [selected, setSelected] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  const printerIds = selected.length > 0 ? selected : printers.map((p) => p.printer_id);
  const { data, isLoading } = useCards(style, printerIds);

  function togglePrinter(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id],
    );
  }

  async function copyYaml() {
    if (!data?.lovelace_yaml) return;
    try {
      await navigator.clipboard.writeText(data.lovelace_yaml);
      setCopied(true);
      toast.success("YAML copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = data.lovelace_yaml;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      toast.success("YAML copied");
      setTimeout(() => setCopied(false), 2000);
    }
  }

  return (
    <PageTransition>
      <div className="view-header">
        <h2 className="view-title">Lovelace Cards</h2>
        <p className="view-sub">Copy generated YAML to add printer cards to your Home Assistant dashboard.</p>
      </div>

      <div className="card">
        <div className="card-title">Lovelace Card Generator</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4, opacity: 0.7 }}>Card Style</div>
            <select className="yaml-style-select" value={style} onChange={(e) => setStyle(e.target.value)} style={{ minWidth: 160 }}>
              {CARD_STYLES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          {printers.length > 1 && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4, opacity: 0.7 }}>Include Printers</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 16px" }}>
                {printers.map((p) => (
                  <label key={p.printer_id} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input type="checkbox" checked={selected.length === 0 || selected.includes(p.printer_id)} onChange={() => togglePrinter(p.printer_id)} />
                    {p.name}
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
        {isLoading ? (
          <div className="yaml-block" style={{ minHeight: 120, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <RefreshCw size={20} className="animate-spin" style={{ color: "var(--accent)" }} />
          </div>
        ) : (
          <div className="yaml-block" style={{ minHeight: 120 }}>{data?.lovelace_yaml || "# No YAML returned"}</div>
        )}
        <button className="btn btn-secondary" style={{ marginTop: 8 }} onClick={copyYaml} disabled={!data?.lovelace_yaml}>
          {copied ? "Copied!" : "Copy YAML"}
        </button>
      </div>
    </PageTransition>
  );
}

export const cardsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/cards",
  component: CardsView,
});
