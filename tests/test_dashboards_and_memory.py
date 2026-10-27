"""
tests/test_dashboards_and_memory.py
Comprehensive verification test suite for Milestone 4 (Requirement R4):

AC1. Unified Memory, Skills & Automatic Project Migration:
- profile.json has 0 duplicate traits and contains 6 skills_learned technical competencies
  (Blender, 3ds Max, Unreal Engine, Export GLB / Draco, Rust, Three.js).
- Startup migration populates projects.json with "Nave Intermarine" linked to the 4 Kanban tasks
  with parsed dates (2026-11-02, 2026-11-03, 2026-11-04, 2026-11-06) and logo rule.
- Prompt context for "Quali sono i miei progetti e di che colore vuole il logo Intermarine?"
  includes both "Nave Intermarine" and "blu / blu marino".
- POST /api/todos/action and POST /api/skills_learned/action return HTTP 200 and persist changes.

AC2. Bidirectional Calendar & Multi-.ics Sync:
- CalendarManager extracts 4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3@group.calendar.google.com
  and appends &src= in Google links.
- Feed changes reconcile cleanly without duplicates.
- POST /api/calendar/action works and /status returns calendar_events and todo_deadlines.

AC3. Clean 🐾 Neko Home + 4 Asana/Trello Dashboards:
- Syntax validation (node --check) for all JS files.
- 5-tab switcher in index.html.
- Clean Home view hides workspace container.
"""

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

import bridge.ai_monitor as am
from bridge.ai_monitor import (
    CalendarManager,
    CompanionRequestHandler,
    MemoryAndProjectsManager,
    OllamaLocalBridge,
    _migrate_todos_and_projects_on_startup,
    extract_todo_deadlines,
    parse_due_date_iso,
    SystemStateMonitor,
)
from bridge.storage_manager import StorageManager, get_storage_manager


