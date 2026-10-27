"""
tests/test_challenger_m3_gate2.py
=================================
Independent Empirical Adversarial Stress Test Suite for Milestone 3 Gate 2.
Authored by challenger_m3_gate2.

Tests cover:
1. High Concurrency (32 threads) on create_project_from_playbook (0 lost projects, 0 lost tasks, 0 JSON corruption).
2. Simultaneous Reader/Writer & Evolve/Instantiate Concurrency (no deadlocks, no race conditions).
3. Adversarial Sparse & Null Playbook Collections (None parameters, client_rules, tech_stack, day_offset, subtasks).
4. Quality Gate 0-Task & Semantic Inversion Hardening (0 tasks -> forced BLOCKED, score <= 50, mandatory task definition recommendation).
5. Data Corruption Recovery (.corrupt.bak creation for both projects.json and todo.json).
6. Windows NTFS Alternate Data Streams and Illegal Path Characters in Slugs.
7. HTTP Route Error Resilience in CompanionRequestHandler (try-except HTTP 400 responses, malformed targets, unknown slugs).
8. Strict Production Data Isolation (bridge/data/ pristine at all times).
"""

from __future__ import annotations

import concurrent.futures
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
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

PRODUCTION_DATA_DIR = os.path.join(BRIDGE_DIR, "data")

from bridge.skill_registry import (
    SkillRegistry,
    evolve_project_to_playbook,
    create_project_from_playbook,
    inspect_project_quality_gate,
    list_playbooks,
    validate_skill_schema,
    CANONICAL_INTERMARINE_PROJECT,
    CANONICAL_INTERMARINE_TASKS,
)
from bridge.storage_manager import StorageManager
from bridge.ai_monitor import CompanionRequestHandler


