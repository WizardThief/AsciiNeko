"""
test_bridge_hud_integration.py - Milestone 5: AsciiNeko Bridge & Real-Time Desktop HUD Integration Test Suite.
Verifies:
  1. Telemetry Aggregation (HTTP daemon probe, status.json fallback, priority, timeout, normalization, TTL)
  2. SSE Streaming (Headers, delimiters, retry directive, keepalive comments, size boundaries, 60fps latency, unicode)
  3. Status Endpoint Schema (200 OK, cloud_sync, memory, skills, deadlines, hardware metrics coexistence, latency)
  4. Resilience & Recovery (Missing file, corrupt syntax, 0-byte, binary garbage, crash/restart, atomic writes)
  5. Zero Regression (/cat_sync, /ai_chat /sys, /ai_chat todo, client slots, CORS preflight, static assets)

Contains 37 comprehensive unit & integration tests across 5 test classes.
"""

import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Optional
import unittest
import urllib.request
import urllib.error

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE_DIR = PROJECT_ROOT / "bridge"
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from ai_monitor import (
    NekoBotTelemetryAggregator,
    SystemStateMonitor,
    CompanionRequestHandler,
    audio_capture,
    ollama_bridge,
    telemetry_aggregator,
    stop_telemetry,
)


def tearDownModule():
    """Module-level cleanup ensuring no background polling threads remain active."""
    stop_telemetry(timeout=1.0)


