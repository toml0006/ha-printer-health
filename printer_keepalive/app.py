#!/usr/bin/env python3
"""Printer Keepalive add-on.

Features:
- Supports multiple IPP printers.
- Performs keepalive prints only when print history indicates they are due.
- Exposes state and control via HTTP API.
- Optionally publishes Home Assistant MQTT discovery devices/entities.
- Generates print templates, including Home Assistant-aware content.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt
from PIL import Image, ImageDraw, ImageFont
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

try:
    import qrcode
except ImportError:
    qrcode = None

APP_VERSION = "0.3.8"
APP_NAME = "Printer Keepalive"
APP_URL = "https://github.com/toml0006/ha-printer-health/tree/main/printer_keepalive"
ADDON_SLUG = "printer_keepalive"
DEFAULT_SUPERVISOR_MQTT_HOST = "core-mosquitto"
SUPERVISOR_API_BASE = "http://supervisor"

OPTIONS_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/state.json")
PRINT_JOB_TEST = "/usr/share/cups/ipptool/print-job.test"
GET_ATTRS_TEST = "/usr/share/cups/ipptool/get-printer-attributes.test"

REQUEST_TIMEOUT_SECONDS = 120
IPP_QUERY_TIMEOUT_SECONDS = 45
HTTP_TIMEOUT_SECONDS = 15
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8099
PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300

SUPPORTED_TEMPLATES = (
    "color_bars",
    "home_summary",
    "weather_snapshot",
    "entity_report",
    "hybrid",
)
SUPPORTED_PRINTER_TYPES = ("inkjet", "laser")

DEFAULT_INKJET_CADENCE_HOURS = 168  # 7 days
DEFAULT_LASER_CADENCE_HOURS = 720   # 30 days
DEFAULT_UNKNOWN_CADENCE_HOURS = 336 # 14 days
DEFAULT_FAILURE_RETRY_MINUTES = 60
DEFAULT_DISCOVERY_INTERVAL_MINUTES = 180
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 6
DEFAULT_DISCOVERY_IPP_QUERY_TIMEOUT_SECONDS = 8

IPP_STATE_MAP = {
    3: "idle",
    4: "processing",
    5: "stopped",
}

MAINTENANCE_GUIDANCE: dict[str, dict[str, Any]] = {
    "inkjet": {
        "summary": (
            "Inkjet printheads can dry and clog when idle. Keepalive prints should "
            "include color output and run regularly."
        ),
        "default_cadence_hours": DEFAULT_INKJET_CADENCE_HOURS,
        "recommended_range_days": "7-30 (environment and model dependent)",
        "research_notes": [
            "Canon inkjet manual guidance recommends periodic printing at least monthly.",
            "Epson ET-3850 guidance emphasizes nozzle checks first and avoiding excessive cleanings due to ink use.",
        ],
        "sources": [
            "https://ij.manual.canon/ij/webmanual/Manual/All/TS8700%20series/EN/UG/ug-154.html",
            "https://download4.epson.biz/sec_pubs/et-3850_series/useg/en/GUID-381C0AF6-12DF-433B-9294-C8845DF3F126.htm",
            "https://download4.epson.biz/sec_pubs/et-3850_series/useg/en/GUID-69CE27D1-1CF9-4678-BA12-3C538DFFFE8A.htm",
        ],
    },
    "laser": {
        "summary": (
            "Laser toner is dry and generally less sensitive to idle periods than liquid ink, "
            "but periodic test prints still help catch media/fuser/supply issues before needed."
        ),
        "default_cadence_hours": DEFAULT_LASER_CADENCE_HOURS,
        "recommended_range_days": "14-60 (model and environment dependent)",
        "research_notes": [
            "Canon toner guidance states toner does not carry the same dry-out risk as ink.",
            "Canon laser toner storage guidance focuses on temperature/humidity and packaging handling.",
        ],
        "sources": [
            "https://www.usa.canon.com/learning/training-articles/training-articles-list/printer-toner-vs-ink",
            "https://downloads.canon.com/cpr/pdf/Manuals/eManuals/LBP3480_eManual/us_LBP3480_Manual/contents/12010030.html",
        ],
    },
}

PRINT_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
DISCOVERY_LOCK = threading.RLock()


@dataclass(frozen=True)
class PrinterConfig:
    printer_id: str
    name: str
    printer_uri: str
    printer_type: str
    enabled: bool
    cadence_hours: int
    template: str
    weather_entity: str
    entity_ids: list[str]
    title: str
    footer: str


@dataclass(frozen=True)
class MqttConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    discovery_prefix: str
    topic_prefix: str
    retain: bool
    tls: bool
    client_id: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    stamp = value or utc_now()
    return stamp.astimezone(timezone.utc).isoformat()


def parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def log(message: str) -> None:
    print(f"[{iso_utc()}] {message}", flush=True)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower())
    cleaned = cleaned.strip("_")
    return cleaned or "printer"


def option_str(options: dict[str, Any], key: str, default: str = "") -> str:
    value = options.get(key, default)
    return value.strip() if isinstance(value, str) else default


def option_bool(options: dict[str, Any], key: str, default: bool = False) -> bool:
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def option_int(options: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = options.get(key, default)
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, numeric))


def bool_from_any(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def option_str_list(options: dict[str, Any], key: str) -> list[str]:
    value = options.get(key, [])
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                result.append(stripped)
    return result


def load_options() -> dict[str, Any]:
    if not OPTIONS_PATH.exists():
        raise RuntimeError(f"Missing options file: {OPTIONS_PATH}")
    try:
        with OPTIONS_PATH.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        if not isinstance(payload, dict):
            raise RuntimeError("Options JSON must be an object")
        return payload
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in options file: {exc}") from exc


def save_options(payload: dict[str, Any]) -> None:
    OPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OPTIONS_PATH.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
        fp.write("\n")


def validate_options_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        return False, f"Options JSON is not serializable: {exc}"

    try:
        parse_printers(payload)
    except Exception as exc:  # noqa: BLE001
        return False, f"Invalid printer configuration: {exc}"

    try:
        parse_mqtt_config(payload)
    except Exception as exc:  # noqa: BLE001
        return False, f"Invalid MQTT configuration: {exc}"

    return True, ""


def supervisor_restart_self() -> tuple[bool, str]:
    token = supervisor_token_env()
    if not token:
        return False, "Supervisor token not available; restart is only supported in Home Assistant add-on runtime."

    request = Request(
        f"{SUPERVISOR_API_BASE}/addons/self/restart",
        data=b"{}",
        method="POST",
    )
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            if 200 <= status < 300:
                return True, "Restart request accepted by Supervisor."
            return False, f"Supervisor restart returned status {status}."
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:  # noqa: BLE001
            body = ""
        detail = f" ({body})" if body else ""
        return False, f"Supervisor restart failed: HTTP {exc.code}{detail}"
    except (URLError, TimeoutError, OSError) as exc:
        return False, f"Supervisor restart failed: {exc}"


def supervisor_token_env() -> str:
    return os.environ.get("SUPERVISOR_TOKEN", "").strip()


def supervisor_get_service(service: str) -> dict[str, Any] | None:
    token = supervisor_token_env()
    if not token:
        return None

    request = Request(f"{SUPERVISOR_API_BASE}/services/{quote(service, safe='')}")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        log(f"Supervisor service request failed for {service}: {exc}")
        return None

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return None


def default_cadence_for_type(printer_type: str) -> int:
    normalized = printer_type.strip().lower()
    if normalized == "inkjet":
        return DEFAULT_INKJET_CADENCE_HOURS
    if normalized == "laser":
        return DEFAULT_LASER_CADENCE_HOURS
    return DEFAULT_UNKNOWN_CADENCE_HOURS


def normalize_printer_uri(raw_uri: str) -> str:
    value = raw_uri.strip()
    if not value:
        return ""

    if "://" not in value:
        value = f"ipp://{value}"

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme == "http":
        scheme = "ipp"
    elif scheme == "https":
        scheme = "ipps"
    elif scheme not in {"ipp", "ipps"}:
        scheme = "ipp"

    host = parsed.hostname or ""
    if not host:
        return value

    try:
        port = parsed.port
    except ValueError:
        port = None

    host_for_uri = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc = f"{host_for_uri}:{port}" if port else host_for_uri

    candidate = parsed.path.strip().strip("/")
    if not candidate:
        path = "/ipp/print"
    else:
        parts = [quote(part, safe="") for part in candidate.split("/") if part]
        path = "/" + "/".join(parts) if parts else "/ipp/print"

    normalized = f"{scheme}://{netloc}{path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    return normalized


def parse_printer_entry(entry: dict[str, Any], index: int, defaults: dict[str, Any]) -> PrinterConfig | None:
    printer_name = option_str(entry, "name", f"Printer {index + 1}")
    raw_printer_uri = option_str(entry, "printer_uri")
    if not raw_printer_uri:
        return None

    printer_uri = normalize_printer_uri(raw_printer_uri)
    if raw_printer_uri.strip() != printer_uri:
        log(f"Normalized printer URI for {printer_name}: '{raw_printer_uri}' -> '{printer_uri}'")

    printer_id = slugify(option_str(entry, "id", printer_name))
    printer_type = option_str(entry, "printer_type", "inkjet").lower()
    if printer_type not in SUPPORTED_PRINTER_TYPES:
        printer_type = "inkjet"

    cadence_default = default_cadence_for_type(printer_type)
    cadence_hours = option_int(
        entry,
        "cadence_hours",
        int(defaults.get("auto_print_interval_hours", cadence_default)),
        1,
        720,
    )

    template = option_str(entry, "template", str(defaults.get("default_template", "home_summary"))).lower()
    if template not in SUPPORTED_TEMPLATES:
        template = "home_summary"

    if isinstance(entry.get("entity_ids"), list):
        entity_ids = option_str_list(entry, "entity_ids")
    else:
        entity_ids = list(defaults.get("entity_ids", []))

    return PrinterConfig(
        printer_id=printer_id,
        name=printer_name,
        printer_uri=printer_uri,
        printer_type=printer_type,
        enabled=option_bool(entry, "enabled", True),
        cadence_hours=cadence_hours,
        template=template,
        weather_entity=option_str(entry, "weather_entity", str(defaults.get("weather_entity", ""))),
        entity_ids=entity_ids,
        title=option_str(entry, "title", str(defaults.get("title", APP_NAME))),
        footer=option_str(entry, "footer", str(defaults.get("footer", "Generated by Home Assistant"))),
    )


def parse_printers(options: dict[str, Any]) -> list[PrinterConfig]:
    defaults = {
        "default_template": option_str(options, "default_template", "home_summary"),
        "auto_print_interval_hours": option_int(options, "auto_print_interval_hours", DEFAULT_INKJET_CADENCE_HOURS, 1, 720),
        "title": option_str(options, "title", APP_NAME),
        "footer": option_str(options, "footer", "Generated by Home Assistant"),
        "weather_entity": option_str(options, "weather_entity", ""),
        "entity_ids": option_str_list(options, "entity_ids"),
    }

    printers: list[PrinterConfig] = []
    raw_printers = options.get("printers")
    if isinstance(raw_printers, list):
        for index, entry in enumerate(raw_printers):
            if not isinstance(entry, dict):
                continue
            parsed = parse_printer_entry(entry, index, defaults)
            if parsed:
                printers.append(parsed)

    # Backward compatibility for previous single-printer config.
    if not printers:
        legacy_uri_raw = option_str(options, "printer_uri")
        if legacy_uri_raw:
            legacy_uri = normalize_printer_uri(legacy_uri_raw)
            if legacy_uri_raw.strip() != legacy_uri:
                log(f"Normalized legacy printer URI: '{legacy_uri_raw}' -> '{legacy_uri}'")
            template = option_str(options, "default_template", "home_summary").lower()
            if template not in SUPPORTED_TEMPLATES:
                template = "home_summary"
            printers.append(
                PrinterConfig(
                    printer_id="printer_1",
                    name="Printer 1",
                    printer_uri=legacy_uri,
                    printer_type=option_str(options, "printer_type", "inkjet").lower() or "inkjet",
                    enabled=True,
                    cadence_hours=option_int(options, "auto_print_interval_hours", DEFAULT_INKJET_CADENCE_HOURS, 1, 720),
                    template=template,
                    weather_entity=option_str(options, "weather_entity", ""),
                    entity_ids=option_str_list(options, "entity_ids"),
                    title=option_str(options, "title", APP_NAME),
                    footer=option_str(options, "footer", "Generated by Home Assistant"),
                )
            )

    # Ensure unique IDs.
    seen: set[str] = set()
    unique_printers: list[PrinterConfig] = []
    for index, printer in enumerate(printers):
        pid = printer.printer_id
        if pid in seen:
            pid = slugify(f"{pid}_{index + 1}")
            printer = PrinterConfig(
                printer_id=pid,
                name=printer.name,
                printer_uri=printer.printer_uri,
                printer_type=printer.printer_type,
                enabled=printer.enabled,
                cadence_hours=printer.cadence_hours,
                template=printer.template,
                weather_entity=printer.weather_entity,
                entity_ids=printer.entity_ids,
                title=printer.title,
                footer=printer.footer,
            )
        seen.add(pid)
        unique_printers.append(printer)

    return unique_printers


def parse_mqtt_config(options: dict[str, Any]) -> MqttConfig:
    mqtt_block = options.get("mqtt")
    mqtt_opts = mqtt_block if isinstance(mqtt_block, dict) else {}

    enabled = option_bool(mqtt_opts, "enabled", True)
    host = option_str(mqtt_opts, "host", "")
    if enabled and not host and os.environ.get("SUPERVISOR_TOKEN", "").strip():
        host = DEFAULT_SUPERVISOR_MQTT_HOST
    port = option_int(mqtt_opts, "port", 1883, 1, 65535)
    username = option_str(mqtt_opts, "username", "")
    password = option_str(mqtt_opts, "password", "")
    tls = option_bool(mqtt_opts, "tls", False)

    if enabled:
        mqtt_service = supervisor_get_service("mqtt")
        if isinstance(mqtt_service, dict):
            service_host = str(mqtt_service.get("host", "")).strip()
            host_is_default = host in {"", DEFAULT_SUPERVISOR_MQTT_HOST}
            applied_service_defaults = False

            if host_is_default and service_host:
                host = service_host
                applied_service_defaults = True

                service_port_raw = mqtt_service.get("port")
                try:
                    service_port = int(service_port_raw)
                except (TypeError, ValueError):
                    service_port = 0
                if 1 <= service_port <= 65535:
                    port = service_port
                    applied_service_defaults = True

                service_tls = bool_from_any(mqtt_service.get("ssl"))
                protocol = str(mqtt_service.get("protocol", "")).strip().lower()
                if service_tls is None and protocol in {"mqtts", "ssl", "tls"}:
                    service_tls = True
                if service_tls is True:
                    tls = True
                    applied_service_defaults = True

            if service_host and host == service_host:
                if not username:
                    username = str(mqtt_service.get("username", "")).strip()
                    if username:
                        applied_service_defaults = True
                if not password:
                    password = str(mqtt_service.get("password", ""))
                    if password:
                        applied_service_defaults = True

            if applied_service_defaults and host:
                auth_label = "with auth" if username else "without auth"
                tls_label = "tls" if tls else "no-tls"
                log(f"Using Supervisor MQTT service defaults ({host}:{port}, {auth_label}, {tls_label}).")

    return MqttConfig(
        enabled=enabled and bool(host),
        host=host,
        port=port,
        username=username,
        password=password,
        discovery_prefix=option_str(mqtt_opts, "discovery_prefix", "homeassistant") or "homeassistant",
        topic_prefix=option_str(mqtt_opts, "topic_prefix", "printer_keepalive") or "printer_keepalive",
        retain=option_bool(mqtt_opts, "retain", True),
        tls=tls,
        client_id=option_str(mqtt_opts, "client_id", f"printer_keepalive_{os.getpid()}"),
    )


OPTIONS = load_options()
PRINTERS = parse_printers(OPTIONS)
if not PRINTERS:
    log("No printers configured. Running in discovery/API-only mode until printers are added.")
PRINTERS_BY_ID = {printer.printer_id: printer for printer in PRINTERS}

AUTO_PRINT_ENABLED = option_bool(OPTIONS, "auto_print_enabled", True)
STATUS_POLL_INTERVAL_SECONDS = option_int(OPTIONS, "status_poll_interval_minutes", 15, 1, 1440) * 60
AUTH_TOKEN = option_str(OPTIONS, "auth_token")
FAILURE_RETRY_MINUTES = option_int(OPTIONS, "failure_retry_minutes", DEFAULT_FAILURE_RETRY_MINUTES, 1, 1440)
DISCOVERY_ENABLED = option_bool(OPTIONS, "discovery_enabled", True)
DISCOVERY_INTERVAL_SECONDS = (
    option_int(
        OPTIONS,
        "discovery_interval_minutes",
        DEFAULT_DISCOVERY_INTERVAL_MINUTES,
        1,
        1440,
    )
    * 60
)
DISCOVERY_TIMEOUT_SECONDS = option_int(
    OPTIONS,
    "discovery_timeout_seconds",
    DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    1,
    30,
)
DISCOVERY_IPP_QUERY_TIMEOUT_SECONDS = option_int(
    OPTIONS,
    "discovery_ipp_query_timeout_seconds",
    DEFAULT_DISCOVERY_IPP_QUERY_TIMEOUT_SECONDS,
    1,
    30,
)
DISCOVERY_INCLUDE_IPPS = option_bool(OPTIONS, "discovery_include_ipps", True)

MQTT_CONFIG = parse_mqtt_config(OPTIONS)

SELECTED_HA_URL = option_str(OPTIONS, "ha_url") or os.environ.get("HA_URL", "").strip()
SELECTED_HA_TOKEN = option_str(OPTIONS, "ha_token") or os.environ.get("HA_TOKEN", "").strip()

SUPERVISOR_TOKEN = supervisor_token_env()
DEFAULT_SUPERVISOR_API_BASE = "http://supervisor/core/api"
HASS_API_BASE = DEFAULT_SUPERVISOR_API_BASE
HASS_AUTH_TOKEN = SUPERVISOR_TOKEN
if not HASS_AUTH_TOKEN:
    if SELECTED_HA_URL:
        HASS_API_BASE = f"{SELECTED_HA_URL.rstrip('/')}/api"
        HASS_AUTH_TOKEN = SELECTED_HA_TOKEN

ADDON_PAGE_URL = option_str(OPTIONS, "addon_page_url")
if not ADDON_PAGE_URL and SELECTED_HA_URL:
    ADDON_PAGE_URL = f"{SELECTED_HA_URL.rstrip('/')}/hassio/addon/{ADDON_SLUG}/info"
if not ADDON_PAGE_URL:
    ADDON_PAGE_URL = APP_URL


DEFAULT_PRINTER_STATE: dict[str, Any] = {
    "history_anchor_at": "",
    "last_polled_at": "",
    "last_keepalive_at": "",
    "last_keepalive_result": "never",
    "last_keepalive_error": "",
    "last_keepalive_attempt_at": "",
    "keepalive_print_count": 0,
    "last_external_print_at": "",
    "external_print_count": 0,
    "last_seen_job_impressions": None,
    "job_impressions_completed": None,
    "queued_job_count": None,
    "printer_state": "unknown",
    "printer_state_reasons": [],
    "printer_is_accepting_jobs": None,
    "marker_levels": [],
    "marker_names": [],
    "marker_colors": [],
    "printer_make_and_model": "",
    "printer_name": "",
    "printer_uuid": "",
    "printer_state_message": "",
    "printer_up_time_seconds": None,
    "media_sheets_completed": None,
    "last_error": "",
    "template_override": "",
    "cadence_hours_override": None,
    "enabled_override": None,
}
DEFAULT_STATE: dict[str, Any] = {
    "version": 2,
    "printers": {},
}
DEFAULT_DISCOVERY_STATE: dict[str, Any] = {
    "last_scan_at": "",
    "last_scan_duration_seconds": 0.0,
    "last_error": "",
    "printers": [],
}


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        if not isinstance(payload, dict):
            return json.loads(json.dumps(DEFAULT_STATE))
        if not isinstance(payload.get("printers"), dict):
            payload["printers"] = {}
        return payload
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state_locked() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as fp:
        json.dump(STATE, fp, indent=2, sort_keys=True)


def ensure_printer_state_locked(printer_id: str) -> dict[str, Any]:
    printers_state = STATE.setdefault("printers", {})
    raw = printers_state.get(printer_id)
    if not isinstance(raw, dict):
        raw = json.loads(json.dumps(DEFAULT_PRINTER_STATE))
        printers_state[printer_id] = raw

    for key, value in DEFAULT_PRINTER_STATE.items():
        if key not in raw:
            raw[key] = json.loads(json.dumps(value)) if isinstance(value, (dict, list)) else value

    if not raw.get("history_anchor_at"):
        raw["history_anchor_at"] = iso_utc()
    return raw


STATE = load_state()
with STATE_LOCK:
    for printer_id in PRINTERS_BY_ID:
        ensure_printer_state_locked(printer_id)
    save_state_locked()

DISCOVERY_STATE = json.loads(json.dumps(DEFAULT_DISCOVERY_STATE))


def line_height(font: ImageFont.ImageFont) -> int:
    left, top, right, bottom = font.getbbox("Ag")
    return bottom - top


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_TITLE = load_font(72)
FONT_SECTION = load_font(42)
FONT_BODY = load_font(32)
FONT_SMALL = load_font(26)


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    max_width: int,
) -> int:
    words = text.split()
    if not words:
        return y

    current = words[0]
    rendered: list[str] = []
    for word in words[1:]:
        candidate = f"{current} {word}"
        left, _, right, _ = draw.textbbox((0, 0), candidate, font=font)
        if right - left <= max_width:
            current = candidate
        else:
            rendered.append(current)
            current = word
    rendered.append(current)

    spacing = line_height(font) + 6
    for line in rendered:
        draw.text((x, y), line, font=font, fill=fill)
        y += spacing
    return y


def hass_get_json(path: str) -> Any | None:
    if not HASS_API_BASE:
        return None

    request = Request(f"{HASS_API_BASE}{path}")
    if HASS_AUTH_TOKEN:
        request.add_header("Authorization", f"Bearer {HASS_AUTH_TOKEN}")
    request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        log(f"Home Assistant API request failed for {path}: {exc}")
        return None


def fetch_all_states() -> list[dict[str, Any]]:
    payload = hass_get_json("/states")
    return payload if isinstance(payload, list) else []


def states_by_entity(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for state in states:
        entity_id = state.get("entity_id")
        if isinstance(entity_id, str):
            indexed[entity_id] = state
    return indexed


def choose_default_entities(states: list[dict[str, Any]], limit: int = 8) -> list[str]:
    domain_order = [
        "person",
        "weather",
        "alarm_control_panel",
        "climate",
        "switch",
        "light",
        "binary_sensor",
        "sensor",
    ]
    by_domain: dict[str, list[str]] = {domain: [] for domain in domain_order}

    for state in states:
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain in by_domain:
            by_domain[domain].append(entity_id)

    selected: list[str] = []
    for domain in domain_order:
        for entity_id in by_domain[domain]:
            if entity_id not in selected:
                selected.append(entity_id)
            if len(selected) >= limit:
                return selected
    return selected


def detect_weather_entity(printer: PrinterConfig, states: list[dict[str, Any]]) -> str:
    if printer.weather_entity:
        return printer.weather_entity
    for state in states:
        entity_id = state.get("entity_id")
        if isinstance(entity_id, str) and entity_id.startswith("weather."):
            return entity_id
    return ""


def format_entity_line(state: dict[str, Any]) -> str:
    entity_id = str(state.get("entity_id", "unknown.entity"))
    attributes = state.get("attributes", {})
    if not isinstance(attributes, dict):
        attributes = {}
    friendly_name = attributes.get("friendly_name") or entity_id
    raw_state = str(state.get("state", "unknown"))
    unit = str(attributes.get("unit_of_measurement", "")).strip()
    suffix = f" {unit}" if unit else ""
    return f"{friendly_name}: {raw_state}{suffix}"


def _format_time_for_page(stamp: str) -> str:
    parsed = parse_iso(stamp)
    if parsed is None:
        return "n/a"
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _sanitize_qr_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return candidate


def _draw_qr_code(image: Image.Image, draw: ImageDraw.ImageDraw, url: str) -> None:
    if not url:
        return

    qr_size = 320
    qr_left = PAGE_WIDTH - qr_size - 120
    qr_top = 70

    if qrcode is None:
        draw.text((qr_left, qr_top), "QR dependency unavailable", font=FONT_SMALL, fill=(80, 80, 80))
        draw.text((qr_left, qr_top + 40), url, font=FONT_SMALL, fill=(80, 80, 80))
        return

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_image = qr_image.resize((qr_size, qr_size))
        image.paste(qr_image, (qr_left, qr_top))

        draw.rectangle(
            (qr_left - 5, qr_top - 5, qr_left + qr_size + 5, qr_top + qr_size + 5),
            outline=(35, 35, 35),
            width=3,
        )
        draw.text((qr_left, qr_top + qr_size + 12), "Open add-on page", font=FONT_SMALL, fill=(60, 60, 60))
    except Exception as exc:  # noqa: BLE001
        log(f"Unable to render QR code: {exc}")


def collect_printer_context_lines(printer: PrinterConfig, limit: int = 6) -> list[str]:
    if not SUPERVISOR_TOKEN and not SELECTED_HA_URL:
        return []

    states = fetch_all_states()
    if not states:
        return []

    indexed = states_by_entity(states)
    lines: list[str] = []
    seen: set[str] = set()

    for entity_id in printer.entity_ids:
        if entity_id in seen:
            continue
        payload = indexed.get(entity_id)
        lines.append(format_entity_line(payload) if payload else f"{entity_id}: unavailable")
        seen.add(entity_id)
        if len(lines) >= limit:
            return lines

    search_tokens: set[str] = set()
    printer_name_slug = slugify(printer.name)
    search_tokens.add(printer.printer_id.lower())
    search_tokens.add(printer_name_slug.lower())
    for token in re.split(r"[^a-zA-Z0-9]+", printer.name.lower()):
        if len(token) >= 3:
            search_tokens.add(token)

    for state in states:
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or entity_id in seen:
            continue
        attrs = state.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}
        friendly = str(attrs.get("friendly_name", ""))
        haystack = f"{entity_id} {friendly}".lower()
        if any(token and token in haystack for token in search_tokens):
            lines.append(format_entity_line(state))
            seen.add(entity_id)
            if len(lines) >= limit:
                break

    return lines


def draw_print_context(
    draw: ImageDraw.ImageDraw,
    y: int,
    print_context: dict[str, Any],
) -> int:
    reason = str(print_context.get("reason", "")).strip()
    trigger = str(print_context.get("trigger", "")).strip()
    source = str(print_context.get("source", "")).strip()
    cadence_hours = print_context.get("cadence_hours")
    last_print_at = str(print_context.get("last_print_at", "")).strip()
    next_due_at = str(print_context.get("next_due_at", "")).strip()

    draw.text((120, y), "Keepalive Context", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 10

    context_lines = []
    if trigger:
        context_lines.append(f"Trigger: {trigger}")
    if source:
        context_lines.append(f"Source: {source}")
    if cadence_hours is not None:
        context_lines.append(f"Cadence: {cadence_hours}h")
    if reason:
        context_lines.append(f"Reason: {reason}")
    if last_print_at:
        context_lines.append(f"Last print seen: {_format_time_for_page(last_print_at)}")
    if next_due_at:
        context_lines.append(f"Next due at: {_format_time_for_page(next_due_at)}")

    for line in context_lines:
        y = draw_wrapped_text(draw, 120, y, line, FONT_BODY, (35, 35, 35), PAGE_WIDTH - 240)
        y += 6

    printer_signals = print_context.get("printer_signals")
    if isinstance(printer_signals, list) and printer_signals:
        y += 8
        draw.text((120, y), "Home Assistant Printer Signals", font=FONT_SECTION, fill=(25, 25, 25))
        y += line_height(FONT_SECTION) + 10
        for raw_line in printer_signals[:6]:
            text = str(raw_line).strip()
            if not text:
                continue
            y = draw_wrapped_text(draw, 120, y, text, FONT_BODY, (35, 35, 35), PAGE_WIDTH - 240)
            y += 6
            if y > PAGE_HEIGHT - 280:
                break

    return y + 12


def build_base_page(
    printer: PrinterConfig,
    template_name: str,
    print_context: dict[str, Any] | None = None,
) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    draw.text((120, 70), printer.title or APP_NAME, font=FONT_TITLE, fill=(20, 20, 20))
    draw.text((120, 160), f"Printer: {printer.name}", font=FONT_SMALL, fill=(70, 70, 70))
    draw.text((120, 195), f"Template: {template_name}", font=FONT_SMALL, fill=(70, 70, 70))
    draw.text((120, 230), f"Generated: {datetime.now().isoformat(timespec='seconds')}", font=FONT_SMALL, fill=(70, 70, 70))
    draw.text((120, 265), f"Add-on: {ADDON_SLUG}", font=FONT_SMALL, fill=(70, 70, 70))

    if print_context and isinstance(print_context, dict):
        qr_url = _sanitize_qr_url(str(print_context.get("addon_page_url", "")))
        _draw_qr_code(image, draw, qr_url)

    swatches = [
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
        (0, 0, 0),
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 255),
    ]
    bar_top = 300
    bar_height = 170
    bar_width = (PAGE_WIDTH - 240) // len(swatches)
    for index, color in enumerate(swatches):
        left = 120 + index * bar_width
        right = left + bar_width
        draw.rectangle((left, bar_top, right, bar_top + bar_height), fill=color)

    gradient_top = bar_top + bar_height + 20
    gradient_height = 160
    for x in range(PAGE_WIDTH - 240):
        ratio = x / max(1, PAGE_WIDTH - 241)
        r = int(255 * ratio)
        g = int(255 * (1.0 - ratio))
        b = int(127 + 128 * (0.5 - abs(ratio - 0.5)))
        draw.line(
            [(120 + x, gradient_top), (120 + x, gradient_top + gradient_height)],
            fill=(r, g, b),
            width=1,
        )

    line_top = gradient_top + gradient_height + 16
    for y in range(line_top, line_top + 180, 4):
        draw.line([(120, y), (PAGE_WIDTH - 120, y)], fill=(0, 0, 0), width=1)
    for x in range(120, PAGE_WIDTH - 120, 6):
        draw.line([(x, line_top), (x, line_top + 180)], fill=(80, 80, 80), width=1)

    draw.line([(120, line_top + 210), (PAGE_WIDTH - 120, line_top + 210)], fill=(190, 190, 190), width=3)

    y = line_top + 250
    if print_context and isinstance(print_context, dict):
        y = draw_print_context(draw, y, print_context)
    return image, draw, y


def draw_footer(draw: ImageDraw.ImageDraw, footer: str) -> None:
    draw.text((120, PAGE_HEIGHT - 110), footer, font=FONT_SMALL, fill=(70, 70, 70))


def get_printer_entity_ids(printer: PrinterConfig, states: list[dict[str, Any]], limit: int) -> list[str]:
    if printer.entity_ids:
        return printer.entity_ids[:limit]
    return choose_default_entities(states, limit=limit)


def build_color_bars_page(printer: PrinterConfig, print_context: dict[str, Any] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    image, draw, y = build_base_page(printer, "color_bars", print_context=print_context)
    draw.text((120, y), "Nozzles are being exercised with color and fine-line patterns.", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 18
    y = draw_wrapped_text(
        draw,
        120,
        y,
        "This maintenance page includes CMYK swatches, gradients, and fine line patterns to keep print systems active.",
        FONT_BODY,
        (45, 45, 45),
        PAGE_WIDTH - 240,
    )
    y += 20
    draw.text((120, y), "Use Home Assistant-aware templates for environment-specific data blocks.", font=FONT_BODY, fill=(45, 45, 45))
    draw_footer(draw, printer.footer)
    return image, {"template": "color_bars"}


def build_entity_report_page(printer: PrinterConfig, print_context: dict[str, Any] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    image, draw, y = build_base_page(printer, "entity_report", print_context=print_context)
    draw.text((120, y), "Home Assistant Entity Report", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 24

    states = fetch_all_states()
    indexed = states_by_entity(states)
    entity_ids = get_printer_entity_ids(printer, states, limit=12)

    rendered = 0
    for entity_id in entity_ids:
        state = indexed.get(entity_id)
        line = f"{entity_id}: unavailable"
        if state:
            line = format_entity_line(state)
        draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
        y += line_height(FONT_BODY) + 12
        rendered += 1
        if y > PAGE_HEIGHT - 220:
            break

    if rendered == 0:
        y = draw_wrapped_text(
            draw,
            120,
            y,
            "No entities configured. Add entity IDs in printer configuration to render instance-specific status rows.",
            FONT_BODY,
            (70, 70, 70),
            PAGE_WIDTH - 240,
        )

    draw_footer(draw, printer.footer)
    return image, {"template": "entity_report", "entities_rendered": rendered}


def build_weather_snapshot_page(printer: PrinterConfig, print_context: dict[str, Any] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    image, draw, y = build_base_page(printer, "weather_snapshot", print_context=print_context)
    draw.text((120, y), "Weather Snapshot", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 24

    states = fetch_all_states()
    indexed = states_by_entity(states)
    weather_entity = detect_weather_entity(printer, states)
    payload = indexed.get(weather_entity)

    weather_rendered = False
    if payload:
        weather_rendered = True
        attrs = payload.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}
        details = [
            f"Entity: {weather_entity}",
            f"Condition: {payload.get('state', 'unknown')}",
            f"Temperature: {attrs.get('temperature', 'n/a')} {attrs.get('temperature_unit', '')}".strip(),
            f"Humidity: {attrs.get('humidity', 'n/a')}%",
            f"Wind Speed: {attrs.get('wind_speed', 'n/a')}",
            f"Pressure: {attrs.get('pressure', 'n/a')}",
        ]
        for line in details:
            draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
            y += line_height(FONT_BODY) + 12
    else:
        y = draw_wrapped_text(
            draw,
            120,
            y,
            "Weather entity unavailable. Configure weather_entity or install a weather integration.",
            FONT_BODY,
            (70, 70, 70),
            PAGE_WIDTH - 240,
        )

    entity_ids = get_printer_entity_ids(printer, states, limit=6)
    if entity_ids:
        y += 18
        draw.text((120, y), "Tracked Entities", font=FONT_SECTION, fill=(25, 25, 25))
        y += line_height(FONT_SECTION) + 16
        for entity_id in entity_ids:
            state = indexed.get(entity_id)
            line = f"{entity_id}: unavailable"
            if state:
                line = format_entity_line(state)
            draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
            y += line_height(FONT_BODY) + 10
            if y > PAGE_HEIGHT - 220:
                break

    draw_footer(draw, printer.footer)
    return image, {
        "template": "weather_snapshot",
        "weather_entity": weather_entity,
        "weather_rendered": weather_rendered,
    }


def build_home_summary_page(printer: PrinterConfig, print_context: dict[str, Any] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    image, draw, y = build_base_page(printer, "home_summary", print_context=print_context)
    draw.text((120, y), "Home Assistant Summary", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 20

    states = fetch_all_states()
    indexed = states_by_entity(states)
    total_entities = len(states)
    unavailable = sum(1 for state in states if str(state.get("state")) in {"unknown", "unavailable"})
    active_binary = sum(
        1
        for state in states
        if str(state.get("entity_id", "")).startswith("binary_sensor.") and str(state.get("state")) == "on"
    )

    summary_lines = [
        f"Total entities: {total_entities}",
        f"Unavailable/unknown entities: {unavailable}",
        f"Active binary sensors: {active_binary}",
    ]
    for line in summary_lines:
        draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
        y += line_height(FONT_BODY) + 10

    entity_ids = get_printer_entity_ids(printer, states, limit=8)
    y += 24
    draw.text((120, y), "Key Entity States", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 16

    rendered_entities = 0
    for entity_id in entity_ids:
        payload = indexed.get(entity_id)
        line = f"{entity_id}: unavailable"
        if payload:
            line = format_entity_line(payload)
        draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
        y += line_height(FONT_BODY) + 10
        rendered_entities += 1
        if y > PAGE_HEIGHT - 220:
            break

    draw_footer(draw, printer.footer)
    return image, {
        "template": "home_summary",
        "entities_rendered": rendered_entities,
        "total_entities": total_entities,
    }


def build_hybrid_page(printer: PrinterConfig, print_context: dict[str, Any] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    image, draw, y = build_base_page(printer, "hybrid", print_context=print_context)
    draw.text((120, y), "Hybrid Summary (Weather + Entities)", font=FONT_SECTION, fill=(25, 25, 25))
    y += line_height(FONT_SECTION) + 20

    states = fetch_all_states()
    indexed = states_by_entity(states)
    weather_entity = detect_weather_entity(printer, states)
    weather = indexed.get(weather_entity)

    if weather:
        attrs = weather.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}
        weather_lines = [
            f"Weather ({weather_entity}): {weather.get('state', 'unknown')}",
            f"Temperature: {attrs.get('temperature', 'n/a')} {attrs.get('temperature_unit', '')}".strip(),
            f"Humidity: {attrs.get('humidity', 'n/a')}%",
        ]
        for line in weather_lines:
            draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
            y += line_height(FONT_BODY) + 10
    else:
        draw.text((120, y), "Weather entity unavailable.", font=FONT_BODY, fill=(90, 90, 90))
        y += line_height(FONT_BODY) + 10

    y += 20
    entity_ids = get_printer_entity_ids(printer, states, limit=6)
    for entity_id in entity_ids:
        payload = indexed.get(entity_id)
        line = f"{entity_id}: unavailable"
        if payload:
            line = format_entity_line(payload)
        draw.text((120, y), line, font=FONT_BODY, fill=(35, 35, 35))
        y += line_height(FONT_BODY) + 10
        if y > PAGE_HEIGHT - 220:
            break

    draw_footer(draw, printer.footer)
    return image, {"template": "hybrid", "weather_entity": weather_entity}


TEMPLATE_BUILDERS = {
    "color_bars": build_color_bars_page,
    "entity_report": build_entity_report_page,
    "weather_snapshot": build_weather_snapshot_page,
    "home_summary": build_home_summary_page,
    "hybrid": build_hybrid_page,
}


def generate_template_image(
    printer: PrinterConfig,
    template_name: str,
    print_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    builder = TEMPLATE_BUILDERS.get(template_name, build_color_bars_page)
    image, metadata = builder(printer, print_context=print_context)
    if print_context:
        metadata["print_context"] = {
            "trigger": str(print_context.get("trigger", "")),
            "reason": str(print_context.get("reason", "")),
            "addon_page_url": str(print_context.get("addon_page_url", "")),
        }
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as handle:
        image.save(handle.name, format="JPEG", quality=95, optimize=True)
        return handle.name, metadata


def submit_print_job(printer_uri: str, file_path: str) -> tuple[bool, str]:
    command = [
        "ipptool",
        "-q",
        "-d",
        "filetype=image/jpeg",
        "-f",
        file_path,
        printer_uri,
        PRINT_JOB_TEST,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        check=False,
    )
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode == 0:
        return True, output or "Print job submitted."
    return False, output or f"ipptool returned {result.returncode}"


_ATTR_RE = re.compile(r"^\s*([a-zA-Z0-9\-]+)\s+\([^)]*\)\s+=\s*(.*)$")


def _parse_ipp_scalar(value: str) -> Any:
    stripped = value.strip().strip('"')
    if stripped.lower() in {"true", "false"}:
        return stripped.lower() == "true"
    if re.fullmatch(r"-?\d+", stripped):
        try:
            return int(stripped)
        except ValueError:
            return stripped
    return stripped


def _parse_ipp_value(value: str) -> Any:
    if "," in value:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return [_parse_ipp_scalar(part) for part in parts]
    return _parse_ipp_scalar(value)


def query_ipp_attributes(printer_uri: str, timeout_seconds: int = IPP_QUERY_TIMEOUT_SECONDS) -> tuple[dict[str, Any], str | None]:
    timeout = max(1, timeout_seconds)
    command = [
        "ipptool",
        "-t",
        "-v",
        "-T",
        str(timeout),
        printer_uri,
        GET_ATTRS_TEST,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, f"IPP query exception: {exc}"

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        return {}, output.strip() or f"ipptool returned {result.returncode}"

    attrs: dict[str, Any] = {}
    for line in output.splitlines():
        match = _ATTR_RE.match(line)
        if not match:
            continue
        key = match.group(1).strip()
        raw_value = match.group(2).strip()
        attrs[key] = _parse_ipp_value(raw_value)

    return attrs, None


def normalize_state_name(raw: Any) -> str:
    if isinstance(raw, int):
        return IPP_STATE_MAP.get(raw, str(raw))
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if re.fullmatch(r"\d+", cleaned):
            return IPP_STATE_MAP.get(int(cleaned), cleaned)
        return cleaned
    return "unknown"


def normalize_reason_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def to_int_list(raw: Any) -> list[int]:
    if isinstance(raw, list):
        result: list[int] = []
        for item in raw:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result
    if isinstance(raw, int):
        return [raw]
    return []


def to_int_or_none(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if re.fullmatch(r"-?\d+", stripped):
            return int(stripped)
    return None


def to_str_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def infer_printer_type_from_text(*values: str) -> str:
    combined = " ".join(value.lower() for value in values if value).strip()
    if not combined:
        return "inkjet"

    laser_keywords = (
        "laser",
        "toner",
        "imageclass",
        "ecosys",
        "lbp",
        "hl-l",
        "phaser",
    )
    inkjet_keywords = (
        "ink",
        "ecotank",
        "officejet",
        "deskjet",
        "pixma",
        "et-",
        "wf-",
    )

    if any(keyword in combined for keyword in laser_keywords):
        return "laser"
    if any(keyword in combined for keyword in inkjet_keywords):
        return "inkjet"
    return "inkjet"


def _normalize_txt_properties(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, bytes):
            key_decoded = key.decode("utf-8", errors="ignore")
        else:
            key_decoded = str(key)

        if isinstance(value, bytes):
            value_decoded = value.decode("utf-8", errors="ignore")
        else:
            value_decoded = str(value)
        normalized[key_decoded.strip()] = value_decoded.strip()
    return normalized


def _service_label(service_name: str, properties: dict[str, str]) -> str:
    ty = properties.get("ty", "").strip()
    if ty:
        return ty
    name = service_name.split("._", 1)[0].strip()
    return name or "Discovered Printer"


def _format_host_for_uri(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _resource_path(raw: str) -> str:
    candidate = raw.strip().strip("/")
    if not candidate:
        return "/ipp/print"
    parts = [quote(part, safe="") for part in candidate.split("/") if part]
    if not parts:
        return "/ipp/print"
    return "/" + "/".join(parts)


def _parse_discovery_force_flag(query: dict[str, list[str]]) -> bool:
    values = query.get("force")
    if not isinstance(values, list) or not values:
        return False
    return str(values[0]).strip().lower() in {"1", "true", "yes", "on"}


class IppServiceDiscoveryListener(ServiceListener):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[str, dict[str, Any]] = {}

    def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self._record(zeroconf, service_type, name)

    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self._record(zeroconf, service_type, name)

    def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        key = f"{service_type}|{name}"
        with self._lock:
            self._services.pop(key, None)

    def _record(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name, timeout=3000)
        if not info:
            return

        addresses = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
        server = getattr(info, "server", "") or ""
        properties = _normalize_txt_properties(getattr(info, "properties", {}))
        key = f"{service_type}|{name}"

        with self._lock:
            self._services[key] = {
                "service_type": service_type,
                "service_name": name,
                "server": server.rstrip("."),
                "addresses": addresses,
                "port": int(getattr(info, "port", 631) or 631),
                "properties": properties,
            }

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(item)) for item in self._services.values()]


def _run_discovery_scan() -> tuple[list[dict[str, Any]], str, float]:
    if not DISCOVERY_ENABLED:
        return [], "", 0.0

    start = time.monotonic()
    service_types = ["_ipp._tcp.local."]
    if DISCOVERY_INCLUDE_IPPS:
        service_types.append("_ipps._tcp.local.")

    configured_uris = {printer.printer_uri.strip() for printer in PRINTERS if printer.printer_uri.strip()}
    configured_hosts: set[str] = set()
    for uri in configured_uris:
        parsed = urlparse(uri)
        if parsed.hostname:
            configured_hosts.add(parsed.hostname.lower())

    listener = IppServiceDiscoveryListener()
    browsers: list[ServiceBrowser] = []
    zeroconf: Zeroconf | None = None

    try:
        zeroconf = Zeroconf()
        for service_type in service_types:
            browsers.append(ServiceBrowser(zeroconf, service_type, listener))
        time.sleep(DISCOVERY_TIMEOUT_SECONDS)
        raw_services = listener.snapshot()
    except Exception as exc:  # noqa: BLE001
        duration = round(time.monotonic() - start, 3)
        return [], f"Discovery scan failed: {exc}", duration
    finally:
        for browser in browsers:
            try:
                browser.cancel()
            except Exception:  # noqa: BLE001
                pass
        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception:  # noqa: BLE001
                pass

    discovered: list[dict[str, Any]] = []
    seen_uris: set[str] = set()
    used_ids: set[str] = set()

    for service in sorted(raw_services, key=lambda item: (str(item.get("service_name", "")), str(item.get("service_type", "")))):
        properties = service.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}

        addresses = service.get("addresses", [])
        if not isinstance(addresses, list):
            addresses = []
        addresses = [str(address).strip() for address in addresses if str(address).strip()]

        host = addresses[0] if addresses else str(service.get("server", "")).strip()
        if not host:
            continue

        port_raw = service.get("port", 631)
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            port = 631
        port = max(1, min(65535, port))

        service_type = str(service.get("service_type", "_ipp._tcp.local."))
        secure = service_type.startswith("_ipps.")
        scheme = "ipps" if secure else "ipp"

        rp = str(properties.get("rp", ""))
        uri = f"{scheme}://{_format_host_for_uri(host)}:{port}{_resource_path(rp)}"
        if uri in seen_uris:
            continue
        seen_uris.add(uri)

        label = _service_label(str(service.get("service_name", "")), properties)
        attrs, error = query_ipp_attributes(uri, timeout_seconds=DISCOVERY_IPP_QUERY_TIMEOUT_SECONDS)
        printer_name = str(attrs.get("printer-name", "")).strip() or label
        model = str(attrs.get("printer-make-and-model", "")).strip() or str(properties.get("ty", "")).strip()
        printer_type_guess = infer_printer_type_from_text(printer_name, model)

        printer_id = slugify(printer_name)
        if printer_id in used_ids:
            suffix = 2
            while f"{printer_id}_{suffix}" in used_ids:
                suffix += 1
            printer_id = f"{printer_id}_{suffix}"
        used_ids.add(printer_id)

        parsed_uri = urlparse(uri)
        host_match = parsed_uri.hostname.lower() if parsed_uri.hostname else ""
        already_configured = uri in configured_uris or (host_match in configured_hosts if host_match else False)

        discovered.append(
            {
                "service_name": str(service.get("service_name", "")),
                "service_type": service_type,
                "secure": secure,
                "host": host,
                "addresses": addresses,
                "port": port,
                "uri": uri,
                "reachable": not bool(error),
                "error": error or "",
                "printer_name": printer_name,
                "printer_make_and_model": model,
                "printer_state": normalize_state_name(attrs.get("printer-state", "unknown")),
                "printer_type_guess": printer_type_guess,
                "already_configured": already_configured,
                "suggested_config": {
                    "id": printer_id,
                    "name": printer_name,
                    "printer_uri": uri,
                    "printer_type": printer_type_guess,
                    "enabled": True,
                    "cadence_hours": default_cadence_for_type(printer_type_guess),
                    "template": "home_summary",
                    "entity_ids": [],
                },
            }
        )

    duration = round(time.monotonic() - start, 3)
    return discovered, "", duration


def discovery_snapshot() -> dict[str, Any]:
    with DISCOVERY_LOCK:
        payload = json.loads(json.dumps(DISCOVERY_STATE))

    last_error = str(payload.get("last_error", ""))
    discovered = payload.get("printers", [])
    if not isinstance(discovered, list):
        discovered = []

    return {
        "enabled": DISCOVERY_ENABLED,
        "interval_seconds": DISCOVERY_INTERVAL_SECONDS,
        "timeout_seconds": DISCOVERY_TIMEOUT_SECONDS,
        "ipp_query_timeout_seconds": DISCOVERY_IPP_QUERY_TIMEOUT_SECONDS,
        "include_ipps": DISCOVERY_INCLUDE_IPPS,
        "last_scan_at": str(payload.get("last_scan_at", "")),
        "last_scan_duration_seconds": payload.get("last_scan_duration_seconds", 0.0),
        "last_error": last_error,
        "printer_count": len(discovered),
        "printers": discovered,
        "ok": not bool(last_error),
    }


def get_discovery_payload(force: bool = False) -> dict[str, Any]:
    if not DISCOVERY_ENABLED:
        snapshot = discovery_snapshot()
        snapshot["ok"] = True
        snapshot["printers"] = []
        snapshot["printer_count"] = 0
        snapshot["last_error"] = ""
        snapshot["message"] = "Printer discovery is disabled."
        return snapshot

    now = utc_now()
    with DISCOVERY_LOCK:
        last_scan_at = parse_iso(DISCOVERY_STATE.get("last_scan_at"))
        recent = bool(last_scan_at and (now - last_scan_at).total_seconds() < DISCOVERY_INTERVAL_SECONDS)
        if not force and recent:
            return discovery_snapshot()

    discovered, error, duration = _run_discovery_scan()
    with DISCOVERY_LOCK:
        DISCOVERY_STATE["last_scan_at"] = iso_utc(now)
        DISCOVERY_STATE["last_scan_duration_seconds"] = duration
        DISCOVERY_STATE["last_error"] = error
        DISCOVERY_STATE["printers"] = discovered

    return discovery_snapshot()


def effective_template(printer: PrinterConfig, state: dict[str, Any]) -> str:
    override = str(state.get("template_override") or "").strip().lower()
    if override in SUPPORTED_TEMPLATES:
        return override
    if printer.template in SUPPORTED_TEMPLATES:
        return printer.template
    return "home_summary"


def effective_cadence_hours(printer: PrinterConfig, state: dict[str, Any]) -> int:
    override = state.get("cadence_hours_override")
    if isinstance(override, int):
        return max(1, min(720, override))
    if isinstance(override, str) and override.strip().isdigit():
        return max(1, min(720, int(override.strip())))
    return printer.cadence_hours


def effective_enabled(printer: PrinterConfig, state: dict[str, Any]) -> bool:
    override = state.get("enabled_override")
    if isinstance(override, bool):
        return override
    if isinstance(override, str):
        return override.strip().lower() in {"1", "true", "yes", "on"}
    return printer.enabled


def compute_activity_times(state: dict[str, Any]) -> tuple[datetime | None, datetime | None, datetime | None]:
    keepalive_time = parse_iso(state.get("last_keepalive_at"))
    external_time = parse_iso(state.get("last_external_print_at"))
    history_anchor = parse_iso(state.get("history_anchor_at"))
    return keepalive_time, external_time, history_anchor


def compute_last_print_time(state: dict[str, Any]) -> datetime | None:
    keepalive_time, external_time, history_anchor = compute_activity_times(state)
    candidates = [stamp for stamp in (keepalive_time, external_time, history_anchor) if stamp is not None]
    if not candidates:
        return None
    return max(candidates)


def marker_supplies(state: dict[str, Any]) -> list[dict[str, Any]]:
    levels = to_int_list(state.get("marker_levels"))
    names = to_str_list(state.get("marker_names"))
    colors = to_str_list(state.get("marker_colors"))

    count = max(len(levels), len(names), len(colors))
    supplies: list[dict[str, Any]] = []
    for index in range(count):
        supplies.append(
            {
                "index": index,
                "name": names[index] if index < len(names) else f"Supply {index + 1}",
                "level": levels[index] if index < len(levels) else None,
                "color": colors[index] if index < len(colors) else "",
            }
        )
    return supplies


def lowest_marker_level(state: dict[str, Any]) -> int | None:
    levels = to_int_list(state.get("marker_levels"))
    if not levels:
        return None
    return min(levels)


def evaluate_health(state: dict[str, Any]) -> tuple[str, str]:
    printer_state = normalize_state_name(state.get("printer_state"))
    reasons = normalize_reason_list(state.get("printer_state_reasons"))

    if printer_state in {"stopped", "5"}:
        return "critical", "Printer state is stopped."
    if any("error" in reason for reason in reasons):
        return "critical", f"Printer reported error reason(s): {', '.join(reasons)}"

    levels = to_int_list(state.get("marker_levels"))
    if levels and min(levels) <= 10:
        return "warning", "One or more consumables are low."

    if printer_state in {"processing", "4"}:
        return "busy", "Printer is currently processing jobs."

    if reasons and reasons != ["none"]:
        return "warning", f"Printer reason(s): {', '.join(reasons)}"

    return "healthy", "Printer appears healthy."


def compute_need_for_keepalive(printer: PrinterConfig, state: dict[str, Any], now: datetime) -> tuple[bool, datetime | None, datetime | None]:
    if not effective_enabled(printer, state):
        return False, None, None

    cadence = effective_cadence_hours(printer, state)
    last_print_time = compute_last_print_time(state)
    if last_print_time is None:
        return False, None, None

    due_at = last_print_time + timedelta(hours=cadence)
    return now >= due_at, last_print_time, due_at


def print_trigger_label(source: str) -> str:
    normalized = source.strip().lower()
    if normalized == "scheduler":
        return "Automatic scheduler"
    if normalized == "mqtt":
        return "Home Assistant MQTT command"
    if normalized == "api":
        return "API request"
    return normalized or "unknown"


def describe_print_reason(
    source: str,
    only_if_needed: bool,
    cadence_hours: int,
    now: datetime,
    last_print_time: datetime | None,
    due_at: datetime | None,
) -> str:
    if only_if_needed:
        if last_print_time is None:
            return "No previous print history; keepalive requested."
        elapsed_hours = (now - last_print_time).total_seconds() / 3600.0
        if due_at is not None and now > due_at:
            overdue_hours = (now - due_at).total_seconds() / 3600.0
            return (
                f"Keepalive was overdue by {overdue_hours:.1f}h "
                f"({elapsed_hours:.1f}h since last print, cadence {cadence_hours}h)."
            )
        return f"Keepalive was due by cadence ({elapsed_hours:.1f}h since last print, cadence {cadence_hours}h)."

    if source.strip().lower() == "mqtt":
        return "Manual keepalive requested from Home Assistant MQTT control."
    if source.strip().lower() == "api":
        return "Manual keepalive requested from API with force enabled."
    return "Manual keepalive requested."


def build_print_context(
    printer: PrinterConfig,
    source: str,
    only_if_needed: bool,
    cadence_hours: int,
    now: datetime,
    last_print_time: datetime | None,
    due_at: datetime | None,
) -> dict[str, Any]:
    return {
        "source": source,
        "trigger": print_trigger_label(source),
        "reason": describe_print_reason(source, only_if_needed, cadence_hours, now, last_print_time, due_at),
        "cadence_hours": cadence_hours,
        "printed_at": iso_utc(now),
        "last_print_at": iso_utc(last_print_time) if last_print_time else "",
        "next_due_at": iso_utc(due_at) if due_at else "",
        "addon_page_url": ADDON_PAGE_URL,
        "printer_signals": collect_printer_context_lines(printer),
    }


def build_printer_payload(printer: PrinterConfig, now: datetime | None = None) -> dict[str, Any]:
    current = now or utc_now()
    with STATE_LOCK:
        state = dict(ensure_printer_state_locked(printer.printer_id))

    keepalive_needed, last_print_time, due_at = compute_need_for_keepalive(printer, state, current)

    elapsed_hours: float | None = None
    if last_print_time:
        elapsed_hours = round((current - last_print_time).total_seconds() / 3600.0, 2)

    guidance = MAINTENANCE_GUIDANCE.get(printer.printer_type, MAINTENANCE_GUIDANCE["inkjet"])
    health_status, health_summary = evaluate_health(state)

    payload = {
        "printer_id": printer.printer_id,
        "name": printer.name,
        "printer_uri": printer.printer_uri,
        "printer_type": printer.printer_type,
        "enabled": effective_enabled(printer, state),
        "template": effective_template(printer, state),
        "cadence_hours": effective_cadence_hours(printer, state),
        "keepalive_needed": keepalive_needed,
        "last_print_at": iso_utc(last_print_time) if last_print_time else "",
        "time_since_last_print_hours": elapsed_hours,
        "next_keepalive_due_at": iso_utc(due_at) if due_at else "",
        "keepalive_print_count": int(state.get("keepalive_print_count", 0)),
        "last_keepalive_at": str(state.get("last_keepalive_at", "")),
        "last_keepalive_result": str(state.get("last_keepalive_result", "never")),
        "last_keepalive_error": str(state.get("last_keepalive_error", "")),
        "last_external_print_at": str(state.get("last_external_print_at", "")),
        "external_print_count": int(state.get("external_print_count", 0)),
        "last_polled_at": str(state.get("last_polled_at", "")),
        "job_impressions_completed": state.get("job_impressions_completed"),
        "queued_job_count": state.get("queued_job_count"),
        "printer_state": normalize_state_name(state.get("printer_state")),
        "printer_state_reasons": normalize_reason_list(state.get("printer_state_reasons")),
        "printer_state_message": str(state.get("printer_state_message", "")),
        "printer_is_accepting_jobs": state.get("printer_is_accepting_jobs"),
        "marker_levels": to_int_list(state.get("marker_levels")),
        "marker_names": to_str_list(state.get("marker_names")),
        "marker_colors": to_str_list(state.get("marker_colors")),
        "lowest_marker_level": lowest_marker_level(state),
        "marker_supplies": marker_supplies(state),
        "printer_make_and_model": str(state.get("printer_make_and_model", "")),
        "printer_name_from_ipp": str(state.get("printer_name", "")),
        "printer_uuid": str(state.get("printer_uuid", "")),
        "printer_up_time_seconds": state.get("printer_up_time_seconds"),
        "media_sheets_completed": state.get("media_sheets_completed"),
        "health_status": health_status,
        "health_summary": health_summary,
        "guidance": {
            "summary": guidance.get("summary", ""),
            "default_cadence_hours": guidance.get("default_cadence_hours"),
            "recommended_range_days": guidance.get("recommended_range_days", ""),
            "research_notes": guidance.get("research_notes", []),
            "sources": guidance.get("sources", []),
        },
    }
    return payload


def poll_printer(printer: PrinterConfig, force: bool = False) -> dict[str, Any]:
    now = utc_now()

    with STATE_LOCK:
        state = ensure_printer_state_locked(printer.printer_id)
        last_polled = parse_iso(state.get("last_polled_at"))
        if not force and last_polled and (now - last_polled).total_seconds() < STATUS_POLL_INTERVAL_SECONDS:
            return build_printer_payload(printer, now)

    attrs, error = query_ipp_attributes(printer.printer_uri)

    with STATE_LOCK:
        state = ensure_printer_state_locked(printer.printer_id)
        state["last_polled_at"] = iso_utc(now)

        if error:
            state["last_error"] = error
            save_state_locked()
            log(f"IPP poll failed for {printer.name}: {error}")
            return build_printer_payload(printer, now)

        state["last_error"] = ""
        state["printer_state"] = normalize_state_name(attrs.get("printer-state", state.get("printer_state")))
        state["printer_state_reasons"] = normalize_reason_list(attrs.get("printer-state-reasons", state.get("printer_state_reasons")))
        queued_jobs = to_int_or_none(attrs.get("queued-job-count"))
        if queued_jobs is not None:
            state["queued_job_count"] = queued_jobs
        state["printer_is_accepting_jobs"] = attrs.get("printer-is-accepting-jobs", state.get("printer_is_accepting_jobs"))
        state["printer_state_message"] = str(attrs.get("printer-state-message", state.get("printer_state_message", "")))
        state["printer_make_and_model"] = str(attrs.get("printer-make-and-model", state.get("printer_make_and_model", "")))
        state["printer_name"] = str(attrs.get("printer-name", state.get("printer_name", "")))
        state["printer_uuid"] = str(attrs.get("printer-uuid", state.get("printer_uuid", "")))
        state["marker_levels"] = to_int_list(attrs.get("marker-levels", state.get("marker_levels", [])))
        state["marker_names"] = to_str_list(attrs.get("marker-names", state.get("marker_names", [])))
        state["marker_colors"] = to_str_list(attrs.get("marker-colors", state.get("marker_colors", [])))
        printer_uptime = to_int_or_none(attrs.get("printer-up-time"))
        if printer_uptime is not None:
            state["printer_up_time_seconds"] = printer_uptime
        media_sheets = to_int_or_none(attrs.get("media-sheets-completed"))
        if media_sheets is not None:
            state["media_sheets_completed"] = media_sheets

        raw_impressions = attrs.get("job-impressions-completed")
        impressions: int | None = None
        if isinstance(raw_impressions, int):
            impressions = raw_impressions
        elif isinstance(raw_impressions, str) and raw_impressions.strip().isdigit():
            impressions = int(raw_impressions.strip())

        previous_impressions = state.get("last_seen_job_impressions")
        if impressions is not None:
            state["job_impressions_completed"] = impressions
            if isinstance(previous_impressions, int):
                if impressions > previous_impressions:
                    state["last_external_print_at"] = iso_utc(now)
                    delta = impressions - previous_impressions
                    state["external_print_count"] = int(state.get("external_print_count", 0)) + delta
            state["last_seen_job_impressions"] = impressions

        save_state_locked()

    return build_printer_payload(printer, now)


def run_keepalive_print(
    printer: PrinterConfig,
    template_override: str | None = None,
    source: str = "api",
    only_if_needed: bool = False,
) -> dict[str, Any]:
    now = utc_now()
    cadence_hours = printer.cadence_hours
    last_print_time: datetime | None = None
    due_at: datetime | None = None

    with STATE_LOCK:
        state = ensure_printer_state_locked(printer.printer_id)
        cadence_hours = effective_cadence_hours(printer, state)
        if only_if_needed:
            needed, last_print_time, due_at = compute_need_for_keepalive(printer, state, now)
            if not needed:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "Keepalive not due based on print history.",
                    "next_keepalive_due_at": iso_utc(due_at) if due_at else "",
                    "printer": build_printer_payload(printer, now),
                }

        if not effective_enabled(printer, state):
            return {
                "ok": False,
                "skipped": True,
                "error": "Printer keepalive is disabled for this printer.",
                "printer": build_printer_payload(printer, now),
            }

        printer_state = normalize_state_name(state.get("printer_state"))
        if printer_state == "processing" and only_if_needed:
            return {
                "ok": True,
                "skipped": True,
                "reason": "Printer is currently processing another job.",
                "printer": build_printer_payload(printer, now),
            }

        template = (template_override or effective_template(printer, state)).strip().lower()
        if template not in SUPPORTED_TEMPLATES:
            template = effective_template(printer, state)

        if only_if_needed and str(state.get("last_keepalive_result")) == "failed":
            last_attempt = parse_iso(state.get("last_keepalive_attempt_at"))
            if last_attempt and (now - last_attempt) < timedelta(minutes=FAILURE_RETRY_MINUTES):
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": f"Previous failure cooldown active ({FAILURE_RETRY_MINUTES} minutes).",
                    "printer": build_printer_payload(printer, now),
                }
        else:
            _, last_print_time, due_at = compute_need_for_keepalive(printer, state, now)

    print_context = build_print_context(
        printer=printer,
        source=source,
        only_if_needed=only_if_needed,
        cadence_hours=cadence_hours,
        now=now,
        last_print_time=last_print_time,
        due_at=due_at,
    )

    with PRINT_LOCK:
        image_path = ""
        metadata: dict[str, Any] = {}
        try:
            image_path, metadata = generate_template_image(printer, template, print_context=print_context)
            ok, details = submit_print_job(printer.printer_uri, image_path)
        except Exception as exc:  # noqa: BLE001
            ok = False
            details = str(exc)
        finally:
            if image_path and Path(image_path).exists():
                try:
                    Path(image_path).unlink()
                except OSError:
                    pass

    with STATE_LOCK:
        state = ensure_printer_state_locked(printer.printer_id)
        state["last_keepalive_attempt_at"] = iso_utc(now)
        if ok:
            state["last_keepalive_at"] = iso_utc(now)
            state["keepalive_print_count"] = int(state.get("keepalive_print_count", 0)) + 1
            state["last_keepalive_result"] = "success"
            state["last_keepalive_error"] = ""
            state["last_error"] = ""
        else:
            state["last_keepalive_result"] = "failed"
            state["last_keepalive_error"] = details
            state["last_error"] = details
        save_state_locked()

    payload = build_printer_payload(printer, now)
    result = {
        "ok": ok,
        "printer_id": printer.printer_id,
        "template": template,
        "source": source,
        "details": details,
        "metadata": metadata,
        "reason": print_context.get("reason", ""),
        "trigger": print_context.get("trigger", ""),
        "timestamp": iso_utc(now),
        "printer": payload,
    }

    if ok:
        log(f"Keepalive print submitted for {printer.name} using template={template}, source={source}.")
    else:
        log(f"Keepalive print failed for {printer.name}: {details}")
    return result


def update_printer_setting(printer: PrinterConfig, updates: dict[str, Any]) -> dict[str, Any]:
    with STATE_LOCK:
        state = ensure_printer_state_locked(printer.printer_id)

        if "template" in updates:
            template = str(updates.get("template", "")).strip().lower()
            if template in SUPPORTED_TEMPLATES:
                state["template_override"] = template

        if "cadence_hours" in updates:
            try:
                cadence = int(updates.get("cadence_hours"))
            except (TypeError, ValueError):
                cadence = None
            if cadence is not None:
                state["cadence_hours_override"] = max(1, min(720, cadence))

        if "enabled" in updates:
            value = updates.get("enabled")
            if isinstance(value, bool):
                state["enabled_override"] = value
            elif isinstance(value, str):
                state["enabled_override"] = value.strip().lower() in {"1", "true", "yes", "on"}

        save_state_locked()

    payload = build_printer_payload(printer)
    return {
        "ok": True,
        "printer": payload,
    }


def generate_lovelace_card_yaml(printer: PrinterConfig) -> str:
    pid = printer.printer_id
    return "\n".join(
        [
            "type: vertical-stack",
            "cards:",
            "  - type: entities",
            f"    title: {printer.name} Health",
            "    entities:",
            f"      - sensor.{pid}_printer_state",
            f"      - sensor.{pid}_health",
            f"      - sensor.{pid}_last_keepalive_result",
            f"      - binary_sensor.{pid}_keepalive_needed",
            f"      - sensor.{pid}_time_since_last_print",
            f"      - sensor.{pid}_next_keepalive_due",
            f"      - sensor.{pid}_keepalive_print_count",
            f"      - sensor.{pid}_queued_job_count",
            f"      - sensor.{pid}_job_impressions_completed",
            f"      - sensor.{pid}_media_sheets_completed",
            f"      - sensor.{pid}_printer_up_time",
            f"      - sensor.{pid}_lowest_marker_level",
            "  - type: gauge",
            f"    entity: sensor.{pid}_lowest_marker_level",
            "    min: 0",
            "    max: 100",
            "    severity:",
            "      green: 50",
            "      yellow: 20",
            "      red: 0",
            "    name: Lowest Supply Level",
            "  - type: entities",
            "    title: Keepalive Controls",
            "    entities:",
            f"      - switch.{pid}_keepalive_enabled",
            f"      - select.{pid}_template",
            f"      - number.{pid}_cadence_hours",
            f"      - button.{pid}_print_now",
        ]
    )


class MqttBridge:
    def __init__(self, config: MqttConfig) -> None:
        self.config = config
        self.client: mqtt.Client | None = None
        self.started = False

    def status_topic(self) -> str:
        return f"{self.config.topic_prefix}/status"

    def printer_state_topic(self, printer_id: str) -> str:
        return f"{self.config.topic_prefix}/{printer_id}/state"

    def command_topic(self, printer_id: str, field: str) -> str:
        return f"{self.config.topic_prefix}/{printer_id}/set/{field}"

    def _publish(self, topic: str, payload: dict[str, Any] | str, retain: bool | None = None) -> None:
        if not self.client:
            return
        raw_payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.client.publish(topic, raw_payload, retain=self.config.retain if retain is None else retain)

    def _device_payload(self, printer: PrinterConfig) -> dict[str, Any]:
        data = build_printer_payload(printer)

        model = data.get("printer_make_and_model") or printer.printer_type
        manufacturer = "Printer"
        if isinstance(model, str) and model:
            manufacturer = model.split(" ", 1)[0]

        return {
            "identifiers": [f"printer_keepalive_{printer.printer_id}"],
            "name": printer.name,
            "manufacturer": manufacturer,
            "model": model,
            "sw_version": APP_VERSION,
            "configuration_url": APP_URL,
        }

    def _discovery_base(self, printer: PrinterConfig) -> dict[str, Any]:
        return {
            "device": self._device_payload(printer),
            "origin": {"name": APP_NAME, "sw_version": APP_VERSION, "support_url": APP_URL},
            "availability_topic": self.status_topic(),
            "payload_available": "online",
            "payload_not_available": "offline",
        }

    def _publish_discovery_for_printer(self, printer: PrinterConfig) -> None:
        base = self._discovery_base(printer)
        state_topic = self.printer_state_topic(printer.printer_id)
        dp = self.config.discovery_prefix
        object_prefix = f"printer_keepalive_{printer.printer_id}"

        entities: list[tuple[str, dict[str, Any]]] = []

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_health/config",
                {
                    **base,
                    "name": "Health",
                    "object_id": f"{printer.printer_id}_health",
                    "unique_id": f"{object_prefix}_health",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.health_status }}",
                    "json_attributes_topic": state_topic,
                    "icon": "mdi:heart-pulse",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_printer_state/config",
                {
                    **base,
                    "name": "Printer State",
                    "object_id": f"{printer.printer_id}_printer_state",
                    "unique_id": f"{object_prefix}_printer_state",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.printer_state }}",
                    "icon": "mdi:printer",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_time_since_last_print/config",
                {
                    **base,
                    "name": "Time Since Last Print",
                    "object_id": f"{printer.printer_id}_time_since_last_print",
                    "unique_id": f"{object_prefix}_time_since_last_print",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.time_since_last_print_hours | default(0) }}",
                    "unit_of_measurement": "h",
                    "icon": "mdi:timer-outline",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_keepalive_print_count/config",
                {
                    **base,
                    "name": "Keepalive Print Count",
                    "object_id": f"{printer.printer_id}_keepalive_print_count",
                    "unique_id": f"{object_prefix}_keepalive_print_count",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.keepalive_print_count | int(0) }}",
                    "icon": "mdi:counter",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_last_keepalive_result/config",
                {
                    **base,
                    "name": "Last Keepalive Result",
                    "object_id": f"{printer.printer_id}_last_keepalive_result",
                    "unique_id": f"{object_prefix}_last_keepalive_result",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.last_keepalive_result }}",
                    "icon": "mdi:check-decagram",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_next_keepalive_due/config",
                {
                    **base,
                    "name": "Next Keepalive Due",
                    "object_id": f"{printer.printer_id}_next_keepalive_due",
                    "unique_id": f"{object_prefix}_next_keepalive_due",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.next_keepalive_due_at }}",
                    "icon": "mdi:calendar-clock",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_queued_job_count/config",
                {
                    **base,
                    "name": "Queued Job Count",
                    "object_id": f"{printer.printer_id}_queued_job_count",
                    "unique_id": f"{object_prefix}_queued_job_count",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.queued_job_count | default(0) }}",
                    "icon": "mdi:format-list-numbered",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_job_impressions_completed/config",
                {
                    **base,
                    "name": "Job Impressions Completed",
                    "object_id": f"{printer.printer_id}_job_impressions_completed",
                    "unique_id": f"{object_prefix}_job_impressions_completed",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.job_impressions_completed | default(0) }}",
                    "icon": "mdi:file-document-multiple",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_media_sheets_completed/config",
                {
                    **base,
                    "name": "Media Sheets Completed",
                    "object_id": f"{printer.printer_id}_media_sheets_completed",
                    "unique_id": f"{object_prefix}_media_sheets_completed",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.media_sheets_completed | default(0) }}",
                    "icon": "mdi:file-multiple",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_printer_up_time/config",
                {
                    **base,
                    "name": "Printer Uptime",
                    "object_id": f"{printer.printer_id}_printer_up_time",
                    "unique_id": f"{object_prefix}_printer_up_time",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.printer_up_time_seconds | default(0) }}",
                    "unit_of_measurement": "s",
                    "icon": "mdi:timer",
                },
            )
        )

        entities.append(
            (
                f"{dp}/sensor/{object_prefix}_lowest_marker_level/config",
                {
                    **base,
                    "name": "Lowest Supply Level",
                    "object_id": f"{printer.printer_id}_lowest_marker_level",
                    "unique_id": f"{object_prefix}_lowest_marker_level",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.lowest_marker_level | default(0) }}",
                    "unit_of_measurement": "%",
                    "icon": "mdi:water-percent",
                },
            )
        )

        entities.append(
            (
                f"{dp}/binary_sensor/{object_prefix}_keepalive_needed/config",
                {
                    **base,
                    "name": "Keepalive Needed",
                    "object_id": f"{printer.printer_id}_keepalive_needed",
                    "unique_id": f"{object_prefix}_keepalive_needed",
                    "state_topic": state_topic,
                    "value_template": "{{ 'ON' if value_json.keepalive_needed else 'OFF' }}",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "icon": "mdi:alert-circle-outline",
                },
            )
        )

        entities.append(
            (
                f"{dp}/switch/{object_prefix}_keepalive_enabled/config",
                {
                    **base,
                    "name": "Keepalive Enabled",
                    "object_id": f"{printer.printer_id}_keepalive_enabled",
                    "unique_id": f"{object_prefix}_keepalive_enabled",
                    "state_topic": state_topic,
                    "value_template": "{{ 'ON' if value_json.enabled else 'OFF' }}",
                    "command_topic": self.command_topic(printer.printer_id, "enabled"),
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                    "icon": "mdi:toggle-switch",
                },
            )
        )

        entities.append(
            (
                f"{dp}/select/{object_prefix}_template/config",
                {
                    **base,
                    "name": "Template",
                    "object_id": f"{printer.printer_id}_template",
                    "unique_id": f"{object_prefix}_template",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.template }}",
                    "command_topic": self.command_topic(printer.printer_id, "template"),
                    "options": list(SUPPORTED_TEMPLATES),
                    "icon": "mdi:file-document-outline",
                },
            )
        )

        entities.append(
            (
                f"{dp}/number/{object_prefix}_cadence_hours/config",
                {
                    **base,
                    "name": "Cadence Hours",
                    "object_id": f"{printer.printer_id}_cadence_hours",
                    "unique_id": f"{object_prefix}_cadence_hours",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.cadence_hours | int(0) }}",
                    "command_topic": self.command_topic(printer.printer_id, "cadence_hours"),
                    "min": 1,
                    "max": 720,
                    "step": 1,
                    "mode": "box",
                    "unit_of_measurement": "h",
                    "icon": "mdi:clock-time-four-outline",
                },
            )
        )

        entities.append(
            (
                f"{dp}/button/{object_prefix}_print_now/config",
                {
                    **base,
                    "name": "Print Now",
                    "object_id": f"{printer.printer_id}_print_now",
                    "unique_id": f"{object_prefix}_print_now",
                    "command_topic": self.command_topic(printer.printer_id, "print_now"),
                    "payload_press": "PRESS",
                    "icon": "mdi:printer-pos",
                },
            )
        )

        for topic, payload in entities:
            self._publish(topic, payload, retain=True)

    def publish_discovery(self) -> None:
        if not self.client:
            return
        for printer in PRINTERS:
            self._publish_discovery_for_printer(printer)

    def publish_printer_state(self, printer: PrinterConfig) -> None:
        payload = build_printer_payload(printer)
        self._publish(self.printer_state_topic(printer.printer_id), payload)

    def publish_all_states(self) -> None:
        for printer in PRINTERS:
            self.publish_printer_state(printer)

    def _handle_command(self, topic: str, payload: str) -> None:
        prefix = f"{self.config.topic_prefix}/"
        if not topic.startswith(prefix):
            return
        tail = topic[len(prefix) :]
        parts = [part for part in tail.split("/") if part]
        if len(parts) < 3 or parts[1] != "set":
            return

        printer_id = parts[0]
        field = parts[2]
        printer = PRINTERS_BY_ID.get(printer_id)
        if not printer:
            return

        normalized = payload.strip()
        if field == "template":
            update_printer_setting(printer, {"template": normalized})
            self.publish_printer_state(printer)
            return

        if field == "cadence_hours":
            update_printer_setting(printer, {"cadence_hours": normalized})
            self.publish_printer_state(printer)
            return

        if field == "enabled":
            value = normalized.lower() in {"1", "true", "yes", "on"}
            update_printer_setting(printer, {"enabled": value})
            self.publish_printer_state(printer)
            return

        if field == "print_now":
            run_keepalive_print(printer, source="mqtt", only_if_needed=False)
            self.publish_printer_state(printer)
            return

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: dict[str, Any], rc: int) -> None:
        if rc != 0:
            log(f"MQTT connect failed with rc={rc}")
            return
        log("MQTT connected.")
        client.subscribe(f"{self.config.topic_prefix}/+/set/#")
        client.subscribe("homeassistant/status")
        self._publish(self.status_topic(), "online", retain=True)
        self.publish_discovery()
        self.publish_all_states()

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        log(f"MQTT disconnected rc={rc}")

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="ignore")
        if topic == "homeassistant/status" and payload.strip().lower() == "online":
            self.publish_discovery()
            self.publish_all_states()
            return

        if topic.startswith(f"{self.config.topic_prefix}/") and "/set/" in topic:
            self._handle_command(topic, payload)

    def start(self) -> None:
        if self.started:
            return
        if not self.config.enabled:
            log("MQTT discovery disabled or incomplete MQTT config.")
            return

        client = mqtt.Client(client_id=self.config.client_id)
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        if self.config.tls:
            client.tls_set()

        client.will_set(self.status_topic(), "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        client.connect_async(self.config.host, self.config.port, keepalive=60)
        client.loop_start()

        self.client = client
        self.started = True
        log(f"MQTT bridge starting for broker {self.config.host}:{self.config.port}.")

    def stop(self) -> None:
        if not self.client:
            return
        try:
            self._publish(self.status_topic(), "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.client = None
        self.started = False


MQTT_BRIDGE = MqttBridge(MQTT_CONFIG)


def publish_printer_state_if_enabled(printer: PrinterConfig) -> None:
    if MQTT_BRIDGE.started:
        MQTT_BRIDGE.publish_printer_state(printer)


def scheduler_loop() -> None:
    while True:
        now = utc_now()

        if DISCOVERY_ENABLED:
            try:
                with DISCOVERY_LOCK:
                    last_scan = parse_iso(DISCOVERY_STATE.get("last_scan_at"))
                if not last_scan or (now - last_scan).total_seconds() >= DISCOVERY_INTERVAL_SECONDS:
                    result = get_discovery_payload(force=True)
                    if result.get("ok"):
                        log(f"Printer discovery scan found {result.get('printer_count', 0)} candidate(s).")
                    else:
                        log(f"Printer discovery scan failed: {result.get('last_error', 'unknown error')}")
            except Exception as exc:  # noqa: BLE001
                log(f"Discovery scheduler error: {exc}")

        for printer in PRINTERS:
            try:
                poll_printer(printer)
                payload = build_printer_payload(printer, now)
                keepalive_needed = bool(payload.get("keepalive_needed"))

                if AUTO_PRINT_ENABLED and keepalive_needed:
                    run_keepalive_print(printer, source="scheduler", only_if_needed=True)

                publish_printer_state_if_enabled(printer)
            except Exception as exc:  # noqa: BLE001
                log(f"Scheduler error for {printer.name}: {exc}")

        time.sleep(30)


def global_payload() -> dict[str, Any]:
    now = utc_now()
    printers = [build_printer_payload(printer, now) for printer in PRINTERS]
    discovery = discovery_snapshot()
    discovery_summary = dict(discovery)
    discovery_summary.pop("printers", None)
    return {
        "ok": True,
        "name": APP_NAME,
        "version": APP_VERSION,
        "auto_print_enabled": AUTO_PRINT_ENABLED,
        "status_poll_interval_seconds": STATUS_POLL_INTERVAL_SECONDS,
        "mqtt_enabled": MQTT_BRIDGE.started,
        "discovery": discovery_summary,
        "printer_count": len(printers),
        "supported_templates": list(SUPPORTED_TEMPLATES),
        "auth_required": bool(AUTH_TOKEN),
        "addon_page_url": ADDON_PAGE_URL,
        "maintenance_guidance": MAINTENANCE_GUIDANCE,
        "printers": printers,
        "timestamp": iso_utc(now),
    }


def ui_dashboard_html() -> str:
    template = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__APP_NAME__</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --font-sans: 'Sora', system-ui, -apple-system, sans-serif;
      --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
      --bg: #f0f2f7;
      --bg-secondary: #e8ebf0;
      --card: #ffffff;
      --card-elevated: #ffffff;
      --text: #0f1729;
      --text-secondary: #475569;
      --muted: #64748b;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --accent-subtle: #eff6ff;
      --accent-border: rgba(59,130,246,0.2);
      --success: #10b981;
      --success-subtle: #ecfdf5;
      --success-border: rgba(16,185,129,0.2);
      --danger: #ef4444;
      --danger-subtle: #fef2f2;
      --danger-border: rgba(239,68,68,0.2);
      --warning: #f59e0b;
      --warning-subtle: #fffbeb;
      --border: #e2e8f0;
      --border-subtle: #f1f5f9;
      --input-bg: #f8fafc;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
      --shadow-md: 0 2px 8px rgba(0,0,0,0.06);
      --shadow-lg: 0 4px 16px rgba(0,0,0,0.08);
      --radius: 12px;
      --radius-sm: 8px;
      --radius-xs: 6px;
      --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      color-scheme: light;
    }
    [data-theme="dark"] {
      --bg: #0c0f1a;
      --bg-secondary: #080b14;
      --card: #161b2e;
      --card-elevated: #1c2238;
      --text: #e8edf5;
      --text-secondary: #94a3b8;
      --muted: #64748b;
      --accent: #60a5fa;
      --accent-hover: #93bbfd;
      --accent-subtle: rgba(96,165,250,0.1);
      --accent-border: rgba(96,165,250,0.2);
      --success: #34d399;
      --success-subtle: rgba(52,211,153,0.1);
      --success-border: rgba(52,211,153,0.2);
      --danger: #f87171;
      --danger-subtle: rgba(248,113,113,0.1);
      --danger-border: rgba(248,113,113,0.2);
      --warning: #fbbf24;
      --warning-subtle: rgba(251,191,36,0.08);
      --border: #1e293b;
      --border-subtle: #1a2236;
      --input-bg: #111827;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.2);
      --shadow-md: 0 2px 8px rgba(0,0,0,0.3);
      --shadow-lg: 0 4px 16px rgba(0,0,0,0.4);
      color-scheme: dark;
    }
    @media (prefers-color-scheme: dark) {
      [data-theme="system"] {
        --bg: #0c0f1a;
        --bg-secondary: #080b14;
        --card: #161b2e;
        --card-elevated: #1c2238;
        --text: #e8edf5;
        --text-secondary: #94a3b8;
        --muted: #64748b;
        --accent: #60a5fa;
        --accent-hover: #93bbfd;
        --accent-subtle: rgba(96,165,250,0.1);
        --accent-border: rgba(96,165,250,0.2);
        --success: #34d399;
        --success-subtle: rgba(52,211,153,0.1);
        --success-border: rgba(52,211,153,0.2);
        --danger: #f87171;
        --danger-subtle: rgba(248,113,113,0.1);
        --danger-border: rgba(248,113,113,0.2);
        --warning: #fbbf24;
        --warning-subtle: rgba(251,191,36,0.08);
        --border: #1e293b;
        --border-subtle: #1a2236;
        --input-bg: #111827;
        --shadow-sm: 0 1px 2px rgba(0,0,0,0.2);
        --shadow-md: 0 2px 8px rgba(0,0,0,0.3);
        --shadow-lg: 0 4px 16px rgba(0,0,0,0.4);
        color-scheme: dark;
      }
    }

    /* === BASE === */
    *, *::before, *::after { box-sizing: border-box; margin: 0; }
    body {
      font-family: var(--font-sans);
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
      transition: background var(--transition), color var(--transition);
      overflow-x: hidden;
    }

    /* === CMYK STRIPE === */
    .cmyk-stripe {
      height: 3px;
      background: linear-gradient(90deg, #00bcd4 25%, #e91e63 25%, #e91e63 50%, #ffc107 50%, #ffc107 75%, #263238 75%);
      flex-shrink: 0;
    }

    /* === APP SHELL === */
    .app-shell {
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* === TOPBAR === */
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 24px;
      background: var(--card);
      border-bottom: 1px solid var(--border);
      gap: 16px;
    }
    .topbar-brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .brand-mark {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      box-shadow: 0 2px 8px rgba(59,130,246,0.3);
    }
    .brand-mark svg {
      width: 20px;
      height: 20px;
      fill: none;
      stroke: #fff;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .brand-text h1 {
      font-size: 17px;
      font-weight: 700;
      letter-spacing: -0.03em;
      line-height: 1.2;
    }
    .brand-text .version {
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
      letter-spacing: 0.02em;
    }
    .topbar-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }
    .topbar-btn {
      width: 36px;
      height: 36px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: var(--muted);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all var(--transition);
      position: relative;
      padding: 0;
      font-weight: 400;
    }
    .topbar-btn:hover { color: var(--text); background: var(--bg-secondary); border-color: var(--text-secondary); transform: none; filter: none; }
    .topbar-btn.active { color: var(--accent); border-color: var(--accent-border); background: var(--accent-subtle); }
    .topbar-btn svg {
      width: 16px;
      height: 16px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .auth-indicator {
      position: absolute;
      top: 6px;
      right: 6px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--muted);
      border: 1.5px solid var(--input-bg);
      transition: background var(--transition);
    }
    .auth-indicator.has-token { background: var(--success); }

    /* === AUTH POPOVER === */
    .auth-wrap { position: relative; }
    .auth-popover {
      position: absolute;
      top: calc(100% + 8px);
      right: 0;
      width: 300px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
      box-shadow: var(--shadow-lg);
      z-index: 100;
      display: none;
      animation: popoverIn 0.15s ease;
    }
    .auth-popover.open { display: block; }
    @keyframes popoverIn {
      from { opacity: 0; transform: translateY(-4px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .auth-popover-title {
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 10px;
      color: var(--text);
    }
    .auth-popover label {
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      display: block;
      margin-bottom: 4px;
    }
    .auth-popover input {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: var(--radius-xs);
      padding: 8px 10px;
      font-size: 13px;
      font-family: var(--font-mono);
      background: var(--input-bg);
      color: var(--text);
    }
    .auth-popover-actions {
      display: flex;
      gap: 6px;
      margin-top: 10px;
    }
    .auth-popover-actions button {
      flex: 1;
      padding: 6px 0;
      font-size: 12px;
    }
    .auth-hint {
      font-size: 11px;
      color: var(--muted);
      margin-top: 8px;
      line-height: 1.4;
    }

    /* === THEME SWITCHER === */
    .theme-group {
      display: flex;
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 2px;
    }
    .theme-btn {
      width: 30px;
      height: 30px;
      border: none;
      background: transparent;
      color: var(--muted);
      border-radius: var(--radius-xs);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all var(--transition);
      padding: 0;
      font-weight: 400;
    }
    .theme-btn:hover { color: var(--text); transform: none; filter: none; }
    .theme-btn.active { background: var(--card); color: var(--accent); box-shadow: var(--shadow-sm); }
    .theme-btn svg {
      width: 14px;
      height: 14px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    /* === TAB NAV === */
    .tab-bar {
      display: flex;
      gap: 1px;
      padding: 0 24px;
      background: var(--card);
      border-bottom: 1px solid var(--border);
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: none;
    }
    .tab-bar::-webkit-scrollbar { display: none; }
    .tab {
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 11px 16px;
      border: none;
      background: transparent;
      color: var(--muted);
      font-family: var(--font-sans);
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all var(--transition);
      white-space: nowrap;
      position: relative;
    }
    .tab::before {
      content: "";
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--tab-color, var(--muted));
      opacity: 0.35;
      transition: all var(--transition);
      flex-shrink: 0;
    }
    .tab:hover { color: var(--text); background: var(--bg); transform: none; filter: none; }
    .tab:hover::before { opacity: 0.6; }
    .tab.active {
      color: var(--text);
      border-bottom-color: var(--tab-color, var(--accent));
    }
    .tab.active::before { opacity: 1; width: 7px; height: 7px; }
    .tab[data-view="dashboard"] { --tab-color: #00bcd4; }
    .tab[data-view="printers"] { --tab-color: #e91e63; }
    .tab[data-view="discovery"] { --tab-color: #f59e0b; }
    .tab[data-view="config"] { --tab-color: #64748b; }
    .tab svg {
      width: 15px;
      height: 15px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      display: none;
    }

    /* === CONTENT === */
    .content {
      flex: 1;
      max-width: 1200px;
      width: 100%;
      margin: 0 auto;
      padding: 20px 24px;
    }
    .view { display: none; }
    .view.active {
      display: block;
      animation: viewIn 0.25s ease;
    }
    @keyframes viewIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* === SECTION HEADER === */
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 14px;
    }
    .section-head h2 {
      font-size: 15px;
      font-weight: 600;
      letter-spacing: -0.01em;
    }
    .section-head .section-link {
      font-size: 12px;
      font-weight: 500;
      color: var(--accent);
      text-decoration: none;
      cursor: pointer;
      transition: color var(--transition);
    }
    .section-head .section-link:hover { color: var(--accent-hover); }
    .view-title {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin-bottom: 4px;
    }
    .view-subtitle {
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 20px;
    }

    /* === METRIC CARDS === */
    .metric-cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }
    .metric-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 18px;
      border-left: 3px solid var(--metric-color, var(--border));
      box-shadow: var(--shadow-sm);
      transition: box-shadow var(--transition), border-color var(--transition);
      animation: cardIn 0.35s ease both;
    }
    .metric-card:hover { box-shadow: var(--shadow-md); }
    .metric-card:nth-child(1) { animation-delay: 0.03s; }
    .metric-card:nth-child(2) { animation-delay: 0.06s; }
    .metric-card:nth-child(3) { animation-delay: 0.09s; }
    .metric-card:nth-child(4) { animation-delay: 0.12s; }
    @keyframes cardIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .metric-card[data-color="cyan"] { --metric-color: #00bcd4; }
    .metric-card[data-color="magenta"] { --metric-color: #e91e63; }
    .metric-card[data-color="yellow"] { --metric-color: #f59e0b; }
    .metric-card[data-color="key"] { --metric-color: var(--muted); }
    .metric-label {
      font-size: 11px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 6px;
    }
    .metric-value {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.03em;
      line-height: 1.1;
      color: var(--text);
    }
    .metric-sub {
      font-size: 12px;
      color: var(--text-secondary);
      margin-top: 3px;
    }

    /* === PANELS === */
    .panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
      transition: background var(--transition), border-color var(--transition), box-shadow var(--transition);
      overflow: hidden;
    }
    .panel-body {
      padding: 18px 20px;
    }

    /* === DASHBOARD PRINTER LIST === */
    .printer-list-head {
      display: grid;
      grid-template-columns: 10px 1fr 120px 140px;
      gap: 12px;
      padding: 10px 18px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      background: var(--input-bg);
      border-bottom: 1px solid var(--border);
    }
    .printer-row {
      display: grid;
      grid-template-columns: 10px 1fr 120px 140px;
      gap: 12px;
      align-items: center;
      padding: 12px 18px;
      border-bottom: 1px solid var(--border-subtle);
      font-size: 13px;
      transition: background var(--transition);
    }
    .printer-row:last-child { border-bottom: none; }
    .printer-row:hover { background: var(--input-bg); }
    .health-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted);
      flex-shrink: 0;
    }
    .health-dot[data-status="ok"],
    .health-dot[data-status="healthy"] { background: var(--success); box-shadow: 0 0 6px rgba(16,185,129,0.4); }
    .health-dot[data-status="idle"] { background: var(--success); box-shadow: 0 0 6px rgba(16,185,129,0.4); }
    .health-dot[data-status="warning"] { background: var(--warning); box-shadow: 0 0 6px rgba(245,158,11,0.4); }
    .health-dot[data-status="error"],
    .health-dot[data-status="critical"],
    .health-dot[data-status="stopped"] { background: var(--danger); box-shadow: 0 0 6px rgba(239,68,68,0.4); }
    .printer-name-cell {
      font-weight: 500;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .printer-state-cell { color: var(--text-secondary); }
    .printer-due-cell {
      color: var(--text-secondary);
      font-size: 12px;
    }
    .printer-due-cell.overdue { color: var(--danger); font-weight: 600; }
    .dash-empty {
      padding: 32px 18px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
    }

    /* === OVERVIEW KV === */
    .kv-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }
    .kv-item {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .kv-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .kv-value {
      font-size: 14px;
      font-weight: 500;
      color: var(--text);
    }

    /* === PRINTER CARDS (full view) === */
    .printer-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 16px;
    }
    .printer-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      display: grid;
      gap: 0;
      transition: box-shadow var(--transition), border-color var(--transition);
    }
    .printer-card:hover { box-shadow: var(--shadow-md); }
    .printer-card-head {
      padding: 16px 18px 14px;
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      align-items: flex-start;
      gap: 10px;
    }
    .printer-card-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-top: 4px;
      flex-shrink: 0;
      background: var(--muted);
    }
    .printer-card-dot[data-status="ok"],
    .printer-card-dot[data-status="healthy"],
    .printer-card-dot[data-status="idle"] { background: var(--success); box-shadow: 0 0 8px rgba(16,185,129,0.4); }
    .printer-card-dot[data-status="warning"] { background: var(--warning); box-shadow: 0 0 8px rgba(245,158,11,0.4); }
    .printer-card-dot[data-status="error"],
    .printer-card-dot[data-status="critical"],
    .printer-card-dot[data-status="stopped"] { background: var(--danger); box-shadow: 0 0 8px rgba(239,68,68,0.4); }
    .printer-card-info h3 {
      font-size: 15px;
      font-weight: 600;
      letter-spacing: -0.01em;
      margin-bottom: 2px;
    }
    .printer-card-meta {
      font-size: 11px;
      font-family: var(--font-mono);
      color: var(--muted);
      word-break: break-all;
      line-height: 1.4;
    }
    .printer-card-stats {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px 16px;
      padding: 14px 18px;
      background: var(--input-bg);
      font-size: 13px;
    }
    .printer-card-stats .stat {
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .printer-card-stats .stat-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .printer-card-stats .stat-value {
      font-weight: 500;
      color: var(--text);
    }
    .printer-card-controls {
      padding: 14px 18px;
      border-top: 1px solid var(--border-subtle);
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: end;
    }
    .control-group {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .control-group label {
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .control-group input[type="number"] {
      width: 90px;
    }
    .control-group select {
      min-width: 130px;
    }
    .printer-card-actions {
      padding: 0 18px 14px;
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .printer-card-status {
      padding: 0 18px 14px;
      min-height: 18px;
      font-size: 12px;
      color: var(--muted);
    }
    .printer-card-status.error { color: var(--danger); }
    .printer-card-status.ok { color: var(--success); }
    .printers-empty {
      text-align: center;
      padding: 48px 24px;
      color: var(--muted);
    }
    .printers-empty-icon {
      width: 48px;
      height: 48px;
      margin: 0 auto 12px;
      fill: none;
      stroke: var(--border);
      stroke-width: 1.5;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    /* === TABLE === */
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      min-width: 640px;
    }
    th, td {
      text-align: left;
      padding: 10px 16px;
      border-bottom: 1px solid var(--border-subtle);
      vertical-align: top;
    }
    th {
      background: var(--input-bg);
      color: var(--muted);
      font-weight: 600;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      border-bottom-color: var(--border);
    }
    td { color: var(--text-secondary); }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--input-bg); }

    /* === FORMS === */
    label {
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    input[type="text"], input[type="password"], input[type="number"], select {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: var(--radius-xs);
      padding: 8px 10px;
      font-size: 13px;
      font-family: var(--font-sans);
      background: var(--input-bg);
      color: var(--text);
      transition: border-color var(--transition), box-shadow var(--transition);
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-border);
    }
    .config-editor {
      width: 100%;
      min-height: 400px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 14px 16px;
      font-size: 13px;
      font-family: var(--font-mono);
      line-height: 1.6;
      background: var(--input-bg);
      color: var(--text);
      resize: vertical;
      transition: border-color var(--transition), box-shadow var(--transition);
    }

    /* === BUTTONS === */
    button {
      border: 1px solid var(--success-border);
      background: var(--success-subtle);
      color: var(--success);
      border-radius: var(--radius-xs);
      padding: 7px 14px;
      font-size: 12px;
      font-weight: 600;
      font-family: var(--font-sans);
      cursor: pointer;
      transition: all var(--transition);
      white-space: nowrap;
    }
    button:hover { filter: brightness(0.95); transform: translateY(-1px); box-shadow: var(--shadow-sm); }
    button:active { transform: translateY(0); }
    button.secondary {
      border-color: var(--accent-border);
      background: var(--accent-subtle);
      color: var(--accent);
    }
    button.neutral {
      border-color: var(--border);
      background: var(--input-bg);
      color: var(--text-secondary);
    }
    button.danger {
      border-color: var(--danger-border);
      background: var(--danger-subtle);
      color: var(--danger);
    }
    .btn-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    /* === STATUS === */
    .status {
      font-size: 12px;
      color: var(--muted);
      padding: 8px 0;
      min-height: 20px;
    }
    .status.error { color: var(--danger); }
    .status.ok { color: var(--success); }
    .hint {
      font-size: 12px;
      color: var(--muted);
      line-height: 1.5;
    }
    .hint code {
      background: var(--input-bg);
      border: 1px solid var(--border);
      padding: 1px 5px;
      border-radius: 3px;
      font-size: 11px;
      font-family: var(--font-mono);
    }

    /* === FOOTER === */
    .app-footer {
      padding: 16px 24px;
      text-align: center;
      font-size: 12px;
      color: var(--muted);
      border-top: 1px solid var(--border);
      margin-top: auto;
    }
    .app-footer a {
      color: var(--accent);
      text-decoration: none;
      font-weight: 500;
    }
    .app-footer a:hover { text-decoration: underline; }

    /* === LAST UPDATED === */
    .last-updated {
      font-size: 11px;
      color: var(--muted);
      text-align: right;
      padding: 4px 0;
    }

    /* === DISCOVERY SUMMARY === */
    .disc-summary {
      display: flex;
      gap: 20px;
      flex-wrap: wrap;
      padding: 14px 18px;
      border-bottom: 1px solid var(--border-subtle);
      font-size: 13px;
    }
    .disc-summary .disc-stat {
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .disc-summary .disc-stat-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .disc-summary .disc-stat-value {
      font-weight: 500;
      color: var(--text);
    }

    /* === RESPONSIVE === */
    @media (max-width: 768px) {
      .topbar { padding: 10px 16px; }
      .tab-bar { padding: 0 16px; }
      .content { padding: 16px; }
      .brand-text h1 { font-size: 15px; }
      .metric-cards { grid-template-columns: repeat(2, 1fr); }
      .metric-value { font-size: 22px; }
      .printer-grid { grid-template-columns: 1fr; }
      .printer-list-head { display: none; }
      .printer-row {
        grid-template-columns: 10px 1fr;
        grid-template-rows: auto auto;
      }
      .printer-state-cell, .printer-due-cell {
        grid-column: 2;
        font-size: 11px;
      }
      .printer-card-stats { grid-template-columns: 1fr; }
      .view-title { font-size: 18px; }
    }
    @media (max-width: 480px) {
      .metric-cards { grid-template-columns: 1fr; }
      .topbar-actions .theme-group { display: none; }
    }
  </style>
  <script>
    (function(){var s=localStorage.getItem("pk_theme")||"system";document.documentElement.setAttribute("data-theme",s)})();
  </script>
</head>
<body>
  <div class="app-shell">
    <div class="cmyk-stripe"></div>

    <header class="topbar">
      <div class="topbar-brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8" rx="1"/></svg>
        </div>
        <div class="brand-text">
          <h1>__APP_NAME__</h1>
          <span class="version">v__APP_VERSION__</span>
        </div>
      </div>
      <div class="topbar-actions">
        <div class="theme-group">
          <button type="button" class="theme-btn" data-theme-value="light" title="Light">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
          </button>
          <button type="button" class="theme-btn" data-theme-value="dark" title="Dark">
            <svg viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
          </button>
          <button type="button" class="theme-btn" data-theme-value="system" title="System">
            <svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </button>
        </div>
        <button type="button" id="refreshBtn" class="topbar-btn" title="Refresh data">
          <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        </button>
        <div class="auth-wrap">
          <button type="button" id="authToggle" class="topbar-btn" title="API Authentication">
            <svg viewBox="0 0 24 24"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
            <span class="auth-indicator" id="authIndicator"></span>
          </button>
          <div class="auth-popover" id="authPopover">
            <div class="auth-popover-title">API Authentication</div>
            <label for="authTokenInput">Bearer Token</label>
            <input id="authTokenInput" type="password" placeholder="Enter token...">
            <div class="auth-popover-actions">
              <button id="saveTokenBtn" class="secondary" type="button">Save</button>
              <button id="clearTokenBtn" class="neutral" type="button">Clear</button>
            </div>
            <div class="auth-hint">Required for POST actions when <code>auth_token</code> is set in config.</div>
          </div>
        </div>
      </div>
    </header>

    <nav class="tab-bar">
      <button type="button" class="tab active" data-view="dashboard">
        <svg viewBox="0 0 24 24"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        Dashboard
      </button>
      <button type="button" class="tab" data-view="printers">
        <svg viewBox="0 0 24 24"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8" rx="1"/></svg>
        Printers
      </button>
      <button type="button" class="tab" data-view="discovery">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        Discovery
      </button>
      <button type="button" class="tab" data-view="config">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        Config
      </button>
    </nav>

    <main class="content">
      <!-- ===== DASHBOARD ===== -->
      <section id="view-dashboard" class="view active">
        <div id="dashboardMetrics" class="metric-cards"></div>

        <div class="section-head">
          <h2>Printer Health</h2>
          <span class="section-link" data-nav="printers">Manage printers &rarr;</span>
        </div>
        <div class="panel" style="margin-bottom:24px;">
          <div class="printer-list-head">
            <span></span><span>Printer</span><span>State</span><span>Next Due</span>
          </div>
          <div id="dashboardPrinters"></div>
        </div>

        <div class="section-head">
          <h2>Network Discovery</h2>
          <span class="section-link" data-nav="discovery">View details &rarr;</span>
        </div>
        <div class="panel">
          <div id="dashboardDiscovery" class="disc-summary"></div>
        </div>

        <div id="lastUpdated" class="last-updated"></div>
      </section>

      <!-- ===== PRINTERS ===== -->
      <section id="view-printers" class="view">
        <div class="view-title">Printers</div>
        <div class="view-subtitle">Manage individual printer settings, run keepalive prints, and poll status.</div>
        <div id="printersGrid" class="printer-grid"></div>
      </section>

      <!-- ===== DISCOVERY ===== -->
      <section id="view-discovery" class="view">
        <div class="view-title">Discovery</div>
        <div class="view-subtitle">Network printer discovery via mDNS/Zeroconf.</div>
        <div class="btn-row" style="margin-bottom:14px;">
          <button id="rescanBtn" class="secondary" type="button">Run Discovery Rescan</button>
        </div>
        <div id="discoveryStatus" class="status"></div>
        <div class="panel">
          <div id="discoverySummary" class="disc-summary"></div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>URI</th>
                  <th>Type Guess</th>
                  <th>Reachable</th>
                  <th>Configured</th>
                </tr>
              </thead>
              <tbody id="discoveryRows"></tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- ===== CONFIG ===== -->
      <section id="view-config" class="view">
        <div class="view-title">Configuration</div>
        <div class="view-subtitle">Edit the add-on configuration file. Save changes, then restart to apply.</div>
        <div class="btn-row" style="margin-bottom:14px;">
          <button id="loadConfigBtn" class="neutral" type="button">Load Config</button>
          <button id="saveConfigBtn" class="secondary" type="button">Save Config</button>
          <button id="restartAddonBtn" class="danger" type="button">Restart Add-on</button>
        </div>
        <textarea id="configEditor" class="config-editor" spellcheck="false" placeholder='{ "printers": [] }'></textarea>
        <div id="configStatus" class="status">Ready.</div>
      </section>
    </main>

    <footer class="app-footer">
      API: <a href="health">health</a> &middot; <a href="printers">printers</a> &middot; <a href="discovery">discovery</a> &middot; <a href="__APP_URL__" target="_blank" rel="noopener">docs</a>
    </footer>
  </div>

  <script>
    /* ===== THEME ===== */
    (function initTheme() {
      var saved = localStorage.getItem("pk_theme") || "system";
      document.documentElement.setAttribute("data-theme", saved);
      document.querySelectorAll(".theme-btn").forEach(function(btn) {
        btn.classList.toggle("active", btn.getAttribute("data-theme-value") === saved);
        btn.addEventListener("click", function() {
          var v = btn.getAttribute("data-theme-value");
          localStorage.setItem("pk_theme", v);
          document.documentElement.setAttribute("data-theme", v);
          document.querySelectorAll(".theme-btn").forEach(function(b) {
            b.classList.toggle("active", b.getAttribute("data-theme-value") === v);
          });
        });
      });
    })();

    /* ===== NAVIGATION ===== */
    var currentView = "dashboard";
    function switchView(name) {
      if (!document.getElementById("view-" + name)) return;
      currentView = name;
      document.querySelectorAll(".view").forEach(function(v) { v.classList.remove("active"); });
      document.querySelectorAll(".tab").forEach(function(t) { t.classList.remove("active"); });
      document.getElementById("view-" + name).classList.add("active");
      var tab = document.querySelector('.tab[data-view="' + name + '"]');
      if (tab) tab.classList.add("active");
      window.location.hash = name;
    }
    document.querySelectorAll(".tab").forEach(function(tab) {
      tab.addEventListener("click", function() { switchView(tab.getAttribute("data-view")); });
    });
    document.querySelectorAll(".section-link[data-nav]").forEach(function(link) {
      link.addEventListener("click", function() { switchView(link.getAttribute("data-nav")); });
    });
    window.addEventListener("hashchange", function() {
      var h = window.location.hash.replace("#", "");
      if (h && h !== currentView) switchView(h);
    });
    (function initNav() {
      var h = window.location.hash.replace("#", "");
      if (h && document.getElementById("view-" + h)) switchView(h);
    })();

    /* ===== AUTH POPOVER ===== */
    var authPopover = document.getElementById("authPopover");
    document.getElementById("authToggle").addEventListener("click", function(e) {
      e.stopPropagation();
      authPopover.classList.toggle("open");
    });
    authPopover.addEventListener("click", function(e) { e.stopPropagation(); });
    document.addEventListener("click", function() { authPopover.classList.remove("open"); });

    /* ===== STATE ===== */
    var state = {
      authToken: window.localStorage.getItem("pk_auth_token") || "",
      refreshInFlight: false,
      health: null,
      configLoaded: false
    };

    var authInput = document.getElementById("authTokenInput");
    var discoveryStatus = document.getElementById("discoveryStatus");
    var configStatus = document.getElementById("configStatus");
    var configEditor = document.getElementById("configEditor");
    var authIndicator = document.getElementById("authIndicator");

    authInput.value = state.authToken;
    if (state.authToken) authIndicator.classList.add("has-token");

    /* ===== API ===== */
    function apiPath(path) {
      var pathname = window.location.pathname;
      if (pathname.endsWith("/index.html")) pathname = pathname.slice(0, -("index.html".length));
      if (!pathname.endsWith("/")) pathname += "/";
      var base = window.location.origin + pathname;
      var clean = String(path || "").replace(/^\/+/, "");
      return new URL(clean, base).toString();
    }

    function setDiscoveryStatus(text, isError) {
      discoveryStatus.textContent = text;
      discoveryStatus.className = "status " + (isError ? "error" : "ok");
    }

    function setConfigStatus(text, isError) {
      configStatus.textContent = text;
      configStatus.className = "status " + (isError ? "error" : "ok");
    }

    function displayDate(value) {
      if (!value) return "n/a";
      var parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value);
      return parsed.toLocaleString();
    }

    function relativeTime(value) {
      if (!value) return "n/a";
      var d = new Date(value);
      if (Number.isNaN(d.getTime())) return String(value);
      var now = Date.now();
      var diff = d.getTime() - now;
      var abs = Math.abs(diff);
      if (abs < 60000) return diff < 0 ? "just now" : "now";
      if (abs < 3600000) { var m = Math.round(abs / 60000); return diff < 0 ? m + "m ago" : "in " + m + "m"; }
      if (abs < 86400000) { var h = Math.round(abs / 3600000); return diff < 0 ? h + "h ago" : "in " + h + "h"; }
      var dy = Math.round(abs / 86400000);
      return diff < 0 ? dy + "d ago" : "in " + dy + "d";
    }

    async function requestJson(path, init) {
      if (!init) init = {};
      var headers = Object.assign({}, init.headers || {});
      if (init.body !== undefined && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
      if (state.authToken) headers["Authorization"] = "Bearer " + state.authToken;
      var response = await fetch(apiPath(path), Object.assign({}, init, { headers: headers }));
      var raw = await response.text();
      var payload = {};
      if (raw) {
        try { payload = JSON.parse(raw); }
        catch (err) { throw new Error("Invalid JSON response for " + path + " (" + response.status + ")"); }
      }
      if (!response.ok || (payload && payload.ok === false)) {
        var message = (payload && (payload.error || payload.details || payload.reason)) || (response.status + " " + response.statusText);
        throw new Error(message);
      }
      return payload;
    }

    /* ===== DASHBOARD RENDERING ===== */
    function renderDashboard(health) {
      var disc = health.discovery || {};
      var printers = Array.isArray(health.printers) ? health.printers : [];

      /* Metric cards */
      var metricsEl = document.getElementById("dashboardMetrics");
      metricsEl.replaceChildren();
      var metrics = [
        { label: "Printers", value: String(health.printer_count || 0), sub: "configured", color: "cyan" },
        { label: "MQTT Bridge", value: health.mqtt_enabled ? "Connected" : "Offline", sub: health.mqtt_enabled ? "publishing" : "disabled", color: "magenta" },
        { label: "Auto Print", value: health.auto_print_enabled ? "Enabled" : "Disabled", sub: health.auto_print_enabled ? "scheduler active" : "manual only", color: "yellow" },
        { label: "Discovery", value: String(disc.printer_count || 0), sub: "found on network", color: "key" }
      ];
      for (var i = 0; i < metrics.length; i++) {
        var m = metrics[i];
        var card = document.createElement("div");
        card.className = "metric-card";
        card.setAttribute("data-color", m.color);
        var lbl = document.createElement("div");
        lbl.className = "metric-label";
        lbl.textContent = m.label;
        var val = document.createElement("div");
        val.className = "metric-value";
        val.textContent = m.value;
        var sub = document.createElement("div");
        sub.className = "metric-sub";
        sub.textContent = m.sub;
        card.appendChild(lbl);
        card.appendChild(val);
        card.appendChild(sub);
        metricsEl.appendChild(card);
      }

      /* Compact printer list */
      var listEl = document.getElementById("dashboardPrinters");
      listEl.replaceChildren();
      if (!printers.length) {
        var empty = document.createElement("div");
        empty.className = "dash-empty";
        empty.textContent = "No printers configured. Add printers in Configuration.";
        listEl.appendChild(empty);
      } else {
        for (var j = 0; j < printers.length; j++) {
          var p = printers[j];
          var row = document.createElement("div");
          row.className = "printer-row";
          var dot = document.createElement("div");
          dot.className = "health-dot";
          dot.setAttribute("data-status", String(p.health_status || p.printer_state || "unknown"));
          var name = document.createElement("span");
          name.className = "printer-name-cell";
          name.textContent = String(p.name || p.printer_id || "Printer");
          var st = document.createElement("span");
          st.className = "printer-state-cell";
          st.textContent = String(p.printer_state || "unknown");
          var due = document.createElement("span");
          due.className = "printer-due-cell";
          if (p.keepalive_needed) {
            due.textContent = "Keepalive due";
            due.classList.add("overdue");
          } else {
            due.textContent = relativeTime(p.next_keepalive_due_at);
          }
          row.appendChild(dot);
          row.appendChild(name);
          row.appendChild(st);
          row.appendChild(due);
          listEl.appendChild(row);
        }
      }

      /* Dashboard discovery summary */
      var ddEl = document.getElementById("dashboardDiscovery");
      ddEl.replaceChildren();
      var dStats = [
        ["Status", disc.enabled ? "Active" : "Disabled"],
        ["Last Scan", relativeTime(disc.last_scan_at)],
        ["Candidates", String(disc.printer_count || 0)]
      ];
      for (var k = 0; k < dStats.length; k++) {
        var ds = document.createElement("div");
        ds.className = "disc-stat";
        var dsl = document.createElement("span");
        dsl.className = "disc-stat-label";
        dsl.textContent = dStats[k][0];
        var dsv = document.createElement("span");
        dsv.className = "disc-stat-value";
        dsv.textContent = dStats[k][1];
        ds.appendChild(dsl);
        ds.appendChild(dsv);
        ddEl.appendChild(ds);
      }

      /* Last updated */
      document.getElementById("lastUpdated").textContent = "Updated " + new Date().toLocaleTimeString();
    }

    /* ===== FULL PRINTER CARDS ===== */
    function renderPrinters(health) {
      var printers = Array.isArray(health.printers) ? health.printers : [];
      var templates = Array.isArray(health.supported_templates) ? health.supported_templates : [];
      var grid = document.getElementById("printersGrid");
      grid.replaceChildren();

      if (!printers.length) {
        var empty = document.createElement("div");
        empty.className = "printers-empty";
        var emptyIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        emptyIcon.setAttribute("class", "printers-empty-icon");
        emptyIcon.setAttribute("viewBox", "0 0 24 24");
        var p1 = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p1.setAttribute("d", "M6 9V2h12v7");
        var p2 = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p2.setAttribute("d", "M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2");
        var p3 = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        p3.setAttribute("x","6"); p3.setAttribute("y","14"); p3.setAttribute("width","12"); p3.setAttribute("height","8"); p3.setAttribute("rx","1");
        emptyIcon.appendChild(p1); emptyIcon.appendChild(p2); emptyIcon.appendChild(p3);
        empty.appendChild(emptyIcon);
        var emptyText = document.createElement("div");
        emptyText.textContent = "No printers configured yet.";
        var emptyHint = document.createElement("div");
        emptyHint.className = "hint";
        emptyHint.style.marginTop = "4px";
        emptyHint.textContent = "Add printers in Configuration, or enable Discovery to find them automatically.";
        empty.appendChild(emptyText);
        empty.appendChild(emptyHint);
        grid.appendChild(empty);
        return;
      }

      for (var i = 0; i < printers.length; i++) {
        var printer = printers[i];
        var card = document.createElement("article");
        card.className = "printer-card";

        /* Header */
        var head = document.createElement("div");
        head.className = "printer-card-head";
        var hdot = document.createElement("div");
        hdot.className = "printer-card-dot";
        hdot.setAttribute("data-status", String(printer.health_status || printer.printer_state || "unknown"));
        var hinfo = document.createElement("div");
        hinfo.className = "printer-card-info";
        var h3 = document.createElement("h3");
        h3.textContent = String(printer.name || printer.printer_id || "Printer");
        var hmeta = document.createElement("div");
        hmeta.className = "printer-card-meta";
        hmeta.textContent = String(printer.printer_id || "") + " \u2022 " + String(printer.printer_uri || "");
        hinfo.appendChild(h3);
        hinfo.appendChild(hmeta);
        head.appendChild(hdot);
        head.appendChild(hinfo);

        /* Stats */
        var stats = document.createElement("div");
        stats.className = "printer-card-stats";
        var statPairs = [
          ["Health", String(printer.health_status || "unknown")],
          ["State", String(printer.printer_state || "unknown")],
          ["Keepalive", printer.keepalive_needed ? "needed" : "not due"],
          ["Template", String(printer.template || "n/a")],
          ["Cadence", String(printer.cadence_hours || "n/a") + "h"],
          ["Last Keepalive", relativeTime(printer.last_keepalive_at)],
          ["Last Print", relativeTime(printer.last_print_at)],
          ["Next Due", relativeTime(printer.next_keepalive_due_at)]
        ];
        for (var s = 0; s < statPairs.length; s++) {
          var stat = document.createElement("div");
          stat.className = "stat";
          var sl = document.createElement("span");
          sl.className = "stat-label";
          sl.textContent = statPairs[s][0];
          var sv = document.createElement("span");
          sv.className = "stat-value";
          sv.textContent = statPairs[s][1];
          stat.appendChild(sl);
          stat.appendChild(sv);
          stats.appendChild(stat);
        }

        /* Controls */
        var controls = document.createElement("div");
        controls.className = "printer-card-controls";

        var enabledGroup = document.createElement("div");
        enabledGroup.className = "control-group";
        var enabledLabel = document.createElement("label");
        enabledLabel.textContent = "Enabled";
        var enabledInput = document.createElement("input");
        enabledInput.type = "checkbox";
        enabledInput.checked = Boolean(printer.enabled);
        enabledInput.style.cssText = "width:18px;height:18px;margin-top:2px;";
        enabledGroup.appendChild(enabledLabel);
        enabledGroup.appendChild(enabledInput);

        var cadenceGroup = document.createElement("div");
        cadenceGroup.className = "control-group";
        var cadenceLabel = document.createElement("label");
        cadenceLabel.textContent = "Cadence (h)";
        var cadenceInput = document.createElement("input");
        cadenceInput.type = "number";
        cadenceInput.min = "1";
        cadenceInput.max = "720";
        cadenceInput.value = String(printer.cadence_hours || 168);
        cadenceGroup.appendChild(cadenceLabel);
        cadenceGroup.appendChild(cadenceInput);

        var templateGroup = document.createElement("div");
        templateGroup.className = "control-group";
        var templateLabel = document.createElement("label");
        templateLabel.textContent = "Template";
        var templateSelect = document.createElement("select");
        for (var t = 0; t < templates.length; t++) {
          var opt = document.createElement("option");
          opt.value = templates[t];
          opt.textContent = templates[t];
          if (templates[t] === printer.template) opt.selected = true;
          templateSelect.appendChild(opt);
        }
        templateGroup.appendChild(templateLabel);
        templateGroup.appendChild(templateSelect);

        controls.appendChild(enabledGroup);
        controls.appendChild(cadenceGroup);
        controls.appendChild(templateGroup);

        /* Actions */
        var actions = document.createElement("div");
        actions.className = "printer-card-actions";

        var saveBtn = document.createElement("button");
        saveBtn.type = "button";
        saveBtn.className = "secondary";
        saveBtn.textContent = "Save Settings";

        var printBtn = document.createElement("button");
        printBtn.type = "button";
        printBtn.textContent = "Print If Needed";

        var forceBtn = document.createElement("button");
        forceBtn.type = "button";
        forceBtn.className = "danger";
        forceBtn.textContent = "Force Print";

        var pollBtn = document.createElement("button");
        pollBtn.type = "button";
        pollBtn.className = "neutral";
        pollBtn.textContent = "Poll Now";

        actions.appendChild(saveBtn);
        actions.appendChild(printBtn);
        actions.appendChild(forceBtn);
        actions.appendChild(pollBtn);

        /* Inline status */
        var inline = document.createElement("div");
        inline.className = "printer-card-status";

        /* Wire events */
        var printerPath = "printers/" + encodeURIComponent(String(printer.printer_id || ""));
        (function(path, inl, enI, caI, tmS) {
          saveBtn.addEventListener("click", async function() {
            inl.textContent = "Saving..."; inl.className = "printer-card-status";
            try {
              await requestJson(path + "/settings", { method: "POST", body: JSON.stringify({ enabled: Boolean(enI.checked), cadence_hours: Number(caI.value || 0), template: String(tmS.value || "") }) });
              inl.textContent = "Settings saved."; inl.className = "printer-card-status ok";
              await refreshAll(true);
            } catch (err) { inl.textContent = err.message || String(err); inl.className = "printer-card-status error"; }
          });
          printBtn.addEventListener("click", async function() {
            inl.textContent = "Submitting..."; inl.className = "printer-card-status";
            try {
              var res = await requestJson(path + "/print", { method: "POST", body: JSON.stringify({ template: String(tmS.value || ""), force: false }) });
              inl.textContent = res.skipped ? String(res.reason || "Skipped.") : "Print submitted.";
              inl.className = "printer-card-status ok";
              await refreshAll(true);
            } catch (err) { inl.textContent = err.message || String(err); inl.className = "printer-card-status error"; }
          });
          forceBtn.addEventListener("click", async function() {
            inl.textContent = "Force printing..."; inl.className = "printer-card-status";
            try {
              await requestJson(path + "/print", { method: "POST", body: JSON.stringify({ template: String(tmS.value || ""), force: true }) });
              inl.textContent = "Force print submitted."; inl.className = "printer-card-status ok";
              await refreshAll(true);
            } catch (err) { inl.textContent = err.message || String(err); inl.className = "printer-card-status error"; }
          });
          pollBtn.addEventListener("click", async function() {
            inl.textContent = "Polling..."; inl.className = "printer-card-status";
            try {
              await requestJson(path + "/poll", { method: "POST", body: "{}" });
              inl.textContent = "Polled."; inl.className = "printer-card-status ok";
              await refreshAll(true);
            } catch (err) { inl.textContent = err.message || String(err); inl.className = "printer-card-status error"; }
          });
        })(printerPath, inline, enabledInput, cadenceInput, templateSelect);

        card.appendChild(head);
        card.appendChild(stats);
        card.appendChild(controls);
        card.appendChild(actions);
        card.appendChild(inline);
        grid.appendChild(card);
      }
    }

    /* ===== DISCOVERY ===== */
    function renderDiscovery(payload) {
      /* Summary */
      var summaryEl = document.getElementById("discoverySummary");
      summaryEl.replaceChildren();
      var sData = [
        ["Enabled", payload.enabled ? "Yes" : "No"],
        ["Last Scan", displayDate(payload.last_scan_at)],
        ["Duration", String(payload.last_scan_duration_seconds || 0) + "s"],
        ["Candidates", String(payload.printer_count || 0)],
        ["Last Error", payload.last_error || "None"]
      ];
      for (var i = 0; i < sData.length; i++) {
        var ds = document.createElement("div");
        ds.className = "disc-stat";
        var dsl = document.createElement("span");
        dsl.className = "disc-stat-label";
        dsl.textContent = sData[i][0];
        var dsv = document.createElement("span");
        dsv.className = "disc-stat-value";
        dsv.textContent = sData[i][1];
        ds.appendChild(dsl);
        ds.appendChild(dsv);
        summaryEl.appendChild(ds);
      }

      /* Table rows */
      var tbody = document.getElementById("discoveryRows");
      tbody.replaceChildren();
      var items = Array.isArray(payload.printers) ? payload.printers : [];
      for (var j = 0; j < items.length; j++) {
        var c = items[j];
        var tr = document.createElement("tr");
        var cols = [
          String(c.printer_name || c.service_name || "unknown"),
          String(c.uri || ""),
          String(c.printer_type_guess || "unknown"),
          c.reachable ? "yes" : "no",
          c.already_configured ? "yes" : "no"
        ];
        for (var k = 0; k < cols.length; k++) {
          var td = document.createElement("td");
          td.textContent = cols[k];
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
    }

    /* ===== CONFIG ===== */
    async function loadConfigEditor(showStatus) {
      if (showStatus) setConfigStatus("Loading...");
      try {
        var payload = await requestJson("config");
        var options = payload && payload.options && typeof payload.options === "object" ? payload.options : {};
        configEditor.value = JSON.stringify(options, null, 2);
        state.configLoaded = true;
        if (showStatus) setConfigStatus("Configuration loaded.");
      } catch (err) {
        setConfigStatus(err && err.message ? err.message : String(err), true);
      }
    }

    async function saveConfigEditor() {
      setConfigStatus("Saving...");
      var parsed;
      try { parsed = JSON.parse(String(configEditor.value || "{}")); }
      catch (err) { setConfigStatus("Invalid JSON: " + (err.message || String(err)), true); return; }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setConfigStatus("Configuration must be a JSON object.", true);
        return;
      }
      try {
        var res = await requestJson("config", { method: "POST", body: JSON.stringify({ options: parsed }) });
        setConfigStatus(res && res.message ? String(res.message) : "Saved.");
        await refreshAll(true);
      } catch (err) { setConfigStatus(err.message || String(err), true); }
    }

    async function restartAddon() {
      setConfigStatus("Requesting restart...");
      try {
        var res = await requestJson("actions/restart", { method: "POST", body: "{}" });
        setConfigStatus(res && res.message ? String(res.message) : "Restart requested.");
      } catch (err) { setConfigStatus(err.message || String(err), true); }
    }

    /* ===== REFRESH ALL ===== */
    async function refreshAll(silent) {
      if (state.refreshInFlight) return;
      state.refreshInFlight = true;
      try {
        var health = await requestJson("health");
        state.health = health;
        renderDashboard(health);
        renderPrinters(health);

        var discovery = await requestJson("discovery");
        renderDiscovery(discovery);

        if (!state.configLoaded) await loadConfigEditor(false);
      } catch (err) {
        var msg = err && err.message ? err.message : String(err);
        if (!silent) {
          document.getElementById("lastUpdated").textContent = "Error: " + msg;
        }
      } finally {
        state.refreshInFlight = false;
      }
    }

    /* ===== EVENT LISTENERS ===== */
    document.getElementById("saveTokenBtn").addEventListener("click", function() {
      state.authToken = String(authInput.value || "").trim();
      window.localStorage.setItem("pk_auth_token", state.authToken);
      authIndicator.classList.toggle("has-token", Boolean(state.authToken));
    });

    document.getElementById("clearTokenBtn").addEventListener("click", function() {
      state.authToken = "";
      authInput.value = "";
      window.localStorage.removeItem("pk_auth_token");
      authIndicator.classList.remove("has-token");
    });

    document.getElementById("refreshBtn").addEventListener("click", function() { refreshAll(false); });

    document.getElementById("loadConfigBtn").addEventListener("click", function() { loadConfigEditor(true); });
    document.getElementById("saveConfigBtn").addEventListener("click", function() { saveConfigEditor(); });
    document.getElementById("restartAddonBtn").addEventListener("click", function() { restartAddon(); });

    document.getElementById("rescanBtn").addEventListener("click", async function() {
      setDiscoveryStatus("Running discovery rescan...");
      try {
        var payload = await requestJson("discovery/rescan", { method: "POST", body: "{}" });
        renderDiscovery(payload);
        setDiscoveryStatus("Rescan complete at " + new Date().toLocaleTimeString());
      } catch (err) { setDiscoveryStatus(err.message || String(err), true); }
    });

    /* ===== INIT ===== */
    refreshAll(false);
    window.setInterval(function() { refreshAll(true); }, 60000);
  </script>
</body>
</html>

"""
    return (
        template.replace("__APP_NAME__", escape(APP_NAME))
        .replace("__APP_VERSION__", escape(APP_VERSION))
        .replace("__APP_URL__", escape(APP_URL))
    )


