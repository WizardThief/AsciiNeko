"""
tests/test_live_server_delivery.py
==================================
Milestone 5: Final Live Verification & E2E Validation of CompanionServer HTTP Service.

Target: http://127.0.0.1:8765
Endpoints Verified:
1. GET /status:
   - Snapshot contains active_context_mode
   - Snapshot contains context_budget with complete 6-track breakdown:
     ('system', 'profile_and_skills', 'instincts', 'memories', 'projects_and_todos', 'chat_history')
   - Snapshot contains instincts (list with seeded items)
   - Snapshot contains window_task_suggestion
   - Snapshot contains session_checkpoint
2. POST /api/context_mode:
   - Switching to 'project_manager'
   - Switching to 'knowledge_curator'
   - Switching back to 'operativo'
   - Case-insensitivity ('PROJECT_MANAGER')
   - Graceful fallback for invalid/non-string modes (None, 'invalid_mode')
3. POST /api/projects/quality_gate:
   - Verification of 'proj_nave_intermarine'
   - Returns readiness_score, status_label, task_summary, client_rules_checklist,
     technical_instincts_checklist, and recommendations.
4. GET /api/playbooks:
   - Discovery of available playbooks and count.
5. Server Lifecycle & Port Hygiene:
   - Clean startup, verification, and termination. Port 8765 is confirmed freed.
6. Data Purity Assertion:
   - Zero .tmp files in bridge/data/
   - Zero .bak files in bridge/data/
   - Zero session_checkpoint.json in bridge/data/
   - instincts.json baseline use_count is [1, 1]
"""

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
    req = urllib.request.Request(url, headers={"User-Agent": "Neko-M5-Delivery-Tester"})
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
            "User-Agent": "Neko-M5-Delivery-Tester",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.status
        body = json.loads(resp.read().decode("utf-8"))
        return code, body