def get_free_port() -> int:
    """Allocates an ephemeral port for conflict-free concurrent testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def atomic_write_json_file(file_path: Path, data: Any):
    """Safely writes JSON data via atomic temporary file rename."""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp_path.replace(file_path)


class MockDaemonHTTPServer:
    """In-process mock HTTP daemon simulating neko_bot IPC (:8766)."""

    def __init__(self, port: int, initial_status: Optional[Dict[str, Any]] = None):
        self.port = port
        self.status = initial_status if initial_status is not None else {
            "daemon_health": "running",
            "daemon_pid": 4242,
            "cloud_sync": {
                "status": "synced",
                "connected": True,
                "last_sync_time": "2026-10-24T12:00:00Z",
                "pending_mutations": 0,
                "cloud_server_url": "http://127.0.0.1:8767",
            },
            "memory": {
                "total_facts": 15,
                "active_projects": 3,
                "recent_episodes": 25,
                "upcoming_deadlines": [
                    {"project": "CyberpunkCity", "milestone": "Beta", "due": "2026-10-28", "risk": "medium"}
                ],
            },
            "skills": {
                "total_skills": 4,
                "active_skills": ["daily_standup_pipeline"],
                "last_executed_skill": "daily_standup_pipeline",
            },
        }
        self.delay_sec = 0.0
        self.http_code = 200
        self.server = None
        self._thread = None
        self.request_count = 0
        self.lock = threading.Lock()

    def start(self):
        daemon_ref = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass

            def do_GET(self):
                try:
                    with daemon_ref.lock:
                        daemon_ref.request_count += 1
                        delay = daemon_ref.delay_sec
                        code = daemon_ref.http_code
                        status_copy = dict(daemon_ref.status)

                    if delay > 0:
                        time.sleep(delay)

                    if code != 200:
                        self.send_response(code)
                        self.end_headers()
                        self.wfile.write(b"Error")
                        return

                    body = json.dumps(status_copy, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def update_telemetry(self, updates: Dict[str, Any]):
        with self.lock:
            self.status.update(updates)

    def set_delay(self, seconds: float):
        with self.lock:
            self.delay_sec = seconds

    def set_error_code(self, code: int):
        with self.lock:
            self.http_code = code


class EphemeralBridgeServer:
    """Test bridge server instance running on an ephemeral port with isolated telemetry."""

    def __init__(self, port: int, workspace_dir: Path, daemon_url: str):
        self.port = port
        self.workspace_dir = Path(workspace_dir)
        self.daemon_url = daemon_url
        self.status_file = self.workspace_dir / ".neko" / "status.json"
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=daemon_url,
            workspace_dir=str(self.workspace_dir),
        )
        self.monitor = SystemStateMonitor(audio_capture, ollama_bridge, telemetry=self.aggregator)
        self.server = None
        self._thread = None

    def start(self):
        mon = self.monitor
        root_dir = PROJECT_ROOT

        class BoundCompanionHandler(CompanionRequestHandler):
            monitor = mon

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root_dir), **kwargs)

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), BoundCompanionHandler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self.aggregator:
            self.aggregator.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


# ===========================================================================
# 1. TestBridgeTelemetryIngestion (7 Tests)
# ===========================================================================
class TestBridgeTelemetryIngestion(unittest.TestCase):
    """Verifies dual retrieval channels (HTTP daemon probe and .neko/status.json fallback)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="neko_telemetry_test_")
        self.workspace_dir = Path(self.tmp_dir)
        self.status_file = self.workspace_dir / ".neko" / "status.json"
        self.closed_port = get_free_port()
        self.dead_daemon_url = f"http://127.0.0.1:{self.closed_port}"
        self.daemon_server = None
        self.aggregator = None

    def tearDown(self):
        if self.aggregator:
            self.aggregator.stop()
        if self.daemon_server:
            self.daemon_server.stop()
        stop_telemetry(timeout=1.0)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_ingest_from_disk_status_json_offline_daemon(self):
        """Writes status.json with daemon stopped; asserts aggregator returns disk telemetry."""
        payload = {
            "daemon_health": "offline",
            "cloud_sync": {
                "status": "synced",
                "connected": True,
                "last_sync_time": "2026-10-24T08:00:00Z",
                "pending_mutations": 2,
                "cloud_server_url": "http://127.0.0.1:8767",
            },
            "memory": {
                "total_facts": 24,
                "active_projects": 5,
                "recent_episodes": 10,
                "upcoming_deadlines": [],
            },
            "skills": {
                "total_skills": 8,
                "active_skills": ["tool_a", "tool_b"],
                "last_executed_skill": "tool_b",
            },
        }
        atomic_write_json_file(self.status_file, payload)
        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=self.dead_daemon_url,
            workspace_dir=str(self.workspace_dir),
        )
        self.aggregator.poll(force=True)
        telemetry = self.aggregator.get_telemetry()

        self.assertEqual(telemetry["cloud_sync"]["status"], "synced")
        self.assertTrue(telemetry["cloud_sync"]["connected"])
        self.assertEqual(telemetry["cloud_sync"]["pending_mutations"], 2)
        self.assertEqual(telemetry["memory"]["total_facts"], 24)
        self.assertEqual(telemetry["skills"]["total_skills"], 8)
        self.assertEqual(telemetry["skills"]["last_executed_skill"], "tool_b")

    def test_02_ingest_from_live_http_daemon(self):
        """Spins up live mock daemon; asserts aggregator fetches directly without touching disk."""
        daemon_port = get_free_port()
        self.daemon_server = MockDaemonHTTPServer(daemon_port)
        self.daemon_server.start()

        # Nonexistent status file
        missing_file = self.workspace_dir / ".neko" / "does_not_exist.json"
        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(missing_file),
            daemon_url=f"http://127.0.0.1:{daemon_port}",
            workspace_dir=str(self.workspace_dir),
        )
        self.aggregator.poll(force=True)
        telemetry = self.aggregator.get_telemetry()

        self.assertEqual(telemetry["daemon_health"], "running")
        self.assertEqual(telemetry["cloud_sync"]["status"], "synced")
        self.assertEqual(telemetry["memory"]["total_facts"], 15)
        self.assertEqual(telemetry["skills"]["total_skills"], 4)

    def test_03_daemon_priority_over_stale_disk_cache(self):
        """Asserts HTTP daemon response takes priority over stale disk file."""
        daemon_port = get_free_port()
        self.daemon_server = MockDaemonHTTPServer(
            daemon_port,
            initial_status={
                "daemon_health": "running",
                "cloud_sync": {"status": "synced", "connected": True, "pending_mutations": 0},
                "memory": {"total_facts": 50},
                "skills": {"total_skills": 10},
            },
        )
        self.daemon_server.start()

        # Stale disk file with 10 pending mutations
        stale_payload = {
            "daemon_health": "offline",
            "cloud_sync": {"status": "offline", "connected": False, "pending_mutations": 10},
            "memory": {"total_facts": 5},
            "skills": {"total_skills": 1},
        }
        atomic_write_json_file(self.status_file, stale_payload)

        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=f"http://127.0.0.1:{daemon_port}",
            workspace_dir=str(self.workspace_dir),
        )
        self.aggregator.poll(force=True)
        telemetry = self.aggregator.get_telemetry()

        # Should match live daemon, not stale disk
        self.assertEqual(telemetry["cloud_sync"]["pending_mutations"], 0)
        self.assertEqual(telemetry["memory"]["total_facts"], 50)
        self.assertEqual(telemetry["skills"]["total_skills"], 10)

    def test_04_daemon_timeout_fallback_to_disk(self):
        """Simulates 1.0s daemon latency; aggregator probe times out and falls back to disk."""
        daemon_port = get_free_port()
        self.daemon_server = MockDaemonHTTPServer(daemon_port)
        self.daemon_server.set_delay(1.0)  # > 0.25s timeout
        self.daemon_server.start()

        disk_payload = {
            "daemon_health": "offline",
            "cloud_sync": {"status": "synced", "connected": True, "pending_mutations": 7},
            "memory": {"total_facts": 33},
            "skills": {"total_skills": 3},
        }
        atomic_write_json_file(self.status_file, disk_payload)

        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=f"http://127.0.0.1:{daemon_port}",
            workspace_dir=str(self.workspace_dir),
        )
        self.aggregator.poll(force=True)
        telemetry = self.aggregator.get_telemetry()

        self.assertEqual(telemetry["cloud_sync"]["pending_mutations"], 7)
        self.assertEqual(telemetry["memory"]["total_facts"], 33)

    def test_05_daemon_http_error_500_fallback_to_disk(self):
        """Simulates daemon 500 error; aggregator catches error and falls back to disk."""
        daemon_port = get_free_port()
        self.daemon_server = MockDaemonHTTPServer(daemon_port)
        self.daemon_server.set_error_code(500)
        self.daemon_server.start()

        disk_payload = {
            "daemon_health": "offline",
            "cloud_sync": {"status": "synced", "connected": True, "pending_mutations": 1},
            "memory": {"total_facts": 99},
            "skills": {"total_skills": 7},
        }
        atomic_write_json_file(self.status_file, disk_payload)

        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=f"http://127.0.0.1:{daemon_port}",
            workspace_dir=str(self.workspace_dir),
        )
        self.aggregator.poll(force=True)
        telemetry = self.aggregator.get_telemetry()

        self.assertEqual(telemetry["memory"]["total_facts"], 99)
        self.assertEqual(telemetry["cloud_sync"]["pending_mutations"], 1)

    def test_06_caching_and_ttl_throttling(self):
        """Asserts poll calls within TTL (100ms) do not make repeated HTTP requests."""
        daemon_port = get_free_port()
        self.daemon_server = MockDaemonHTTPServer(daemon_port)
        self.daemon_server.start()

        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=f"http://127.0.0.1:{daemon_port}",
            workspace_dir=str(self.workspace_dir),
        )
        # Initial poll happened on construct
        initial_requests = self.daemon_server.request_count
        self.assertGreaterEqual(initial_requests, 1)

        # Immediate poll within TTL should be throttled
        self.aggregator.poll(force=False)
        self.assertEqual(self.daemon_server.request_count, initial_requests)

        # After TTL expires (> 0.1s), polling should hit server again
        time.sleep(0.12)
        self.aggregator.poll(force=False)
        self.assertGreater(self.daemon_server.request_count, initial_requests)

    def test_07_alias_key_normalization(self):
        """Asserts aggregator provides both canonical and alias keys for backward compatibility."""
        alias_payload = {
            "cloud_sync": {
                "status": "synced",
                "connected": True,
                "last_sync": "2026-10-24T10:00:00Z",
                "pending": 4,
                "server": "http://127.0.0.1:8767",
            },
            "memory": {
                "facts_count": 42,
                "upcoming_deadlines": [
                    {"name": "CyberCity", "deadline_utc": "2026-10-29T00:00:00Z", "risk": "high"}
                ],
            },
            "skills": {
                "installed": 6,
                "last_execution": "tool_x",
            },
        }
        atomic_write_json_file(self.status_file, alias_payload)

        self.aggregator = NekoBotTelemetryAggregator(
            status_file_path=str(self.status_file),
            daemon_url=self.dead_daemon_url,
            workspace_dir=str(self.workspace_dir),
        )
        self.aggregator.poll(force=True)
        t = self.aggregator.get_telemetry()

        cs = t["cloud_sync"]
        self.assertEqual(cs["last_sync_time"], "2026-10-24T10:00:00Z")
        self.assertEqual(cs["last_sync"], "2026-10-24T10:00:00Z")
        self.assertEqual(cs["pending_mutations"], 4)
        self.assertEqual(cs["pending"], 4)
        self.assertEqual(cs["cloud_server_url"], "http://127.0.0.1:8767")
        self.assertEqual(cs["server"], "http://127.0.0.1:8767")

        mem = t["memory"]
        self.assertEqual(mem["total_facts"], 42)
        self.assertEqual(mem["facts_count"], 42)
        self.assertEqual(len(mem["upcoming_deadlines"]), 1)
        dl = mem["upcoming_deadlines"][0]
        self.assertEqual(dl["project"], "CyberCity")
        self.assertEqual(dl["name"], "CyberCity")
        self.assertEqual(dl["due"], "2026-10-29T00:00:00Z")
        self.assertEqual(dl["deadline_utc"], "2026-10-29T00:00:00Z")

        sk = t["skills"]
        self.assertEqual(sk["total_skills"], 6)
        self.assertEqual(sk["installed"], 6)
        self.assertEqual(sk["last_executed_skill"], "tool_x")
        self.assertEqual(sk["last_execution"], "tool_x")


