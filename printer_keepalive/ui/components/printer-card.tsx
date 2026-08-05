import { useState } from "react";
import {
  Printer,
  RefreshCw,
  Trash2,
  Zap,
} from "lucide-react";
import { toast } from "@/components/toast";
import { InkLevelBars } from "@/components/ink-level-bar";
import {
  usePrintMutation,
  usePrinterSettingsMutation,
  usePrinterPollMutation,
  usePrinterDeleteMutation,
} from "@/lib/queries";
import type { PrinterState, TemplateName } from "@/lib/types";
import { relTime } from "@/lib/utils";

const TEMPLATES: TemplateName[] = [
  "color_bars",
  "home_summary",
  "weather_snapshot",
  "entity_report",
  "hybrid",
  "daily_summary",
];

export function PrinterCard({ printer }: { printer: PrinterState }) {
  const [enabled, setEnabled] = useState(printer.enabled);
  const [cadence, setCadence] = useState(
    Math.round(printer.cadence_hours / 24),
  );
  const [template, setTemplate] = useState(printer.template);
  const [dirty, setDirty] = useState(false);

  const saveMut = usePrinterSettingsMutation();
  const printMut = usePrintMutation();
  const pollMut = usePrinterPollMutation();
  const deleteMut = usePrinterDeleteMutation();

  const printerState = printer.printer_state || printer.ipp_state || "unknown";
  const supplies = printer.marker_supplies || printer.supplies || [];
  const totalPrints = printer.total_prints ?? printer.keepalive_print_count ?? 0;
  const lastPrintAt = printer.last_print_at || printer.last_print_time || "";
  const nextDueAt = printer.next_keepalive_due_at || printer.next_due_time || "";
  const keepaliveStatus = printer.keepalive_needed
    ? "needed"
    : printer.keepalive_deferred_by_activity_hint
      ? "deferred by hint"
      : "not due";
  const activityConfidence = printer.last_activity_confidence || "none";

  const stateClass =
    printerState === "idle"
      ? "ok"
      : printerState === "processing"
        ? "warning"
        : "error";

  function markDirty() {
    setDirty(true);
  }

  async function handleSave() {
    try {
      await saveMut.mutateAsync({
        printerId: printer.printer_id,
        settings: {
          enabled,
          cadence_hours: cadence * 24,
          template,
        },
      });
      setDirty(false);
      toast.success("Settings saved");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function handlePrint(force: boolean) {
    try {
      const result = await printMut.mutateAsync({
        printerId: printer.printer_id,
        force,
      });
      if (result.skipped) {
        toast.success(result.reason || "Health print skipped because it is not needed yet");
      } else {
        toast.success(force ? "Print sent" : "Health print sent");
      }
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Print failed");
    }
  }

  async function handlePoll() {
    try {
      await pollMut.mutateAsync(printer.printer_id);
      toast.success("Status polled");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Poll failed");
    }
  }

  async function handleDelete() {
    if (!confirm(`Remove "${printer.name}" from configuration?`)) return;
    try {
      await deleteMut.mutateAsync(printer.printer_id);
      toast.success("Printer removed");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Remove failed");
    }
  }

  return (
    <div className="card">
      {/* Header */}
      <div className="printer-card-header">
        <span className={`status-dot ${stateClass}`} />
        <div style={{ flex: 1 }}>
          <div className="printer-name">{printer.name}</div>
          <div className="printer-card-uri">{printer.printer_uri}</div>
        </div>
        <span className="pill pill-blue">{printerState}</span>
      </div>

      {/* Stats */}
      <div className="stats-grid">
        <div className="stat-box">
          <div className="stat-label">Type</div>
          <div className="stat-value">{printer.printer_type}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Total Prints</div>
          <div className="stat-value">{totalPrints}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Keepalive</div>
          <div className="stat-value">{keepaliveStatus}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Cadence</div>
          <div className="stat-value">{Math.round(printer.cadence_hours / 24)} days</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Last Print</div>
          <div className="stat-value">{relTime(lastPrintAt)}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Next Due</div>
          <div className="stat-value">{relTime(nextDueAt)}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Activity</div>
          <div className="stat-value">{activityConfidence}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Hint Seen</div>
          <div className="stat-value">{relTime(printer.last_activity_hint_at || "")}</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Last Health Decision</div>
          <div className="stat-value">{printer.last_keepalive_decision || "never"}</div>
        </div>
      </div>
      {printer.last_keepalive_was_skipped_for_recent_print && (
        <div className="health-skip-callout">
          <strong>Health print skipped {relTime(printer.last_keepalive_skipped_at || "")}</strong>
          <span>{printer.last_keepalive_skip_reason}</span>
        </div>
      )}
      {printer.keepalive_deferred_by_activity_hint && (
        <p style={{ marginTop: 10, fontSize: 13, color: "var(--warning)" }}>
          Due keepalive is currently deferred by recent external activity hint.
        </p>
      )}
      {printer.last_activity_hint_reason && (
        <p style={{ marginTop: 6, fontSize: 13, color: "var(--text-secondary)" }}>
          Hint reason: {printer.last_activity_hint_reason}
        </p>
      )}

      {/* Ink Levels */}
      {supplies.length > 0 && <InkLevelBars supplies={supplies} />}

      {/* Native HA IPP */}
      {printer.native_ha_ipp?.matched && printer.native_ha_ipp.supplies?.length ? (
        <div style={{ marginTop: 12 }}>
          <div className="metric-label">Native HA IPP</div>
          <InkLevelBars supplies={printer.native_ha_ipp.supplies} />
        </div>
      ) : null}

      {/* Controls */}
      <div className="control-row" style={{ marginTop: 18, paddingTop: 18, borderTop: "1px solid var(--divider)" }}>
        <label>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => {
              setEnabled(e.target.checked);
              markDirty();
            }}
          />
          Enabled
        </label>

        <label>
          Cadence
          <input
            type="number"
            min={1}
            max={30}
            value={cadence}
            onChange={(e) => {
              setCadence(Number(e.target.value));
              markDirty();
            }}
          />
          <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>days</span>
        </label>

        <label>
          Template
          <select
            value={template}
            onChange={(e) => {
              setTemplate(e.target.value);
              markDirty();
            }}
          >
            {TEMPLATES.map((t) => (
              <option key={t} value={t}>
                {t.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Action Buttons */}
      <div className="btn-row" style={{ marginTop: 12 }}>
        <button
          className="btn btn-primary btn-sm"
          onClick={handleSave}
          disabled={!dirty || saveMut.isPending}
        >
          Save
        </button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => handlePrint(false)}
          disabled={printMut.isPending}
        >
          <Printer size={13} /> Print if Due
        </button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => handlePrint(true)}
          disabled={printMut.isPending}
        >
          <Zap size={13} /> Force Print
        </button>
        <button
          className="btn btn-ghost btn-sm"
          onClick={handlePoll}
          disabled={pollMut.isPending}
        >
          <RefreshCw size={13} /> Poll
        </button>
        <button
          className="btn btn-danger btn-sm"
          onClick={handleDelete}
          disabled={deleteMut.isPending}
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}
