import { useState } from "react";
import { createRoute } from "@tanstack/react-router";
import { RefreshCw, Shield } from "lucide-react";
import { rootRoute } from "./__root";
import { useDiscovery, useRescanMutation, useUpdateConfig, useConfig } from "@/lib/queries";
import { toast } from "@/components/toast";
import { relTime } from "@/lib/utils";
import { PageTransition, StaggerChildren, StaggerItem } from "@/lib/motion";

function DiscoveryView() {
  const { data, isLoading } = useDiscovery();
  const rescan = useRescanMutation();

  if (!data && isLoading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "80px 0" }}>
        <RefreshCw size={24} className="animate-spin" style={{ color: "var(--accent)" }} />
      </div>
    );
  }

  if (!data) return null;

  return (
    <PageTransition>
      <div className="view-header">
        <h2 className="view-title">Discovery</h2>
        <p className="view-sub">Network printers found via IPP/DNS-SD scanning.</p>
      </div>

      <StaggerChildren>
        {/* Scan Summary card */}
        <StaggerItem className="card" style={{ marginBottom: 16 }}>
          <div className="card-title">Scan Summary</div>
          <ul className="kv-list">
            <li className="kv-item">
              <span className="kv-key">Status</span>
              <span className={`kv-val${data.enabled ? " ok" : ""}`}>{data.enabled ? "Enabled" : "Disabled"}</span>
            </li>
            <li className="kv-item">
              <span className="kv-key">Last Scan</span>
              <span className="kv-val">{relTime(data.last_scan_time)}</span>
            </li>
            <li className="kv-item">
              <span className="kv-key">Duration</span>
              <span className="kv-val">{data.scan_duration_seconds ?? "—"}s</span>
            </li>
            <li className="kv-item">
              <span className="kv-key">Found</span>
              <span className="kv-val">{data.printers?.length ?? 0} printers</span>
            </li>
          </ul>
          <div style={{ marginTop: 16 }}>
            <button
              className="btn btn-secondary"
              onClick={async () => {
                try { await rescan.mutateAsync(); toast.success("Rescan complete"); }
                catch (e: unknown) { toast.error(e instanceof Error ? e.message : "Rescan failed"); }
              }}
              disabled={rescan.isPending}
            >
              {rescan.isPending ? "Scanning…" : "Rescan Now"}
            </button>
          </div>
        </StaggerItem>

        {/* Discovered Printers table */}
        {data.printers?.length > 0 ? (
          <StaggerItem className="card">
            <div className="card-title">Discovered Printers</div>
            <div style={{ overflowX: "auto" }}>
              <table className="disc-table">
                <thead>
                  <tr>
                    <th>Printer</th><th>Model</th><th>State</th><th>Type</th><th>Secure</th><th>Status</th><th>Config</th>
                  </tr>
                </thead>
                <tbody>
                  {data.printers.map((p: any, i: number) => (
                    <DiscoveryRow key={i} printer={p} />
                  ))}
                </tbody>
              </table>
            </div>
          </StaggerItem>
        ) : (
          <StaggerItem className="card" style={{ textAlign: "center", padding: "48px var(--pad)" }}>
            <p style={{ color: "var(--text-secondary)" }}>No printers discovered. Try rescanning the network.</p>
          </StaggerItem>
        )}
      </StaggerChildren>
    </PageTransition>
  );
}

function DiscoveryRow({ printer }: { printer: any }) {
  const [useIpps, setUseIpps] = useState(false);
  const [printerType, setPrinterType] = useState(printer.printer_type_guess || "inkjet");
  const configQuery = useConfig();
  const updateConfig = useUpdateConfig();

  const uri = useIpps && printer.ipps_uri ? printer.ipps_uri : printer.ipp_uri;

  async function handleAdd() {
    try {
      const config = configQuery.data?.options;
      if (!config) { toast.error("Config not loaded"); return; }
      const printers = Array.isArray(config.printers) ? [...config.printers] : [];
      const cadence = printerType === "laser" ? 720 : 168;
      printers.push({
        name: printer.make_model || printer.name,
        printer_uri: uri, printer_type: printerType,
        enabled: true, cadence_hours: cadence, template: "home_summary",
      });
      await updateConfig.mutateAsync({ ...config, printers });
      toast.success(`Added ${printer.name || printer.make_model}`);
    } catch (e: unknown) { toast.error(e instanceof Error ? e.message : "Failed to add printer"); }
  }

  return (
    <tr>
      <td style={{ minWidth: 180 }}>
        <div style={{ fontWeight: 600, marginBottom: 2 }}>{printer.name || printer.make_model || "Unknown"}</div>
        <div className="mono" style={{ fontSize: 11, opacity: 0.7 }}>{uri}</div>
      </td>
      <td>{printer.make_model || "—"}</td>
      <td>{printer.printer_state || "—"}</td>
      <td>
        {printer.already_configured ? printerType : (
          <select value={printerType} onChange={(e) => setPrinterType(e.target.value)}
            style={{ fontSize: 12, padding: "2px 4px", background: "var(--bg)", border: "1px solid var(--divider)", borderRadius: 6, color: "var(--text)", fontFamily: "inherit" }}>
            <option value="inkjet">inkjet</option>
            <option value="laser">laser</option>
          </select>
        )}
      </td>
      <td>
        {printer.ipps_uri ? (
          <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 }}>
            <input type="checkbox" checked={useIpps} onChange={(e) => setUseIpps(e.target.checked)} /> IPPS
          </label>
        ) : <span style={{ opacity: 0.4, fontSize: 12 }}>—</span>}
      </td>
      <td>
        {printer.already_configured ? <span className="pill pill-blue">Configured</span>
          : printer.reachable !== false ? <span className="pill pill-green">Online</span>
          : <span className="pill pill-red">Offline</span>}
      </td>
      <td>
        {!printer.already_configured && (
          <button className="btn btn-primary" style={{ fontSize: 12, padding: "4px 12px" }}
            onClick={handleAdd} disabled={updateConfig.isPending}>Add</button>
        )}
      </td>
    </tr>
  );
}

export const discoveryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/discovery",
  component: DiscoveryView,
});