def _load_design_file(variant: str) -> str | None:
    """Load a design HTML file and perform template substitutions."""
    candidates = [
        Path(__file__).parent / "designs" / f"{variant}.html",
        Path("/app/designs") / f"{variant}.html",
    ]
    for filepath in candidates:
        if filepath.is_file():
            html = filepath.read_text(encoding="utf-8")
            return (
                html.replace("__APP_NAME__", escape(APP_NAME))
                .replace("__APP_VERSION__", escape(APP_VERSION))
                .replace("__APP_URL__", escape(APP_URL))
            )
    return None


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        log(format % args)

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_html(self, status: HTTPStatus, payload: str) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _is_authorized(self) -> bool:
        if not AUTH_TOKEN:
            return True
        auth_header = self.headers.get("Authorization", "")
        return auth_header == f"Bearer {AUTH_TOKEN}"

    def _read_json_body(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length", "")
        try:
            size = int(content_length)
        except (TypeError, ValueError):
            return {}
        if size <= 0:
            return {}
        try:
            raw = self.rfile.read(min(size, 65536))
            decoded = raw.decode("utf-8").strip()
            if not decoded:
                return {}
            parsed = json.loads(decoded)
            return parsed if isinstance(parsed, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _resolve_printer(self, printer_id: str | None) -> PrinterConfig | None:
        if printer_id:
            return PRINTERS_BY_ID.get(printer_id)
        return PRINTERS[0] if PRINTERS else None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in {"", "/", "/index.html"}:
            # Check cookie for design preference and serve that design
            cookie_header = self.headers.get("Cookie", "")
            design_choice = ""
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith("pk_design="):
                    design_choice = part.split("=", 1)[1].strip()
                    break
            if design_choice in {"v1", "v2", "v3", "v4", "v5"}:
                design_html = _load_design_file(design_choice)
                if design_html is not None:
                    self._write_html(HTTPStatus.OK, design_html)
                    return
            self._write_html(HTTPStatus.OK, ui_dashboard_html())
            return

        if path == "/ui":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "./")
            self.end_headers()
            return

        if path == "/ui/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "../")
            self.end_headers()
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if path.startswith("/design/"):
            variant = path.split("/design/", 1)[1].rstrip("/")
            if variant in {"v1", "v2", "v3", "v4", "v5"}:
                design_html = _load_design_file(variant)
                if design_html is not None:
                    self._write_html(HTTPStatus.OK, design_html)
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Design file {variant} not found"})
            else:
                designs = {"v1": "Bento Grid", "v2": "Glassmorphism", "v3": "Neubrutalist", "v4": "Cinematic Dark", "v5": "Home Assistant"}
                items = "".join(f'<li style="margin:8px 0"><a href="/design/{k}" style="font-size:18px">{k} &mdash; {v}</a></li>' for k, v in designs.items())
                self._write_html(HTTPStatus.OK, f'<html><head><title>Design Picker</title></head><body style="font-family:system-ui;max-width:600px;margin:40px auto;padding:20px"><h2>Choose a Design</h2><ul style="list-style:none;padding:0">{items}</ul><p style="color:#888;font-size:14px;margin-top:24px">Your choice is saved automatically. Switch anytime from the dropdown in the top bar.</p></body></html>')
            return

        if path == "/config":
            if not self._is_authorized():
                self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
                return
            try:
                options_payload = load_options()
            except RuntimeError as exc:
                self._write_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                return
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "options": options_payload,
                    "restart_supported": bool(SUPERVISOR_TOKEN),
                    "message": "Configuration changes require restart to apply.",
                },
            )
            return

        if path == "/health":
            self._write_json(HTTPStatus.OK, global_payload())
            return

        if path == "/templates":
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "templates": list(SUPPORTED_TEMPLATES),
                    "maintenance_guidance": MAINTENANCE_GUIDANCE,
                },
            )
            return

        if path == "/guidance":
            self._write_json(HTTPStatus.OK, {"ok": True, "maintenance_guidance": MAINTENANCE_GUIDANCE})
            return

        if path == "/discovery":
            force = _parse_discovery_force_flag(query)
            payload = get_discovery_payload(force=force)
            status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_GATEWAY
            self._write_json(status, payload)
            return

        if path == "/printers":
            self._write_json(HTTPStatus.OK, {"ok": True, "printers": [build_printer_payload(p) for p in PRINTERS]})
            return

        if path.startswith("/printers/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2:
                printer = PRINTERS_BY_ID.get(parts[1])
                if not printer:
                    self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown printer id"})
                    return
                self._write_json(HTTPStatus.OK, {"ok": True, "printer": build_printer_payload(printer)})
                return

            if len(parts) == 3 and parts[2] == "card":
                printer = PRINTERS_BY_ID.get(parts[1])
                if not printer:
                    self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown printer id"})
                    return
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "printer_id": printer.printer_id,
                        "lovelace_yaml": generate_lovelace_card_yaml(printer),
                    },
                )
                return

        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if not self._is_authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return

        body = self._read_json_body()

        if path == "/config":
            candidate: dict[str, Any] | None = None
            if isinstance(body.get("options"), dict):
                candidate = body.get("options")
            elif body:
                candidate = body

            if not isinstance(candidate, dict):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Expected JSON object or {\"options\": {...}} payload."},
                )
                return

            valid, reason = validate_options_payload(candidate)
            if not valid:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": reason})
                return

            try:
                save_options(candidate)
            except OSError as exc:
                self._write_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": f"Unable to save options: {exc}"})
                return

            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "Configuration saved to /data/options.json. Restart the add-on/service to apply changes.",
                    "restart_supported": bool(SUPERVISOR_TOKEN),
                },
            )
            return

        if path == "/actions/restart":
            ok, message = supervisor_restart_self()
            status = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
            self._write_json(status, {"ok": ok, "message": message, "restart_supported": bool(SUPERVISOR_TOKEN)})
            return

        if path == "/discovery/rescan":
            payload = get_discovery_payload(force=True)
            status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_GATEWAY
            self._write_json(status, payload)
            return

        if path == "/print":
            printer_id = ""
            if isinstance(query.get("printer_id"), list) and query["printer_id"]:
                printer_id = str(query["printer_id"][0]).strip()
            elif isinstance(body.get("printer_id"), str):
                printer_id = str(body.get("printer_id", "")).strip()

            printer = self._resolve_printer(printer_id or None)
            if not printer:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown printer"})
                return

            template = ""
            if isinstance(query.get("template"), list) and query["template"]:
                template = str(query["template"][0]).strip().lower()
            elif isinstance(body.get("template"), str):
                template = str(body.get("template", "")).strip().lower()

            force = False
            if isinstance(query.get("force"), list) and query["force"]:
                force = str(query["force"][0]).strip().lower() in {"1", "true", "yes", "on"}
            elif "force" in body:
                parsed_force = bool_from_any(body.get("force"))
                force = bool(parsed_force) if parsed_force is not None else False

            result = run_keepalive_print(printer, template_override=template or None, source="api", only_if_needed=not force)
            publish_printer_state_if_enabled(printer)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_GATEWAY
            self._write_json(status, result)
            return

        if path.startswith("/printers/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) < 3:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})
                return

            printer = PRINTERS_BY_ID.get(parts[1])
            if not printer:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown printer"})
                return

            action = parts[2]
            if action == "print":
                template = ""
                if isinstance(body.get("template"), str):
                    template = str(body.get("template")).strip().lower()

                parsed_force = bool_from_any(body.get("force", False))
                force = bool(parsed_force) if parsed_force is not None else False
                result = run_keepalive_print(printer, template_override=template or None, source="api", only_if_needed=not force)
                publish_printer_state_if_enabled(printer)
                status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_GATEWAY
                self._write_json(status, result)
                return

            if action == "settings":
                updates: dict[str, Any] = {}
                for field in ("template", "cadence_hours", "enabled"):
                    if field in body:
                        updates[field] = body[field]
                result = update_printer_setting(printer, updates)
                publish_printer_state_if_enabled(printer)
                self._write_json(HTTPStatus.OK, result)
                return

            if action == "poll":
                result = poll_printer(printer, force=True)
                publish_printer_state_if_enabled(printer)
                self._write_json(HTTPStatus.OK, {"ok": True, "printer": result})
                return

        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})