class IsolatedGate2TestCase(unittest.TestCase):
    """Base class ensuring isolated temporary directory and zero pollution of production bridge/data/."""

    def setUp(self):
        self._temp_dir_obj = tempfile.TemporaryDirectory()
        self.test_dir = os.path.abspath(self._temp_dir_obj.name)
        self.test_db_path = os.path.join(self.test_dir, "neko_storage.db")

        # Snapshot production data before test
        self._prod_data_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)

        # Initialize test storage
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

        # Verify zero pollution of production data
        curr_prod_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)
        self.assertEqual(
            self._prod_data_snapshot,
            curr_prod_snapshot,
            "CRITICAL VIOLATION: Production files in bridge/data/ were modified during test execution!",
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


# =============================================================================
# 1. HIGH-CONCURRENCY ADVERSARIAL STRESS (32 THREADS)
# =============================================================================

class TestConcurrencyHighLoad(IsolatedGate2TestCase):
    """Stress-test concurrent read/write and project creation under heavy thread contention."""

    def test_high_concurrency_32_threads_create_project(self):
        """
        Stress test 32 simultaneous worker threads calling create_project_from_playbook.
        Must produce:
        - Exactly 32 projects in projects.json without corruption.
        - Exactly 128 tasks in todo.json (32 * 4 canonical tasks) without lost updates.
        - Zero unhandled exceptions or JSONDecodeError.
        """
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="gate2_base")

        num_threads = 32
        errors = []
        results = []

        def worker(idx: int):
            try:
                res = sr.create_project_from_playbook("gate2_base", f"Heavy Load Project {idx:02d}")
                return res
            except Exception as e:
                errors.append((idx, str(e)))
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    results.append(res)

        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        self.assertEqual(len(results), num_threads)

        proj_file = os.path.join(self.test_dir, "projects.json")
        todo_file = os.path.join(self.test_dir, "todo.json")

        with open(proj_file, "r", encoding="utf-8") as f:
            saved_projs = json.load(f)
        self.assertEqual(len(saved_projs), num_threads, f"Expected {num_threads} projects, got {len(saved_projs)}")

        with open(todo_file, "r", encoding="utf-8") as f:
            saved_todos = json.load(f)
        expected_tasks = num_threads * 4
        self.assertEqual(len(saved_todos), expected_tasks, f"Expected {expected_tasks} tasks, got {len(saved_todos)}")

    def test_interleaved_evolve_and_instantiate_concurrency(self):
        """
        Interleave 16 threads evolving playbooks and 16 threads creating projects simultaneously.
        Verifies that process and instance locks do not deadlock or leave files in half-written state.
        """
        sr = SkillRegistry(data_dir=self.test_dir)

        # Seed initial projects
        projs = []
        for i in range(8):
            projs.append({
                "id": f"proj_source_{i}",
                "name": f"Source Project {i}",
                "client": f"Client {i}",
                "notes": [f"Rule {i}"],
                "tech_stack": ["Blender", "Unreal"],
            })
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projs, f)

        # Pre-seed 1 playbook
        sr.evolve_project_to_playbook("proj_source_0", slug="initial_pb")

        evolve_errors = []
        create_errors = []

        def evolver(i: int):
            try:
                p_id = f"proj_source_{i % 8}"
                slug = f"evolved_pb_{i}"
                sr.evolve_project_to_playbook(p_id, slug=slug)
            except Exception as e:
                evolve_errors.append(str(e))

        def creator(i: int):
            try:
                sr.create_project_from_playbook("initial_pb", f"Instantiated {i}")
            except Exception as e:
                create_errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            futures = []
            for i in range(16):
                futures.append(executor.submit(evolver, i))
                futures.append(executor.submit(creator, i))
            for f in concurrent.futures.as_completed(futures):
                f.result()

        self.assertEqual(len(evolve_errors), 0, f"Evolver concurrency errors: {evolve_errors}")
        self.assertEqual(len(create_errors), 0, f"Creator concurrency errors: {create_errors}")

        # Ensure all generated playbook files on disk are valid JSON
        skills_dir = os.path.join(self.test_dir, "skills")
        for fname in os.listdir(skills_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(skills_dir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIsInstance(data, dict)


# =============================================================================
# 2. ADVERSARIAL SPARSE & NULL PLAYBOOKS
# =============================================================================

class TestAdversarialSparsePlaybooks(IsolatedGate2TestCase):
    """Stress test create_project_from_playbook against pathological sparse and null payloads."""

    def test_sparse_playbook_all_optional_fields_null(self):
        """
        Playbook where EVERY optional collection and task field is explicitly JSON null:
        - parameters: None
        - client_rules: None
        - tech_stack: None
        - domain_instincts: None
        - execution_contract: None
        - task_sequence: [
            {"title": None, "day_offset": None, "subtasks": None, "priority": None},
            None,  # non-dict element
            {"title": "Valid Title", "day_offset": 5, "subtasks": None}
          ]
        """
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        pathological_pb = {
            "schema_version": "1.0",
            "id": "playbook_sparse_nulls",
            "slug": "sparse_nulls",
            "name": "Sparse Nulls Playbook",
            "parameters": None,
            "client_rules": None,
            "tech_stack": None,
            "domain_instincts": None,
            "execution_contract": None,
            "task_sequence": [
                {"title": None, "day_offset": None, "subtasks": None, "priority": None},
                None,
                {"title": "Valid Title", "day_offset": 5, "subtasks": None},
            ],
        }

        with open(os.path.join(skills_dir, "playbook_sparse_nulls.json"), "w", encoding="utf-8") as f:
            json.dump(pathological_pb, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        res = sr.create_project_from_playbook("sparse_nulls", "Project Sparse Robustness")

        self.assertTrue(res.get("ok"), f"create_project_from_playbook failed on nulls: {res}")
        proj = res.get("project", {})
        self.assertEqual(proj.get("notes"), [])
        self.assertEqual(proj.get("tech_stack"), [])

        tasks = res.get("tasks", [])
        # Task 1 (index 0) and Task 3 (index 2) should be created; None element skipped
        self.assertEqual(len(tasks), 2)
        # Verify subtasks are safely converted to []
        self.assertEqual(tasks[0]["subtasks"], [])
        self.assertEqual(tasks[1]["subtasks"], [])

    def test_sparse_playbook_missing_task_sequence_and_execution_contract(self):
        """Playbook completely missing task_sequence and execution_contract keys."""
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        empty_pb = {
            "schema_version": "1.0",
            "id": "playbook_no_tasks",
            "slug": "no_tasks",
            "name": "No Tasks Playbook",
        }
        with open(os.path.join(skills_dir, "playbook_no_tasks.json"), "w", encoding="utf-8") as f:
            json.dump(empty_pb, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        res = sr.create_project_from_playbook("no_tasks", "Project No Tasks")
        self.assertTrue(res.get("ok"))
        self.assertEqual(len(res.get("tasks", [])), 0)


# =============================================================================
# 3. QUALITY GATE 0-TASK & SEMANTIC INVERSION HARDENING
# =============================================================================

class TestQualityGateSemanticInversionHardening(IsolatedGate2TestCase):
    """Exhaustive stress tests verifying that 0-task projects can never pass the Quality Gate."""

    def test_quality_gate_zero_tasks_always_blocked(self):
        """An empty project with 0 tasks must be BLOCKED, readiness score <= 50, completion 0.0%."""
        proj = {
            "id": "proj_gate2_empty",
            "name": "Empty Project Gate 2",
            "client": "Client Gate 2",
            "notes": ["Regola Pinned 1", "Regola Pinned 2"],
            "tech_stack": ["Three.js"],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        # Even with verified memories and instincts:
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([
                {"id": "m1", "project_id": "proj_gate2_empty", "text": "Regola Pinned 1", "pinned": True},
                {"id": "m2", "project_id": "proj_gate2_empty", "text": "Regola Pinned 2", "pinned": True},
            ], f)
        with open(os.path.join(self.test_dir, "instincts.json"), "w", encoding="utf-8") as f:
            json.dump([
                {"id": "i1", "project_id": "proj_gate2_empty", "trigger": "X", "action": "Y", "confidence": 0.95},
            ], f)

        report = inspect_project_quality_gate("proj_gate2_empty", data_dir=self.test_dir)

        self.assertEqual(report["status_label"], "BLOCKED")
        self.assertLessEqual(report["readiness_score"], 50)
        self.assertEqual(report["task_summary"]["total"], 0)
        self.assertEqual(report["task_summary"]["completion_pct"], 0.0)
        self.assertTrue(any("definire le attività di progetto" in r.lower() for r in report["recommendations"]))
        self.assertFalse(any("pronto per la consegna finale" in r.lower() for r in report["recommendations"]))

    def test_quality_gate_unverified_rules_and_incomplete_tasks_is_blocked(self):
        """Project with 4 tasks where all are in 'todo' status (0% complete) and unverified rules must evaluate to BLOCKED."""
        proj = {"id": "proj_all_todo", "name": "All Todo Proj", "notes": ["Regola Unverified"], "tech_stack": []}
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)
        todos = [
            {"id": f"t_{i}", "project_id": "proj_all_todo", "title": f"T{i}", "status": "todo"}
            for i in range(4)
        ]
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "m1", "project_id": "proj_all_todo", "text": "Regola Unverified", "pinned": False}], f)

        report = inspect_project_quality_gate("proj_all_todo", data_dir=self.test_dir)
        self.assertEqual(report["status_label"], "BLOCKED")
        self.assertLess(report["readiness_score"], 50)
        self.assertEqual(report["task_summary"]["completion_pct"], 0.0)

    def test_quality_gate_perfect_score_only_on_full_completion(self):
        """Quality Gate yields 100/100 PASSED only when all tasks are done, rules verified, and instincts applied."""
        proj = {"id": "proj_done", "name": "Done Proj", "notes": ["Regola Alpha"], "tech_stack": []}
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)
        todos = [
            {"id": "t_1", "project_id": "proj_done", "title": "T1", "status": "done"},
            {"id": "t_2", "project_id": "proj_done", "title": "T2", "status": "done"},
        ]
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "m1", "project_id": "proj_done", "text": "Regola Alpha", "pinned": True}], f)

        report = inspect_project_quality_gate("proj_done", data_dir=self.test_dir)
        self.assertEqual(report["readiness_score"], 100)
        self.assertEqual(report["status_label"], "PASSED")
        self.assertTrue(any("pronto per la consegna finale" in r.lower() for r in report["recommendations"]))


