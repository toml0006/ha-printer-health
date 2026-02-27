# HA Printer Health

Proactive printer maintenance and health telemetry for Home Assistant.

## What

Printer Keepalive is a Home Assistant app that keeps your printers healthy by automatically printing maintenance pages on a configurable schedule. It supports multiple printers, discovers network printers via mDNS, exposes each printer as a native HA device with sensors and controls, and publishes IPP telemetry for dashboards and automations.

## Why

Inkjet printers dry out when idle. Nozzles clog, print heads degrade, and the first page after weeks of inactivity comes out streaky or fails entirely. Laser printers are more forgiving but still benefit from periodic readiness checks. Printer Keepalive solves this by:

- **Printing only when needed** — tracks actual print activity (including external jobs) and only fires a keepalive when the printer has been idle past its cadence threshold.
- **Exercising all nozzles** — templates include CMYK swatches, gradients, fine line patterns, and rainbow strips designed to push ink through every channel.
- **Making prints useful** — instead of wasting a page on a test pattern, templates render real Home Assistant data: daily summaries, weather forecasts, energy usage, sensor activity, and household status.
- **Zero manual effort** — once configured, the scheduler handles everything. MQTT discovery auto-creates HA entities so you can monitor printer health, ink levels, and keepalive status from your dashboard.

## Quickstart

1. In Home Assistant, go to **Settings > Apps > App Store > Repositories**.
2. Add this repository URL:
   ```
   https://github.com/toml0006/ha-printer-health
   ```
3. Install **Printer Keepalive** and start it.
4. Open the app from the sidebar — the ingress UI lets you configure printers, preview templates, trigger prints, and monitor health.
5. Add your printer(s) in the Configuration tab. At minimum you need:
   ```yaml
   printers:
     - name: Office Printer
       printer_uri: ipp://192.168.1.40/ipp/print
       printer_type: inkjet
       cadence_hours: 168
       template: daily_summary
   ```
6. Save and restart. The scheduler will handle the rest.

For Docker/Container installs (no app store), see [`printer_keepalive/GETTING_STARTED.md`](printer_keepalive/GETTING_STARTED.md).

Full configuration reference and API docs: [`printer_keepalive/DOCS.md`](printer_keepalive/DOCS.md).

## How It Works

### Maintenance Model

Each printer has a cadence (default: 168 hours / 7 days). The app tracks the last known print time from three sources:

1. Keepalive jobs it submits.
2. External printing detected via IPP `job-impressions-completed` counters.
3. An initial anchor at first startup.

When `last_print + cadence_hours` has passed, the next scheduler cycle generates and prints a maintenance page. If someone printed recently, the keepalive is skipped.

### Templates

Six built-in print templates, all designed to exercise ink channels while showing useful data:

| Template | Description |
|----------|-------------|
| `daily_summary` | Full previous-day report: energy, weather forecast, sensor activity, household status, nozzle exercise patterns |
| `home_summary` | Entity counts, availability stats, key entity states |
| `weather_snapshot` | Current conditions, tracked entities |
| `hybrid` | Weather + entity states combined |
| `entity_report` | Detailed state dump of configured entities |
| `color_bars` | Pure maintenance: CMYK swatches, gradients, fine line grids |

### Architecture

- **Single Python file** (`app.py`) — HTTP server, scheduler, MQTT bridge, template renderer, all in one.
- **PIL/Pillow** for image generation — templates render to 2550x3300 JPEG (letter size at 300 DPI).
- **Theme-aware fonts** — five bundled variable fonts (Outfit, Sora, DM Sans, JetBrains Mono, Roboto) match the UI design themes with proper weight axes.
- **IPP via `ipptool`** — print jobs submitted using standard IPP protocol, works with any IPP-capable printer.
- **MQTT discovery** — each printer becomes a HA device with sensors (ink levels, job counts, health status) and controls (enable/disable, cadence, template select, print buttons).
- **mDNS/Zeroconf** — automatic network printer discovery for `_ipp._tcp` and `_ipps._tcp` services.

### API

The app exposes an HTTP API on port 8099 (ingress-routed in HA):

- `GET /health` — service status, printer states, MQTT status, discovery results
- `GET /printers/<id>/preview?template=<name>` — static template preview (JPEG)
- `POST /print?printer_id=<id>&force=true` — trigger a print job
- `GET /discovery` — discovered network printers
- `GET /config` / `POST /config` — read/write configuration
- `POST /actions/restart` — restart the app

Full API reference in [`printer_keepalive/DOCS.md`](printer_keepalive/DOCS.md).

## Contributing

### Local Development

The repo includes a Docker Compose setup for local iteration without a HA install:

```bash
make pk-dev-up        # Build and start the container
make pk-dev-health    # Check the health endpoint
make pk-dev-discovery # View discovered printers
make pk-dev-logs      # Tail container logs
make pk-dev-down      # Stop everything
```

Configuration lives in `printer_keepalive/dev/data/options.json` (auto-created from the example on first run).

### Project Structure

```
printer_keepalive/
  app.py              # The entire app — server, scheduler, templates, MQTT
  config.yaml         # HA app metadata and schema
  Dockerfile          # Container image definition
  fonts/              # Bundled theme fonts (variable TTF)
  designs/            # UI theme HTML files (v1-v5)
  DOCS.md             # Full configuration and API reference
  GETTING_STARTED.md  # Install guide for HA OS and Docker
  CHANGELOG.md        # Version history
```

### Making Changes

1. Fork and clone the repo.
2. Run `make pk-dev-up` to start a local instance.
3. Edit `printer_keepalive/app.py` — the container mounts it live.
4. Test your changes via the API or ingress UI.
5. Verify syntax: `python3 -c "import py_compile; py_compile.compile('printer_keepalive/app.py', doraise=True)"`
6. Open a pull request with a clear description of the change.

### Guidelines

- Keep it in one file. The single-file architecture is intentional — it simplifies the container, deployment, and debugging.
- Bump the version in both `app.py` (`APP_VERSION`) and `config.yaml` (`version`) for any user-facing change.
- Update `CHANGELOG.md` with a summary of what changed.
- Test template rendering locally — generate a PDF or JPEG and visually verify before pushing.
