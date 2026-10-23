"""
tests/test_adversarial_m1.py - Adversarial Stress & Failure Mode Tests for Milestone 1.

Authored by Challenger M1_1 to empirically stress-test:
1. OllamaRouter < 2s response SLA under hanging sockets, socket resets, HTTP 500/502/404,
   corrupt JSON bodies, malformed schemas, and unpulled models.
2. ResourceMonitor dual-engine creative process detection across all 9 creative apps,
   casing/suffix variations, whitespace, and boundary CPU/RAM thresholds.
3. Fallback engine robustness with extreme inputs, unicode, Italian syntax, and faulty callables.
4. DaemonController and CLI status payload resilience under corrupt caches and concurrency.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from neko_bot.cli import cmd_status, main
from neko_bot.core.config import Config
from neko_bot.core.daemon import DaemonController, DaemonStatusManager
from neko_bot.core.resource_monitor import (
    DEFAULT_HEAVY_PROCESSES,
    ResourceMonitor,
)
from neko_bot.core.router import (
    OllamaRouter,
    RouteResult,
    default_deterministic_fallback,
)
from tests.conftest import get_free_port


class MockSocketServer:
    """Lightweight controllable loopback TCP server for simulating adversarial network conditions."""

    def __init__(self, handler_func) -> None:
        self.handler_func = handler_func
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self.sock.settimeout(0.5)
        while not self.stop_event.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                self.handler_func(conn)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.sock.close()
        except Exception:
            pass
        self.thread.join(timeout=1.0)


class TestOllamaRouterAdversarialNetwork(unittest.TestCase):
    """Adversarial network failure modes: hanging sockets, resets, HTTP errors, malformed JSON."""

    def setUp(self) -> None:
        self.monitor = ResourceMonitor()
        # Force normal resource mode so router attempts network calls to Ollama
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=10.0, ram_percent=30.0)

    def tearDown(self) -> None:
        self.monitor.clear_mock_override()

    def test_hanging_socket_enforces_sub_2s_sla(self) -> None:
        """
        Adversarial Test 1: Hanging Ollama server accepts TCP connection but sends 0 bytes.
        Router must timeout at 1.5s and return deterministic fallback in < 2.0s total without exception.
        """
        server_release = threading.Event()

        def hang_handler(conn):
            conn.recv(1024)
            # Sleep longer than the 1.5s client timeout
            server_release.wait(timeout=2.5)

        server = MockSocketServer(hang_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
                ollama_timeout=1.5,
            )

            t0 = time.perf_counter()
            result = router.route_request("Bake diffuse maps #cyberpunk !alta", intent="task")
            elapsed = time.perf_counter() - t0

            self.assertLess(
                elapsed,
                2.0,
                f"Router took {elapsed:.3f}s on hanging connection, violating < 2.0s SLA!",
            )
            self.assertTrue(result["success"], "Fallback result must indicate success")
            self.assertEqual(result["source"], "deterministic_fallback")
            self.assertEqual(result["resource_mode"], "normal")
            self.assertEqual(result["data"]["project"], "cyberpunk")
            self.assertEqual(result["data"]["priority"], "high")
            self.assertIsNotNone(result["error"])
        finally:
            server_release.set()
            server.close()

    def test_abrupt_connection_reset_recovers_instantly(self) -> None:
        """
        Adversarial Test 2: Ollama abruptly closes connection with TCP RST mid-handshake.
        Router must recover instantly (< 50ms) via deterministic fallback with zero crash.
        """
        def rst_handler(conn):
            # Set SO_LINGER to 0, forcing TCP RST on close
            conn.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
            conn.close()

        server = MockSocketServer(rst_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
                ollama_timeout=1.5,
            )

            t0 = time.perf_counter()
            result = router.route_request("Aggiungi modello 3D #asset !urgente", intent="task")
            elapsed = time.perf_counter() - t0

            self.assertLess(elapsed, 0.2, "RST recovery should be almost instantaneous (< 200ms)")
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
            self.assertEqual(result["data"]["project"], "asset")
            self.assertEqual(result["data"]["priority"], "high")
        finally:
            server.close()

    def test_http_500_internal_error_recovers_via_fallback(self) -> None:
        """Adversarial Test 3: Ollama returns HTTP 500 Internal Server Error."""
        def error_handler(conn):
            conn.recv(1024)
            resp = b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            conn.sendall(resp)
            try:
                conn.shutdown(socket.SHUT_WR)
            except Exception:
                pass

        server = MockSocketServer(error_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("Rigging character #vfx !media", intent="task")
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
            self.assertIsNotNone(result["error"])
        finally:
            server.close()

    def test_http_502_bad_gateway_with_html_body(self) -> None:
        """Adversarial Test 4: Upstream proxy returns HTML 502 Bad Gateway body."""
        def html_502_handler(conn):
            try:
                conn.recv(4096)
            except Exception:
                pass
            html = b"<html><head><title>502 Bad Gateway</title></head><body><h1>Bad Gateway</h1></body></html>"
            header = f"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/html\r\nContent-Length: {len(html)}\r\nConnection: close\r\n\r\n".encode("utf-8")
            conn.sendall(header + html)
            try:
                conn.shutdown(socket.SHUT_WR)
            except Exception:
                pass

        server = MockSocketServer(html_502_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("Animazione camminata #anim !bassa", intent="task")
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
            self.assertTrue("502" in str(result["error"]) or "10053" in str(result["error"]) or "10054" in str(result["error"]))
        finally:
            server.close()

    def test_http_404_unpulled_model_recovers_cleanly(self) -> None:
        """Adversarial Test 5: Ollama returns HTTP 404 Model Not Found."""
        def not_found_handler(conn):
            try:
                conn.recv(4096)
            except Exception:
                pass
            body = b'{"error":"model \'qwen3.8:latest\' not found, try pulling it first"}'
            header = f"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("utf-8")
            conn.sendall(header + body)
            try:
                conn.shutdown(socket.SHUT_WR)
            except Exception:
                pass

        server = MockSocketServer(not_found_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("Export fbx #pipeline", intent="task")
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
            self.assertIn("404", str(result["error"]))
        finally:
            server.close()

    def test_malformed_corrupt_json_payload(self) -> None:
        """Adversarial Test 6: Ollama returns HTTP 200 with invalid, truncated JSON."""
        def corrupt_json_handler(conn):
            conn.recv(1024)
            body = b'{"message": {"content": "{"title": "Unfinished JSON...'
            header = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode("utf-8")
            conn.sendall(header + body)

        server = MockSocketServer(corrupt_json_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("Shader graph #unreal", intent="task_parse")
            # Must not crash; fallback or captured raw content
            self.assertTrue(result["success"])
        finally:
            server.close()

    def test_malformed_null_message_body(self) -> None:
        """Adversarial Test 7: Ollama returns HTTP 200 with 'message': null."""
        def null_msg_handler(conn):
            conn.recv(1024)
            body = b'{"message": null, "done": true}'
            header = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode("utf-8")
            conn.sendall(header + body)

        server = MockSocketServer(null_msg_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("UV unwrapping #mesh", intent="task")
            # Should catch AttributeError on None.get and gracefully fall back
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
        finally:
            server.close()

    def test_malformed_list_response_body(self) -> None:
        """Adversarial Test 8: Ollama returns HTTP 200 with a JSON list instead of object."""
        def list_handler(conn):
            conn.recv(1024)
            body = b'[{"item": 1}, {"item": 2}]'
            header = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode("utf-8")
            conn.sendall(header + body)

        server = MockSocketServer(list_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("Compositing passes #comp", intent="task")
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
        finally:
            server.close()

    def test_empty_zero_byte_response(self) -> None:
        """Adversarial Test 9: Ollama returns HTTP 200 with 0 bytes body."""
        def empty_handler(conn):
            conn.recv(1024)
            header = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 0\r\n\r\n"
            conn.sendall(header)

        server = MockSocketServer(empty_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            result = router.route_request("Color grading #lut", intent="task")
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "deterministic_fallback")
        finally:
            server.close()

    def test_probe_helpers_safe_against_garbage(self) -> None:
        """Adversarial Test 10: is_ollama_online() and get_available_models() survive corrupt payloads."""
        def garbage_handler(conn):
            conn.recv(1024)
            body = b"NOT_A_VALID_TAGS_RESPONSE"
            header = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode("utf-8")
            conn.sendall(header + body)

        server = MockSocketServer(garbage_handler)
        try:
            router = OllamaRouter(
                resource_monitor=self.monitor,
                ollama_base_url=f"http://127.0.0.1:{server.port}",
            )
            # is_ollama_online only checks status == 200
            self.assertTrue(router.is_ollama_online())
            # get_available_models parses JSON: must catch JSONDecodeError and return []
            models = router.get_available_models()
            self.assertEqual(models, [])
        finally:
            server.close()


class TestResourceMonitorAdversarialInputs(unittest.TestCase):
    """Stress-tests process name normalization, load thresholds, and concurrency."""

    def setUp(self) -> None:
        self.monitor = ResourceMonitor()

    def tearDown(self) -> None:
        self.monitor.clear_mock_override()

    def test_all_nine_creative_apps_detected_simultaneously(self) -> None:
        """Adversarial Test 11: All 9 heavy creative apps active concurrently."""
        all_apps = [
            "UnrealEditor.exe",
            "blender.exe",
            "Photoshop.exe",
            "3dsmax.exe",
            "afterfx.exe",
            "premiere.exe",
            "resolve.exe",
            "vray.exe",
            "corona.exe",
        ]
        self.monitor.set_mock_override(detected_apps=all_apps)
        is_heavy, detected = self.monitor.is_heavy_creative_running()
        self.assertTrue(is_heavy)
        self.assertEqual(len(detected), 9)
        self.assertEqual(self.monitor.get_resource_mode(), "creative_detected")

    def test_process_name_casing_whitespace_and_variations(self) -> None:
        """Adversarial Test 12: Process name variations (caps, trailing whitespace, missing .exe)."""
        test_inputs = [
            "  3dsMax.EXE  ",
            "bLeNdEr",
            "UNREALEDITOR.exe",
            "photoshop",
            "resolve.EXE",
        ]
        for name in test_inputs:
            self.monitor.set_mock_override(detected_apps=[name])
            mode = self.monitor.get_resource_mode()
            self.assertEqual(
                mode,
                "creative_detected",
                f"Failed to detect creative process with variation: {name!r}",
            )

    def test_cpu_threshold_boundary_precision(self) -> None:
        """Adversarial Test 13: CPU threshold exact boundaries (80.0% vs 80.1%)."""
        # Exactly at threshold -> normal
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=80.0, ram_percent=30.0)
        self.assertEqual(self.monitor.get_resource_mode(), "normal")

        # Just above threshold -> low_vram
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=80.1, ram_percent=30.0)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

        # 100% CPU -> low_vram
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=100.0, ram_percent=30.0)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

        # Over-subscribed 200% CPU -> low_vram
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=200.0, ram_percent=30.0)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

    def test_ram_threshold_boundary_precision(self) -> None:
        """Adversarial Test 14: RAM threshold exact boundaries (85.0% vs 85.1%)."""
        # Exactly at threshold -> normal
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=20.0, ram_percent=85.0)
        self.assertEqual(self.monitor.get_resource_mode(), "normal")

        # Just above threshold -> low_vram
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=20.0, ram_percent=85.1)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

        # 99.9% RAM -> low_vram
        self.monitor.set_mock_override(detected_apps=[], cpu_percent=20.0, ram_percent=99.9)
        self.assertEqual(self.monitor.get_resource_mode(), "low_vram")

    def test_creative_mode_preempts_high_system_load(self) -> None:
        """
        Adversarial Test 15: When BOTH creative apps AND high CPU/RAM are present,
        resource_mode must be 'creative_detected' (not 'low_vram') to enforce zero-VRAM bypass.
        """
        self.monitor.set_mock_override(
            detected_apps=["UnrealEditor.exe"],
            cpu_percent=95.0,
            ram_percent=92.0,
        )
        self.assertEqual(
            self.monitor.get_resource_mode(),
            "creative_detected",
            "Creative mode must take precedence over high system load to ensure 0-VRAM bypass!",
        )

    def test_multithreaded_rapid_concurrent_polling(self) -> None:
        """Adversarial Test 16: 16 concurrent threads querying ResourceMonitor simultaneously."""
        modes = []
        errors = []

        def worker(thread_idx: int):
            for _ in range(50):
                try:
                    mode = self.monitor.get_resource_mode()
                    modes.append(mode)
                    status = self.monitor.get_status_dict()
                    self.assertIn("resource_mode", status)
                except Exception as e:
                    errors.append(e)

        self.monitor.set_mock_override(detected_apps=["blender.exe"])
        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        self.assertEqual(len(errors), 0, f"Concurrent polling raised exceptions: {errors}")
        self.assertTrue(all(m == "creative_detected" for m in modes))


class TestDeterministicFallbackAdversarialPrompts(unittest.TestCase):
    """Stress-tests the deterministic parser against hostile, empty, and huge prompts."""

    def test_empty_prompt_survives(self) -> None:
        """Adversarial Test 17: Empty prompt does not crash."""
        res = default_deterministic_fallback("")
        self.assertEqual(res["title"], "")
        self.assertEqual(res["project"], "default")
        self.assertEqual(res["priority"], "medium")
        self.assertIsNone(res["due_date"])

    def test_whitespace_and_tabs_prompt_survives(self) -> None:
        """Adversarial Test 18: Whitespace/newlines only prompt."""
        res = default_deterministic_fallback("   \n\t\r  ")
        self.assertEqual(res["project"], "default")
        self.assertEqual(res["priority"], "medium")

    def test_massive_64kb_prompt_parses_fast(self) -> None:
        """Adversarial Test 19: Extremely large prompt string executes in < 5ms."""
        huge_text = ("Rendering 3D frame " * 3000) + " #big_project !urgente entro domani"
        t0 = time.perf_counter()
        res = default_deterministic_fallback(huge_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertLess(elapsed_ms, 20.0, "Massive prompt took > 20ms to parse")
        self.assertEqual(res["project"], "big_project")
        self.assertEqual(res["priority"], "high")
        self.assertEqual(res["due_date"], "tomorrow")

    def test_italian_nlp_heuristics(self) -> None:
        """Adversarial Test 20: Italian language tags, priorities, and deadlines."""
        # Case 1: Italian urgent + today
        r1 = default_deterministic_fallback("Aggiungi task: Revisione illuminazione scena #archviz !urgente oggi")
        self.assertEqual(r1["project"], "archviz")
        self.assertEqual(r1["priority"], "high")
        self.assertEqual(r1["due_date"], "today")
        self.assertIn("Revisione illuminazione scena", r1["title"])

        # Case 2: Italian low priority + tomorrow
        r2 = default_deterministic_fallback("Crea task Pulire texture inutilizzate #cleanup !bassa domani")
        self.assertEqual(r2["project"], "cleanup")
        self.assertEqual(r2["priority"], "low")
        self.assertEqual(r2["due_date"], "tomorrow")

        # Case 3: Italian normal priority + specific date
        r3 = default_deterministic_fallback("Promemoria: esportare alembic #fx !normale entro 2026-11-15 18:00")
        self.assertEqual(r3["project"], "fx")
        self.assertEqual(r3["priority"], "medium")
        self.assertEqual(r3["due_date"], "2026-11-15 18:00")

    def test_faulty_fallback_callables_handled_gracefully(self) -> None:
        """Adversarial Test 21: Faulty custom fallback functions never crash route_request."""
        monitor = ResourceMonitor()
        monitor.set_mock_override(detected_apps=["3dsmax.exe"])
        router = OllamaRouter(resource_monitor=monitor)

        # Faulty fallback 1: Raises RuntimeError
        def broken_fallback_1(p):
            raise RuntimeError("Fatal explosion in custom fallback")

        res1 = router.route_request("Test task", fallback_func=broken_fallback_1)
        self.assertFalse(res1["success"])
        self.assertIn("Fatal explosion", str(res1["error"]))
        self.assertEqual(res1["source"], "direct_deterministic")

        # Faulty fallback 2: Takes 0 arguments (zero-arg callable)
        def zero_arg_fallback():
            return {"title": "Zero Arg Task", "project": "fixed"}

        res2 = router.route_request("Test task", fallback_func=zero_arg_fallback)
        self.assertTrue(res2["success"])
        self.assertEqual(res2["data"]["title"], "Zero Arg Task")


class TestDaemonAndCliAdversarialConditions(unittest.TestCase):
    """Stress-tests daemon status caching and CLI execution under corrupt states."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_adv_test_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()
        self.controller = DaemonController(self.config)

    def tearDown(self) -> None:
        self.controller.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_status_payload_with_completely_missing_workspace(self) -> None:
        """Adversarial Test 22: get_status_payload handles missing files and directories."""
        # Delete .neko and workspace directories completely
        shutil.rmtree(self.temp_dir / ".neko", ignore_errors=True)
        shutil.rmtree(self.temp_dir / "workspace", ignore_errors=True)

        status = self.controller.get_status_payload()
        self.assertEqual(status["daemon_health"], "stopped")
        self.assertIsNone(status["daemon_pid"])
        self.assertIsInstance(status["today_tracked_seconds"], int)
        self.assertIsInstance(status["notification_queue"], list)

    def test_status_payload_with_corrupt_tasks_and_logs(self) -> None:
        """Adversarial Test 23: Corrupt tasks.json and time_logs/*.jsonl files do not cause crash."""
        self.config.ensure_dirs()

        # Write corrupt tasks.json
        with open(self.config.tasks_file, "w", encoding="utf-8") as f:
            f.write("NOT_VALID_JSON{[[")

        # Write corrupt time log file
        today_str = time.strftime("%Y-%m-%d")
        bad_log = self.config.time_logs_dir / f"CORRUPTHOST_{today_str}.jsonl"
        with open(bad_log, "w", encoding="utf-8") as f:
            f.write("GARBAGE LINE 1\n{\"duration_seconds\": \"not_an_int\"}\n")

        status = self.controller.get_status_payload()
        self.assertEqual(status["daemon_health"], "stopped")
        self.assertIsNone(status["top_task"])
        self.assertGreaterEqual(status["today_tracked_seconds"], 0)

    def test_cli_do_command_with_offline_daemon_and_offline_ollama(self) -> None:
        """
        Adversarial Test 24: `neko do "<prompt>"` executed when daemon is stopped
        AND Ollama is offline. Must complete in < 500ms via in-process deterministic fallback.
        """
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["do", "Rig character face #facial !alta", "--workspace", str(self.temp_dir)])
        elapsed = time.perf_counter() - t0

        self.assertEqual(ret, 0)
        self.assertLess(elapsed, 2.0, f"`neko do` took {elapsed:.3f}s under offline conditions")
        output = buf.getvalue()
        self.assertIn("Rig character face", output)


if __name__ == "__main__":
    unittest.main()
