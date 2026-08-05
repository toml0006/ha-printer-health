import { useState } from "react";
import { createRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { rootRoute } from "./__root";
import { useTemplates, usePrinters, usePrintMutation } from "@/lib/queries";
import { previewUrl } from "@/lib/api";
import { toast } from "@/components/toast";
import type { TemplateName } from "@/lib/types";
import { PageTransition, StaggerChildren, StaggerItem } from "@/lib/motion";

const TEMPLATE_DESCRIPTIONS: Record<string, string> = {
  color_bars: "CMYK color swatches, gradients, and fine-line patterns to exercise all printer nozzles.",
  home_summary: "Home Assistant instance overview with entity counts, active sensors, and key states.",
  weather_snapshot: "Current weather conditions with temperature, humidity, wind, and tracked entities.",
  entity_report: "Detailed state report for up to 12 configured Home Assistant entities.",
  hybrid: "Combined weather snapshot and entity report on a single page.",
  daily_summary: "Previous day's summary with energy use, sensor rollups, weather, household status, and colorful nozzle-exercising patterns.",
};

function TemplatesView() {
  const { data, isLoading } = useTemplates();
  const { printers } = usePrinters();
  const [detail, setDetail] = useState<TemplateName | null>(null);
  const [selectedPrinters, setSelectedPrinters] = useState<string[]>([]);
  const printMut = usePrintMutation();

  if (!data && isLoading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "80px 0" }}>
        <RefreshCw size={24} className="animate-spin" style={{ color: "var(--accent)" }} />
      </div>
    );
  }

  const templates = data?.templates ?? [];

  // Detail view
  if (detail) {
    const previewPrinterId = printers[0]?.printer_id;

    async function handlePrint() {
      const targets = selectedPrinters.length > 0 ? selectedPrinters : printers.map((p) => p.printer_id);
      for (const pid of targets) {
        try {
          await printMut.mutateAsync({ printerId: pid, template: detail!, force: true });
          toast.success(`Printed ${detail} to ${pid}`);
        } catch (e: unknown) { toast.error(e instanceof Error ? e.message : "Print failed"); }
      }
    }

    return (
      <PageTransition>
        <div className="view-header">
          <h2 className="view-title">Print Templates</h2>
          <p className="view-sub">Preview and print maintenance page templates for each printer.</p>
        </div>
        <div style={{ marginBottom: 12 }}>
          <button className="btn btn-ghost" onClick={() => setDetail(null)} style={{ fontSize: 14 }}>&larr; All Templates</button>
        </div>
        <div className="card">
          <div style={{ fontSize: 20, fontWeight: 600, textTransform: "capitalize" as const, marginBottom: 4 }}>{detail.replace(/_/g, " ")}</div>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 16 }}>{TEMPLATE_DESCRIPTIONS[detail] || ""}</div>
          {previewPrinterId && <img src={previewUrl(previewPrinterId, detail)} alt="Template preview" className="tpl-preview-img" style={{ maxWidth: "100%" }} />}
          {printers.length > 1 && (
            <div style={{ margin: "12px 0" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Print to:</div>
              {printers.map((p) => (
                <label key={p.printer_id} style={{ display: "inline-flex", alignItems: "center", gap: 6, marginRight: 16, fontSize: 13 }}>
                  <input type="checkbox" checked={selectedPrinters.length === 0 || selectedPrinters.includes(p.printer_id)}
                    onChange={() => setSelectedPrinters((prev) => prev.includes(p.printer_id) ? prev.filter((id) => id !== p.printer_id) : [...prev, p.printer_id])} />
                  {p.name}
                </label>
              ))}
            </div>
          )}
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button className="btn btn-primary" onClick={handlePrint} disabled={printMut.isPending}>{printMut.isPending ? "Printing…" : "Print Template"}</button>
          </div>
        </div>
      </PageTransition>
    );
  }

  // Template list view
  return (
    <PageTransition>
      <div className="view-header">
        <h2 className="view-title">Print Templates</h2>
        <p className="view-sub">Preview and print maintenance page templates for each printer.</p>
      </div>
      <StaggerChildren className="tpl-cards">
        {templates.map((t) => (
          <StaggerItem
            key={t}
            className="card"
            style={{ cursor: "pointer" }}
            onClick={() => { setDetail(t); setSelectedPrinters([]); }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 600, textTransform: "capitalize" as const }}>{t.replace(/_/g, " ")}</div>
                <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>{TEMPLATE_DESCRIPTIONS[t] || ""}</div>
              </div>
              <div style={{ color: "var(--text-tertiary)", fontSize: 20 }}>&rsaquo;</div>
            </div>
          </StaggerItem>
        ))}
      </StaggerChildren>
    </PageTransition>
  );
}

export const templatesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/templates",
  component: TemplatesView,
});
