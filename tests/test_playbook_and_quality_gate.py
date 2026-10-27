"""
tests/test_playbook_and_quality_gate.py
=======================================
Comprehensive Unit & Integration Test Suite for Milestone 3 (R3):
- Playbook Evolution from Project (SkillRegistry.evolve_project_to_playbook)
- Project & Kanban Task Instantiation from Playbook (SkillRegistry.create_project_from_playbook)
- Pre-Delivery Quality Gate Inspector (inspect_project_quality_gate)
- List Playbooks (list_playbooks)
- HTTP Endpoints (POST /api/projects/evolve, POST /api/projects/from-playbook, POST /api/projects/quality_gate, GET /api/playbooks)
- Slash Commands (/evolve, /quality-gate)
- Boundary & Edge Cases (clamping, invalid dates, missing projects, weights)
- Production Data Isolation (guaranteeing zero pollution of bridge/data/)
"""

import http.server
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

PRODUCTION_DATA_DIR = os.path.join(BRIDGE_DIR, "data")

from bridge.skill_registry import (
    SkillRegistry,
    validate_skill_schema,
    validate_code_safety,
    evolve_project_to_playbook,
    create_project_from_playbook,
    inspect_project_quality_gate,
    list_playbooks,
    CANONICAL_INTERMARINE_PROJECT,
    CANONICAL_INTERMARINE_TASKS,
)
from bridge.ai_monitor import (
    CompanionRequestHandler,
    OllamaLocalBridge,
    MemoryAndProjectsManager,
    get_skill_registry,
)
from bridge.storage_manager import StorageManager


