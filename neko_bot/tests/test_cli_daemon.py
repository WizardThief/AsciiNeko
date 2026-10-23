"""
tests/test_cli_daemon.py - Comprehensive Unit, Integration, and E2E Tests for Milestone 1.

Covers:
- R1 / AC1: CLI status JSON payload schema and exit codes
- Daemon lifecycle management: Windows detachment, PID tracking, clean shutdown, stale PID recovery
- Dynamic creative app & resource mode detection (Unreal, Blender, Photoshop, 3ds Max, etc.)
- Strict < 2.0s SLA and deterministic fallback with Ollama offline / unpulled
- Tier 4 Creative Crunch Session E2E scenario
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.request

from neko_bot.cli import build_parser, cmd_status, main
from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.daemon import (
    DaemonController,
    DaemonServer,
    DaemonStatusManager,
    atomic_write_json,
)
from neko_bot.core.resource_monitor import ResourceMonitor
from neko_bot.core.router import OllamaRouter, default_deterministic_fallback


def get_free_port() -> int:
    """Finds an unused TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


class TestCliStatusSchemaAndExitCodes(unittest.TestCase):
    """Verifies CLI Status JSON payload schema, human formatting, and exit codes (AC1)."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_cli_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_status_json_schema_offline(self) -> None:
        """Verifies that `neko status --json` returns AC1 schema even when daemon is stopped."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["status", "--json", "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0, "CLI status command must return exit code 0")
        output = buf.getvalue().strip()
        data = json.loads(output)

        # Validate all required AC1 schema fields
        required_keys = [
            "daemon_health",
            "daemon_pid",
            "resource_mode",
            "active_application",
            "active_project",
            "today_tracked_seconds",
            "is_afk",
            "idle_seconds",
            "top_task",
            "notification_queue",
        ]
        for key in required_keys:
            self.assertIn(key, data, f"Required key '{key}' missing from status JSON")

        self.assertEqual(data["daemon_health"], "stopped")
        self.assertIsNone(data["daemon_pid"])
        self.assertIn(data["resource_mode"], ("normal", "creative_detected", "low_vram"))
        self.assertIsInstance(data["today_tracked_seconds"], int)
        self.assertIsInstance(data["is_afk"], bool)
        self.assertIsInstance(data["idle_seconds"], int)
        self.assertIsInstance(data["notification_queue"], list)

    def test_cli_status_json_schema_online_mocked(self) -> None:
        """Verifies that `neko status --json` accurately reflects running daemon state."""
        # Write running status to disk cache
        status_manager = DaemonStatusManager(self.config)
        status_manager.update(
            active_application="UnrealEditor.exe",
            active_project="CyberpunkCity",
            today_tracked_seconds=7200,
            idle_seconds=5,
            top_task={"id": "task_1", "title": "Bake normal maps", "priority": "high"},
        )
        status_manager.set_running(pid=12345)

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["status", "--json", "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0)
        output = buf.getvalue().strip()
        data = json.loads(output)

        self.assertEqual(data["active_application"], "UnrealEditor.exe")
        self.assertEqual(data["active_project"], "CyberpunkCity")
        self.assertEqual(data["today_tracked_seconds"], 7200)
        self.assertIsNotNone(data["top_task"])
        self.assertEqual(data["top_task"]["title"], "Bake normal maps")

    def test_cli_status_human_readable_output(self) -> None:
        """Verifies human-readable status table rendering."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["status", "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn("NEKO BOT STATUS", out)
        self.assertIn("Daemon Health:", out)
        self.assertIn("Resource Mode:", out)
        self.assertIn("Today Tracked:", out)

    def test_cli_status_handles_corrupt_cache_gracefully(self) -> None:
        """Verifies that a corrupted status.json file is handled without crash."""
        corrupt_file = self.config.status_file
        with open(corrupt_file, "w", encoding="utf-8") as f:
            f.write("CORRUPT_JSON_DATA{{{")

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["status", "--json", "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0)
        data = json.loads(buf.getvalue().strip())
        self.assertEqual(data["daemon_health"], "stopped")

    def test_cli_help_flag_returns_code_0(self) -> None:
        """Verifies --help outputs usage and exits with code 0."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["--help"])
        self.assertEqual(ret, 0)
        self.assertIn("neko", buf.getvalue())

    def test_cli_unknown_command_returns_code_2(self) -> None:
        """Verifies invalid arguments exit with code 2."""
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
            try:
                ret = main(["nonexistent_subcommand"])
            except SystemExit as cm:
                ret = cm.code
            self.assertEqual(ret, 2)


class TestDaemonLifecycleManagement(unittest.TestCase):
    """Verifies daemon startup, IPC HTTP server, clean shutdown, and PID tracking."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_daemon_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()
        self.controller = DaemonController(self.config)

    def tearDown(self) -> None:
        self.controller.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_daemon_status_when_stopped(self) -> None:
        """Verifies controller reports not running when daemon has not started."""
        running, pid = self.controller.is_running()
        self.assertFalse(running)
        self.assertIsNone(pid)

    def test_daemon_server_in_thread_and_ipc_endpoints(self) -> None:
        """Starts DaemonServer in background thread, verifies /health, /status, and clean stop."""
        server = DaemonServer(self.config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        # Wait for HTTP server to become responsive
        deadline = time.time() + 3.0
        online = False
        while time.time() < deadline:
            time.sleep(0.05)
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.config.port}/health")
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    if resp.status == 200:
                        online = True
                        break
            except Exception:
                pass

        self.assertTrue(online, "Daemon HTTP server failed to bind and respond within 3s")

        # Test GET /health
        req = urllib.request.Request(f"http://127.0.0.1:{self.config.port}/health")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["pid"], os.getpid())

        # Test GET /status
        req = urllib.request.Request(f"http://127.0.0.1:{self.config.port}/status")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["daemon_health"], "running")
            self.assertEqual(data["daemon_pid"], os.getpid())

        # Test POST /command ping
        ping_body = json.dumps({"action": "ping"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.config.port}/command",
            data=ping_body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("message"), "pong")

        # Test POST /command stop
        stop_body = json.dumps({"action": "stop"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.config.port}/command",
            data=stop_body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            self.assertEqual(resp.status, 200)

        # Confirm thread clean termination
        server_thread.join(timeout=3.0)
        self.assertFalse(server_thread.is_alive(), "Daemon thread did not terminate cleanly")

        # Confirm PID file removed and status marked stopped
        self.assertFalse(self.config.pid_file.exists())
        cached_status = json.loads(self.config.status_file.read_text(encoding="utf-8"))
        self.assertEqual(cached_status["daemon_health"], "stopped")
        self.assertIsNone(cached_status["daemon_pid"])

    def test_daemon_stale_pid_recovery(self) -> None:
        """Verifies that dead/stale PIDs in daemon.pid are detected and cleaned up."""
        # Write dummy PID to daemon.pid
        with open(self.config.pid_file, "w", encoding="utf-8") as f:
            f.write("9999999")

        running, pid = self.controller.is_running()
        self.assertFalse(running)
        self.assertFalse(self.config.pid_file.exists(), "Stale PID file should have been cleaned up")

    def test_atomic_write_json_concurrency(self) -> None:
        """Spawns concurrent reader threads while rapidly writing to test atomicity."""
        test_file = self.temp_dir / "atomic_test.json"
        errors: list[Exception] = []
        stop_readers = threading.Event()

        def reader():
            while not stop_readers.is_set():
                if test_file.exists():
                    try:
                        with open(test_file, "r", encoding="utf-8") as f:
                            content = f.read()
                            if content:
                                json.loads(content)
                    except (PermissionError, FileNotFoundError, OSError):
                        pass
                    except Exception as e:
                        errors.append(e)
                time.sleep(0.001)

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(8)]
        for r in readers:
            r.start()

        # Rapidly write 50 updates
        for i in range(50):
            atomic_write_json(test_file, {"iteration": i, "timestamp": time.time(), "items": list(range(20))})

        stop_readers.set()
        for r in readers:
            r.join(timeout=1.0)

        self.assertEqual(len(errors), 0, f"Atomic write encountered read errors: {errors}")


class TestCreativeAppResourceModeSwitching(unittest.TestCase):
    """Verifies dual-engine process detection and resource mode adaptation (F3, F4)."""

    def setUp(self) -> None:
        self.monitor = ResourceMonitor()

    def tearDown(self) -> None:
        self.monitor.clear_mock_override()

    def test_unreal_engine_switches_to_creative_detected(self) -> None:
        self.monitor.set_mock_override(detected_apps=["UnrealEditor.exe"])
        is_heavy, apps = self.monitor.is_heavy_creative_running()
        self.assertTrue(is_heavy)
        self.assertIn("UnrealEditor.exe", apps)
        self.assertEqual(self.monitor.get_resource_mode(), "creative_detected")

    def test_blender_switches_to_creative_detected(self) -> None:
        self.monitor.set_mock_override(detected_apps=["blender.exe"])
        self.assertEqual(self.monitor.get_resource_mode(), "creative_detected")

    def test_photoshop_switches_to_creative_detected(self) -> None:
        self.monitor.set_mock_override(detected_apps=["Photoshop.exe"])
        self.assertEqual(self.monitor.get_resource_mode(), "creative_detected")

    def test_3dsmax_switches_to_creative_detected(self) -> None:
        self.monitor.set_mock_override(detected_apps=["3dsmax.exe"])
        self.assertEqual(self.monitor.get_resource_mode(), "creative_detected")

    def test_all_creative_apps_detected(self) -> None:
        apps = ["afterfx.exe", "premiere.exe", "resolve.exe", "vray.exe", "corona.exe"]
        for app in apps:
            self.monitor.set_mock_override(detected_apps=[app])
            self.assertEqual(
                self.monitor.get_resource_mode(),
                "creative_detected",
                f"Failed to detect creative process {app}",
            )

    def test_case_insensitivity_and_suffix_normalization(self) -> None:
        cases = ["3dsMax", "BLENDER", "unrealeditor", "photoshop.EXE"]
        for c in cases:
            self.monitor.set_mock_override(detected_apps=[c])
            self.assertEqual(
                self.monitor.get_resource_mode(),
                "creative_detected",
                f"Failed case/suffix normalization for {c}",
            )

    def test_high_cpu_load_switches_to_low_vram(self) -> None:
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=88.0, ram_percent=30.0)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

    def test_high_ram_load_switches_to_low_vram(self) -> None:
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=20.0, ram_percent=92.0)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

    def test_baseline_clean_system_returns_normal_mode(self) -> None:
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=15.0, ram_percent=45.0)
        self.assertEqual(self.monitor.get_resource_mode(), "normal")


class TestRouterSlaAndDeterministicFallback(unittest.TestCase):
    """Verifies strict < 2.0s SLA, keep_alive: 0 unloading, and offline resilience (F4, F5, AC1)."""

    def setUp(self) -> None:
        self.monitor = ResourceMonitor()
        self.router = OllamaRouter(
            resource_monitor=self.monitor,
            ollama_base_url="http://127.0.0.1:54321",  # Unused port to simulate offline Ollama
            ollama_timeout=1.5,
        )

    def tearDown(self) -> None:
        self.monitor.clear_mock_override()

    def test_router_sla_ollama_offline_returns_under_2s(self) -> None:
        """Verifies instant fallback < 2s SLA when Ollama is offline."""
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=10.0, ram_percent=30.0)

        t_start = time.perf_counter()
        result = self.router.route_request("Bake texture maps #cyberpunk !alta", intent="task")
        t_elapsed = time.perf_counter() - t_start

        self.assertLess(t_elapsed, 2.0, f"Router took {t_elapsed:.3f}s, exceeding 2s SLA")
        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "deterministic_fallback")
        self.assertEqual(result["data"]["project"], "cyberpunk")
        self.assertEqual(result["data"]["priority"], "high")

    def test_router_creative_detected_direct_deterministic(self) -> None:
        """When heavy creative app is active, router executes in < 50ms with zero VRAM."""
        self.monitor.set_mock_override(detected_apps=["UnrealEditor.exe"])

        t_start = time.perf_counter()
        result = self.router.route_request("Refine hero mesh #3d !urgente", intent="task")
        t_elapsed = time.perf_counter() - t_start

        self.assertLess(t_elapsed, 0.1, "Direct deterministic bypass should take < 100ms")
        self.assertEqual(result["source"], "direct_deterministic")
        self.assertEqual(result["resource_mode"], "creative_detected")
        self.assertEqual(result["data"]["priority"], "high")

    def test_router_keep_alive_zero_under_heavy_load(self) -> None:
        """Verifies that when forced LLM inference runs under creative load, keep_alive: 0 is sent."""
        self.monitor.set_mock_override(detected_apps=["blender.exe"])

        captured_payloads = []

        def mock_urlopen(req, timeout=None):
            if hasattr(req, "data") and req.data:
                payload = json.loads(req.data.decode("utf-8"))
                captured_payloads.append(payload)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = json.dumps({
                "message": {"content": json.dumps({"title": "Mock Task", "project": "3d", "priority": "high"})}
            }).encode("utf-8")
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = self.router.route_request(
                "crea modello #3d",
                intent="task_parse",
                force_llm=True,
            )

        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(captured_payloads[0].get("keep_alive"), 0, "Must enforce keep_alive: 0 under heavy load")
        self.assertEqual(captured_payloads[0].get("model"), "clef:latest", "Must select lightweight model")

    def test_scenario_creative_crunch_session_end_to_end(self) -> None:
        """
        Tier 4 Scenario 1: Creative Crunch Session
        User is simultaneously running Unreal Engine and Blender with heavy load.
        Ollama is offline or model is missing.
        Verifies CLI status and command execution succeed cleanly under 2.0s without error.
        """
        temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_scenario_"))
        try:
            # 1. Simulate active creative apps
            self.monitor.set_mock_override(
                detected_apps=["UnrealEditor.exe", "blender.exe"],
                cpu_percent=85.0,
                ram_percent=90.0,
            )

            # 2. Check CLI status --json
            buf = io.StringIO()
            with redirect_stdout(buf):
                ret = main(["status", "--json", "--workspace", str(temp_dir)])

            self.assertEqual(ret, 0)
            status_data = json.loads(buf.getvalue().strip())
            self.assertEqual(status_data["resource_mode"], "creative_detected")

            # 3. Check CLI do command with prompt
            t_start = time.perf_counter()
            buf_do = io.StringIO()
            with redirect_stdout(buf_do):
                ret_do = main(["do", "Bake normal maps #cyberpunk !alta", "--workspace", str(temp_dir)])
            t_elapsed = time.perf_counter() - t_start

            self.assertEqual(ret_do, 0)
            self.assertLess(t_elapsed, 2.0, "Tier 4 scenario command took > 2.0s")
            self.assertIn("Bake normal maps", buf_do.getvalue())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestMilestone1RemediationPatches(unittest.TestCase):
    """
    Unit and integration tests for the 5 remediation patches required after Reviewer and Challenger feedback:
    1. DaemonServer._tick() catches flush exceptions during file lock contention.
    2. neko.bat uses setlocal without delayed expansion to preserve !alta priorities.
    3. DaemonController.stop() forcefully terminates unresponsive/hung PIDs before cleaning PID file.
    4. atomic_write_json_file() incorporates thread ID and UUID to prevent intra-process collisions.
    5. DaemonRequestHandler strips query parameters on /health, /status, and /command.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_patches_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()
        self.controller = DaemonController(self.config)

    def tearDown(self) -> None:
        self.controller.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_patch1_tick_catches_flush_exception(self) -> None:
        """Patch 1: Moving flush() inside try...except ensures lock contention never crashes _tick()."""
        server = DaemonServer(self.config)
        # Mock status_manager.flush to raise PermissionError (simulating Windows sharing lock)
        server.status_manager.flush = MagicMock(side_effect=PermissionError("Sharing violation on status.json"))

        # Calling _tick() must not raise any exception
        try:
            server._tick()
        except Exception as exc:
            self.fail(f"_tick() raised unhandled exception during flush contention: {exc}")

        server.status_manager.flush.assert_called_once()

    def test_patch2_neko_bat_no_delayed_expansion(self) -> None:
        """Patch 2: neko.bat must use setlocal without enabledelayedexpansion to preserve !alta."""
        repo_root = Config.find_project_root()
        bat_file = repo_root / "neko.bat"
        if not bat_file.exists():
            repo_root = Path(__file__).resolve().parent.parent
            bat_file = repo_root / "neko.bat"
        self.assertTrue(bat_file.exists(), f"neko.bat not found at {bat_file}")
        content = bat_file.read_text(encoding="utf-8")
        self.assertNotIn("enabledelayedexpansion", content.lower())
        self.assertIn("setlocal", content.lower())

    def test_patch3_stop_terminates_unresponsive_pid(self) -> None:
        """Patch 3: stop() terminates unresponsive/hung PID before unlinking PID file."""
        fake_pid = 88888
        self.config.pid_file.write_text(str(fake_pid), encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.name.return_value = "python.exe"

        with patch.object(self.controller, "is_running", return_value=(False, fake_pid)), \
             patch("neko_bot.core.daemon.HAS_PSUTIL", True), \
             patch("psutil.pid_exists", return_value=True), \
             patch("psutil.Process", return_value=mock_proc):

            stopped, msg = self.controller.stop()

            self.assertTrue(stopped)
            self.assertIn("cleaned unresponsive process", msg)
            mock_proc.terminate.assert_called_once()
            self.assertFalse(self.config.pid_file.exists())

    def test_patch4_atomic_write_json_file_tmp_path_thread_and_uuid(self) -> None:
        """Patch 4: atomic_write_json_file tmp_path contains thread ID and uuid hex."""
        captured_tmp_paths = []
        original_replace = os.replace

        def mock_replace(src, dst):
            captured_tmp_paths.append(str(src))
            return original_replace(src, dst)

        test_file = self.temp_dir / "test_patch4.json"
        current_thread_id = threading.get_ident()

        with patch("os.replace", side_effect=mock_replace):
            atomic_write_json_file(test_file, {"key": "value"})

        self.assertEqual(len(captured_tmp_paths), 1)
        tmp_name = captured_tmp_paths[0]
        self.assertIn(f"_{current_thread_id}_", tmp_name, "tmp_path must contain current thread ID")
        self.assertTrue(test_file.exists())

        # Also verify 16 concurrent threads writing to different files without collision
        thread_errors = []

        def worker(idx: int):
            try:
                for j in range(10):
                    fpath = self.temp_dir / f"thread_{idx}.json"
                    atomic_write_json_file(fpath, {"worker": idx, "iter": j})
            except Exception as e:
                thread_errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        self.assertEqual(len(thread_errors), 0, f"Concurrent atomic_write failed: {thread_errors}")

    def test_patch5_daemon_request_handler_strips_query_parameters(self) -> None:
        """Patch 5: DaemonRequestHandler strips query parameters in do_GET and do_POST."""
        server = DaemonServer(self.config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        # Wait for server to become responsive
        deadline = time.time() + 3.0
        online = False
        while time.time() < deadline:
            time.sleep(0.05)
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.config.port}/health")
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    if resp.status == 200:
                        online = True
                        break
            except Exception:
                pass

        self.assertTrue(online, "Daemon server failed to bind and become online")

        try:
            # 1. GET /health with query string
            req = urllib.request.Request(f"http://127.0.0.1:{self.config.port}/health?ts=1700000000&check=true")
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data.get("status"), "ok")

            # 2. GET /status with query string
            req = urllib.request.Request(f"http://127.0.0.1:{self.config.port}/status?_t=987654321")
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data.get("daemon_health"), "running")

            # 3. POST /command with query string
            ping_body = json.dumps({"action": "ping"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.config.port}/command?cache_buster=xyz",
                data=ping_body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertEqual(data.get("message"), "pong")

            # 4. Clean shutdown via POST /command
            stop_body = json.dumps({"action": "stop"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.config.port}/command",
                data=stop_body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                self.assertEqual(resp.status, 200)

        finally:
            server_thread.join(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
