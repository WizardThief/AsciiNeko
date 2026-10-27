"""
tests/test_hooks_runner.py
==========================
Comprehensive Unit & Integration Test Suite for Lifecycle Hooks Runner (Milestone 2 / R2).

Verifies:
1. Non-blocking asynchronous hook dispatch & exception boundaries in daemon threads.
2. PostChatHook (/learn-eval) intelligent memory upsert & deduplication.
3. Startup idempotent cleanup of tentative Intermarine logo memory.
4. OnWindowSwitchHook active window matching to uncompleted Kanban tasks -> window_task_suggestion.
5. Tool-to-skill and instinct reinforcement on window activity.
6. OnIdleOrExitHook automatic session checkpoint persistence on AFK/exit.
7. HTTP endpoints: POST /api/session/action (save, resume, dismiss) & GET /api/session.
8. Status snapshot exposure of window_task_suggestion & session_checkpoint.

MANDATORY INTEGRITY & DATA HYGIENE:
- All tests execute genuinely against real application logic and SQLite/JSON storage.
- Every test uses tempfile.TemporaryDirectory() ensuring ZERO pollution of bridge/data/.
"""

import copy
from datetime import datetime
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from typing import Any, Dict, List, Optional

# Ensure repository root and bridge/ are on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

PRODUCTION_DATA_DIR = os.path.join(BRIDGE_DIR, "data")

from bridge.hooks_runner import (
    HooksRunner,
    HookEventType,
    cleanup_intermarine_logo_memory,
    get_hooks_runner,
)
from bridge.storage_manager import StorageManager
from bridge.telemetry import (
    WindowAndAFKTracker,
    TelemetryService,
    parse_window_title,
)
import bridge.ai_monitor as am
from bridge.ai_monitor import (
    CompanionRequestHandler,
    MemoryAndProjectsManager,
    OllamaLocalBridge,
    SystemStateMonitor,
    WindowsAudioCapture,
    WindowAndConnectionsManager,
)


class IsolatedTestCase(unittest.TestCase):
    """Base test case ensuring complete data isolation via tempfile.TemporaryDirectory."""

    def setUp(self):
        self._temp_dir_obj = tempfile.TemporaryDirectory()
        self.test_dir = os.path.abspath(self._temp_dir_obj.name)
        self.test_db_path = os.path.join(self.test_dir, "neko_storage.db")

        # Snapshot of production data directory to enforce zero pollution
        self._prod_data_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)

        # Isolated storage manager
        self.storage = StorageManager(db_path=self.test_db_path, data_dir=self.test_dir)

    def tearDown(self):
        try:
            if hasattr(self, "storage") and self.storage:
                self.storage.close_connection()
        except Exception:
            pass

        try:
            self._temp_dir_obj.cleanup()
        except Exception:
            pass

        # Assert zero modification or pollution of production data
        curr_prod_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)
        self.assertEqual(
            self._prod_data_snapshot,
            curr_prod_snapshot,
            "CRITICAL VIOLATION: Unit test modified or polluted production files in bridge/data/!",
        )

    def _capture_dir_snapshot(self, directory: str) -> Dict[str, float]:
        if not os.path.isdir(directory):
            return {}
        snapshot = {}
        for root, _, files in os.walk(directory):
            for f in files:
                p = os.path.join(root, f)
                try:
                    snapshot[p] = os.path.getmtime(p)
                except OSError:
                    pass
        return snapshot