# =============================================================================
# 4. CORRUPTION RECOVERY & .corrupt.bak INVARIANT
# =============================================================================

class TestCorruptionRecovery(IsolatedGate2TestCase):
    """Verify that malformed JSON in projects.json and todo.json creates .corrupt.bak and self-heals."""

    def test_corrupt_projects_json_preserves_bak_and_recovers(self):
        """When projects.json contains invalid syntax, preserve it to .corrupt.bak and write valid new project."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="rec_pb")

        proj_file = os.path.join(self.test_dir, "projects.json")
        with open(proj_file, "w", encoding="utf-8") as f:
            f.write("<<< INVALID CORRUPTED JSON PROJECTS >>>")

        res = sr.create_project_from_playbook("rec_pb", "Recovered Project")
        self.assertTrue(res.get("ok"))

        # Verify .corrupt.bak exists
        bak_files = [f for f in os.listdir(self.test_dir) if "projects.json" in f and "corrupt.bak" in f]
        self.assertGreater(len(bak_files), 0, "No .corrupt.bak created for projects.json")

        # Verify resulting projects.json is valid and contains new project
        with open(proj_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "Recovered Project")

    def test_corrupt_todo_json_preserves_bak_and_recovers(self):
        """When todo.json contains invalid syntax, preserve it to .corrupt.bak and write valid new tasks."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="rec_todo_pb")

        todo_file = os.path.join(self.test_dir, "todo.json")
        with open(todo_file, "w", encoding="utf-8") as f:
            f.write("{{ MALFORMED TODO JSON ]]}")

        res = sr.create_project_from_playbook("rec_todo_pb", "Recovered Todo Proj")
        self.assertTrue(res.get("ok"))

        # Verify .corrupt.bak exists
        bak_files = [f for f in os.listdir(self.test_dir) if "todo.json" in f and "corrupt.bak" in f]
        self.assertGreater(len(bak_files), 0, "No .corrupt.bak created for todo.json")

        # Verify resulting todo.json is valid and contains new tasks
        with open(todo_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 4)