class IsolatedM3TestCase(unittest.TestCase):
    """
    Base test case guaranteeing 100% data isolation.
    Uses tempfile.TemporaryDirectory and verifies that production data is never touched.
    """

    def setUp(self):
        self._temp_dir_obj = tempfile.TemporaryDirectory()
        self.test_dir = os.path.abspath(self._temp_dir_obj.name)
        self.test_db_path = os.path.join(self.test_dir, "neko_storage.db")

        # Snapshot production data directory
        self._prod_data_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)

        # Initialize test storage
        self.storage = StorageManager(db_path=self.test_db_path, data_dir=self.test_dir)

        # Seed baseline project in SQLite to satisfy relational constraints
        with self.storage.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (id, name, client, color, is_active, created_at, updated_at)
                VALUES ('proj_nave_intermarine', 'Nave Intermarine', 'Intermarine', '#0055ff', 1, ?, ?)
                """,
                (time.time(), time.time()),
            )

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

        # Verify zero pollution of production data
        curr_prod_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)
        self.assertEqual(
            self._prod_data_snapshot,
            curr_prod_snapshot,
            "CRITICAL VIOLATION: Test modified or polluted production files in bridge/data/!",
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


class TestPlaybookEvolution(IsolatedM3TestCase):
    """Tests project-to-playbook evolution and schema conformance."""

    def test_evolve_canonical_intermarine(self):
        """Evolves Nave Intermarine into playbook_nave_intermarine.json."""
        sr = SkillRegistry(data_dir=self.test_dir)
        playbook = sr.evolve_project_to_playbook("proj_nave_intermarine")

        self.assertIsNotNone(playbook)
        self.assertEqual(playbook.get("slug"), "nave_intermarine")
        self.assertEqual(playbook.get("id"), "playbook_nave_intermarine")
        self.assertIn("Nave Intermarine", playbook.get("name", ""))

        # Check saved file
        pb_file = os.path.join(self.test_dir, "skills", "playbook_nave_intermarine.json")
        self.assertTrue(os.path.isfile(pb_file))
        with open(pb_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["id"], "playbook_nave_intermarine")

        # Check task sequence and relative day offsets [0, 1, 2, 4]
        tasks = playbook.get("task_sequence", [])
        self.assertEqual(len(tasks), 4)
        offsets = [t.get("day_offset") for t in tasks]
        self.assertEqual(offsets, [0, 1, 2, 4])

        # Check tech stack capture
        tech = playbook.get("tech_stack", [])
        for req in ["Blender", "Draco", "Rust", "Three.js"]:
            self.assertTrue(any(req.lower() in t.lower() for t in tech))

        # Check client rules and domain instincts
        self.assertTrue(len(playbook.get("client_rules", [])) >= 1)
        self.assertTrue(len(playbook.get("domain_instincts", [])) >= 1)

    def test_evolve_custom_project(self):
        """Evolves a user-defined project with dynamic task dates."""
        proj_file = os.path.join(self.test_dir, "projects.json")
        custom_proj = [
            {
                "id": "proj_drone_inspection",
                "name": "Drone Inspection 3D",
                "client": "AeroTech",
                "color": "#ff5500",
                "tech_stack": ["Blender", "Photogrammetry", "Unreal Engine"],
                "notes": ["Color calibration 5500K mandatory"],
            }
        ]
        with open(proj_file, "w", encoding="utf-8") as f:
            json.dump(custom_proj, f)

        todo_file = os.path.join(self.test_dir, "todo.json")
        custom_tasks = [
            {
                "id": "t1",
                "title": "Mesh Photogrammetry Cleanup",
                "project": "Drone Inspection 3D",
                "due_date": "2026-12-10",
                "status": "todo",
            },
            {
                "id": "t2",
                "title": "LOD Generation",
                "project": "Drone Inspection 3D",
                "due_date": "2026-12-13",
                "status": "todo",
            },
            {
                "id": "t3",
                "title": "UE5 Level Setup",
                "project": "Drone Inspection 3D",
                "due_date": "2026-12-17",
                "status": "todo",
            },
        ]
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump(custom_tasks, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook("proj_drone_inspection", slug="drone_workflow")

        self.assertEqual(pb.get("slug"), "drone_workflow")
        self.assertEqual(pb.get("id"), "playbook_drone_workflow")
        tasks = pb.get("task_sequence", [])
        self.assertEqual(len(tasks), 3)

        # Offsets from 2026-12-10: 0, 3 (+3 days), 7 (+7 days)
        offsets = [t["day_offset"] for t in tasks]
        self.assertEqual(offsets, [0, 3, 7])

    def test_evolve_schema_conformance(self):
        """Verifies that evolved playbooks pass validate_skill_schema."""
        sr = SkillRegistry(data_dir=self.test_dir)
        playbook = sr.evolve_project_to_playbook("proj_nave_intermarine")
        valid, msg = validate_skill_schema(playbook)
        self.assertTrue(valid, f"Playbook schema validation failed: {msg}")
        self.assertEqual(playbook.get("safety_tier"), "CONFIRM_ONCE")
        self.assertEqual(playbook.get("execution_contract", {}).get("type"), "PROJECT_PLAYBOOK")

    def test_evolve_fallback_sequential_offsets(self):
        """Verifies sequential day offsets [0, 1, 2] when tasks lack parseable dates."""
        proj_file = os.path.join(self.test_dir, "projects.json")
        with open(proj_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "proj_undated", "name": "Undated Project"}], f)

        todo_file = os.path.join(self.test_dir, "todo.json")
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "t1", "title": "Step A", "project": "Undated Project"},
                {"id": "t2", "title": "Step B", "project": "Undated Project"},
                {"id": "t3", "title": "Step C", "project": "Undated Project"},
            ], f)

        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook("proj_undated")
        offsets = [t["day_offset"] for t in pb["task_sequence"]]
        self.assertEqual(offsets, [0, 1, 2])


class TestProjectInstantiationFromPlaybook(IsolatedM3TestCase):
    """Tests creating new projects and structured Kanban tasks from Playbooks."""

    def test_create_project_with_custom_dates(self):
        """Creates new project with start_date='2026-12-01' and verifies calculated due dates."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine")

        res = sr.create_project_from_playbook(
            playbook_slug="nave_intermarine",
            project_name="Yacht Pegaso 60",
            client="Cantieri Tirreno",
            start_date="2026-12-01",
        )

        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("tasks_created"), 4)
        new_proj = res.get("project")
        self.assertEqual(new_proj.get("name"), "Yacht Pegaso 60")
        self.assertEqual(new_proj.get("client"), "Cantieri Tirreno")

        # Verify projects.json
        proj_file = os.path.join(self.test_dir, "projects.json")
        with open(proj_file, "r", encoding="utf-8") as f:
            projects = json.load(f)
        found_p = next((p for p in projects if p.get("name") == "Yacht Pegaso 60"), None)
        self.assertIsNotNone(found_p)

        # Verify todo.json tasks and dates (offsets [0, 1, 2, 4] from 2026-12-01)
        todo_file = os.path.join(self.test_dir, "todo.json")
        with open(todo_file, "r", encoding="utf-8") as f:
            todos = json.load(f)

        pegaso_todos = [t for t in todos if t.get("project") == "Yacht Pegaso 60"]
        self.assertEqual(len(pegaso_todos), 4)

        expected_dates = ["2026-12-01", "2026-12-02", "2026-12-03", "2026-12-05"]
        actual_dates = [t.get("due_date") for t in pegaso_todos]
        self.assertEqual(actual_dates, expected_dates)

        # Verify all new tasks are created with status="todo"
        for t in pegaso_todos:
            self.assertEqual(t.get("status"), "todo")
            self.assertTrue(t.get("due_label"))

    def test_create_project_default_today_date(self):
        """Creates project with default start date (today)."""
        sr = SkillRegistry(data_dir=self.test_dir)
        res = sr.create_project_from_playbook(
            playbook_slug="nave_intermarine",
            project_name="Pattugliatore Delta",
        )
        self.assertTrue(res.get("ok"))
        today_iso = datetime.now().strftime("%Y-%m-%d")
        todo_file = os.path.join(self.test_dir, "todo.json")
        with open(todo_file, "r", encoding="utf-8") as f:
            todos = json.load(f)
        delta_todos = [t for t in todos if t.get("project") == "Pattugliatore Delta"]
        self.assertEqual(delta_todos[0]["due_date"], today_iso)

    def test_create_project_unknown_playbook_fails(self):
        """Attempting to instantiate an unknown playbook returns error dictionary."""
        sr = SkillRegistry(data_dir=self.test_dir)
        res = sr.create_project_from_playbook("non_existent_playbook", "Test Proj")
        self.assertFalse(res.get("ok"))
        self.assertIn("non trovato", res.get("message", "").lower())

    def test_create_project_preserves_existing_todos(self):
        """Instantiating from playbook appends to existing tasks without data loss."""
        todo_file = os.path.join(self.test_dir, "todo.json")
        initial = [{"id": "pre_existing_1", "title": "Buy groceries", "status": "todo"}]
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump(initial, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        sr.create_project_from_playbook("nave_intermarine", "Nuovo Progetto")

        with open(todo_file, "r", encoding="utf-8") as f:
            todos = json.load(f)
        self.assertEqual(len(todos), 5)
        self.assertEqual(todos[0]["id"], "pre_existing_1")


class TestQualityGateInspection(IsolatedM3TestCase):
    """Tests Pre-Delivery Quality Gate checklists, scoring, weights, and status labels."""

    def test_quality_gate_initial_state(self):
        """Initial state (0/4 done) produces 50 readiness score and NEEDS_ATTENTION."""
        report = inspect_project_quality_gate("proj_nave_intermarine", data_dir=self.test_dir)
        self.assertTrue(report.get("ok"))
        self.assertEqual(report.get("status_label"), "NEEDS_ATTENTION")
        self.assertEqual(report.get("readiness_score"), 50)  # 50% tasks=0 + 30% rules=100 + 20% instincts=100 -> 50

        summary = report.get("task_summary", {})
        self.assertEqual(summary.get("total"), 4)
        self.assertEqual(summary.get("done"), 0)
        self.assertEqual(summary.get("todo"), 4)
        self.assertEqual(summary.get("completion_pct"), 0.0)

        # Checklists exist and are populated
        self.assertTrue(len(report.get("client_rules_checklist", [])) >= 1)
        self.assertTrue(len(report.get("technical_instincts_checklist", [])) >= 1)
        self.assertTrue(len(report.get("recommendations", [])) >= 1)

    def test_quality_gate_partial_completion(self):
        """2/4 tasks completed produces 75 score (50% tasks=50 + 30% rules=100 + 20% instincts=100 = 75)."""
        todo_file = os.path.join(self.test_dir, "todo.json")
        todos = [dict(t) for t in CANONICAL_INTERMARINE_TASKS]
        todos[0]["status"] = "done"
        todos[1]["status"] = "done"
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report = inspect_project_quality_gate("proj_nave_intermarine", data_dir=self.test_dir)
        self.assertEqual(report.get("readiness_score"), 75)
        self.assertEqual(report.get("status_label"), "NEEDS_ATTENTION")
        self.assertEqual(report.get("task_summary", {}).get("completion_pct"), 50.0)

    def test_quality_gate_full_completion_passed(self):
        """100% completion produces 100 readiness score and PASSED status label."""
        todo_file = os.path.join(self.test_dir, "todo.json")
        todos = [dict(t) for t in CANONICAL_INTERMARINE_TASKS]
        for t in todos:
            t["status"] = "done"
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report = inspect_project_quality_gate("proj_nave_intermarine", data_dir=self.test_dir)
        self.assertEqual(report.get("readiness_score"), 100)
        self.assertEqual(report.get("status_label"), "PASSED")
        recs = report.get("recommendations", [])
        self.assertTrue(any("superati con successo" in r for r in recs))

    def test_quality_gate_ready_for_delivery_label(self):
        """Score between 80 and 99 produces READY_FOR_DELIVERY status label."""
        todo_file = os.path.join(self.test_dir, "todo.json")
        todos = [dict(t) for t in CANONICAL_INTERMARINE_TASKS]
        todos[0]["status"] = "done"
        todos[1]["status"] = "done"
        todos[2]["status"] = "done"  # 3/4 = 75% -> 0.50*75 + 30 + 20 = 87.5 -> 88
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report = inspect_project_quality_gate("proj_nave_intermarine", data_dir=self.test_dir)
        self.assertEqual(report.get("readiness_score"), 88)
        self.assertEqual(report.get("status_label"), "READY_FOR_DELIVERY")

    def test_quality_gate_blocked_label(self):
        """Zero tasks done, unpinned rules, low instincts produces BLOCKED status label."""
        proj_file = os.path.join(self.test_dir, "projects.json")
        with open(proj_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "proj_blocked", "name": "Blocked Project", "notes": []}], f)

        mem_file = os.path.join(self.test_dir, "memory.json")
        with open(mem_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "m1", "text": "Regola in attesa di conferma", "project_id": "proj_blocked", "pinned": False}], f)

        todo_file = os.path.join(self.test_dir, "todo.json")
        with open(todo_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "t1", "title": "Blocked task", "project": "Blocked Project", "status": "todo"}], f)

        inst_file = os.path.join(self.test_dir, "instincts.json")
        with open(inst_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "i1", "trigger": "Blocked", "action": "Wait", "confidence": 0.40, "project_id": "proj_blocked"}], f)

        report = inspect_project_quality_gate("proj_blocked", data_dir=self.test_dir)
        # Score: 0.50*0 + 0.30*0 + 0.20*0 = 0
        self.assertEqual(report.get("readiness_score"), 0)
        self.assertEqual(report.get("status_label"), "BLOCKED")