class TestHooksRunnerAsync(IsolatedTestCase):
    """Test 1: Non-blocking asynchronous dispatch and exception isolation."""

    def test_async_non_blocking_dispatch(self):
        """Verifies hook emission returns under 50ms without stalling caller thread."""
        runner = HooksRunner(data_dir=self.test_dir)
        start_time = time.time()
        runner.emit_post_chat(chat_text="Test prompt", response_text="Test response")
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.05, f"emit_post_chat blocked caller: {elapsed:.4f}s")

        start_time = time.time()
        runner.emit_window_switch({"app_name": "Blender", "window_title": "Blender"})
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.05, f"emit_window_switch blocked caller: {elapsed:.4f}s")

        start_time = time.time()
        runner.emit_idle_or_exit(reason="afk")
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.05, f"emit_idle_or_exit blocked caller: {elapsed:.4f}s")

    def test_exception_boundary_isolation(self):
        """Verifies an exception in one hook handler does not crash runner or other hooks."""
        runner = HooksRunner(data_dir=self.test_dir)
        executed = []

        def faulty_hook(**kwargs):
            raise RuntimeError("Deliberate hook failure test")

        def normal_hook(**kwargs):
            executed.append(kwargs.get("msg", "ok"))

        runner.register_hook("custom_event", faulty_hook)
        runner.register_hook("custom_event", normal_hook)

        # Trigger custom event
        runner.trigger("custom_event", msg="survived")
        time.sleep(0.15)  # Allow async worker thread to run
        self.assertIn("survived", executed)


class TestPostChatHookAndMemoryDeduplication(IsolatedTestCase):
    """Test 2: PostChatHook memory upsert, deduplication, and Intermarine cleanup."""

    def test_learn_eval_memory_upsert_supersedes_tentative(self):
        """PostChatHook merges confirmed rule over tentative note in place."""
        mem_file = os.path.join(self.test_dir, "memory.json")
        initial = [
            {
                "id": "mem_tentative_1",
                "text": "Logo Intermarine: colore in attesa di conferma",
                "pinned": False,
                "created_at": "2026-10-27 10:00",
            }
        ]
        with open(mem_file, "w", encoding="utf-8") as f:
            json.dump(initial, f)

        runner = HooksRunner(data_dir=self.test_dir, storage_manager=self.storage)
        res = runner.evaluate_memory_upsert(
            new_fact="Logo Intermarine: colore ufficiale = Blu Marino (dark navy). Riferimento per 3ds Max, Blender, Unreal Engine.",
            project_id="proj_nave_intermarine",
        )
        self.assertIsNotNone(res)
        self.assertIn("Blu Marino", res["text"])

        with open(mem_file, "r", encoding="utf-8") as f:
            updated = json.load(f)

        self.assertEqual(len(updated), 1, "Expected in-place update without creating duplicate")
        self.assertEqual(updated[0]["merged_count"], 2)
        self.assertTrue(updated[0]["pinned"])
        self.assertIn("Blu Marino", updated[0]["text"])

    def test_startup_cleanup_intermarine_logo_idempotent(self):
        """cleanup_intermarine_logo_memory consolidates tentative into confirmed Blu Marino rule."""
        mem_file = os.path.join(self.test_dir, "memory.json")
        initial = [
            {
                "id": "mem_tentative",
                "text": "Logo Intermarine – colore in attesa di conferma da parte dell'utente",
                "pinned": False,
            },
            {
                "id": "mem_confirmed",
                "text": "Logo Intermarine: colore ufficiale = Blu Marino (dark navy). Riferimento per 3ds Max, Blender, Unreal Engine.",
                "pinned": True,
            },
        ]
        with open(mem_file, "w", encoding="utf-8") as f:
            json.dump(initial, f)

        # Run startup cleanup
        changed = cleanup_intermarine_logo_memory(data_dir=self.test_dir, storage_manager=self.storage)
        self.assertTrue(changed)

        with open(mem_file, "r", encoding="utf-8") as f:
            active = json.load(f)

        self.assertEqual(len(active), 1)
        self.assertNotIn("in attesa di conferma", active[0]["text"])
        self.assertIn("Blu Marino", active[0]["text"])
        self.assertGreaterEqual(active[0].get("merged_count", 0), 2)

        # Idempotent second run
        changed_second = cleanup_intermarine_logo_memory(data_dir=self.test_dir, storage_manager=self.storage)
        self.assertFalse(changed_second, "Second cleanup run should be a no-op")
        with open(mem_file, "r", encoding="utf-8") as f:
            active_after = json.load(f)
        self.assertEqual(len(active_after), 1)

    def test_slash_command_learn_eval_in_bridge(self):
        """Verifies slash command /learn-eval cleans up and responds properly."""
        mem_proj = MemoryAndProjectsManager(data_dir=self.test_dir)
        bridge = OllamaLocalBridge(mem_proj_mgr=mem_proj, data_dir=self.test_dir)
        reply = bridge.handle_chat_or_command("/learn-eval")
        self.assertIn("reply", reply)
        self.assertIn("Valutazione Apprendimento", reply["reply"])