class TestLiveCompanionServerDelivery(unittest.TestCase):
    """End-to-End Live Verification of the CompanionServer."""

    server_process: Optional[subprocess.Popen] = None

    @classmethod
    def setUpClass(cls):
        # 1. Ensure port 8765 is not occupied by a stale zombie process
        if is_port_in_use(PORT):
            # Attempt to kill previous instances if any
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*ai_monitor.py*' -or $_.CommandLine -like '*app_window.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
                ],
                capture_output=True,
                check=False,
            )
            time.sleep(1.0)

        # 2. Launch the companion server with --test-delivery flag
        server_script = os.path.join(BRIDGE_DIR, "ai_monitor.py")
        cls.server_process = subprocess.Popen(
            [sys.executable, server_script, "--test-delivery"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # 3. Wait for port 8765 to become live
        t_start = time.time()
        ready = False
        while time.time() - t_start < 12.0:
            if is_port_in_use(PORT):
                try:
                    code, _ = http_get(f"{BASE_URL}/status", timeout=2.0)
                    if code == 200:
                        ready = True
                        break
                except Exception:
                    pass
            time.sleep(0.2)

        if not ready:
            cls.tearDownClass()
            raise RuntimeError(f"Server on 127.0.0.1:{PORT} failed to start within 12 seconds.")

    @classmethod
    def tearDownClass(cls):
        # Cleanly terminate the test server process
        if cls.server_process:
            try:
                cls.server_process.terminate()
                cls.server_process.wait(timeout=3.0)
            except Exception:
                try:
                    cls.server_process.kill()
                    cls.server_process.wait(timeout=2.0)
                except Exception:
                    pass
            finally:
                if cls.server_process.stdout:
                    cls.server_process.stdout.close()
                if cls.server_process.stderr:
                    cls.server_process.stderr.close()
            cls.server_process = None

        # Give OS time to close sockets
        time.sleep(0.5)

    def test_01_status_endpoint_complete_structure(self):
        """GET /status: returns snapshot with active_context_mode, 6-track budget, instincts, window suggestion, session checkpoint."""
        code, snap = http_get(f"{BASE_URL}/status")
        self.assertEqual(code, 200, "GET /status must return HTTP 200")
        self.assertIsInstance(snap, dict, "Snapshot must be a JSON object")

        # 1. active_context_mode
        self.assertIn("active_context_mode", snap, "Snapshot must contain active_context_mode")
        self.assertIn(
            snap["active_context_mode"],
            ("operativo", "project_manager", "knowledge_curator"),
            f"Unexpected active_context_mode: {snap.get('active_context_mode')}",
        )

        # 2. context_budget & 6-track breakdown
        self.assertIn("context_budget", snap, "Snapshot must contain context_budget")
        budget = snap["context_budget"]
        self.assertIsInstance(budget, dict)
        self.assertIn("total_budget", budget)
        self.assertIn("used_tokens", budget)
        self.assertIn("usage_pct", budget)
        self.assertIn("breakdown", budget)

        expected_tracks = [
            "system",
            "profile_and_skills",
            "instincts",
            "memories",
            "projects_and_todos",
            "chat_history",
        ]
        breakdown = budget["breakdown"]
        for track in expected_tracks:
            self.assertIn(track, breakdown, f"context_budget breakdown missing track '{track}'")
            self.assertIsInstance(breakdown[track], int, f"Track '{track}' must be an int")
            self.assertGreaterEqual(breakdown[track], 0, f"Track '{track}' must be >= 0")

        # 3. instincts
        self.assertIn("instincts", snap, "Snapshot must contain instincts")
        instincts = snap["instincts"]
        self.assertIsInstance(instincts, list)
        self.assertGreaterEqual(len(instincts), 2, "Instincts list must have at least 2 seeded instincts")
        seed_ids = [i.get("id") for i in instincts]
        self.assertIn("inst_seed_intermarine_logo", seed_ids)
        self.assertIn("inst_seed_glb_draco_pipeline", seed_ids)

        # 4. window_task_suggestion
        self.assertIn("window_task_suggestion", snap, "Snapshot must contain window_task_suggestion key")

        # 5. session_checkpoint
        self.assertIn("session_checkpoint", snap, "Snapshot must contain session_checkpoint key")

    def test_02_context_mode_api_cycling_and_fallbacks(self):
        """POST /api/context_mode: cycles through project_manager, knowledge_curator, operativo with case-insensitivity & fallback."""
        url = f"{BASE_URL}/api/context_mode"

        # 1. Switch to project_manager
        code, resp = http_post(url, {"mode": "project_manager"})
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("active_mode"), "project_manager")
        # Verify reflected in /status
        _, status_snap = http_get(f"{BASE_URL}/status")
        self.assertEqual(status_snap.get("active_context_mode"), "project_manager")

        # 2. Switch to knowledge_curator
        code, resp = http_post(url, {"mode": "knowledge_curator"})
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("active_mode"), "knowledge_curator")
        _, status_snap = http_get(f"{BASE_URL}/status")
        self.assertEqual(status_snap.get("active_context_mode"), "knowledge_curator")

        # 3. Switch to operativo
        code, resp = http_post(url, {"mode": "operativo"})
        self.assertEqual(code, 200)
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("active_mode"), "operativo")
        _, status_snap = http_get(f"{BASE_URL}/status")
        self.assertEqual(status_snap.get("active_context_mode"), "operativo")

        # 4. Case-insensitivity: 'PROJECT_MANAGER'
        code, resp = http_post(url, {"mode": "PROJECT_MANAGER"})
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("active_mode"), "project_manager")

        # 5. Graceful fallback for invalid mode
        code, resp = http_post(url, {"mode": "invalid_mode_xyz_unknown"})
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("active_mode"), "operativo", "Invalid mode must fall back to 'operativo'")

        # 6. Graceful fallback for non-string / null mode
        code, resp = http_post(url, {"mode": None})
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("active_mode"), "operativo", "Null mode must fall back to 'operativo'")

        # Ensure server ends in 'operativo'
        http_post(url, {"mode": "operativo"})

    def test_03_quality_gate_inspection_endpoint(self):
        """POST /api/projects/quality_gate: verifies proj_nave_intermarine inspection."""
        url = f"{BASE_URL}/api/projects/quality_gate"
        code, resp = http_post(url, {"project_id": "proj_nave_intermarine"})
        self.assertEqual(code, 200, "POST /api/projects/quality_gate must return HTTP 200")
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("status"), "ok")

        self.assertIn("quality_gate", resp, "Response must include 'quality_gate' report")
        qg = resp["quality_gate"]

        # Check required fields
        self.assertEqual(qg.get("project_id"), "proj_nave_intermarine")
        self.assertIn("readiness_score", qg)
        self.assertIsInstance(qg["readiness_score"], int)
        self.assertTrue(0 <= qg["readiness_score"] <= 100)

        self.assertIn("status_label", qg)
        self.assertIn(qg["status_label"], ("BLOCKED", "NEEDS_ATTENTION", "READY_FOR_DELIVERY", "PASSED"))

        self.assertIn("task_summary", qg)
        ts = qg["task_summary"]
        self.assertIn("total", ts)
        self.assertIn("done", ts)
        self.assertIn("todo", ts)
        self.assertIn("completion_pct", ts)

        self.assertIn("client_rules_checklist", qg)
        self.assertIsInstance(qg["client_rules_checklist"], list)
        self.assertGreater(len(qg["client_rules_checklist"]), 0)
        for r in qg["client_rules_checklist"]:
            self.assertIn("rule", r)
            self.assertIn("status", r)
            self.assertIn("pinned", r)

        self.assertIn("technical_instincts_checklist", qg)
        self.assertIsInstance(qg["technical_instincts_checklist"], list)
        self.assertGreater(len(qg["technical_instincts_checklist"]), 0)
        for i in qg["technical_instincts_checklist"]:
            self.assertIn("instinct_id", i)
            self.assertIn("trigger", i)
            self.assertIn("action", i)
            self.assertIn("status", i)

        self.assertIn("recommendations", qg)
        self.assertIsInstance(qg["recommendations"], list)

    def test_04_playbooks_discovery_endpoint(self):
        """GET /api/playbooks: verifies discovery and listing of playbooks."""
        code, resp = http_get(f"{BASE_URL}/api/playbooks")
        self.assertEqual(code, 200, "GET /api/playbooks must return HTTP 200")
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("status"), "ok")
        self.assertIn("playbooks", resp)
        self.assertIsInstance(resp["playbooks"], list)
        self.assertIn("count", resp)
        self.assertEqual(resp["count"], len(resp["playbooks"]))

    def test_05_clean_server_termination_and_port_freed(self):
        """Clean termination of test server frees port 8765."""
        # Terminate server process early for this assertion
        if self.__class__.server_process:
            proc = self.__class__.server_process
            try:
                proc.terminate()
                proc.wait(timeout=5.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
            finally:
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()
            self.__class__.server_process = None

        time.sleep(0.5)
        self.assertFalse(
            is_port_in_use(PORT),
            f"Port {PORT} must be completely freed after server process termination.",
        )

    def test_06_data_purity_assertion(self):
        """Verify 100% data purity in bridge/data/ after server execution."""
        # 1. Zero .tmp files
        tmp_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".tmp") or ".tmp" in f]
        self.assertEqual(tmp_files, [], f"Data directory contains .tmp files: {tmp_files}")

        # 2. Zero .bak files
        bak_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".bak") or ".bak" in f]
        self.assertEqual(bak_files, [], f"Data directory contains .bak files: {bak_files}")

        # 3. Zero session_checkpoint.json
        ckpt_file = os.path.join(DATA_DIR, "session_checkpoint.json")
        self.assertFalse(
            os.path.isfile(ckpt_file),
            f"Production data directory contains unwanted session_checkpoint.json: {ckpt_file}",
        )

        # 4. instincts.json baseline use_count is [1, 1]
        inst_file = os.path.join(DATA_DIR, "instincts.json")
        self.assertTrue(os.path.isfile(inst_file), "instincts.json must exist")
        with open(inst_file, "r", encoding="utf-8") as f:
            insts = json.load(f)
        use_counts = [i.get("use_count") for i in insts]
        self.assertEqual(
            use_counts,
            [1, 1],
            f"Instincts use_count violated baseline [1, 1]: got {use_counts}",
        )


if __name__ == "__main__":
    unittest.main()
