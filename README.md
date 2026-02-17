# HA Printer Health

Home Assistant add-on repository for proactive printer maintenance and health telemetry.

## Included Add-ons

- `printer_keepalive`

## Quick Start

1. In Home Assistant, open `Settings -> Add-ons -> Add-on Store -> Repositories`.
2. Add this repository URL:
   `https://github.com/toml0006/ha-printer-health`
3. Install `Printer Keepalive`.
4. Follow:
   - `printer_keepalive/GETTING_STARTED.md` for install/run setup
   - `printer_keepalive/DOCS.md` for full configuration and API details

## Local Development

From repository root:

```bash
make pk-dev-up
make pk-dev-health
make pk-dev-discovery
```

Stop:

```bash
make pk-dev-down
```