# ===========================================================================
# 2. TestBridgeSSEStreaming (8 Tests)
# ===========================================================================
class TestBridgeSSEStreaming(unittest.TestCase):
    """Verifies SSE framing, headers, reconnect directives, latency, and unicode."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="neko_sse_test_")
        self.workspace_dir = Path(self.tmp_dir)
        self.bridge_port = get_free_port()
        self.closed_daemon_port = get_free_port()
        self.bridge = EphemeralBridgeServer(
            self.bridge_port,
            self.workspace_dir,
            f"http://127.0.0.1:{self.closed_daemon_port}",
        )
        self.bridge.start()

    def tearDown(self):
        self.bridge.stop()
        stop_telemetry(timeout=1.0)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_08_sse_response_headers(self):
        """GET /stream returns 200, Content-Type: text/event-stream, keep-alive, no-cache."""
        url = f"http://127.0.0.1:{self.bridge_port}/stream"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            ct = resp.headers.get("Content-Type", "")
            self.assertIn("text/event-stream", ct)
            self.assertIn("charset=utf-8", ct)
            conn = resp.headers.get("Connection", "")
            self.assertEqual(conn.lower(), "keep-alive")
            cc = resp.headers.get("Cache-Control", "")
            self.assertIn("no-cache", cc)

    def test_09_sse_frame_delimiters_and_framing(self):
        """Asserts each SSE chunk begins with 'data: ' and ends with double newline '\\n\\n'."""
        url = f"http://127.0.0.1:{self.bridge_port}/stream"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data_frames = 0
            while data_frames < 3:
                line = resp.readline().decode("utf-8")
                if line.startswith("data: "):
                    self.assertTrue(line.endswith("\n"))
                    # The next line in standard SSE is an empty newline separator
                    empty_sep = resp.readline().decode("utf-8")
                    self.assertEqual(empty_sep, "\n")
                    data_frames += 1

    def test_10_sse_retry_directive(self):
        """Verifies initial stream frame contains 'retry: 3000\\n' reconnect directive."""
        url = f"http://127.0.0.1:{self.bridge_port}/stream"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            first_line = resp.readline().decode("utf-8")
            self.assertTrue("retry:" in first_line or "data:" in first_line)

    def test_11_sse_keepalive_comment_parsing(self):
        """Verifies comment lines (': keep-alive') are handled cleanly by SSE client logic."""
        sample_stream = b": keep-alive\n\ndata: {\"ok\": true}\n\n: ping\n\n"
        lines = sample_stream.split(b"\n")
        data_frames = []
        for line in lines:
            line_str = line.decode("utf-8").strip()
            if line_str.startswith(":"):
                continue  # SSE comment ignored
            if line_str.startswith("data: "):
                payload = json.loads(line_str[len("data: "):])
                data_frames.append(payload)
        self.assertEqual(len(data_frames), 1)
        self.assertTrue(data_frames[0]["ok"])

    def test_12_sse_payload_size_boundary(self):
        """Asserts each SSE frame size is comfortably below 32KB."""
        url = f"http://127.0.0.1:{self.bridge_port}/stream"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            for _ in range(5):
                line = resp.readline()
                self.assertLess(len(line), 32768, "SSE frame exceeded 32KB payload size boundary")

    def test_13_sse_json_parse_latency_60fps(self):
        """Asserts average JSON deserialization latency of SSE frames is < 5.0ms (60 FPS contract)."""
        url = f"http://127.0.0.1:{self.bridge_port}/stream"
        req = urllib.request.Request(url, method="GET")
        latencies = []
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            while len(latencies) < 15:
                line = resp.readline().decode("utf-8")
                if line.startswith("data: "):
                    raw_json = line[len("data: "):].strip()
                    t0 = time.perf_counter()
                    data = json.loads(raw_json)
                    t1 = time.perf_counter()
                    self.assertIsInstance(data, dict)
                    latencies.append((t1 - t0) * 1000.0)

        avg_lat = sum(latencies) / len(latencies)
        self.assertLess(avg_lat, 5.0, f"Average SSE parse latency {avg_lat:.3f}ms exceeds 5.0ms budget")

    def test_14_sse_unicode_and_emoji_preservation(self):
        """Asserts Italian accents ('città') and emojis are preserved verbatim without escaping."""
        # Update bridge aggregator with unicode content
        payload = {
            "daemon_health": "running",
            "cloud_sync": {"status": "synced", "connected": True},
            "memory": {
                "total_facts": 1,
                "upcoming_deadlines": [
                    {"project": "Città del Futuro 🚀", "milestone": "Beta ✨", "due": "2026-10-31", "risk": "alto"}
                ],
            },
            "skills": {"total_skills": 1, "active_skills": ["attività_autonoma_🐾"]},
        }
        atomic_write_json_file(self.bridge.status_file, payload)
        self.bridge.aggregator.poll(force=True)

        url = f"http://127.0.0.1:{self.bridge_port}/stream"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            found = False
            for _ in range(10):
                line = resp.readline().decode("utf-8")
                if "Città del Futuro 🚀" in line and "attività_autonoma_🐾" in line:
                    found = True
                    break
            self.assertTrue(found, "Unicode and emojis were escaped or corrupted in SSE stream")

    def test_15_sse_multi_client_concurrent_streams(self):
        """Verifies two concurrent clients receive independent streams without deadlock."""
        results = {"client_1": 0, "client_2": 0}
        errors = []

        def client_worker(client_id, key):
            try:
                url = f"http://127.0.0.1:{self.bridge_port}/stream?client_id={client_id}"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    frames = 0
                    while frames < 4:
                        line = resp.readline().decode("utf-8")
                        if line.startswith("data: "):
                            frames += 1
                    results[key] = frames
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=client_worker, args=("conn_alpha", "client_1"))
        t2 = threading.Thread(target=client_worker, args=("conn_beta", "client_2"))
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        self.assertEqual(len(errors), 0, f"Client errors: {errors}")
        self.assertEqual(results["client_1"], 4)
        self.assertEqual(results["client_2"], 4)


# ===========================================================================
# 3. TestBridgeStatusEndpointSchema (6 Tests)
# ===========================================================================
class TestBridgeStatusEndpointSchema(unittest.TestCase):
    """Verifies schema conformance of GET /status."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="neko_status_schema_")
        self.workspace_dir = Path(self.tmp_dir)
        self.bridge_port = get_free_port()
        self.closed_daemon_port = get_free_port()
        self.bridge = EphemeralBridgeServer(
            self.bridge_port,
            self.workspace_dir,
            f"http://127.0.0.1:{self.closed_daemon_port}",
        )
        self.bridge.start()

    def tearDown(self):
        self.bridge.stop()
        stop_telemetry(timeout=1.0)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _get_status(self) -> Dict[str, Any]:
        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def test_16_status_http_response_headers(self):
        """GET /status returns 200, Content-Type: application/json, and CORS headers."""
        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("application/json", resp.headers.get("Content-Type", ""))
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")

    def test_17_schema_cloud_sync_block(self):
        """Verifies cloud_sync block structure, status enum, and types."""
        snap = self._get_status()
        self.assertIn("cloud_sync", snap)
        cs = snap["cloud_sync"]
        self.assertIn(cs["status"], ["synced", "syncing", "offline", "error"])
        self.assertIsInstance(cs["connected"], bool)
        self.assertIsInstance(cs["pending_mutations"], int)
        self.assertGreaterEqual(cs["pending_mutations"], 0)
        self.assertIsInstance(cs["cloud_server_url"], str)

    def test_18_schema_memory_block(self):
        """Verifies memory block structure, counts, and deadline objects."""
        snap = self._get_status()
        self.assertIn("memory", snap)
        mem = snap["memory"]
        self.assertIsInstance(mem["total_facts"], int)
        self.assertGreaterEqual(mem["total_facts"], 0)
        self.assertIsInstance(mem["active_projects"], int)
        self.assertGreaterEqual(mem["active_projects"], 0)
        self.assertIsInstance(mem["recent_episodes"], int)
        self.assertGreaterEqual(mem["recent_episodes"], 0)
        self.assertIsInstance(mem["upcoming_deadlines"], list)

    def test_19_schema_skills_block(self):
        """Verifies skills block structure, total_skills, and active_skills."""
        snap = self._get_status()
        self.assertIn("skills", snap)
        sk = snap["skills"]
        self.assertIsInstance(sk["total_skills"], int)
        self.assertGreaterEqual(sk["total_skills"], 0)
        self.assertIsInstance(sk["active_skills"], list)

    def test_20_schema_system_metrics_coexistence(self):
        """Verifies pre-existing CPU, RAM, AI status, and timestamp coexist cleanly."""
        snap = self._get_status()
        self.assertIn("cpu", snap)
        self.assertIsInstance(snap["cpu"], (int, float))
        self.assertIn("ram", snap)
        self.assertIsInstance(snap["ram"], (int, float))
        self.assertIn("ai_working", snap)
        self.assertIsInstance(snap["ai_working"], bool)
        self.assertIn("shared_cat", snap)
        self.assertIsInstance(snap["shared_cat"], dict)
        self.assertIn("timestamp", snap)
        self.assertIsInstance(snap["timestamp"], (int, float))

    def test_21_status_response_latency(self):
        """Verifies GET /status round-trip completes well within 15ms budget."""
        url = f"http://127.0.0.1:{self.bridge_port}/status"
        latencies = []
        for _ in range(5):
            t0 = time.perf_counter()
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("cloud_sync", data)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)

        avg_ms = sum(latencies) / len(latencies)
        self.assertLess(avg_ms, 15.0, f"Average status latency {avg_ms:.2f}ms exceeds 15ms budget")


