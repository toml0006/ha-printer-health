# Printer Keepalive Home Assistant Add-on

Home Assistant add-on that prints color-rich keepalive pages to one or more IPP printers.

Start here: `GETTING_STARTED.md`

Features:

- Multi-printer support with per-printer cadence/template/type.
- Built-in network printer discovery (`/discovery`) for IPP/IPPS services.
- History-aware keepalive logic (print only when due).
- Printed context on keepalive pages (trigger, reason, cadence timing, and printer-specific HA signals).
- Printed QR code that opens the add-on page/docs URL.
- Ingress dashboard UI for full config editing, status, printer overrides, print-now, poll-now, and discovery rescan.
- Optional MQTT discovery so each printer appears as a Home Assistant device.
- IPP health telemetry sensors (state, queue, counters, marker/supply levels, uptime).
- Selectable templates:
  - `color_bars`
  - `home_summary`
  - `weather_snapshot`
  - `entity_report`
  - `hybrid`
- Home Assistant-aware templates using Supervisor API or direct HA API (`ha_url` + `ha_token`).
- API endpoints for manual print, per-printer settings, and Lovelace card YAML.

## Local Development

From repo root:

```bash
make pk-dev-up
```

This starts a local dev container on `http://127.0.0.1:18099`, mounted to source code:

- `printer_keepalive/app.py` -> `/app/app.py`
- `printer_keepalive/dev/data/options.json` -> `/data/options.json`

Useful commands:

```bash
make pk-dev-logs
make pk-dev-health
make pk-dev-discovery
make pk-dev-rescan
make pk-dev-restart
make pk-dev-down
```

`make pk-dev-init` creates `printer_keepalive/dev/data/options.json`
from `printer_keepalive/dev/options.example.json` if missing.

See `GETTING_STARTED.md` for install/run setup and `DOCS.md` for full configuration/API details.