# =============================================================================
# 5. WINDOWS NTFS & ADVERSARIAL SLUG SANITIZATION
# =============================================================================

class TestWindowsNTFSSlugSanitization(IsolatedGate2TestCase):
    """Stress test slug sanitization against Alternate Data Streams and path traversal."""

    def test_ntfs_colon_and_illegal_characters_slugs(self):
        """
        Verify that slugs with colons, path traversals, or illegal characters:
        - Never produce Alternate Data Streams (':') on Windows NTFS.
        - Are sanitized to strictly ^[a-z0-9_]+$.
        - Save as regular .json files visible to list_playbooks().
        """
        sr = SkillRegistry(data_dir=self.test_dir)

        malicious_slugs = [
            ("attack:stream", "attack_stream"),
            ("test:alt:stream:$DATA", "test_alt_stream_data"),
            ("dir/../../traversal", "dir_traversal"),
            ("bad?char*and<more>|pipes", "bad_char_and_more_pipes"),
            ('quote"test', "quote_test"),
            ("   spaced   slug   ", "spaced_slug"),
        ]

        skills_dir = os.path.join(self.test_dir, "skills")

        for raw_slug, expected_slug in malicious_slugs:
            pb = sr.evolve_project_to_playbook("proj_nave_intermarine", slug=raw_slug)
            clean = pb.get("slug")
            self.assertEqual(clean, expected_slug, f"Raw slug '{raw_slug}' sanitized to '{clean}', expected '{expected_slug}'")
            self.assertTrue(re.match(r"^[a-z0-9_]+$", clean), f"Slug '{clean}' contains invalid characters")

            expected_fname = f"playbook_{expected_slug}.json"
            self.assertTrue(os.path.isfile(os.path.join(skills_dir, expected_fname)), f"File {expected_fname} not found on disk")

        # Test reload in a completely new SkillRegistry instance
        sr2 = SkillRegistry(data_dir=self.test_dir)
        all_pbs = sr2.list_playbooks()
        discovered_slugs = {p.get("slug") for p in all_pbs}
        for _, expected_slug in malicious_slugs:
            self.assertIn(expected_slug, discovered_slugs, f"Playbook '{expected_slug}' not discovered by list_playbooks()")


# =============================================================================
# 6. HTTP ROUTE ERROR RESILIENCE (CompanionRequestHandler)
# =============================================================================

class TestHttpRouteResilience(IsolatedGate2TestCase):
    """Verify that backend HTTP endpoints for Milestone 3 return structured errors on invalid payloads."""

    def setUp(self):
        super().setUp()
        self.server = http.server.HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        super().tearDown()

    def test_post_from_playbook_unknown_slug_returns_error(self):
        """POST /api/projects/from-playbook with unknown slug returns ok=False and descriptive message."""
        url = f"http://127.0.0.1:{self.port}/api/projects/from-playbook"
        payload = json.dumps({
            "playbook_slug": "completely_nonexistent_slug_999",
            "project_name": "Ghost Project",
            "data_dir": self.test_dir,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                code = resp.status
        except urllib.error.HTTPError as e:
            data = json.loads(e.read().decode("utf-8"))
            code = e.code
        self.assertEqual(code, 400)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("status"), "error")
        self.assertIn("non trovato", data.get("message", "").lower())

    def test_post_evolve_invalid_target_dir_returns_http_400(self):
        """POST /api/projects/evolve with a path that causes filesystem error returns HTTP 400 with error."""
        url = f"http://127.0.0.1:{self.port}/api/projects/evolve"
        # Create a file at fake_dir path so os.makedirs fails
        fake_dir = os.path.join(self.test_dir, "blocking_file")
        with open(fake_dir, "w", encoding="utf-8") as f:
            f.write("I am a file, not a directory")
        invalid_path = os.path.join(fake_dir, "sub_directory")

        payload = json.dumps({
            "project_id": "proj_nave_intermarine",
            "data_dir": invalid_path,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.fail(f"Expected HTTP 400 but received {resp.status}")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            body = json.loads(e.read().decode("utf-8"))
            self.assertFalse(body.get("ok"))
            self.assertEqual(body.get("status"), "error")
            self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