class TestAC1UnifiedMemoryAndSkills(unittest.TestCase):
    """
    AC1 Acceptance Criteria Verification:
    - profile.json has 0 duplicate traits
    - profile.json contains 6 skills_learned technical competencies
    - Startup migration populates projects.json with "Nave Intermarine" linked to 4 Kanban tasks
      with parsed dates (2026-11-02, 2026-11-03, 2026-11-04, 2026-11-06) and logo rule
    - Prompt context for "Quali sono i miei progetti e di che colore vuole il logo Intermarine?"
      includes both "Nave Intermarine" and "blu / blu marino"
    - POST /api/todos/action and POST /api/skills_learned/action return HTTP 200
    """

    def test_live_profile_zero_duplicate_traits_and_six_skills(self):
        """Verifies live bridge/data/profile.json satisfies 0 duplicate traits and 6 competencies."""
        profile_path = os.path.join(BRIDGE_DIR, "data", "profile.json")
        self.assertTrue(os.path.isfile(profile_path), f"Missing {profile_path}")
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)

        traits = profile.get("traits", [])
        self.assertIsInstance(traits, list)
        lower_traits = [t.strip().lower() for t in traits if isinstance(t, str)]
        # 0 duplicate traits check
        self.assertEqual(len(lower_traits), len(set(lower_traits)), f"Duplicate traits found: {traits}")

        # 6 skills_learned technical competencies
        skills = profile.get("skills_learned", [])
        self.assertIsInstance(skills, list)
        self.assertGreaterEqual(len(skills), 6)
        skill_names = {s.get("name") for s in skills}
        expected_competencies = {
            "Blender",
            "3ds Max",
            "Unreal Engine",
            "Export GLB / Draco",
            "Rust",
            "Three.js",
        }
        for expected in expected_competencies:
            self.assertIn(expected, skill_names, f"Expected competency '{expected}' not found in profile skills_learned")

        # Verify structured attributes for each skill
        for s in skills:
            self.assertTrue(bool(s.get("id")), f"Skill missing id: {s}")
            self.assertTrue(bool(s.get("name")), f"Skill missing name: {s}")
            self.assertTrue(bool(s.get("category")), f"Skill missing category: {s}")
            self.assertIn(s.get("level"), ["base", "intermedio", "avanzato", "expert"], f"Invalid skill level: {s}")
            self.assertTrue(bool(s.get("evidence")), f"Skill missing evidence: {s}")

    def test_startup_migration_intermarine_project_and_kanban_dates(self):
        """
        Verifies startup migration populates projects.json with "Nave Intermarine",
        links the 4 Kanban tasks with exact parsed dates (2026-11-02, 2026-11-03, 2026-11-04, 2026-11-06),
        and associates the logo rule.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            # 1. Setup raw legacy files in temp_dir
            prof_file = os.path.join(temp_dir, "profile.json")
            with open(prof_file, "w", encoding="utf-8") as f:
                json.dump({
                    "username": "Diego",
                    "traits": [
                        "Architetto 3D",
                        "Strumenti chiave: 3ds Max, Blender e Unreal Engine",
                        "Strumenti chiave: 3ds Max, Blender e Unreal Engine",
                        "Strumenti chiave: 3ds Max, Blender e Unreal Engine",
                    ],
                }, f)

            todo_file = os.path.join(temp_dir, "todo.json")
            with open(todo_file, "w", encoding="utf-8") as f:
                json.dump([
                    "Intermarine – Cleanup modello in Blender (lun 2/11)",
                    "Intermarine – Export GLB ottimizzato con Draco (mar 3/11)",
                    "Intermarine – Integrazione pipeline Rust + Three.js (mer-gio 4/5 nov)",
                    "Intermarine – Interattività + ottimizzazione realtime (ven 6/11)",
                ], f)

            mem_file = os.path.join(temp_dir, "memory.json")
            with open(mem_file, "w", encoding="utf-8") as f:
                json.dump([
                    {
                        "id": "mem_logo_1",
                        "text": "Intermarine vuole sempre il colore blu per il suo Logo",
                        "pinned": True,
                    }
                ], f)

            # Run startup migration
            _migrate_todos_and_projects_on_startup(temp_dir)

            # Verify projects.json
            proj_file = os.path.join(temp_dir, "projects.json")
            self.assertTrue(os.path.isfile(proj_file))
            with open(proj_file, "r", encoding="utf-8") as f:
                projects = json.load(f)

            intermarine = next((p for p in projects if p.get("name") == "Nave Intermarine"), None)
            self.assertIsNotNone(intermarine, "Project 'Nave Intermarine' was not created by startup migration")
            self.assertEqual(intermarine.get("client"), "Intermarine")
            # Logo rule linked in notes
            notes_str = " ".join(intermarine.get("notes", [])).lower()
            self.assertTrue("blu" in notes_str or "navy" in notes_str)

            # Verify todo.json tasks and parsed dates
            with open(todo_file, "r", encoding="utf-8") as f:
                todos = json.load(f)

            self.assertEqual(len(todos), 4)
            expected_dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-06"]
            actual_dates = [t.get("due_date") for t in todos]
            self.assertEqual(actual_dates, expected_dates)

            for t in todos:
                self.assertEqual(t.get("project"), "Nave Intermarine")
                self.assertEqual(t.get("status"), "todo")
                self.assertEqual(t.get("priority"), "high")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_chat_prompt_context_assembly_intermarine_and_logo_rule(self):
        """
        Verifies that querying OllamaLocalBridge.handle_chat_or_command for
        'Quali sono i miei progetti e di che colore vuole il logo Intermarine?'
        assembles prompt context containing both 'Nave Intermarine' and 'blu / blu marino'.
        """
        temp_dir = tempfile.mkdtemp()
        bridge = None
        try:
            _migrate_todos_and_projects_on_startup(temp_dir)
            mgr = MemoryAndProjectsManager(data_dir=temp_dir)
            bridge = OllamaLocalBridge(
                calendar_mgr=CalendarManager(),
                mem_proj_mgr=mgr,
                model="qwen2.5:7b",
                data_dir=temp_dir,
            )
            bridge.ollama_online = True

            captured_messages = []

            def fake_build_payload(model, messages, stream, num_ctx):
                captured_messages.extend(messages)
                return json.dumps({"model": model, "messages": messages}).encode("utf-8")

            class FakeResponse:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def read(self):
                    return json.dumps({
                        "message": {"role": "assistant", "content": "I tuoi progetti includono Nave Intermarine e il logo deve essere Blu Marino."},
                        "done": True,
                    }).encode("utf-8")

            with patch("bridge.ai_monitor.build_ollama_payload", side_effect=fake_build_payload), \
                 patch("urllib.request.urlopen", return_value=FakeResponse()):
                bridge.handle_chat_or_command("Quali sono i miei progetti e di che colore vuole il logo Intermarine?")

            self.assertTrue(len(captured_messages) > 0, "No prompt messages assembled")
            system_msg = next((m.get("content", "") for m in captured_messages if m.get("role") == "system"), "")

            self.assertIn("Nave Intermarine", system_msg, "System prompt context missing 'Nave Intermarine'")
            sys_lower = system_msg.lower()
            self.assertTrue(
                "blu" in sys_lower or "blu marino" in sys_lower,
                "System prompt context missing Intermarine logo color rule ('blu' / 'blu marino')",
            )
        finally:
            if bridge is not None and hasattr(bridge, "storage") and bridge.storage:
                try:
                    bridge.storage.close()
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_http_todos_and_skills_action_endpoints(self):
        """
        Verifies POST /api/todos/action (add, status, edit, delete) and
        POST /api/skills_learned/action (add, level, remove) return HTTP 200 and persist changes.
        """
        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        def _post(path, body_dict):
            body_bytes = json.dumps(body_dict).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))

        try:
            # 1. Test POST /api/skills_learned/action - add
            st, res_add_skill = _post("/api/skills_learned/action", {
                "action": "add",
                "name": "WebGPU Realtime",
                "category": "Graphics",
                "level": "intermedio",
                "evidence": "Test Suite M4",
            })
            self.assertEqual(st, 200)
            self.assertEqual(res_add_skill.get("status"), "ok")

            # 2. Test POST /api/skills_learned/action - level
            st, res_lvl = _post("/api/skills_learned/action", {
                "action": "level",
                "name": "WebGPU Realtime",
                "level": "avanzato",
            })
            self.assertEqual(st, 200)
            self.assertEqual(res_lvl.get("status"), "ok")

            # 3. Test POST /api/skills_learned/action - remove
            st, res_rm = _post("/api/skills_learned/action", {
                "action": "remove",
                "name": "WebGPU Realtime",
            })
            self.assertEqual(st, 200)
            self.assertEqual(res_rm.get("status"), "ok")

            # 4. Test POST /api/todos/action - add
            st, res_add_todo = _post("/api/todos/action", {
                "action": "add",
                "title": "Verifica di Collaudo E2E M4",
                "project": "Nave Intermarine",
                "due_label": "sab 14/11",
                "priority": "high",
                "subtasks": ["Subtask 1", "Subtask 2"],
            })
            self.assertEqual(st, 200)
            self.assertTrue(res_add_todo.get("ok"))
            item = res_add_todo.get("item")
            self.assertIsNotNone(item)
            todo_id = item["id"]

            # 5. Test POST /api/todos/action - status
            st, res_stat = _post("/api/todos/action", {
                "action": "status",
                "id": todo_id,
                "status": "doing",
            })
            self.assertEqual(st, 200)
            self.assertTrue(res_stat.get("ok"))

            # 6. Test POST /api/todos/action - edit
            st, res_edit = _post("/api/todos/action", {
                "action": "edit",
                "id": todo_id,
                "title": "Verifica di Collaudo E2E M4 (Aggiornato)",
                "priority": "normal",
            })
            self.assertEqual(st, 200)
            self.assertTrue(res_edit.get("ok"))

            # 7. Test POST /api/todos/action - delete
            st, res_del = _post("/api/todos/action", {
                "action": "delete",
                "id": todo_id,
            })
            self.assertEqual(st, 200)
            self.assertTrue(res_del.get("ok"))
        finally:
            server.shutdown()
            server.server_close()


class TestAC2CalendarSyncAndReconciliation(unittest.TestCase):
    """
    AC2 Acceptance Criteria Verification:
    - CalendarManager extracts 4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3@group.calendar.google.com
      and appends &src= in Google links.
    - Feed changes reconcile cleanly without duplicates.
    - POST /api/calendar/action works and /status returns calendar_events and todo_deadlines.
    """

    def test_calendar_id_extraction_and_src_parameter(self):
        """Verifies calendar ID extraction from Google .ics URL and appending &src= in generate_gcal_url."""
        raw_url = (
            "https://calendar.google.com/calendar/ical/"
            "4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3%40group.calendar.google.com"
            "/private-2ad9e3e0308425580be8a6f3a931f414/basic.ics"
        )
        extracted = CalendarManager.extract_calendar_id(raw_url)
        expected_id = "4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3@group.calendar.google.com"
        self.assertEqual(extracted, expected_id)

        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = os.path.join(temp_dir, "calendar_config.json")
            events_path = os.path.join(temp_dir, "events.json")
            with patch.object(am, "CALENDAR_CONFIG_FILE", cfg_path), \
                 patch.object(am, "EVENTS_FILE", events_path):
                cm = CalendarManager()
                cm.config["ics_urls"] = [raw_url]
                url = cm.generate_gcal_url(
                    "Call Revisione Nave",
                    datetime(2026, 11, 2, 10, 0, 0),
                    datetime(2026, 11, 2, 11, 0, 0),
                )
                self.assertIn("&src=", url)
                self.assertIn("4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3", url)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_feed_reconciliation_zero_duplicates_and_time_updates(self):
        """
        Verifies bidirectional reconciliation: moving DTSTART or updating title on Google Calendar
        updates the local event without creating duplicates.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = os.path.join(temp_dir, "calendar_config.json")
            events_path = os.path.join(temp_dir, "events.json")
            with patch.object(am, "CALENDAR_CONFIG_FILE", cfg_path), \
                 patch.object(am, "EVENTS_FILE", events_path):
                cm = CalendarManager()

                # Step 1: User created local event
                local_ev = cm.add_event("Collaudo Motori", datetime(2026, 11, 5, 9, 0, 0))
                self.assertEqual(len(cm.local_events), 1)

                # Step 2: First sync matching remote feed
                cm.remote_events = [
                    {
                        "id": "google_uid_motor_100",
                        "uid": "google_uid_motor_100",
                        "title": "Collaudo Motori",
                        "start_iso": "2026-11-05T09:00:00",
                        "end_iso": "2026-11-05T10:00:00",
                        "all_day": False,
                        "calendar_name": "Neko",
                        "source": "google_ics",
                        "last_modified": "20261101T120000Z",
                    }
                ]
                cm.reconcile_events()
                upcoming_1 = cm.get_upcoming_events(days_ahead=30)
                self.assertEqual(len(upcoming_1), 1, "Expected exactly 1 event after first reconciliation")
                self.assertTrue(cm.local_events[0].get("synced_with_remote"))
                self.assertEqual(cm.local_events[0].get("remote_id"), "google_uid_motor_100")

                # Step 3: Remote feed changes: event moved to 14:30 and renamed
                cm.remote_events = [
                    {
                        "id": "google_uid_motor_100",
                        "uid": "google_uid_motor_100",
                        "title": "Collaudo Motori (Spostato)",
                        "start_iso": "2026-11-05T14:30:00",
                        "end_iso": "2026-11-05T15:30:00",
                        "all_day": False,
                        "calendar_name": "Neko",
                        "source": "google_ics",
                        "last_modified": "20261102T080000Z",
                    }
                ]
                cm.reconcile_events()
                upcoming_2 = cm.get_upcoming_events(days_ahead=30)
                # Must reconcile without duplicate entries
                self.assertEqual(len(upcoming_2), 1, "Duplicate events created during feed reconciliation!")
                self.assertEqual(upcoming_2[0]["title"], "Collaudo Motori (Spostato)")
                self.assertEqual(upcoming_2[0]["start_iso"], "2026-11-05T14:30:00")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_http_calendar_action_and_status_response(self):
        """
        Verifies POST /api/calendar/action executes CRUD actions (add, move, delete, sync_now)
        and GET /status returns calendar_events, todo_deadlines, and calendar_sync_status.
        """
        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        def _request(path, method="GET", body=None):
            body_bytes = json.dumps(body).encode("utf-8") if body else None
            headers = {"Content-Type": "application/json"} if body else {}
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=body_bytes,
                headers=headers,
                method=method,
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))

        try:
            # 1. POST /api/calendar/action - add
            st, res_add = _request(
                "/api/calendar/action",
                method="POST",
                body={
                    "action": "add",
                    "title": "Test Event M4",
                    "start": "2026-11-10T15:00:00",
                    "duration_minutes": 45,
                    "location": "Ufficio Tecnico",
                },
            )
            self.assertEqual(st, 200)
            self.assertTrue(res_add.get("ok"))
            ev = res_add.get("event")
            self.assertIsNotNone(ev)
            ev_id = ev["id"]

            # 2. POST /api/calendar/action - move / edit
            st, res_move = _request(
                "/api/calendar/action",
                method="POST",
                body={
                    "action": "move",
                    "id": ev_id,
                    "title": "Test Event M4 Spostato",
                    "start_iso": "2026-11-10T16:00:00",
                },
            )
            self.assertEqual(st, 200)
            self.assertTrue(res_move.get("ok"))
            self.assertEqual(res_move["event"]["title"], "Test Event M4 Spostato")

            # 3. POST /api/calendar/action - sync_now
            st, res_sync = _request(
                "/api/calendar/action",
                method="POST",
                body={"action": "sync_now"},
            )
            self.assertEqual(st, 200)
            self.assertTrue(res_sync.get("ok"))
            self.assertIn("calendar_sync_status", res_sync)

            # 4. POST /api/calendar/action - delete
            st, res_del = _request(
                "/api/calendar/action",
                method="POST",
                body={"action": "delete", "id": ev_id},
            )
            self.assertEqual(st, 200)
            self.assertTrue(res_del.get("ok"))

            # 5. GET /status verification of upgraded schema
            st, snap = _request("/status")
            self.assertEqual(st, 200)
            self.assertIn("calendar_sync_status", snap, "Missing 'calendar_sync_status' in /status")
            self.assertIn("calendar_events", snap, "Missing 'calendar_events' in /status")
            self.assertIn("todo_deadlines", snap, "Missing 'todo_deadlines' in /status")
            self.assertIsInstance(snap["calendar_events"], list)
            self.assertIsInstance(snap["todo_deadlines"], list)
            self.assertIsInstance(snap["calendar_sync_status"], dict)
        finally:
            server.shutdown()
            server.server_close()


