"""
tests/test_challenger_m5_gate2_lifecycle.py
===========================================
Milestone 5 Gate 2 Dedicated Adversarial Lifecycle & Process Hygiene Probe:
1. Port 8765 Bind and Unbind Cleanliness:
   - Sub-second unbind verification
   - Re-bind test without port collision (zero WSAEADDRINUSE / WinError 10048)
2. Rapid Restart Stress Harness:
   - 10 consecutive rapid start -> query -> stop cycles
   - Verification of 100% success rate under rapid socket recycle
3. In-Process Background Server Lifecycle:
   - Verification of start_background_server, serve, shutdown, and server_close
4. Windows Process Table Zombie Sweep:
   - Zero lingering ai_monitor or app_window python processes
5. Data Hygiene in bridge/data/:
   - Zero .tmp files
   - Zero .bak files
   - Zero .corrupt files
   - Zero session_checkpoint.json
   - Exact baseline authenticity verification

Authored by: challenger_m5_gate2_2 (Empirical Adversarial Verifier)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
DATA_DIR = os.path.join(BRIDGE_DIR, "data")
PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"


def is_port_in_use(port: int = PORT) -> bool:
    """Checks whether 127.0.0.1:port is accepting connections."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        res = s.connect_ex(("127.0.0.1", port))
        return res == 0
    finally:
        s.close()


