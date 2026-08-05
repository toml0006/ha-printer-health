import { useState } from "react";
import { createRoute } from "@tanstack/react-router";
import { rootRoute } from "./__root";
import { PageTransition, StaggerChildren, StaggerItem, CollapsibleContent } from "@/lib/motion";

const TEMPLATE_DESCRIPTIONS: Record<string, string> = {
  color_bars: "CMYK color swatches, gradients, and fine-line patterns to exercise all printer nozzles.",
  home_summary: "Home Assistant instance overview with entity counts, active sensors, and key states.",
  weather_snapshot: "Current weather conditions with temperature, humidity, wind, and tracked entities.",
  entity_report: "Detailed state report for up to 12 configured Home Assistant entities.",
  hybrid: "Combined weather snapshot and entity report on a single page.",
  daily_summary: "Previous day's summary with energy use, sensor rollups, weather, household status, and colorful nozzle-exercising patterns.",
};

function HelpView() {
  return (
    <PageTransition>
      <StaggerChildren className="help-content">
        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#7c3aed" }} />What is Printer Keepalive?</div>
          <div className="help-body">
            <ul>
              <li>Multi-printer IPP keepalive add-on for Home Assistant</li>
              <li>Prevents inkjet nozzle dry-out by periodically printing small maintenance pages</li>
              <li>Discovers network printers via mDNS/DNS-SD</li>
              <li>Exposes printers as HA devices via MQTT discovery</li>
            </ul>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#3b82f6" }} />Maintenance Model</div>
          <div className="help-body">
            <ul>
              <li>Tracks last print time from keepalive jobs, external printing (IPP impression counts), and initial startup</li>
              <li>Calculates <code>next_keepalive_due = last_print + cadence_hours</code></li>
              <li>Only prints when actually due (history-aware)</li>
            </ul>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#06b6d4" }} />Inkjet vs Laser</div>
          <div className="help-body">
            <ul>
              <li><strong>Inkjet:</strong> Needs frequent activity to reduce nozzle dry-out risk</li>
              <li><strong>Laser:</strong> Toner is dry, tolerates idle periods better, but periodic test prints are still useful</li>
            </ul>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#e91e63" }} />Configuration Guide</div>
          <div className="help-body">
            <p>Configure the add-on in the Config tab. Each section controls a different aspect:</p>
            <h4>Printers</h4><p>Define multiple printers with individual URIs, types, cadences, and templates. Each printer runs independently.</p>
            <h4>Scheduling</h4><p>Controls the internal auto-print scheduler and how often printer status is polled via IPP. Failure cooldown prevents rapid retries after errors.</p>
            <h4>Discovery</h4><p>Network scanning for printers using mDNS/zeroconf. Produces ready-to-copy config snippets for found printers.</p>
            <h4>Templates &amp; Content</h4><p>Default template and content settings inherited by printers that don't override them. Weather entity enables weather-aware templates.</p>
            <h4>MQTT</h4><p>Enables Home Assistant device/entity discovery. Each printer becomes an HA device with sensors, switches, and buttons. Defaults to the HA Mosquitto broker.</p>
            <h4>Integration</h4><p>For non-Supervisor installs, provide HA URL and long-lived token. Auth token protects write API endpoints.</p>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#22c55e" }} />Exposed HA Entities</div>
          <div className="help-body">
            <p>Each printer gets the following entities via MQTT:</p>
            <div className="help-entity-grid">
              <div className="help-entity-group">
                <h4>Controls</h4>
                <ul>
                  <li><code>switch</code> keepalive_enabled</li>
                  <li><code>select</code> template</li>
                  <li><code>number</code> cadence_hours</li>
                  <li><code>button</code> print_now</li>
                </ul>
              </div>
              <div className="help-entity-group">
                <h4>Status</h4>
                <ul>
                  <li><code>binary_sensor</code> keepalive_needed</li>
                  <li><code>sensor</code> time_since_last_print</li>
                  <li><code>sensor</code> keepalive_print_count</li>
                  <li><code>sensor</code> next_keepalive_due</li>
                  <li><code>sensor</code> last_keepalive_result</li>
                  <li><code>sensor</code> printer_state</li>
                  <li><code>sensor</code> health</li>
                </ul>
              </div>
            </div>
            <h4>IPP Stats</h4>
            <ul>
              <li><code>sensor</code> queued_job_count</li>
              <li><code>sensor</code> job_impressions_completed</li>
              <li><code>sensor</code> media_sheets_completed</li>
              <li><code>sensor</code> printer_up_time</li>
              <li><code>sensor</code> lowest_marker_level</li>
            </ul>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#f59e0b" }} />Print Templates</div>
          <div className="help-body">
            <p>Six built-in templates are available. Preview them in the Templates tab.</p>
            <ul>
              {Object.entries(TEMPLATE_DESCRIPTIONS).map(([key, desc]) => (
                <li key={key}><strong>{key}</strong> — {desc}</li>
              ))}
            </ul>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#8b5cf6" }} />API Reference</div>
          <div className="help-body">
            <HelpCollapsible title="Show all API endpoints" defaultCollapsed>
              <h4>GET Endpoints</h4>
              {GET_ENDPOINTS.map((ep) => <div key={ep} className="help-endpoint"><span className="method">GET</span> {ep}</div>)}
              <h4>POST Endpoints</h4>
              {POST_ENDPOINTS.map((ep) => <div key={ep} className="help-endpoint"><span className="method">POST</span> {ep}</div>)}
              <h4>Authentication</h4>
              <p>When <code>auth_token</code> is configured, include a <code>Bearer</code> token in the <code>Authorization</code> header for write endpoints.</p>
            </HelpCollapsible>
          </div>
        </StaggerItem>

        <StaggerItem className="card">
          <div className="help-section-title"><span className="dot" style={{ background: "#ec4899" }} />Lovelace Cards</div>
          <div className="help-body">
            <p>Five card styles are available for your HA dashboard. Generate and copy YAML from the Cards tab.</p>
            <ul>
              <li><strong>Full Dashboard</strong> — Complete printer overview with all stats and controls</li>
              <li><strong>Compact</strong> — Condensed card with key info</li>
              <li><strong>Glance</strong> — Minimal at-a-glance status</li>
              <li><strong>Status Only</strong> — Printer status without controls</li>
              <li><strong>Controls Only</strong> — Just the control buttons and inputs</li>
            </ul>
          </div>
        </StaggerItem>
      </StaggerChildren>
    </PageTransition>
  );
}

function HelpCollapsible({
  title,
  children,
  defaultCollapsed = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  return (
    <div className={`help-collapsible${collapsed ? " collapsed" : ""}`}>
      <div className="help-collapsible-header" onClick={() => setCollapsed(!collapsed)}>
        {title}
        <span className="help-collapsible-chevron">&#9660;</span>
      </div>
      <CollapsibleContent open={!collapsed} className="help-collapsible-body">
        {children}
      </CollapsibleContent>
    </div>
  );
}

const GET_ENDPOINTS = ["/", "/health", "/config", "/printers", "/printers/{id}", "/printers/{id}/card", "/printers/{id}/preview", "/templates", "/guidance", "/discovery"];
const POST_ENDPOINTS = ["/print", "/printers/{id}/print", "/printers/{id}/settings", "/printers/{id}/poll", "/discovery/rescan", "/config", "/actions/restart"];

export const helpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/help",
  component: HelpView,
});