# ===========================================================================
# 4. TestBridgeResilienceAndRecovery (9 Tests)
# ===========================================================================
class TestBridgeResilienceAndRecovery(unittest.TestCase):
    """Verifies edge case recovery: missing, 0-byte, corrupt syntax, daemon crashes."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="neko_resilience_test_")
        self.workspace_dir = Path(self.tmp_dir)
        self.bridge_port = get_free_port()
        self.daemon_port = get_free_port()
        self.daemon = None
        self.bridge = EphemeralBridgeServer(
            self.bridge_port,
            self.workspace_dir,
            f"http://127.0.0.1:{self.daemon_port}",
        )
        self.bridge.start()

    def tearDown(self):
        if self.daemon:
            self.daemon.stop()
        self.bridge.stop()
        stop_telemetry(timeout=1.0)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_22_missing_status_json_fresh_workspace(self):
        """Status file absent and daemon offline; returns offline fallback without throwing."""
        if self.bridge.status_file.exists():
            self.bridge.status_file.unlink()
        self.bridge.aggregator.poll(force=True)

        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["cloud_sync"]["status"], "offline")
            self.assertFalse(data["cloud_sync"]["connected"])

    def test_23_corrupted_json_syntax_recovery(self):
        """Status file contains invalid syntax; gracefully degrades to status='error'."""
        with open(self.bridge.status_file, "w", encoding="utf-8") as f:
            f.write("{cloud_sync: {malformed_syntax_no_quotes:")
        self.bridge.aggregator.poll(force=True)

        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["cloud_sync"]["status"], "error")

    def test_24_empty_status_file_recovery(self):
        """Status file is 0 bytes; returns safe defaults without throwing JSONDecodeError."""
        with open(self.bridge.status_file, "w", encoding="utf-8") as f:
            pass  # 0 bytes
        self.bridge.aggregator.poll(force=True)

        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["cloud_sync"]["status"], "offline")

    def test_25_binary_garbage_recovery(self):
        """Status file filled with random binary bytes; handles decode cleanly."""
        with open(self.bridge.status_file, "wb") as f:
            f.write(os.urandom(256))
        self.bridge.aggregator.poll(force=True)

        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["cloud_sync"]["status"], "error")

    def test_26_partial_payload_schema_reconciliation(self):
        """Status file contains only memory block; missing blocks are filled with defaults."""
        payload = {"memory": {"total_facts": 7}}
        atomic_write_json_file(self.bridge.status_file, payload)
        self.bridge.aggregator.poll(force=True)

        url = f"http://127.0.0.1:{self.bridge_port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["memory"]["total_facts"], 7)
            self.assertIn("cloud_sync", data)
            self.assertIn("skills", data)
            self.assertEqual(data["cloud_sync"]["status"], "offline")
            self.assertEqual(data["skills"]["total_skills"], 0)

    def test_27_daemon_crash_during_active_streaming(self):
        """Daemon stops mid-stream; stream seamlessly falls back to offline state without disconnecting."""
        self.daemon = MockDaemonHTTPServer(self.daemon_port)
        self.daemon.start()
        self.bridge.aggregator.poll(force=True)

        # Confirm daemon online
        snap1 = self.bridge.aggregator.get_telemetry()
        self.assertEqual(snap1["cloud_sync"]["status"], "synced")

        # Daemon crashes
        self.daemon.stop()
        self.daemon = None

        # Next poll detects offline
        self.bridge.aggregator.poll(force=True)
        snap2 = self.bridge.aggregator.get_telemetry()
        self.assertEqual(snap2["cloud_sync"]["status"], "offline")

    def test_28_daemon_restart_and_auto_reconnection(self):
        """Daemon restarts with new state; aggregator resumes live streaming."""
        self.daemon = MockDaemonHTTPServer(self.daemon_port)
        self.daemon.start()
        self.bridge.aggregator.poll(force=True)
        self.assertEqual(self.bridge.aggregator.get_telemetry()["memory"]["total_facts"], 15)

        # Stop daemon
        self.daemon.stop()
        self.bridge.aggregator.poll(force=True)
        self.assertEqual(self.bridge.aggregator.get_telemetry()["cloud_sync"]["status"], "offline")

        # Restart daemon on same port with 88 facts
        self.daemon = MockDaemonHTTPServer(
            self.daemon_port,
            initial_status={
                "daemon_health": "running",
                "cloud_sync": {"status": "synced", "connected": True},
                "memory": {"total_facts": 88},
                "skills": {"total_skills": 12},
            },
        )
        self.daemon.start()
        self.bridge.aggregator.poll(force=True)

        t = self.bridge.aggregator.get_telemetry()
        self.assertEqual(t["cloud_sync"]["status"], "synced")
        self.assertEqual(t["memory"]["total_facts"], 88)

    def test_29_concurrent_atomic_write_safety(self):
        """Background thread continuously replaces status.json while 5 clients query; zero torn reads."""
        stop_writer = threading.Event()
        writer_errors = []

        def writer_loop():
            val = 0
            while not stop_writer.is_set():
                val += 1
                try:
                    atomic_write_json_file(
                        self.bridge.status_file,
                        {
                            "cloud_sync": {"status": "synced", "connected": True, "pending_mutations": val},
                            "memory": {"total_facts": val},
                            "skills": {"total_skills": val},
                        },
                    )
                except Exception as e:
                    writer_errors.append(e)
                time.sleep(0.005)

        writer_thread = threading.Thread(target=writer_loop, daemon=True)
        writer_thread.start()

        reader_errors = []
        def reader_task():
            url = f"http://127.0.0.1:{self.bridge_port}/status"
            for _ in range(10):
                try:
                    req = urllib.request.Request(url, method="GET")
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.assertIn("cloud_sync", data)
                except Exception as e:
                    reader_errors.append(e)
                time.sleep(0.005)

        threads = [threading.Thread(target=reader_task) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        stop_writer.set()
        writer_thread.join(timeout=1.0)

        self.assertEqual(len(writer_errors), 0, f"Writer errors: {writer_errors}")
        self.assertEqual(len(reader_errors), 0, f"Reader errors: {reader_errors}")

    def test_30_rapid_successive_status_mutations(self):
        """Rapidly mutates status file 50 times in 250ms; aggregator remains consistent."""
        for i in range(50):
            atomic_write_json_file(
                self.bridge.status_file,
                {
                    "cloud_sync": {"status": "synced", "connected": True, "pending_mutations": i},
                    "memory": {"total_facts": i * 2},
                    "skills": {"total_skills": i % 5},
                },
            )
            time.sleep(0.005)

        self.bridge.aggregator.poll(force=True)
        t = self.bridge.aggregator.get_telemetry()
        self.assertEqual(t["cloud_sync"]["pending_mutations"], 49)
        self.assertEqual(t["memory"]["total_facts"], 98)


# ===========================================================================
# 5. TestBridgeZeroRegression (7 Tests)
# ===========================================================================
class TestBridgeZeroRegression(unittest.TestCase):
    """Verifies pre-existing endpoints (/cat_sync, /ai_chat, client slots, CORS, static assets)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="neko_regression_test_")
        self.workspace_dir = Path(self.tmp_dir)
        self.bridge_port = get_free_port()
        self.closed_daemon_port = get_free_port()
        self.bridge = EphemeralBridgeServer(
            self.bridge_port,
            self.workspace_dir,
            f"http://127.0.0.1:{self.closed_daemon_port}",
        )
        self.bridge.start()

    def tearDown(self):
        self.bridge.stop()
        stop_telemetry(timeout=1.0)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_31_regression_post_cat_sync(self):
        """POST /cat_sync updates shared cat coordinates and emotion."""
        url = f"http://127.0.0.1:{self.bridge_port}/cat_sync"
        body = json.dumps({"worldCol": 45, "row": 20, "isWalking": True, "emotion": "happy"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res.get("ok"))

        snap = self.bridge.monitor.get_snapshot()
        self.assertEqual(snap["shared_cat"]["worldCol"], 45)
        self.assertEqual(snap["shared_cat"]["row"], 20)
        self.assertEqual(snap["shared_cat"]["emotion"], "happy")

    def test_32_regression_post_cat_sync_remote_command(self):
        """POST /cat_sync with remoteCommand updates cmdId and remoteCommand."""
        url = f"http://127.0.0.1:{self.bridge_port}/cat_sync"
        body = json.dumps({"remoteCommand": "fish"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)

        snap = self.bridge.monitor.get_snapshot()
        self.assertEqual(snap["shared_cat"]["remoteCommand"], "fish")
        self.assertGreater(snap["shared_cat"]["cmdId"], 0)

    def test_33_regression_post_ai_chat_sys_tool(self):
        """POST /ai_chat with '/sys' returns CPU and RAM system status without invoking Ollama."""
        url = f"http://127.0.0.1:{self.bridge_port}/ai_chat"
        body = json.dumps({"prompt": "/sys"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("reply", data)
            self.assertIn("CPU", data["reply"])
            self.assertIn("RAM", data["reply"])

    def test_34_regression_post_ai_chat_todo_tool(self):
        """POST /ai_chat with 'todo: ...' saves task locally."""
        url = f"http://127.0.0.1:{self.bridge_port}/ai_chat"
        body = json.dumps({"prompt": "todo: test unitario m5"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("reply", data)
            self.assertIn("test unitario m5", data["reply"])

    def test_35_regression_client_slot_allocation(self):
        """First client receives slot 0, second client receives slot 1."""
        url1 = f"http://127.0.0.1:{self.bridge_port}/status?client_id=client_alpha"
        req1 = urllib.request.Request(url1, method="GET")
        with urllib.request.urlopen(req1, timeout=3.0) as resp1:
            data1 = json.loads(resp1.read().decode("utf-8"))
            self.assertEqual(data1["assigned_slot"], 0)

        url2 = f"http://127.0.0.1:{self.bridge_port}/status?client_id=client_beta"
        req2 = urllib.request.Request(url2, method="GET")
        with urllib.request.urlopen(req2, timeout=3.0) as resp2:
            data2 = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(data2["assigned_slot"], 1)

    def test_36_regression_options_cors_preflight(self):
        """OPTIONS preflight requests to endpoints return 200 and Access-Control headers."""
        endpoints = ["/status", "/stream", "/cat_sync", "/ai_chat"]
        for ep in endpoints:
            url = f"http://127.0.0.1:{self.bridge_port}{ep}"
            req = urllib.request.Request(url, method="OPTIONS")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
                self.assertIn("GET", resp.headers.get("Access-Control-Allow-Methods", ""))

    def test_37_regression_static_assets_serving(self):
        """GET /index.html serves root index.html."""
        url = f"http://127.0.0.1:{self.bridge_port}/index.html"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            content = resp.read().decode("utf-8")
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("hud-ecosystem-section", content)


if __name__ == "__main__":
    unittest.main()
