export interface PrinterConfig {
  id: string;
  name: string;
  printer_uri: string;
  printer_type: "inkjet" | "laser";
  enabled: boolean;
  cadence_hours: number;
  template: TemplateName;
  weather_entity: string;
  entity_ids: string[];
  title: string;
  footer: string;
}

export type TemplateName =
  | "color_bars"
  | "home_summary"
  | "weather_snapshot"
  | "entity_report"
  | "hybrid"
  | "daily_summary";

export interface SupplyLevel {
  name: string;
  level: number;
  color: string;
}

export interface PrinterState {
  printer_id: string;
  name: string;
  printer_uri: string;
  printer_type: string;
  enabled: boolean;
  cadence_hours: number;
  template: string;
  health_status?: string;
  health_summary?: string;
  keepalive_needed?: boolean;
  keepalive_deferred_by_activity_hint?: boolean;
  printer_state?: string;
  printer_state_reasons?: string[];
  ipp_state?: string;
  ipp_state_reasons?: string[];
  marker_supplies?: SupplyLevel[];
  supplies?: SupplyLevel[];
  last_print_at?: string;
  last_print_time?: string;
  next_keepalive_due_at?: string;
  next_due_time?: string;
  keepalive_print_count?: number;
  total_prints?: number;
  queued_job_count?: number;
  queued_jobs?: number;
  media_sheets_completed?: number;
  printer_up_time_seconds?: number;
  uptime_seconds?: number;
  lowest_marker_level?: number;
  lowest_supply_pct?: number;
  last_activity_hint_at?: string;
  last_activity_hint_reason?: string;
  last_activity_confidence?: "high" | "medium" | "low" | "none" | string;
  last_external_print_at?: string;
  last_keepalive_decision?: string;
  last_keepalive_decision_at?: string;
  last_keepalive_decision_reason?: string;
  last_keepalive_skipped_at?: string;
  last_keepalive_skip_reason?: string;
  keepalive_skip_count?: number;
  last_keepalive_was_skipped_for_recent_print?: boolean;
  native_ha_ipp?: NativeHaIpp;
}

export interface NativeHaIpp {
  matched: boolean;
  device_name?: string;
  supplies?: SupplyLevel[];
}

export interface MqttStatus {
  enabled: boolean;
  connected: boolean;
  last_error: string | null;
}

export interface SchedulerStatus {
  auto_print_enabled: boolean;
  status_poll_interval_minutes: number;
  next_poll_time: string | null;
}

export interface HealthResponse {
  ok: boolean;
  version: string;
  is_supervisor: boolean;
  uptime_seconds: number;
  printers: PrinterState[];
  scheduler: SchedulerStatus;
  mqtt: MqttStatus;
  discovery_summary: {
    enabled: boolean;
    last_scan_time: string | null;
    printer_count: number;
  };
}

export interface ConfigPayload {
  ok: boolean;
  options: Record<string, unknown>;
  restart_supported: boolean;
  message: string;
}

export interface DiscoveredPrinter {
  name: string;
  host: string;
  port: number;
  ipp_uri: string;
  ipps_uri?: string;
  make_model: string;
  printer_type_guess: string;
  suggested_config: Partial<PrinterConfig>;
  already_configured: boolean;
}

export interface DiscoveryResponse {
  ok: boolean;
  enabled: boolean;
  printers: DiscoveredPrinter[];
  last_scan_time: string | null;
  scan_duration_seconds: number;
}

export interface CardsResponse {
  ok: boolean;
  style: string;
  styles_available: string[];
  printer_ids: string[];
  lovelace_yaml: string;
}

export interface TemplatesResponse {
  ok: boolean;
  templates: TemplateName[];
  maintenance_guidance: Record<string, unknown>;
}

export interface PrintResponse {
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  error?: string;
  printer_id?: string;
  result?: string;
}

export interface ActionResponse {
  ok: boolean;
  message: string;
  restart_supported?: boolean;
}

export type DesignVariant = "v1" | "v2" | "v3" | "v4" | "v5";
export type ThemeMode = "light" | "dark" | "system";

export const DESIGN_NAMES: Record<DesignVariant, string> = {
  v1: "Bento Grid",
  v2: "Glassmorphism",
  v3: "Neubrutalist",
  v4: "Cinematic Dark",
  v5: "Home Assistant",
};