def main() -> None:
    log(f"Starting {APP_NAME} API server.")
    log(f"Version: {APP_VERSION}")
    log(f"Configured printers: {len(PRINTERS)}")
    for printer in PRINTERS:
        log(
            f"Printer {printer.printer_id}: name='{printer.name}', type={printer.printer_type}, "
            f"cadence={printer.cadence_hours}h, template={printer.template}"
        )

    if SUPERVISOR_TOKEN:
        log("Home Assistant API mode: Supervisor token.")
    elif HASS_API_BASE != DEFAULT_SUPERVISOR_API_BASE:
        if HASS_AUTH_TOKEN:
            log(f"Home Assistant API mode: direct ({HASS_API_BASE}) with token.")
        else:
            log(f"Home Assistant API mode: direct ({HASS_API_BASE}) without token.")
    else:
        log("Home Assistant API mode: disabled (no Supervisor token or direct HA URL configured).")
    log(f"Printed QR target URL: {ADDON_PAGE_URL}")

    log(f"Auto-print enabled: {AUTO_PRINT_ENABLED}")
    log(f"Status poll interval: {STATUS_POLL_INTERVAL_SECONDS} seconds")
    log(
        "Printer discovery: "
        f"enabled={DISCOVERY_ENABLED}, interval={DISCOVERY_INTERVAL_SECONDS}s, "
        f"timeout={DISCOVERY_TIMEOUT_SECONDS}s, include_ipps={DISCOVERY_INCLUDE_IPPS}"
    )

    if DISCOVERY_ENABLED:
        discovery = get_discovery_payload(force=True)
        if discovery.get("ok"):
            log(f"Initial discovery found {discovery.get('printer_count', 0)} candidate(s).")
        else:
            log(f"Initial discovery failed: {discovery.get('last_error', 'unknown error')}")

    MQTT_BRIDGE.start()

    scheduler = threading.Thread(target=scheduler_loop, name="scheduler", daemon=True)
    scheduler.start()
    log("Scheduler thread started.")

    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), RequestHandler)
    try:
        server.serve_forever()
    finally:
        MQTT_BRIDGE.stop()
        server.server_close()


if __name__ == "__main__":
    main()
