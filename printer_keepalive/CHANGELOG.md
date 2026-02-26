# Changelog

## 0.4.0

### UI Overhaul
- Rebuilt ingress dashboard as a full SPA with hash-based client-side routing.
- Added light/dark/system theme support with FOUC prevention.
- Introduced 5 selectable design themes: Bento Grid, Glassmorphism, Neubrutalist, Cinematic Dark, and Home Assistant Native.
- User design preference persisted via cookie with server-side routing.
- Added dual-mode configuration editor: organized form fields and advanced raw JSON with bidirectional sync.

### New Tabs
- **Cards**: Lovelace YAML generator with 5 card styles (Full Dashboard, Compact, Glance, Status Only, Controls Only) and copy-to-clipboard per printer.
- **Templates**: Live JPEG preview of all print templates per printer with direct print action.
- **Help**: Full add-on documentation including maintenance model, configuration guide, HA entity reference, and collapsible API docs. Config sections link to Help via "Learn more".

### API
- Added `GET /printers/<id>/preview?template=<name>` endpoint returning rendered template as JPEG.
- Expanded `GET /printers/<id>/card` with `?style=` parameter and `styles_available` in response.

### Fixes
- Enabled `host_network: true` so mDNS printer discovery works from the add-on container.
- Removed duplicate MQTT connection fields (host, port, username, password, TLS) from config schema — these are auto-discovered from the Supervisor MQTT service at runtime.
- Added `BUILD_VERSION` label to Dockerfile for cache busting on rebuilds.

## 0.3.8

- Added ingress-first configuration workflow:
  - New config editor in ingress that loads/saves `/data/options.json`.
  - New restart action button in ingress for Supervisor add-on runtime.
  - Ingress now supports managing full add-on configuration payload in one place.
- Added configuration/restart API endpoints:
  - `GET /config`
  - `POST /config`
  - `POST /actions/restart`
- Bumped runtime/app metadata version to `0.3.8`.

## 0.3.7

- Added an ingress dashboard UI served by the add-on at `/` for in-HA management.
  - Shows service overview and discovery status.
  - Lists configured printers with controls for:
    - `enabled`
    - `cadence_hours`
    - `template`
    - print now (due-check or forced)
    - poll now
  - Supports discovery rescan from the UI.
  - Supports optional bearer token entry (stored in browser local storage) for protected POST actions.
- Enabled Home Assistant ingress metadata in add-on config:
  - `ingress: true`
  - `ingress_port: 8099`
  - panel title/icon set for sidebar launch.
- Bumped runtime/app metadata version to `0.3.7`.

## 0.3.6

- Added keepalive print context rendering on generated pages:
  - trigger/source
  - reason for print (due cadence vs manual/forced request)
  - cadence and timing details
  - printer-specific Home Assistant signal lines
- Added QR code rendering on printed pages that links to add-on/docs URL.
- Added `addon_page_url` option to explicitly control QR destination.
  - Falls back to `<ha_url>/hassio/addon/printer_keepalive/info` when `ha_url` is set.
  - Falls back to docs URL when no Home Assistant URL is available.
- Fixed API `force` body parsing so string values like `"false"` no longer force a print.
- Bumped runtime/app metadata version to `0.3.6`.

## 0.3.5

- Added Supervisor MQTT service defaults resolution at startup:
  - Queries `http://supervisor/services/mqtt` when running under Supervisor.
  - Auto-applies local broker host/port when MQTT host is unset/default.
  - Auto-applies broker credentials when using the local Supervisor broker.
- Bumped runtime/app metadata version to `0.3.5`.

## 0.3.4

- Defaulted MQTT settings to assume Home Assistant's Mosquitto add-on:
  - `mqtt.enabled: true`
  - `mqtt.host: core-mosquitto`
- Added add-on service hint `mqtt:want` in metadata.
- Added runtime fallback: when running under Supervisor with MQTT enabled and empty host, use `core-mosquitto`.

## 0.3.3

- Fixed startup behavior when no printers are configured: the add-on now runs in discovery/API-only mode instead of crashing.
- Added URI normalization for configured printers:
  - Bare host/IP like `10.0.20.112` is normalized to `ipp://10.0.20.112/ipp/print`.
  - `http://` and `https://` inputs are converted to `ipp://` and `ipps://`.
- Synced app runtime version to `0.3.3`.

## 0.3.2

- Fixed options schema so `entity_ids` is optional for each entry in `printers[]`.
- Fixed options schema so top-level `entity_ids` can be omitted.

## 0.3.1

- Added printer discovery support using mDNS/zeroconf for `_ipp._tcp` and optional `_ipps._tcp`.
- Added discovery API endpoints:
  - `GET /discovery`
  - `GET /discovery?force=true`
  - `POST /discovery/rescan`
- Discovery results now include suggested per-printer config stubs and guessed printer type/cadence.
- Added configurable discovery behavior:
  - `discovery_enabled`
  - `discovery_interval_minutes`
  - `discovery_timeout_seconds`
  - `discovery_ipp_query_timeout_seconds`
  - `discovery_include_ipps`
- Added background discovery refresh in scheduler loop and discovery summary in `/health`.
- Added runtime dependency on `zeroconf`.
- Added local dev workflow files and Make targets (`pk-dev-*`) for Docker-based iteration.
- Added `GETTING_STARTED.md` with install/run walkthrough for HA OS/Supervised and HA Container.

## 0.3.0

- Added multi-printer configuration via `printers[]` with per-printer:
  - cadence
  - template
  - enabled state
  - printer type (`inkjet`/`laser`)
- Added print-history-aware keepalive logic to print only when due.
- Added external print tracking from IPP `job-impressions-completed`.
- Added MQTT discovery bridge so each printer is exposed as a Home Assistant device.
- Added HA control entities (`switch`/`select`/`number`/`button`) and health/status sensors.
- Added additional IPP telemetry in payloads and discovered entities:
  - queued jobs
  - media sheets completed
  - printer uptime
  - lowest supply level
  - last keepalive result / next due
- Added `/printers/<id>/card` endpoint that returns a Lovelace dashboard snippet.
- Added built-in maintenance guidance (inkjet vs laser) with source links.
- Added direct Home Assistant API mode (`ha_url` + `ha_token`) for non-Supervisor installs.
- Added MQTT runtime dependency (`paho-mqtt`) to container image.
- Fixed state-lock re-entrancy deadlock risk by using a reentrant lock.

## 0.2.0

- Renamed add-on to generic `Printer Keepalive`
- Added template selection (`color_bars`, `home_summary`, `weather_snapshot`, `entity_report`, `hybrid`)
- Added Home Assistant-aware rendering via Supervisor API
- Added optional internal scheduling (`auto_print_enabled`, interval hours)
- Added state persistence for last run and next schedule
- Added `/templates` API endpoint and richer `/health` payload

## 0.1.0

- Initial release as ET-3850 focused keepalive print endpoint