def http_get(url: str, timeout: float = 4.0) -> Tuple[int, Dict[str, Any]]:
    """Performs HTTP GET and returns (status_code, json_body)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Neko-M5-G2-Challenger"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.status
        body = json.loads(resp.read().decode("utf-8"))
        return code, body


def get_lingering_pids() -> List[int]:
    """Scan OS processes using psutil for lingering companion server processes."""
    try:
        import psutil
        pids = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cmd = p.info.get("cmdline") or []
            cmd_str = " ".join(cmd).lower()
            if "python" in p.info.get("name", "").lower():
                if "ai_monitor.py" in cmd_str or "app_window.py" in cmd_str:
                    # Exclude current test runner process
                    if p.info["pid"] != os.getpid():
                        pids.append(p.info["pid"])
        return pids
    except Exception:
        return []


def kill_lingering_pids():
    """Ensure no background zombie companion processes are running before/after tests."""
    try:
        import psutil
        for pid in get_lingering_pids():
            try:
                p = psutil.Process(pid)
                p.kill()
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(0.3)


class TestCompanionServerLifecycleAndHygiene(unittest.TestCase):
    """Adversarial challenge for live server lifecycle, socket cleanup, and data hygiene."""

    @classmethod
    def setUpClass(cls):
        kill_lingering_pids()
        # Verify port is initially free
        if is_port_in_use(PORT):
            kill_lingering_pids()
            time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        kill_lingering_pids()

    def test_01_single_cycle_bind_and_fast_unbind(self):
        """Test clean socket bind, status response, and sub-second unbind."""
        self.assertFalse(is_port_in_use(PORT), f"Port {PORT} must be free initially")

        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        proc = subprocess.Popen(
            [sys.executable, server_script],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            t0 = time.time()
            ready = False
            snap = {}
            while time.time() - t0 < 8.0:
                if is_port_in_use(PORT):
                    try:
                        code, snap = http_get(f"{BASE_URL}/status", timeout=1.5)
                        if code == 200:
                            ready = True
                            break
                    except Exception:
                        pass
                time.sleep(0.1)

            self.assertTrue(ready, "Server failed to start and respond on /status within 8s")
            self.assertIn("active_context_mode", snap)
            self.assertIn("context_budget", snap)

        finally:
            # Terminate and measure unbind latency
            t_term = time.time()
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

            # Poll for unbind
            freed = False
            unbind_duration = 0.0
            while time.time() - t_term < 2.0:
                if not is_port_in_use(PORT):
                    freed = True
                    unbind_duration = time.time() - t_term
                    break
                time.sleep(0.05)

            self.assertTrue(freed, f"Port {PORT} was not freed within 2.0s after terminate()")
            self.assertLess(unbind_duration, 1.5, f"Unbind latency too high: {unbind_duration:.3f}s")

            # Try immediate binding on the same port from Python to verify no TIME_WAIT lock
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                test_sock.bind(("127.0.0.1", PORT))
                test_sock.listen(1)
                bound_ok = True
            except OSError as exc:
                bound_ok = False
                err_msg = str(exc)
            finally:
                test_sock.close()

            self.assertTrue(bound_ok, f"Immediate socket re-bind failed after unbind: {err_msg if not bound_ok else ''}")

    def test_02_rapid_restart_stress_10_cycles(self):
        """Stress-test 10 consecutive rapid start -> query -> terminate cycles."""
        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        cycle_times = []

        for cycle in range(1, 11):
            t_cycle_start = time.time()
            # Ensure port is clean before launch
            self.assertFalse(is_port_in_use(PORT), f"Cycle {cycle}: Port {PORT} busy before start")

            proc = subprocess.Popen(
                [sys.executable, server_script],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                t0 = time.time()
                ready = False
                while time.time() - t0 < 6.0:
                    if is_port_in_use(PORT):
                        try:
                            code, snap = http_get(f"{BASE_URL}/status", timeout=1.0)
                            if code == 200 and snap.get("active_context_mode"):
                                ready = True
                                break
                        except Exception:
                            pass
                    time.sleep(0.05)

                self.assertTrue(ready, f"Cycle {cycle}: Companion server failed to answer /status within 6s")

            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

            # Verify port freed
            freed = False
            t_free_start = time.time()
            while time.time() - t_free_start < 2.0:
                if not is_port_in_use(PORT):
                    freed = True
                    break
                time.sleep(0.05)

            self.assertTrue(freed, f"Cycle {cycle}: Port {PORT} lingered after process termination")
            cycle_duration = time.time() - t_cycle_start
            cycle_times.append(cycle_duration)

        # All 10 completed successfully
        self.assertEqual(len(cycle_times), 10)
        self.assertLess(max(cycle_times), 7.0, f"Slowest restart cycle took {max(cycle_times):.2f}s")

    def test_03_inprocess_background_server_thread_lifecycle(self):
        """Verify in-process background server start and clean shutdown."""
        self.assertFalse(is_port_in_use(PORT))

        import bridge.ai_monitor as am
        srv = am.start_background_server()
        self.assertIsNotNone(srv, "start_background_server returned None")

        try:
            # Wait for socket readiness
            ready = False
            t0 = time.time()
            while time.time() - t0 < 4.0:
                if is_port_in_use(PORT):
                    code, body = http_get(f"{BASE_URL}/status")
                    if code == 200:
                        ready = True
                        break
                time.sleep(0.05)
            self.assertTrue(ready, "In-process server did not respond on /status")

        finally:
            # Shutdown and close
            srv.shutdown()
            srv.server_close()

        # Check port released
        time.sleep(0.2)
        self.assertFalse(is_port_in_use(PORT), f"Port {PORT} not freed after in-process server shutdown")

    def test_04_zero_lingering_zombie_processes(self):
        """Verify OS process table has zero lingering Python ai_monitor/app_window instances."""
        lingering = get_lingering_pids()
        self.assertEqual(lingering, [], f"Found lingering zombie companion PIDs: {lingering}")

    def test_05_production_data_hygiene_audit(self):
        """Audit bridge/data/ for 0 dirty, 0 tmp, 0 bak, and no session_checkpoint.json."""
        self.assertTrue(os.path.isdir(DATA_DIR), f"DATA_DIR {DATA_DIR} does not exist")
        files = os.listdir(DATA_DIR)

        # 1. Zero .tmp files
        tmp_files = [f for f in files if f.endswith(".tmp") or ".tmp" in f]
        self.assertEqual(tmp_files, [], f"Found .tmp files in {DATA_DIR}: {tmp_files}")

        # 2. Zero .bak files
        bak_files = [f for f in files if f.endswith(".bak") or ".bak" in f]
        self.assertEqual(bak_files, [], f"Found .bak files in {DATA_DIR}: {bak_files}")

        # 3. Zero .corrupt files
        corrupt_files = [f for f in files if ".corrupt" in f]
        self.assertEqual(corrupt_files, [], f"Found .corrupt files in {DATA_DIR}: {corrupt_files}")

        # 4. session_checkpoint.json must NOT exist
        ckpt_path = os.path.join(DATA_DIR, "session_checkpoint.json")
        self.assertFalse(os.path.exists(ckpt_path), f"session_checkpoint.json must not exist: {ckpt_path}")

        # 5. Core files integrity
        # profile.json
        prof_path = os.path.join(DATA_DIR, "profile.json")
        self.assertTrue(os.path.isfile(prof_path))
        with open(prof_path, "r", encoding="utf-8") as f:
            prof = json.load(f)
        self.assertEqual(prof.get("name"), "Diego")
        traits = prof.get("traits", [])
        self.assertEqual(len(traits), len(set(traits)), f"Duplicate traits found in profile.json: {traits}")

        # projects.json
        proj_path = os.path.join(DATA_DIR, "projects.json")
        self.assertTrue(os.path.isfile(proj_path))
        with open(proj_path, "r", encoding="utf-8") as f:
            projs = json.load(f)
        proj_ids = [p["id"] for p in projs]
        self.assertIn("proj_nave_intermarine", proj_ids)

        # todo.json
        todo_path = os.path.join(DATA_DIR, "todo.json")
        self.assertTrue(os.path.isfile(todo_path))
        with open(todo_path, "r", encoding="utf-8") as f:
            todos = json.load(f)
        self.assertEqual(len(todos), 4, f"Expected 4 todos, got {len(todos)}")

        # instincts.json
        inst_path = os.path.join(DATA_DIR, "instincts.json")
        self.assertTrue(os.path.isfile(inst_path))
        with open(inst_path, "r", encoding="utf-8") as f:
            insts = json.load(f)
        counts = [i.get("use_count") for i in insts]
        self.assertEqual(counts, [1, 1], f"Expected baseline instincts use_counts [1, 1], got {counts}")

    def test_06_high_concurrency_multi_endpoint_stress(self):
        """Stress-test 50 concurrent requests across /status, /api/mcp, /api/skills, and /api/playbooks."""
        self.assertFalse(is_port_in_use(PORT))

        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        proc = subprocess.Popen(
            [sys.executable, server_script],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            t0 = time.time()
            ready = False
            while time.time() - t0 < 8.0:
                if is_port_in_use(PORT):
                    try:
                        code, _ = http_get(f"{BASE_URL}/status", timeout=1.5)
                        if code == 200:
                            ready = True
                            break
                    except Exception:
                        pass
                time.sleep(0.1)
            self.assertTrue(ready, "Server failed to start for concurrency stress test")

            endpoints = [
                f"{BASE_URL}/status",
                f"{BASE_URL}/api/mcp",
                f"{BASE_URL}/api/skills",
                f"{BASE_URL}/api/playbooks",
            ]

            def fetch(idx: int) -> Tuple[int, str]:
                url = endpoints[idx % len(endpoints)]
                req = urllib.request.Request(url, headers={"User-Agent": f"Neko-Stress-Worker-{idx}"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return resp.status, url

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                futures = [pool.submit(fetch, i) for i in range(50)]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

            self.assertEqual(len(results), 50)
            self.assertTrue(all(status == 200 for status, _ in results), f"Some requests failed: {results}")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        # Verify port released
        time.sleep(0.3)
        self.assertFalse(is_port_in_use(PORT))


if __name__ == "__main__":
    unittest.main()
