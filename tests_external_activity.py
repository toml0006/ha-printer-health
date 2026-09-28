import json
import sys
import tempfile
import types
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


def _install_test_stubs() -> None:
    try:
        import paho.mqtt.client  # noqa: F401
    except Exception:
        paho_mod = types.ModuleType("paho")
        mqtt_pkg = types.ModuleType("paho.mqtt")
        mqtt_client_mod = types.ModuleType("paho.mqtt.client")

        class DummyMQTTMessage:  # pragma: no cover - simple test stub
            topic = b""
            payload = b""

        class DummyClient:  # pragma: no cover - simple test stub
            def __init__(self, *args, **kwargs):
                self.on_connect = None
                self.on_disconnect = None
                self.on_message = None

            def username_pw_set(self, *args, **kwargs):
                return None

            def tls_set(self, *args, **kwargs):
                return None

            def will_set(self, *args, **kwargs):
                return None

            def connect_async(self, *args, **kwargs):
                return None

            def loop_start(self):
                return None

            def loop_stop(self):
                return None

            def disconnect(self):
                return None

            def subscribe(self, *args, **kwargs):
                return (0, 0)

            def publish(self, *args, **kwargs):
                return (0,)

        mqtt_client_mod.Client = DummyClient
        mqtt_client_mod.MQTTMessage = DummyMQTTMessage
        mqtt_client_mod.connack_string = lambda rc: f"rc={rc}"
        mqtt_client_mod.error_string = lambda rc: f"rc={rc}"

        sys.modules["paho"] = paho_mod
        sys.modules["paho.mqtt"] = mqtt_pkg
        sys.modules["paho.mqtt.client"] = mqtt_client_mod

    try:
        import zeroconf  # noqa: F401
    except Exception:
        zeroconf_mod = types.ModuleType("zeroconf")

        class DummyZeroconf:  # pragma: no cover - simple test stub
            def __init__(self, *args, **kwargs):
                pass

            def close(self):
                return None

        class DummyServiceBrowser:  # pragma: no cover - simple test stub
            def __init__(self, *args, **kwargs):
                pass

        class DummyServiceListener:  # pragma: no cover - simple test stub
            pass

        zeroconf_mod.Zeroconf = DummyZeroconf
        zeroconf_mod.ServiceBrowser = DummyServiceBrowser
        zeroconf_mod.ServiceListener = DummyServiceListener
        sys.modules["zeroconf"] = zeroconf_mod


