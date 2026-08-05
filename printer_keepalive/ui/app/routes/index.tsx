import { createRoute } from "@tanstack/react-router";
import {
  Printer,
  Radio,
  Clock,
  Radar,
  RefreshCw,
} from "lucide-react";
import { rootRoute } from "./__root";
import { useHealth, useRescanMutation } from "@/lib/queries";
import { InkLevelBars } from "@/components/ink-level-bar";
import { relTime } from "@/lib/utils";
import { toast } from "@/components/toast";
import { PageTransition, StaggerChildren, StaggerItem } from "@/lib/motion";

function Dashboard() {
  const { data, isLoading, refetch } = useHealth();
  const rescan = useRescanMutation();

  if (!data && isLoading) return <Loading />;
  if (!data) return <p style={{ color: "var(--text-secondary)" }}>Loading...</p>;

  const printers = data.printers ?? [];
  const mqtt = data.mqtt;
  const scheduler = data.scheduler;
  const discovery = data.discovery_summary;

  return (
    <PageTransition>
      <StaggerChildren className="bento">
        {/* Metrics Row */}
        <StaggerItem className="card">
          <div className="metric-icon"><Printer size={18} /></div>
          <div className="metric-label">Printers</div>
          <div className="metric-value">{printers.length}</div>
          <div className="metric-sub">Configured</div>
        </StaggerItem>
        <StaggerItem className="card">
          <div className="metric-icon"><Radio size={18} /></div>
          <div className="metric-label">MQTT</div>
          <div className="metric-value">{mqtt.enabled ? (mqtt.connected ? "Connected" : "Disconnected") : "Off"}</div>
          <div className="metric-sub">{mqtt.enabled ? (mqtt.connected ? "Publishing" : (mqtt.last_error || "Connecting")) : "No broker configured"}</div>
        </StaggerItem>
        <StaggerItem className="card">
          <div className="metric-icon"><Clock size={18} /></div>
          <div className="metric-label">Auto Print</div>
          <div className="metric-value">{scheduler.auto_print_enabled ? "On" : "Off"}</div>
          <div className="metric-sub">{scheduler.auto_print_enabled ? "Scheduler active" : "Disabled"}</div>
        </StaggerItem>
        <StaggerItem className="card">
          <div className="metric-icon"><Radar size={18} /></div>
          <div className="metric-label">Discovery</div>
          <div className="metric-value">{discovery.printer_count}</div>
          <div className="metric-sub">Found on network</div>
        </StaggerItem>

        {/* Printer Health - span 3 */}
        <StaggerItem className="card span-3">
          <div className="card-title">Printer Health</div>
          {printers.length === 0 ? (
            <p style={{ color: "var(--text-secondary)" }}>No printers configured. Add printers in the Config tab.</p>
          ) : (
            printers.map((p) => {
              const printerState = p.printer_state || p.ipp_state || "unknown";
              const healthStatus = p.health_status || (printerState === "idle" ? "ok" : printerState === "processing" ? "warning" : "error");
              const supplies = p.marker_supplies || p.supplies || [];
              const nextDue = p.next_keepalive_due_at || p.next_due_time || "";
              return (
                <div key={p.printer_id} className="printer-row">
                  <span className={`status-dot ${healthStatus}`} />
                  <div className="printer-info">
                    <div className="printer-name">{p.name}</div>
                    <div className="printer-state">
                      {printerState} &middot; {p.template || "—"}
                    </div>
                    {supplies.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <InkLevelBars supplies={supplies} />
                      </div>
                    )}
                  </div>
                  <div className="printer-due">
                    <div className="due-label">Next due</div>
                    <div>{p.keepalive_deferred_by_activity_hint ? "Deferred by hint" : relTime(nextDue)}</div>
                  </div>
                </div>
              );
            })
          )}
        </StaggerItem>

        {/* System Info */}
        <StaggerItem className="card">
          <div className="card-title">System Info</div>
          <ul className="kv-list">
            <li className="kv-item"><span className="kv-key">Version</span><span className="kv-val">{data.version}</span></li>
            <li className="kv-item"><span className="kv-key">Uptime</span><span className="kv-val">{formatUptime(data.uptime_seconds)}</span></li>
            <li className="kv-item"><span className="kv-key">Supervisor</span><span className="kv-val">{data.is_supervisor ? "Yes" : "No"}</span></li>
            <li className="kv-item"><span className="kv-key">Poll Interval</span><span className="kv-val">{scheduler.status_poll_interval_minutes}m</span></li>
          </ul>
        </StaggerItem>

        {/* Discovery Summary - span 2 */}
        <StaggerItem className="card span-2">
          <div className="card-title">Discovery Summary</div>
          <ul className="kv-list">
            <li className="kv-item"><span className="kv-key">Status</span><span className={`kv-val${discovery.enabled ? " ok" : ""}`}>{discovery.enabled ? "Enabled" : "Disabled"}</span></li>
            <li className="kv-item"><span className="kv-key">Last Scan</span><span className="kv-val">{relTime(discovery.last_scan_time)}</span></li>
            <li className="kv-item"><span className="kv-key">Candidates</span><span className="kv-val">{discovery.printer_count} printers</span></li>
          </ul>
        </StaggerItem>

        {/* Quick Actions */}
        <StaggerItem className="card">
          <div className="card-title">Quick Actions</div>
          <div className="quick-actions">
            <button className="btn btn-primary" onClick={() => { refetch(); toast.success("Health refreshed"); }}>Refresh Health</button>
            <button className="btn btn-secondary" onClick={async () => { try { await rescan.mutateAsync(); toast.success("Rescan complete"); } catch (e: unknown) { toast.error(e instanceof Error ? e.message : "Rescan failed"); } }}>Rescan Network</button>
            <button className="btn btn-ghost" onClick={() => { toast.success("Opening HA..."); }}>Open in HA</button>
          </div>
        </StaggerItem>

        {/* About Keepalive - span 2 */}
        <StaggerItem className="card span-2">
          <div className="card-title">About Keepalive</div>
          <p style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.6 }}>
            Printer Keepalive periodically sends small print jobs to prevent inkjet nozzles from drying out.
            Configure cadence per-printer and choose from multiple page templates.
          </p>
        </StaggerItem>
      </StaggerChildren>
    </PageTransition>
  );
}

function Loading() {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "80px 0" }}>
      <RefreshCw size={24} className="animate-spin" style={{ color: "var(--accent)" }} />
    </div>
  );
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  return `${h}h ${m}m`;
}

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: Dashboard,
});
