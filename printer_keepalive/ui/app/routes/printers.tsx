import { createRoute } from "@tanstack/react-router";
import { rootRoute } from "./__root";
import { usePrinters } from "@/lib/queries";
import { PrinterCard } from "@/components/printer-card";
import { RefreshCw } from "lucide-react";
import { PageTransition, StaggerChildren, StaggerItem } from "@/lib/motion";

function PrintersView() {
  const { printers, isLoading } = usePrinters();

  if (printers.length === 0 && isLoading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "80px 0" }}>
        <RefreshCw size={24} className="animate-spin" style={{ color: "var(--accent)" }} />
      </div>
    );
  }

  return (
    <PageTransition>
      <div className="view-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h2 className="view-title">Printers</h2>
          <p className="view-sub">Manage keepalive settings for each configured printer.</p>
        </div>
        <button
          className="btn btn-primary"
          style={{ flexShrink: 0 }}
          onClick={() => { window.location.hash = "/config"; }}
        >
          + Add Printer
        </button>
      </div>

      {printers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px var(--pad)" }}>
          <p style={{ color: "var(--text-secondary)" }}>
            No printers configured. Click "Add Printer" or add one from the Discovery tab.
          </p>
        </div>
      ) : (
        <StaggerChildren className="printer-cards">
          {printers.map((p) => (
            <StaggerItem key={p.printer_id}>
              <PrinterCard printer={p} />
            </StaggerItem>
          ))}
        </StaggerChildren>
      )}
    </PageTransition>
  );
}

export const printersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/printers",
  component: PrintersView,
});
