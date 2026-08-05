import { useState, useEffect, useCallback } from "react";
import { createRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { rootRoute } from "./__root";
import { useConfig, useUpdateConfig, useRestartMutation } from "@/lib/queries";
import { fetchPing } from "@/lib/api";
import { toast } from "@/components/toast";
import { PageTransition, CollapsibleContent } from "@/lib/motion";

const SECTION_META: Record<string, { color: string; description: string }> = {
  Printers: { color: "#e91e63", description: "Define your printers with individual URIs, types, and print schedules." },
  Scheduling: { color: "#7c3aed", description: "Controls the auto-print scheduler and IPP polling frequency." },
  Discovery: { color: "#06b6d4", description: "Network scanning for printers via mDNS/DNS-SD." },
  "Templates & Content": { color: "#ffc107", description: "Default template and content settings for printed pages." },
  MQTT: { color: "#3b82f6", description: "Home Assistant device discovery via MQTT broker." },
  Integration: { color: "#ec4899", description: "Home Assistant connection and API authentication." },
};

function ConfigView() {
  const { data, isLoading, refetch } = useConfig();
  const updateConfig = useUpdateConfig();
  const restartMut = useRestartMutation();

  const [mode, setMode] = useState<"form" | "json">("form");
  const [formState, setFormState] = useState<Record<string, any>>({});
  const [rawJson, setRawJson] = useState("");
  const [dirty, setDirty] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    if (data?.options) {
      setFormState(structuredClone(data.options));
      setRawJson(JSON.stringify(data.options, null, 2));
      setDirty(false);
    }
  }, [data]);

  const updateField = useCallback((key: string, value: any) => {
    setFormState((prev) => {
      const next = { ...prev };
      const parts = key.split(".");
      let obj = next;
      for (let i = 0; i < parts.length - 1; i++) {
        if (typeof obj[parts[i]] !== "object") obj[parts[i]] = {};
        obj = obj[parts[i]];
      }
      obj[parts[parts.length - 1]] = value;
      return next;
    });
    setDirty(true);
  }, []);

  function syncFormToJson() {
    setRawJson(JSON.stringify(formState, null, 2));
  }

  function syncJsonToForm() {
    try {
      const parsed = JSON.parse(rawJson);
      setFormState(parsed);
      setDirty(true);
    } catch {
      toast.error("Invalid JSON");
    }
  }

  async function handleSave() {
    const payload = mode === "json" ? JSON.parse(rawJson) : formState;
    try {
      await updateConfig.mutateAsync(payload);
      setDirty(false);
      toast.success("Configuration saved");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    }
  }

  function handleLoadConfig() {
    refetch();
    toast.success("Configuration reloaded");
  }

  async function handleRestart() {
    try {
      await restartMut.mutateAsync();
      setRestarting(true);
      toast.success("Restarting…");

      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        try {
          await fetchPing();
          clearInterval(poll);
          setRestarting(false);
          refetch();
          toast.success("App restarted");
        } catch {
          if (attempts > 60) {
            clearInterval(poll);
            setRestarting(false);
            toast.error("Restart timed out");
          }
        }
      }, 1000);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Restart failed");
    }
  }

  if (!data && isLoading) {
    return (
      <div className="cfg-loading">
        <RefreshCw size={24} className="animate-spin" style={{ color: "var(--accent)" }} />
      </div>
    );
  }

  if (restarting) {
    return (
      <div className="cfg-restarting">
        <RefreshCw size={32} className="animate-spin" style={{ color: "var(--accent)" }} />
        <p className="metric-sub">Restarting app…</p>
      </div>
    );
  }

  const printerCount = (formState.printers || []).length;
  const discoveryCount = formState.discovery_enabled ? "1 found" : undefined;

  return (
    <PageTransition>
      {/* Mode Toggle */}
      <div className="config-mode-toggle">
        <button
          onClick={() => { setMode("form"); if (mode === "json") syncJsonToForm(); }}
          className={`config-mode-btn${mode === "form" ? " active" : ""}`}
        >
          Form
        </button>
        <button
          onClick={() => { setMode("json"); if (mode === "form") syncFormToJson(); }}
          className={`config-mode-btn${mode === "json" ? " active" : ""}`}
        >
          Advanced (JSON)
        </button>
      </div>

      {/* Form or JSON */}
      {mode === "json" ? (
        <div className="card">
          <textarea
            className="config-editor"
            value={rawJson}
            onChange={(e) => {
              setRawJson(e.target.value);
              setDirty(true);
            }}
            rows={30}
            spellCheck={false}
          />
        </div>
      ) : (
        <>
          {/* Printers Section */}
          <ConfigSection
            title="Printers"
            summary={`${printerCount} printer${printerCount !== 1 ? "s" : ""}`}
          >
            {printerCount === 0 ? (
              <p className="metric-sub">No printers configured. Add printers via Discovery or raw JSON.</p>
            ) : (
              (formState.printers || []).map((p: any, i: number) => (
                <div key={i} className="cfg-printer-card">
                  <div className="cfg-printer-header">{p.name || `Printer ${i + 1}`}</div>
                  <div className="cfg-printer-body">
                    <div className="cfg-field">
                      <label className="cfg-field-label">URI</label>
                      <input
                        type="text"
                        value={p.printer_uri || ""}
                        onChange={(v) => {
                          const arr = [...(formState.printers || [])];
                          arr[i] = { ...arr[i], printer_uri: v.target.value };
                          updateField("printers", arr);
                        }}
                      />
                    </div>
                    <div className="cfg-field">
                      <label className="cfg-field-label">Name</label>
                      <input
                        type="text"
                        value={p.name || ""}
                        onChange={(v) => {
                          const arr = [...(formState.printers || [])];
                          arr[i] = { ...arr[i], name: v.target.value };
                          updateField("printers", arr);
                        }}
                      />
                    </div>
                  </div>
                </div>
              ))
            )}
          </ConfigSection>

          {/* Scheduling Section */}
          <ConfigSection
            title="Scheduling"
            summary={formState.auto_print_enabled ? "Active" : "Disabled"}
          >
            <ToggleField
              label="Auto Print Enabled"
              checked={!!formState.auto_print_enabled}
              onChange={(v) => updateField("auto_print_enabled", v)}
            />
            <NumberField
              label="Print Interval (hours)"
              value={formState.auto_print_interval_hours ?? 168}
              min={1} max={720}
              onChange={(v) => updateField("auto_print_interval_hours", v)}
            />
            <NumberField
              label="Status Poll Interval (minutes)"
              value={formState.status_poll_interval_minutes ?? 15}
              min={1} max={1440}
              onChange={(v) => updateField("status_poll_interval_minutes", v)}
            />
            <NumberField
              label="Failure Retry (minutes)"
              value={formState.failure_retry_minutes ?? 60}
              min={1} max={1440}
              onChange={(v) => updateField("failure_retry_minutes", v)}
            />
          </ConfigSection>

          {/* Discovery Section */}
          <ConfigSection
            title="Discovery"
            summary={discoveryCount}
          >
            <ToggleField
              label="Discovery Enabled"
              checked={!!formState.discovery_enabled}
              onChange={(v) => updateField("discovery_enabled", v)}
            />
            <NumberField
              label="Scan Interval (minutes)"
              value={formState.discovery_interval_minutes ?? 180}
              min={1} max={1440}
              onChange={(v) => updateField("discovery_interval_minutes", v)}
            />
            <NumberField
              label="Timeout (seconds)"
              value={formState.discovery_timeout_seconds ?? 6}
              min={1} max={30}
              onChange={(v) => updateField("discovery_timeout_seconds", v)}
            />
            <ToggleField
              label="Include IPPS"
              checked={!!formState.discovery_include_ipps}
              onChange={(v) => updateField("discovery_include_ipps", v)}
            />
          </ConfigSection>

          {/* Templates & Content Section */}
          <ConfigSection title="Templates & Content">
            <div className="cfg-field">
              <label className="cfg-field-label">Default Template</label>
              <select
                value={formState.default_template || "home_summary"}
                onChange={(e) => updateField("default_template", e.target.value)}
              >
                {["color_bars", "home_summary", "weather_snapshot", "entity_report", "hybrid", "daily_summary"].map((o) => (
                  <option key={o} value={o}>{o.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>
            <FormField label="Title" value={formState.title || ""} onChange={(v) => updateField("title", v)} />
            <FormField label="Footer" value={formState.footer || ""} onChange={(v) => updateField("footer", v)} />
            <FormField label="Weather Entity" value={formState.weather_entity || ""} onChange={(v) => updateField("weather_entity", v)} />
          </ConfigSection>

          {/* MQTT Section */}
          <ConfigSection
            title="MQTT"
            summary={formState.mqtt?.enabled ? "Connected" : "Disabled"}
          >
            <ToggleField
              label="MQTT Enabled"
              checked={!!formState.mqtt?.enabled}
              onChange={(v) => updateField("mqtt.enabled", v)}
            />
            <FormField
              label="Discovery Prefix"
              value={formState.mqtt?.discovery_prefix || "homeassistant"}
              onChange={(v) => updateField("mqtt.discovery_prefix", v)}
            />
            <FormField
              label="Topic Prefix"
              value={formState.mqtt?.topic_prefix || "printer_keepalive"}
              onChange={(v) => updateField("mqtt.topic_prefix", v)}
            />
            <ToggleField
              label="Retain"
              checked={!!formState.mqtt?.retain}
              onChange={(v) => updateField("mqtt.retain", v)}
            />
          </ConfigSection>

          {/* Integration Section */}
          <ConfigSection title="Integration">
            <FormField
              label="HA URL"
              value={formState.ha_url || ""}
              onChange={(v) => updateField("ha_url", v)}
              placeholder="http://homeassistant.local:8123"
            />
            <FormField
              label="HA Token"
              value={formState.ha_token || ""}
              onChange={(v) => updateField("ha_token", v)}
              type="password"
            />
            <FormField
              label="Add-on Page URL"
              value={formState.addon_page_url || ""}
              onChange={(v) => updateField("addon_page_url", v)}
            />
            <FormField
              label="Auth Token"
              value={formState.auth_token || ""}
              onChange={(v) => updateField("auth_token", v)}
              type="password"
            />
          </ConfigSection>
        </>
      )}

      {/* Bottom Action Bar */}
      <div className="card cfg-action-bar" style={{ opacity: 1, transform: "none" }}>
        <div className="btn-row">
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={!dirty || updateConfig.isPending}
          >
            {updateConfig.isPending ? "Saving…" : "Save Configuration"}
          </button>
          <button
            className="btn btn-secondary"
            onClick={handleLoadConfig}
          >
            Reload
          </button>
          {data?.restart_supported !== false && (
            <button
              className="btn btn-danger"
              onClick={handleRestart}
              disabled={restartMut.isPending}
            >
              {restartMut.isPending ? "Restarting…" : "Restart App"}
            </button>
          )}
        </div>
      </div>
    </PageTransition>
  );
}

function ConfigSection({
  title,
  summary,
  children,
  defaultCollapsed = false,
}: {
  title: string;
  summary?: string;
  children: React.ReactNode;
  defaultCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const meta = SECTION_META[title];

  return (
    <div className={`cfg-section${collapsed ? "" : " open"}`}>
      <div
        className="cfg-section-header"
        onClick={() => setCollapsed(!collapsed)}
      >
        {meta && <span className="cfg-section-dot" style={{ background: meta.color }} />}
        <span className="cfg-section-title">{title}</span>
        {summary && (
          <span className="cfg-section-summary">{summary}</span>
        )}
        <span className="cfg-section-chevron">&#9660;</span>
      </div>
      {meta && (
        <div className="cfg-section-desc">
          {meta.description}{" "}
          <a href="#" className="help-link" onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>Learn more</a>
        </div>
      )}
      <CollapsibleContent open={!collapsed} className="cfg-section-body">
        {children}
      </CollapsibleContent>
    </div>
  );
}

function FormField({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div className="cfg-field">
      <label className="cfg-field-label">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="cfg-field">
      <label className="cfg-field-label">{label}</label>
      <div className="cfg-field-inline">
        <input
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </div>
    </div>
  );
}

function ToggleField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="cfg-field">
      <div className="cfg-toggle-wrap">
        <label className="cfg-toggle">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span className="cfg-toggle-track" />
        </label>
        <span className="cfg-field-label" style={{ margin: 0 }}>{label}</span>
      </div>
    </div>
  );
}

export const configRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/config",
  component: ConfigView,
});
