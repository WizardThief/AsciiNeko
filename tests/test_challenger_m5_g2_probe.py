"""
tests/test_challenger_m5_g2_probe.py
====================================
Adversarial Empirical Challenge Probe for Milestone 5:
- Live Server HTTP Contract Probe (HTTP 200, mcp_connectors, skills_registry, 6-track context_budget)
- Live Control Plane Toggling over HTTP (MCP & Skills toggle with channel token deduction & restoration)
- Concurrent HTTP Request Stress (25 threads)
- Rapid Start/Stop Socket Port 8765 Release & Linger Stress Harness (5 consecutive rapid cycles)
- Windows Process Table Hygiene (zero lingering python processes)
- Production Data Hygiene & Zero Pollution in bridge/data/

Authored by: challenger_m5_g2 (Empirical Adversarial Verifier)
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
    s.settimeout(0.5)
    try:
        res = s.connect_ex(("127.0.0.1", port))
        return res == 0
    finally:
        s.close()


def http_get(url: str, timeout: float = 5.0) -> Tuple[int, Dict[str, Any]]:
    """Performs HTTP GET and returns (status_code, json_body)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Neko-M5-Challenger-G2"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.status
        body = json.loads(resp.read().decode("utf-8"))
        return code, body


def http_post(url: str, payload: Any, timeout: float = 5.0) -> Tuple[int, Dict[str, Any]]:
    """Performs HTTP POST with JSON body and returns (status_code, json_body)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Neko-M5-Challenger-G2",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.status
        body = json.loads(resp.read().decode("utf-8"))
        return code, body


def kill_existing_companion_processes():
    """Terminate any lingering ai_monitor or app_window python processes."""
    cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*ai_monitor.py*' -or $_.CommandLine -like '*app_window.py*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, check=False)
    time.sleep(0.5)


def get_lingering_companion_pids() -> List[int]:
    """Return list of PIDs for lingering ai_monitor or app_window python processes."""
    cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*ai_monitor.py*' -or $_.CommandLine -like '*app_window.py*') } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, check=False)
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip().isdigit()]
    return [int(x) for x in lines]


class TestLiveServerAdversarialProbes(unittest.TestCase):
    """Adversarial suite probing live server lifecycle, socket hygiene, and data isolation."""

    @classmethod
    def setUpClass(cls):
        kill_existing_companion_processes()

    @classmethod
    def tearDownClass(cls):
        kill_existing_companion_processes()

    def test_01_live_server_complete_contracts(self):
        """Verify live server starts, returns HTTP 200 on /status with valid mcp_connectors, skills_registry, and 6-track context_budget."""
        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        proc = subprocess.Popen(
            [sys.executable, server_script, "--test-delivery"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            ready = False
            t0 = time.time()
            snap = {}
            while time.time() - t0 < 10.0:
                if is_port_in_use(PORT):
                    try:
                        code, snap = http_get(f"{BASE_URL}/status", timeout=2.0)
                        if code == 200:
                            ready = True
                            break
                    except Exception:
                        pass
                time.sleep(0.2)

            self.assertTrue(ready, "Live server failed to bind and respond HTTP 200 on /status within 10s")

            # 1. Check mcp_connectors
            self.assertIn("mcp_connectors", snap, "Missing 'mcp_connectors' in /status snapshot")
            mcps = snap["mcp_connectors"]
            self.assertIsInstance(mcps, list, "'mcp_connectors' must be a list")
            self.assertGreaterEqual(len(mcps), 4, "Expected at least 4 built-in MCP connectors")
            mcp_ids = [m.get("id") for m in mcps]
            expected_mcps = ["mcp_filesystem_scanner", "mcp_calendar_sync", "mcp_dcc_telemetry", "mcp_vram_guard"]
            for em in expected_mcps:
                self.assertIn(em, mcp_ids, f"Expected MCP connector '{em}' not found in status snapshot")
            for m in mcps:
                self.assertIn("id", m)
                self.assertIn("name", m)
                self.assertIn("enabled", m)
                self.assertIn("estimated_tokens", m)
                self.assertIsInstance(m["enabled"], bool)
                self.assertIsInstance(m["estimated_tokens"], int)

            # 2. Check skills_registry
            self.assertIn("skills_registry", snap, "Missing 'skills_registry' in /status snapshot")
            skills_dict = snap["skills_registry"]
            self.assertIsInstance(skills_dict, dict, "'skills_registry' must be a dict")
            self.assertIn("skills", skills_dict)
            skills = skills_dict["skills"]
            self.assertIsInstance(skills, list, "'skills_registry.skills' must be a list")
            self.assertGreaterEqual(len(skills), 2, "Expected skills in skills_registry")
            skill_ids = [s.get("id") for s in skills]
            self.assertIn("glb_draco_optimizer", skill_ids, "ECC skill 'glb_draco_optimizer' missing from skills_registry")
            self.assertIn("client_brand_compliance", skill_ids, "ECC skill 'client_brand_compliance' missing from skills_registry")
            for s in skills:
                self.assertIn("id", s)
                self.assertIn("name", s)
                self.assertIn("format", s)
                self.assertIn("enabled", s)
                self.assertIn("estimated_tokens", s)
                self.assertIn(s["format"], ("builtin", "yaml", "markdown"))

            # 3. Check 6-track context_budget breakdown
            self.assertIn("context_budget", snap, "Missing 'context_budget' in /status snapshot")
            cb = snap["context_budget"]
            self.assertIn("total_budget", cb)
            self.assertIn("used_tokens", cb)
            self.assertIn("usage_pct", cb)
            self.assertIn("breakdown", cb)

            breakdown = cb["breakdown"]
            expected_tracks = [
                "system",
                "profile_and_skills",
                "instincts",
                "memories",
                "projects_and_todos",
                "chat_history",
            ]
            self.assertEqual(len(expected_tracks), 6, "Expected exactly 6 tracks in breakdown")
            track_sum = 0
            for t in expected_tracks:
                self.assertIn(t, breakdown, f"Missing track '{t}' in context_budget breakdown")
                self.assertIsInstance(breakdown[t], int, f"Track '{t}' token count must be int")
                self.assertGreaterEqual(breakdown[t], 0, f"Track '{t}' token count cannot be negative")
                track_sum += breakdown[t]

            self.assertEqual(
                cb["used_tokens"],
                track_sum,
                f"used_tokens ({cb['used_tokens']}) does not equal sum of tracks ({track_sum})",
            )

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

        time.sleep(0.3)
        self.assertFalse(is_port_in_use(PORT), f"Port {PORT} was not freed after server termination")

    def test_02_live_control_plane_toggling(self):
        """Verify real-time MCP and Skill toggle over live HTTP with immediate channel token deduction and restoration."""
        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        proc = subprocess.Popen(
            [sys.executable, server_script, "--test-delivery"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            t0 = time.time()
            ready = False
            while time.time() - t0 < 10.0:
                if is_port_in_use(PORT):
                    try:
                        code, _ = http_get(f"{BASE_URL}/status", timeout=2.0)
                        if code == 200:
                            ready = True
                            break
                    except Exception:
                        pass
                time.sleep(0.2)
            self.assertTrue(ready, "Live server not ready for control plane test")

            # 1. Baseline status
            _, snap0 = http_get(f"{BASE_URL}/status")
            vram_mcp = next(m for m in snap0["mcp_connectors"] if m["id"] == "mcp_vram_guard")
            vram_tokens = vram_mcp["estimated_tokens"]
            self.assertEqual(vram_tokens, 20)

            # 2. Toggle mcp_vram_guard OFF
            code, resp_off = http_post(f"{BASE_URL}/api/mcp/toggle", {"id": "mcp_vram_guard", "enabled": False})
            self.assertEqual(code, 200)
            self.assertTrue(resp_off.get("ok"))
            sys_after_off = resp_off["context_budget"]["breakdown"]["system"]

            # 3. Toggle mcp_vram_guard back ON
            code, resp_on = http_post(f"{BASE_URL}/api/mcp/toggle", {"id": "mcp_vram_guard", "enabled": True})
            self.assertEqual(code, 200)
            self.assertTrue(resp_on.get("ok"))
            sys_after_on = resp_on["context_budget"]["breakdown"]["system"]
            self.assertEqual(
                sys_after_on - sys_after_off,
                vram_tokens,
                f"Toggling mcp_vram_guard ON should increase system channel by {vram_tokens}: was {sys_after_off}, now {sys_after_on}",
            )

            # 4. Toggle skill glb_draco_optimizer OFF and ON
            glb_skill = next(s for s in snap0["skills_registry"]["skills"] if s["id"] == "glb_draco_optimizer")
            glb_tokens = glb_skill["estimated_tokens"]

            code, s_off = http_post(f"{BASE_URL}/api/skills/toggle", {"skill_id": "glb_draco_optimizer", "enabled": False})
            self.assertEqual(code, 200)
            self.assertTrue(s_off.get("ok"))
            prof_after_off = s_off["context_budget"]["breakdown"]["profile_and_skills"]

            # Re-enable skill
            code, s_on = http_post(f"{BASE_URL}/api/skills/toggle", {"skill_id": "glb_draco_optimizer", "enabled": True})
            self.assertEqual(code, 200)
            self.assertTrue(s_on.get("ok"))
            prof_after_on = s_on["context_budget"]["breakdown"]["profile_and_skills"]
            delta = prof_after_on - prof_after_off
            self.assertGreater(delta, 0, "Toggling skill ON must increase profile_and_skills tokens")
            self.assertAlmostEqual(
                delta,
                glb_tokens,
                delta=20,
                msg=f"Toggling glb_draco_optimizer ON should restore profile_and_skills by approx {glb_tokens}: got {delta}",
            )

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

        time.sleep(0.3)
        self.assertFalse(is_port_in_use(PORT))

    def test_03_concurrent_requests_stress(self):
        """Stress test live server with 25 concurrent requests across multiple endpoints."""
        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        proc = subprocess.Popen(
            [sys.executable, server_script, "--test-delivery"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            t0 = time.time()
            ready = False
            while time.time() - t0 < 10.0:
                if is_port_in_use(PORT):
                    try:
                        code, _ = http_get(f"{BASE_URL}/status", timeout=2.0)
                        if code == 200:
                            ready = True
                            break
                    except Exception:
                        pass
                time.sleep(0.2)
            self.assertTrue(ready, "Live server not ready for concurrency stress")

            endpoints = [
                f"{BASE_URL}/status",
                f"{BASE_URL}/api/skills",
                f"{BASE_URL}/api/mcp",
                f"{BASE_URL}/api/playbooks",
            ]

            results = []

            def worker_req(url):
                return http_get(url, timeout=5.0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker_req, endpoints[i % len(endpoints)]) for i in range(25)]
                for f in concurrent.futures.as_completed(futures):
                    code, body = f.result()
                    results.append(code)

            self.assertEqual(len(results), 25)
            self.assertTrue(all(c == 200 for c in results), f"Non-200 responses under concurrency: {results}")

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

        time.sleep(0.3)
        self.assertFalse(is_port_in_use(PORT))

    def test_04_rapid_restart_socket_release_stress_harness(self):
        """Adversarially probe rapid start-query-stop cycles (5 consecutive cycles) to detect socket TIME_WAIT or bind failures."""
        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")

        for cycle in range(1, 6):
            # Port must be free before starting
            self.assertFalse(is_port_in_use(PORT), f"Cycle {cycle}: Port {PORT} is occupied prior to launch!")

            proc = subprocess.Popen(
                [sys.executable, server_script, "--test-delivery"],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                ready = False
                t0 = time.time()
                while time.time() - t0 < 8.0:
                    if is_port_in_use(PORT):
                        try:
                            code, snap = http_get(f"{BASE_URL}/status", timeout=2.0)
                            if code == 200 and "active_context_mode" in snap:
                                ready = True
                                break
                        except Exception:
                            pass
                    time.sleep(0.1)

                self.assertTrue(ready, f"Cycle {cycle}: Live server failed to respond on 127.0.0.1:{PORT}")

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

            # Immediate check that port is released (within 2s max)
            port_freed = False
            t_free = time.time()
            while time.time() - t_free < 2.0:
                if not is_port_in_use(PORT):
                    port_freed = True
                    break
                time.sleep(0.05)

            self.assertTrue(port_freed, f"Cycle {cycle}: Port {PORT} was NOT freed within 2.0s after termination!")

    def test_05_process_table_hygiene_no_zombies(self):
        """Verify Windows process table contains zero orphaned ai_monitor or app_window python processes."""
        lingering = get_lingering_companion_pids()
        self.assertEqual(lingering, [], f"Zombie Python companion processes detected in OS process table: {lingering}")

    def test_06_data_hygiene_and_authenticity(self):
        """Verify absolute data purity in bridge/data/ (no checkpoints, no tmp, authentic records intact)."""
        # 1. session_checkpoint.json must not exist
        ckpt = os.path.join(DATA_DIR, "session_checkpoint.json")
        self.assertFalse(os.path.isfile(ckpt), f"session_checkpoint.json exists: {ckpt}")

        # 2. Zero temporary or backup files
        bad_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".tmp") or f.endswith(".bak") or ".corrupt" in f]
        self.assertEqual(bad_files, [], f"Dirty files found in bridge/data: {bad_files}")

        # 3. Authentic user profile
        prof_file = os.path.join(DATA_DIR, "profile.json")
        self.assertTrue(os.path.isfile(prof_file))
        with open(prof_file, "r", encoding="utf-8") as f:
            prof = json.load(f)
        self.assertEqual(prof.get("name"), "Diego")

        # 4. Authentic project
        proj_file = os.path.join(DATA_DIR, "projects.json")
        self.assertTrue(os.path.isfile(proj_file))
        with open(proj_file, "r", encoding="utf-8") as f:
            projs = json.load(f)
        proj_ids = [p["id"] for p in projs]
        self.assertIn("proj_nave_intermarine", proj_ids)

        # 5. Authentic todos (4 tasks)
        todo_file = os.path.join(DATA_DIR, "todo.json")
        self.assertTrue(os.path.isfile(todo_file))
        with open(todo_file, "r", encoding="utf-8") as f:
            todos = json.load(f)
        self.assertEqual(len(todos), 4)

        # 6. Baseline instincts use_count
        inst_file = os.path.join(DATA_DIR, "instincts.json")
        self.assertTrue(os.path.isfile(inst_file))
        with open(inst_file, "r", encoding="utf-8") as f:
            insts = json.load(f)
        counts = [i.get("use_count") for i in insts]
        self.assertEqual(counts, [1, 1], f"Instincts baseline use_count expected [1, 1], got {counts}")


if __name__ == "__main__":
    unittest.main()