class TestAC3CleanHomeAndDashboardsFrontend(unittest.TestCase):
    """
    AC3 Acceptance Criteria Verification:
    - Syntax validation (node --check) for all JS files.
    - 5-tab switcher in index.html.
    - Clean Home view hides workspace container.
    """

    def test_node_check_all_javascript_files(self):
        """Verifies that all frontend JavaScript files pass 'node --check' syntax validation."""
        js_files = [
            os.path.join(REPO_ROOT, "js", "main.js"),
            os.path.join(REPO_ROOT, "js", "hud-terminal.js"),
            os.path.join(REPO_ROOT, "js", "system-bridge.js"),
        ]
        for fpath in js_files:
            self.assertTrue(os.path.isfile(fpath), f"JS file not found: {fpath}")

        cmd = ["node", "--check"] + js_files
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(
            proc.returncode,
            0,
            f"JavaScript syntax error detected via node --check:\n{proc.stderr}\n{proc.stdout}",
        )

    def test_five_tab_switcher_in_index_html(self):
        """Verifies 5-tab switcher and badges in index.html."""
        index_path = os.path.join(REPO_ROOT, "index.html")
        self.assertTrue(os.path.isfile(index_path))
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Workspace navigation container
        self.assertIn('id="hud-workspace-nav"', html)

        # 5 Tab buttons
        self.assertIn('id="tab-nav-home"', html, "Missing Home tab (#tab-nav-home)")
        self.assertIn('id="tab-nav-todo"', html, "Missing To-Do tab (#tab-nav-todo)")
        self.assertIn('id="tab-nav-projects"', html, "Missing Projects tab (#tab-nav-projects)")
        self.assertIn('id="tab-nav-calendar"', html, "Missing Calendar tab (#tab-nav-calendar)")
        self.assertIn('id="tab-nav-memory"', html, "Missing Memory tab (#tab-nav-memory)")

        # Tab badges for counters
        self.assertIn('id="tab-todo-badge"', html)
        self.assertIn('id="tab-proj-badge"', html)
        self.assertIn('id="tab-cal-badge"', html)
        self.assertIn('id="tab-mem-badge"', html)

        # The 4 full-view workspace views
        self.assertIn('id="workspace-view-todo"', html)
        self.assertIn('id="workspace-view-projects"', html)
        self.assertIn('id="workspace-view-calendar"', html)
        self.assertIn('id="workspace-view-memory"', html)

    def test_clean_home_view_hides_workspace_container(self):
        """
        Verifies that in clean 🐾 Neko Home view, the workspace container is hidden
        via CSS rule `body.view-home #hud-workspace-container { display: none !important; }`
        and subtle next-task chip is present.
        """
        css_path = os.path.join(REPO_ROOT, "css", "terminal.css")
        self.assertTrue(os.path.isfile(css_path))
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        index_path = os.path.join(REPO_ROOT, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Next-task chip in top bar
        self.assertIn('id="home-next-task-chip"', html)

        # CSS rule ensures complete hiding of workspace container in Home view
        # so the 60 FPS ASCII cat is 100% visible and unobstructed
        match_home_rule = re.search(
            r"body\.view-home\s+#hud-workspace-container\s*\{[^}]*display\s*:\s*none\s*!important[^}]*\}",
            css,
        )
        self.assertIsNotNone(
            match_home_rule,
            "CSS rule 'body.view-home #hud-workspace-container { display: none !important; }' not found in terminal.css",
        )


if __name__ == "__main__":
    unittest.main()
