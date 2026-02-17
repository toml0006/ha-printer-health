# Changelog

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