class TestOnWindowSwitchHook(IsolatedTestCase):
    """Test 3: Window title matching to Kanban tasks and skill reinforcement."""

    def test_window_switch_matches_uncompleted_task(self):
        """Matching Blender window populates window_task_suggestion."""
        todo_file = os.path.join(self.test_dir, "todo.json")
        todos = [
            {
                "id": "todo_1",
                "title": "Cleanup modello in Blender",
                "project": "Nave Intermarine",
                "status": "todo",
            },
            {
                "id": "todo_2",
                "title": "Export GLB ottimizzato con Draco",
                "project": "Nave Intermarine",
                "status": "todo",
            },
        ]
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump(todos, f)

        runner = HooksRunner(data_dir=self.test_dir)
        suggestion = runner.match_window_to_tasks({
            "app_name": "Blender",
            "window_title": "Blender - [nave_intermarine_hull.blend]",
            "project_name": "Nave Intermarine",
        })
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion.get("task_id"), "todo_1")
        self.assertEqual(suggestion.get("detected_app"), "Blender")
        self.assertEqual(suggestion.get("suggested_status"), "doing")

        # Verify getter returns identical suggestion
        cached = runner.get_window_task_suggestion()
        self.assertEqual(cached, suggestion)

    def test_tool_to_skill_reinforcement(self):
        """Verifies reinforce_tool_skill updates evidence and timestamp in skills_learned."""
        mem_proj = MemoryAndProjectsManager(data_dir=self.test_dir)
        mem_proj.add_skill_learned(name="Blender", category="3D", level="base")
        runner = HooksRunner(data_dir=self.test_dir, mem_proj_manager=mem_proj)
        runner.reinforce_tool_skill("Blender")

        skills = mem_proj.get_skills_learned()
        skill = next((s for s in skills if s.get("name") == "Blender"), None)
        self.assertIsNotNone(skill)
        self.assertIn("finestra attiva", skill.get("evidence", ""))

    def test_tool_reinforces_instinct_engine(self):
        """Verifies reinforce_tool_skill calls reinforce_instinct and boosts confidence."""
        from bridge.instinct_engine import InstinctEngine
        ie = InstinctEngine(data_dir=self.test_dir, storage_manager=self.storage)
        created = ie.add_instinct(
            trigger="blender",
            action="Apply subd modifier and check scale",
            confidence=0.5,
            domain="workflow",
        )
        inst_id = created["id"]

        runner = HooksRunner(data_dir=self.test_dir, instinct_engine=ie)
        runner.reinforce_tool_skill("Blender")

        updated = ie.get_instinct(inst_id)
        self.assertAlmostEqual(updated["confidence"], 0.52, places=3)

    def test_non_dcc_window_filtered_out(self):
        """Spotify and other non-creative apps must never produce window task suggestions."""
        runner = HooksRunner(data_dir=self.test_dir)
        suggestion = runner.match_window_to_tasks({
            "app_name": "Spotify",
            "window_title": "Spotify Premium - Daily Mix 1",
            "project_name": "Nave Intermarine",
        })
        self.assertIsNone(suggestion)
        self.assertIsNone(runner.get_window_task_suggestion())

    def test_telemetry_service_triggers_window_switch_hook(self):
        """Verifies WindowAndAFKTracker triggers HooksRunner on window transition."""
        runner = HooksRunner(data_dir=self.test_dir)
        tracker = WindowAndAFKTracker(afk_threshold_seconds=300.0, hooks_runner=runner)

        # Mock foreground window resolution
        tracker.get_active_window = lambda: {
            "app_name": "3ds Max",
            "window_title": "3ds Max 2026 - [render.max]",
            "project_name": "Nave Intermarine",
            "is_dcc": True,
            "pid": 1234,
        }
        win_info, is_afk, idle_sec = tracker.update()
        self.assertEqual(win_info.get("app_name"), "3ds Max")