class TestListPlaybooks(IsolatedM3TestCase):
    """Tests list_playbooks function and method."""

    def test_list_playbooks_returns_playbooks(self):
        """Evolves two projects and verifies list_playbooks lists both."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="nave_intermarine")

        pbs = sr.list_playbooks(data_dir=self.test_dir)
        self.assertTrue(any(p.get("id") == "playbook_nave_intermarine" for p in pbs))

        # Also standalone list_playbooks
        pbs_standalone = list_playbooks(data_dir=self.test_dir)
        self.assertEqual(len(pbs), len(pbs_standalone))


class TestHttpEndpointsAndSlashCommands(IsolatedM3TestCase):
    """Tests CompanionRequestHandler HTTP endpoints and slash commands for Milestone 3."""

    def test_http_api_projects_evolve(self):
        """Tests POST /api/projects/evolve endpoint."""
        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{port}/api/projects/evolve"
            payload = json.dumps({
                "project_id": "proj_nave_intermarine",
                "slug": "nave_intermarine_api",
                "data_dir": self.test_dir,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertTrue(data.get("ok"))
                self.assertEqual(data.get("status"), "ok")
                self.assertIn("playbook", data)
                self.assertEqual(data["playbook"]["slug"], "nave_intermarine_api")
        finally:
            server.shutdown()
            server.server_close()

    def test_http_api_projects_from_playbook(self):
        """Tests POST /api/projects/from-playbook endpoint."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="intermarine_test")

        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{port}/api/projects/from-playbook"
            payload = json.dumps({
                "playbook_slug": "intermarine_test",
                "project_name": "Nave Pattuglia Alfa",
                "client": "Guardia Costiera",
                "start_date": "2026-11-15",
                "data_dir": self.test_dir,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertTrue(data.get("ok"))
                self.assertEqual(data.get("status"), "ok")
                self.assertIn("project", data)
                self.assertEqual(data["project"]["name"], "Nave Pattuglia Alfa")
                self.assertEqual(data.get("tasks_created"), 4)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_api_projects_quality_gate(self):
        """Tests POST /api/projects/quality_gate endpoint."""
        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{port}/api/projects/quality_gate"
            payload = json.dumps({
                "project_id": "proj_nave_intermarine",
                "data_dir": self.test_dir,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertTrue(data.get("ok"))
                self.assertEqual(data.get("status"), "ok")
                self.assertIn("quality_gate", data)
                qg = data["quality_gate"]
                self.assertIn("readiness_score", qg)
                self.assertIn("status_label", qg)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_api_get_playbooks(self):
        """Tests GET /api/playbooks endpoint."""
        server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{port}/api/playbooks"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertTrue(data.get("ok"))
                self.assertIn("playbooks", data)
                self.assertIsInstance(data["playbooks"], list)
        finally:
            server.shutdown()
            server.server_close()

    def test_slash_command_evolve(self):
        """Tests /evolve slash command in OllamaLocalBridge."""
        bridge = OllamaLocalBridge()
        with patch.object(bridge, "_finish_reply", side_effect=lambda text, rep, *a, **kw: {"reply": rep, "text": text}), \
             patch("bridge.ai_monitor.get_skill_registry", return_value=SkillRegistry(data_dir=self.test_dir)):
            res = bridge.handle_chat_or_command("/evolve proj_nave_intermarine")
            self.assertIn("reply", res)
            reply = res["reply"]
            self.assertIn("Playbook evoluto con successo", reply)
            self.assertIn("playbook_nave_intermarine", reply)

    def test_slash_command_quality_gate(self):
        """Tests /quality-gate slash command in OllamaLocalBridge."""
        bridge = OllamaLocalBridge()
        with patch.object(bridge, "_finish_reply", side_effect=lambda text, rep, *a, **kw: {"reply": rep, "text": text}), \
             patch("bridge.ai_monitor.inspect_project_quality_gate", side_effect=lambda pid, **kw: inspect_project_quality_gate(pid, data_dir=self.test_dir)):
            res = bridge.handle_chat_or_command("/quality-gate proj_nave_intermarine")
            self.assertIn("reply", res)
            reply = res["reply"]
            self.assertIn("Pre-Delivery Quality Gate", reply)
            self.assertIn("Readiness Score", reply)


if __name__ == "__main__":
    unittest.main()