def _load_app_module():
    _install_test_stubs()
    app_path = Path(__file__).resolve().parent / "printer_keepalive" / "app.py"
    test_data_dir = Path(tempfile.gettempdir()) / "ha_printer_keepalive_testdata"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    options_path = test_data_dir / "options.json"
    state_path = test_data_dir / "state.json"
    options_path.write_text(
        json.dumps(
            {
                "printers": [],
                "mqtt": {"enabled": False},
                "discovery_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    if not state_path.exists():
        state_path.write_text(json.dumps({"version": 1, "printers": {}}), encoding="utf-8")

    source = app_path.read_text(encoding="utf-8")
    source = source.replace('OPTIONS_PATH = Path("/data/options.json")', f'OPTIONS_PATH = Path(r"{options_path}")')
    source = source.replace('STATE_PATH = Path("/data/state.json")', f'STATE_PATH = Path(r"{state_path}")')

    module = types.ModuleType("printer_keepalive_app")
    module.__file__ = str(app_path)
    sys.modules[module.__name__] = module
    exec(compile(source, str(app_path), "exec"), module.__dict__)
    return module


app = _load_app_module()


class ExternalDetectionTests(unittest.TestCase):
    def test_extracts_completed_job_ids_from_ipptool_output(self):
        output = """
        job-id (integer) = 37
        job-state (enum) = completed
        job-id (integer) = 36
        job-id (integer) = 37
        """
        self.assertEqual(app._extract_ipp_integer_attributes(output, "job-id"), [37, 36])

    def test_high_confidence_when_impressions_increase(self):
        external, delta, hint, confidence, reason = app.detect_external_activity(
            previous_impressions=100,
            impressions=103,
            previous_media_sheets=50,
            media_sheets=51,
            previous_printer_state="idle",
            current_printer_state="idle",
            previous_queue_count=0,
            current_queue_count=0,
            self_keepalive_exclusion_active=False,
        )
        self.assertTrue(external)
        self.assertEqual(delta, 3)
        self.assertFalse(hint)
        self.assertEqual(confidence, "high")
        self.assertIn("printer-impressions-completed", reason)

    def test_medium_confidence_when_media_sheets_increase(self):
        external, delta, hint, confidence, reason = app.detect_external_activity(
            previous_impressions=100,
            impressions=100,
            previous_media_sheets=50,
            media_sheets=54,
            previous_printer_state="idle",
            current_printer_state="idle",
            previous_queue_count=0,
            current_queue_count=0,
            self_keepalive_exclusion_active=False,
        )
        self.assertTrue(external)
        self.assertEqual(delta, 4)
        self.assertFalse(hint)
        self.assertEqual(confidence, "medium")
        self.assertIn("printer-media-sheets-completed", reason)

    def test_low_hint_for_processing_to_idle_queue_drop(self):
        external, delta, hint, confidence, reason = app.detect_external_activity(
            previous_impressions=100,
            impressions=100,
            previous_media_sheets=50,
            media_sheets=50,
            previous_printer_state="processing",
            current_printer_state="idle",
            previous_queue_count=3,
            current_queue_count=0,
            self_keepalive_exclusion_active=False,
        )
        self.assertFalse(external)
        self.assertEqual(delta, 0)
        self.assertTrue(hint)
        self.assertEqual(confidence, "low")
        self.assertIn("processing->idle", reason)

    def test_no_external_event_on_first_baseline_poll(self):
        external, delta, hint, confidence, reason = app.detect_external_activity(
            previous_impressions=None,
            impressions=7,
            previous_media_sheets=None,
            media_sheets=11,
            previous_printer_state="unknown",
            current_printer_state="idle",
            previous_queue_count=None,
            current_queue_count=0,
            self_keepalive_exclusion_active=False,
        )
        self.assertFalse(external)
        self.assertEqual(delta, 0)
        self.assertFalse(hint)
        self.assertEqual(confidence, "none")
        self.assertEqual(reason, "")

    def test_counter_reset_does_not_generate_external_event(self):
        external, delta, hint, confidence, reason = app.detect_external_activity(
            previous_impressions=120,
            impressions=12,
            previous_media_sheets=200,
            media_sheets=3,
            previous_printer_state="idle",
            current_printer_state="idle",
            previous_queue_count=0,
            current_queue_count=0,
            self_keepalive_exclusion_active=False,
        )
        self.assertFalse(external)
        self.assertEqual(delta, 0)
        self.assertFalse(hint)
        self.assertEqual(confidence, "none")
        self.assertEqual(reason, "")

    def test_no_medium_or_low_activity_during_self_keepalive_exclusion(self):
        medium = app.detect_external_activity(
            previous_impressions=100,
            impressions=100,
            previous_media_sheets=8,
            media_sheets=9,
            previous_printer_state="idle",
            current_printer_state="idle",
            previous_queue_count=0,
            current_queue_count=0,
            self_keepalive_exclusion_active=True,
        )
        low = app.detect_external_activity(
            previous_impressions=100,
            impressions=100,
            previous_media_sheets=8,
            media_sheets=8,
            previous_printer_state="processing",
            current_printer_state="idle",
            previous_queue_count=2,
            current_queue_count=0,
            self_keepalive_exclusion_active=True,
        )
        self.assertEqual(medium, (False, 0, False, "none", ""))
        self.assertEqual(low, (False, 0, False, "none", ""))


class KeepaliveDeferralTests(unittest.TestCase):
    def setUp(self):
        self.old_detection_enabled = app.EXTERNAL_ACTIVITY_DETECTION_ENABLED
        self.old_hint_grace = app.EXTERNAL_ACTIVITY_HINT_GRACE_MINUTES
        app.EXTERNAL_ACTIVITY_DETECTION_ENABLED = True
        app.EXTERNAL_ACTIVITY_HINT_GRACE_MINUTES = 120

    def tearDown(self):
        app.EXTERNAL_ACTIVITY_DETECTION_ENABLED = self.old_detection_enabled
        app.EXTERNAL_ACTIVITY_HINT_GRACE_MINUTES = self.old_hint_grace

    def _printer(self):
        return app.PrinterConfig(
            printer_id="test-printer",
            name="Test Printer",
            printer_uri="ipp://127.0.0.1/ipp/print",
            printer_type="inkjet",
            enabled=True,
            cadence_hours=24,
            template="home_summary",
            weather_entity="",
            entity_ids=[],
            title="",
            footer="",
        )

    def _base_state(self, now):
        state = json.loads(json.dumps(app.DEFAULT_PRINTER_STATE))
        state["history_anchor_at"] = app.iso_utc(now - timedelta(hours=48))
        return state

    def test_due_keepalive_proceeds_without_hint(self):
        now = app.utc_now()
        needed, _last, _due_at, deferred = app.compute_need_for_keepalive(
            self._printer(),
            self._base_state(now),
            now,
        )
        self.assertTrue(needed)
        self.assertFalse(deferred)

    def test_due_keepalive_is_deferred_when_recent_low_hint_exists(self):
        now = app.utc_now()
        state = self._base_state(now)
        state["last_activity_hint_at"] = app.iso_utc(now - timedelta(minutes=20))
        state["last_activity_confidence"] = "low"

        needed, _last, _due_at, deferred = app.compute_need_for_keepalive(
            self._printer(),
            state,
            now,
        )
        self.assertFalse(needed)
        self.assertTrue(deferred)

    def test_deferred_keepalive_becomes_due_after_grace_expires(self):
        now = app.utc_now()
        state = self._base_state(now)
        state["last_activity_hint_at"] = app.iso_utc(now - timedelta(minutes=app.EXTERNAL_ACTIVITY_HINT_GRACE_MINUTES + 1))
        state["last_activity_confidence"] = "low"

        needed, _last, _due_at, deferred = app.compute_need_for_keepalive(
            self._printer(),
            state,
            now,
        )
        self.assertTrue(needed)
        self.assertFalse(deferred)


class CompletedJobFallbackTests(unittest.TestCase):
    def _printer(self):
        return app.PrinterConfig(
            printer_id="completed-jobs-printer",
            name="Completed Jobs Printer",
            printer_uri="ipp://127.0.0.1/ipp/print",
            printer_type="inkjet",
            enabled=True,
            cadence_hours=24,
            template="home_summary",
            weather_entity="",
            entity_ids=[],
            title="",
            footer="",
        )

    def _seed_state(self, *, self_job_ids=None):
        printer = self._printer()
        with app.STATE_LOCK:
            state = app.ensure_printer_state_locked(printer.printer_id)
            state.clear()
            state.update(json.loads(json.dumps(app.DEFAULT_PRINTER_STATE)))
            state["history_anchor_at"] = app.iso_utc(app.utc_now() - timedelta(hours=1))
            state["completed_jobs_baselined"] = True
            state["last_seen_completed_job_ids"] = [36]
            state["self_keepalive_job_ids"] = list(self_job_ids or [])
            state["printer_up_time_seconds"] = 100
            app.save_state_locked()
        return printer

    def test_new_completed_job_resets_external_print_time(self):
        printer = self._seed_state()
        attrs = {
            "printer-state": "idle",
            "queued-job-count": 0,
            "printer-up-time": 101,
        }
        with (
            patch.object(app, "query_ipp_attributes", return_value=(attrs, None)),
            patch.object(app, "query_completed_job_ids", return_value=([37, 36], None)),
            patch.object(app, "match_ha_ipp_device", return_value={}),
        ):
            payload = app.poll_printer(printer, force=True)

        self.assertEqual(payload["external_print_count"], 1)
        self.assertTrue(payload["last_external_print_at"])
        self.assertEqual(payload["last_activity_confidence"], "high")
        self.assertIn("37", payload["last_activity_hint_reason"])
        self.assertEqual(payload["last_keepalive_decision"], "skipped_recent_print")
        self.assertTrue(payload["last_keepalive_was_skipped_for_recent_print"])
        self.assertEqual(payload["keepalive_skip_count"], 1)
        self.assertIn("Cadence reset to 24h", payload["last_keepalive_skip_reason"])

        attrs["printer-up-time"] = 102
        with (
            patch.object(app, "query_ipp_attributes", return_value=(attrs, None)),
            patch.object(app, "query_completed_job_ids", return_value=([37, 36], None)),
            patch.object(app, "match_ha_ipp_device", return_value={}),
        ):
            unchanged = app.poll_printer(printer, force=True)
        self.assertEqual(unchanged["keepalive_skip_count"], 1)

    def test_own_completed_job_is_excluded(self):
        printer = self._seed_state(self_job_ids=[37])
        attrs = {
            "printer-state": "idle",
            "queued-job-count": 0,
            "printer-up-time": 101,
        }
        with (
            patch.object(app, "query_ipp_attributes", return_value=(attrs, None)),
            patch.object(app, "query_completed_job_ids", return_value=([37, 36], None)),
            patch.object(app, "match_ha_ipp_device", return_value={}),
        ):
            payload = app.poll_printer(printer, force=True)

        self.assertEqual(payload["external_print_count"], 0)
        self.assertFalse(payload["last_external_print_at"])
        self.assertEqual(payload["last_keepalive_decision"], "never")
        with app.STATE_LOCK:
            state = app.ensure_printer_state_locked(printer.printer_id)
            self.assertEqual(state["self_keepalive_job_ids"], [])

class PayloadSmokeTests(unittest.TestCase):
    def _printer(self):
        return app.PrinterConfig(
            printer_id="smoke-printer",
            name="Smoke Printer",
            printer_uri="ipp://127.0.0.1/ipp/print",
            printer_type="inkjet",
            enabled=True,
            cadence_hours=24,
            template="home_summary",
            weather_entity="",
            entity_ids=[],
            title="",
            footer="",
        )

    def test_health_and_printer_payload_include_new_activity_fields(self):
        printer = self._printer()
        now = app.utc_now()

        with app.STATE_LOCK:
            state = app.ensure_printer_state_locked(printer.printer_id)
            state.clear()
            state.update(json.loads(json.dumps(app.DEFAULT_PRINTER_STATE)))
            state["history_anchor_at"] = app.iso_utc(now - timedelta(hours=1))
            app.save_state_locked()

        original_printers = list(app.PRINTERS)
        original_map = dict(app.PRINTERS_BY_ID)
        app.PRINTERS[:] = [printer]
        app.PRINTERS_BY_ID.clear()
        app.PRINTERS_BY_ID[printer.printer_id] = printer

        try:
            payload = app.build_printer_payload(printer, now)
            self.assertIn("last_activity_hint_at", payload)
            self.assertIn("last_activity_hint_reason", payload)
            self.assertIn("last_activity_confidence", payload)
            self.assertIn("keepalive_deferred_by_activity_hint", payload)
            self.assertIn("last_keepalive_decision", payload)
            self.assertIn("last_keepalive_skip_reason", payload)
            self.assertIn("keepalive_skip_count", payload)

            health = app.global_payload()
            self.assertIn("printers", health)
            self.assertEqual(len(health["printers"]), 1)
            self.assertIn("last_activity_confidence", health["printers"][0])
        finally:
            app.PRINTERS[:] = original_printers
            app.PRINTERS_BY_ID.clear()
            app.PRINTERS_BY_ID.update(original_map)

    def test_mqtt_state_payload_includes_new_activity_fields(self):
        printer = self._printer()
        now = app.utc_now()

        with app.STATE_LOCK:
            state = app.ensure_printer_state_locked(printer.printer_id)
            state["history_anchor_at"] = app.iso_utc(now - timedelta(hours=1))
            app.save_state_locked()

        published: dict[str, str] = {}

        class FakeClient:
            def publish(self, topic, payload, retain=False):
                published["topic"] = topic
                published["payload"] = payload
                return (0,)

        bridge = app.MqttBridge(app.MQTT_CONFIG)
        bridge.started = True
        bridge.connected = True
        bridge.client = FakeClient()

        bridge.publish_printer_state(printer)

        self.assertIn("payload", published)
        data = json.loads(published["payload"])
        self.assertIn("last_activity_hint_at", data)
        self.assertIn("last_activity_hint_reason", data)
        self.assertIn("last_activity_confidence", data)
        self.assertIn("keepalive_deferred_by_activity_hint", data)
        self.assertIn("last_keepalive_decision", data)
        self.assertIn("last_keepalive_skip_reason", data)
        self.assertIn("keepalive_skip_count", data)

    def test_mqtt_discovery_includes_cadence_and_skip_entities(self):
        printer = self._printer()
        published: dict[str, str] = {}

        class FakeClient:
            def publish(self, topic, payload, retain=False):
                published[topic] = payload
                return (0,)

        bridge = app.MqttBridge(app.MQTT_CONFIG)
        bridge.started = True
        bridge.connected = True
        bridge.client = FakeClient()

        with patch.object(app, "match_ha_ipp_device", return_value={}):
            bridge._publish_discovery_for_printer(printer)

        topic_prefix = app.MQTT_CONFIG.discovery_prefix
        object_prefix = f"printer_keepalive_{printer.printer_id}"
        expected_suffixes = (
            f"number/{object_prefix}_cadence_hours/config",
            f"sensor/{object_prefix}_last_print/config",
            f"sensor/{object_prefix}_last_keepalive_decision/config",
            f"sensor/{object_prefix}_last_keepalive_skip_reason/config",
            f"sensor/{object_prefix}_keepalive_skip_count/config",
            f"binary_sensor/{object_prefix}_keepalive_skipped_recent_print/config",
        )
        for suffix in expected_suffixes:
            self.assertIn(f"{topic_prefix}/{suffix}", published)


class MqttConfigTests(unittest.TestCase):
    SERVICE = {
        "host": "core-mosquitto",
        "port": 1884,
        "username": "addons",
        "password": "service-secret",
        "ssl": False,
        "protocol": "3.1.1",
    }

    def _parse(self, mqtt_opts):
        with patch.dict(app.os.environ, {"SUPERVISOR_TOKEN": "token"}), patch.object(
            app, "supervisor_get_service", return_value=dict(self.SERVICE)
        ) as get_service:
            cfg = app.parse_mqtt_config({"mqtt": mqtt_opts})
        return cfg, get_service

    def test_no_host_uses_supervisor_service_discovery(self):
        cfg, get_service = self._parse({"enabled": True})
        get_service.assert_called_once_with("mqtt")
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.host, "core-mosquitto")
        self.assertEqual(cfg.port, 1884)
        self.assertEqual(cfg.username, "addons")
        self.assertEqual(cfg.password, "service-secret")
        self.assertFalse(cfg.tls)

    def test_empty_host_uses_supervisor_service_discovery(self):
        cfg, _ = self._parse({"enabled": True, "host": "", "port": 1883})
        self.assertEqual((cfg.host, cfg.port, cfg.username), ("core-mosquitto", 1884, "addons"))

    def test_external_host_skips_supervisor_defaults(self):
        cfg, _ = self._parse(
            {
                "enabled": True,
                "host": "10.0.10.32",
                "port": 8883,
                "username": "printer",
                "password": "own-secret",
                "tls": True,
            }
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.host, "10.0.10.32")
        self.assertEqual(cfg.port, 8883)
        self.assertEqual(cfg.username, "printer")
        self.assertEqual(cfg.password, "own-secret")
        self.assertTrue(cfg.tls)

    def test_schema_declares_optional_connection_options(self):
        # app.py reads these keys; if the add-on schema omits them, Supervisor
        # drops the values and an external broker can never be configured.
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("PyYAML not installed")
        config_path = Path(__file__).resolve().parent / "printer_keepalive" / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        schema = config["schema"]["mqtt"]
        for key in ("host", "port", "username", "password", "tls"):
            self.assertIn(key, schema)
            self.assertTrue(schema[key].endswith("?"), f"mqtt.{key} must be optional")
            self.assertNotIn(key, config["options"]["mqtt"])

    def test_external_host_without_port_or_auth_defaults_to_1883_no_auth(self):
        cfg, _ = self._parse({"enabled": True, "host": "10.0.10.32"})
        self.assertEqual(cfg.host, "10.0.10.32")
        self.assertEqual(cfg.port, 1883)
        self.assertEqual(cfg.username, "")
        self.assertEqual(cfg.password, "")
        self.assertFalse(cfg.tls)


if __name__ == "__main__":
    unittest.main()
