# Getting Started

This guide covers installing and running `Printer Keepalive` in Home Assistant.

## Choose Your Home Assistant Install Type

1. Home Assistant OS / Home Assistant Supervised:
   Install `Printer Keepalive` as a real add-on.
2. Home Assistant Container (Docker):
   Run `Printer Keepalive` as a sidecar container (add-ons are not available).

## Prerequisites

1. At least one reachable IPP or IPPS printer.
2. Printer URI (example: `ipp://192.168.1.40/ipp/print`).
   Bare host/IP values are accepted and auto-normalized (for example `192.168.1.40` -> `ipp://192.168.1.40/ipp/print`).
3. Optional but recommended: MQTT broker for automatic device/entity creation.

## Home Assistant OS / Supervised (Add-on Install)

1. Add this repository in Home Assistant:
   `Settings -> Add-ons -> Add-on Store -> Repositories`.
2. Install `Printer Keepalive`.
3. Configure add-on options (minimum example):

```yaml
printers:
  - id: office_printer
    name: Office Printer
    printer_uri: ipp://192.168.1.40/ipp/print
    printer_type: inkjet
    enabled: true
    cadence_hours: 168
    template: home_summary
    entity_ids: []
auto_print_enabled: true
```

4. Start the add-on.
5. Open logs and confirm startup is clean.
6. Trigger one print to validate connectivity:
   - Use your existing HA script/REST command if configured, or call API:
     `POST /print?printer_id=office_printer&force=true`.

## Home Assistant Container (Docker Sidecar)

Home Assistant Container does not support add-ons. Use the same app as a Docker service.

1. Add a `printer_keepalive` service to your compose stack (or use the dev compose in this repo).
2. Mount:
   - `/app/app.py` from project source (optional for live editing)
   - `/data/options.json` for runtime config
3. Set options in `/data/options.json` (same schema as add-on).
4. Start the service and verify:
   - `GET /health`
   - `GET /discovery`
5. In HA, point `rest_command` or automations at:
   - `http://printer_keepalive:8099` (from HA container network)
   - or published host port if calling externally.

## Optional: Enable MQTT Device Discovery

To expose each printer as a Home Assistant device with sensors/controls, set:

```yaml
mqtt:
  enabled: true
  host: core-mosquitto
  port: 1883
  username: ""
  password: ""
  discovery_prefix: homeassistant
  topic_prefix: printer_keepalive
  retain: true
  tls: false
  client_id: printer_keepalive
```

Defaults already assume Home Assistant Mosquitto (`enabled: true`, `host: core-mosquitto`).
On Supervisor installs, the add-on also reads `services/mqtt` and can auto-fill
broker host/port/credentials when those values are not explicitly set.
Set `username`/`password` if your broker requires authentication for this add-on.

Then restart `Printer Keepalive`. New entities should appear automatically.

## Optional: Discover Printers Automatically

Use discovery endpoints to find candidate printers and copy suggested config stubs:

1. `GET /discovery`
2. `GET /discovery?force=true`
3. `POST /discovery/rescan`

## First-Run Checklist

1. `/health` returns `ok: true`.
2. At least one configured printer is listed under `/printers`.
3. Forced print succeeds for one printer.
4. If MQTT enabled, printer device/entities appear in Home Assistant.