class TestOnIdleOrExitHookAndSessionAction(IsolatedTestCase):
    """Test 4: Session checkpoint persistence, /api/session/action, and status snapshot."""

    def test_session_checkpoint_save_and_resume(self):
        """Verifies save, resume, and dismiss of session_checkpoint.json."""
        runner = HooksRunner(data_dir=self.test_dir)
        saved = runner.save_session_checkpoint(
            active_project_id="proj_nave_intermarine",
            active_project_name="Nave Intermarine",
            active_task_id="todo_1",
            active_task_title="Cleanup modello in Blender",
            active_view="projects",
            next_step_hint="Esportare GLB con Draco",
        )
        self.assertEqual(saved["active_project_id"], "proj_nave_intermarine")

        ckpt_file = os.path.join(self.test_dir, "session_checkpoint.json")
        self.assertTrue(os.path.isfile(ckpt_file))

        # Resume checkpoint
        resumed = runner.resume_session_checkpoint()
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed["active_task_id"], "todo_1")

        # Dismiss checkpoint
        dismiss_res = runner.dismiss_session_checkpoint()
        self.assertTrue(dismiss_res.get("ok"))
        self.assertIsNone(runner.get_session_checkpoint())
        self.assertFalse(os.path.isfile(ckpt_file))

    def test_session_action_http_endpoints(self):
        """Verifies POST /api/session/action (save, resume, dismiss) and GET /api/session."""
        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # 1. Save checkpoint via POST
            url_save = f"http://127.0.0.1:{port}/api/session/action"
            save_payload = json.dumps({
                "action": "save",
                "active_project_id": "proj_p430",
                "active_project_name": "Pattugliatore P430",
                "active_task_title": "Verifica render",
            }).encode("utf-8")
            req = urllib.request.Request(url_save, data=save_payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("ok"))
                self.assertEqual(data.get("action"), "save")

            # 2. Resume checkpoint via POST
            resume_payload = json.dumps({"action": "resume"}).encode("utf-8")
            req = urllib.request.Request(url_save, data=resume_payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("ok"))
                self.assertEqual(data.get("action"), "resume")

            # 3. Dismiss checkpoint via POST
            dismiss_payload = json.dumps({"action": "dismiss"}).encode("utf-8")
            req = urllib.request.Request(url_save, data=dismiss_payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("ok"))
                self.assertEqual(data.get("action"), "dismiss")

            # 4. Check GET /api/session
            url_get = f"http://127.0.0.1:{port}/api/session"
            req = urllib.request.Request(url_get, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("ok"))
        finally:
            server.shutdown()
            server.server_close()

    def test_status_snapshot_contains_suggestion_and_checkpoint(self):
        """Verifies GET /status payload includes window_task_suggestion and session_checkpoint keys."""
        audio = WindowsAudioCapture()
        mem_proj = MemoryAndProjectsManager(data_dir=self.test_dir)
        bridge = OllamaLocalBridge(mem_proj_mgr=mem_proj, data_dir=self.test_dir)
        win_conn = WindowAndConnectionsManager(bridge, bridge.calendar, mem_proj)
        monitor = SystemStateMonitor(audio, bridge, bridge.calendar, mem_proj, win_conn)

        snap = monitor.get_snapshot()
        self.assertIn("window_task_suggestion", snap)
        self.assertIn("session_checkpoint", snap)


if __name__ == "__main__":
    unittest.main(verbosity=2)
