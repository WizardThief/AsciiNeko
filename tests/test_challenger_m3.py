"""
tests/test_challenger_m3.py
===========================
Adversarial & Empirical Stress Verification Suite for Milestone 3 (R3):
- Edge cases in evolve_project_to_playbook (non-existent project, 0 tasks, identical/missing due dates, special characters/slugs)
- Edge cases in create_project_from_playbook (invalid slug, missing fields, corrupted JSON, leap year / month boundaries)
- Edge cases in inspect_project_quality_gate (0 tasks, 0 rules, contradictory rules, missing instincts, score clamping [0, 100])
- Concurrency stress testing (simultaneous multi-threaded evolve, create, and quality gate calls)
- Strict production data isolation (ensuring bridge/data/ is 100% pristine)
"""

from __future__ import annotations

import concurrent.futures
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


class IsolatedChallengerTestCase(unittest.TestCase):
    """Base class providing isolated temporary directory and zero-pollution checks."""

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
            "CRITICAL: Production files in bridge/data/ were modified during test execution!",
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
# PART 1: CONFIRMED EMPIRICAL VULNERABILITIES & FAILURE MODES
# =============================================================================


class TestEmpiricalVulnerabilities(IsolatedChallengerTestCase):
    """
    Empirical demonstrations of failure modes, race conditions, and unhandled exceptions
    discovered during Milestone 3 challenger stress testing.
    """

    def test_vuln_concurrency_race_condition_and_data_loss(self):
        """
        VULN-M3-01: Concurrency Race Condition in create_project_from_playbook.
        When multiple threads concurrently call create_project_from_playbook:
        1. projects.json suffers interleaved unbuffered writes causing JSONDecodeError (Extra data).
        2. todo.json suffers massive lost-updates / wipeout because threads read stale data and
           write back partial lists without synchronization or file locking.
        """
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="base_pb")

        num_threads = 16
        results = []

        def worker(thread_idx: int):
            p_name = f"Concurrent Proj {thread_idx}"
            return sr.create_project_from_playbook("base_pb", p_name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        self.assertEqual(len(results), num_threads)

        # Inspect resulting files
        todo_file = os.path.join(self.test_dir, "todo.json")
        proj_file = os.path.join(self.test_dir, "projects.json")

        # 1. Check projects.json parseability
        proj_corruption_detected = False
        saved_projects_count = 0
        try:
            with open(proj_file, "r", encoding="utf-8") as f:
                projs = json.load(f)
                saved_projects_count = len(projs)
        except json.decoder.JSONDecodeError:
            proj_corruption_detected = True

        # 2. Check todo.json task loss
        todo_corruption_detected = False
        todo_tasks_count = 0
        try:
            with open(todo_file, "r", encoding="utf-8") as f:
                todos = json.load(f)
                todo_tasks_count = len(todos)
        except json.decoder.JSONDecodeError:
            todo_corruption_detected = True
        except Exception:
            pass

        expected_tasks = num_threads * 4  # 16 projects * 4 tasks = 64 tasks

        # Verify that concurrency contention and lost updates are eliminated
        self.assertFalse(proj_corruption_detected, "projects.json suffered JSONDecodeError under concurrency")
        self.assertFalse(todo_corruption_detected, "todo.json suffered JSONDecodeError under concurrency")
        self.assertEqual(saved_projects_count, num_threads, f"Expected {num_threads} projects, got {saved_projects_count}")
        self.assertEqual(todo_tasks_count, expected_tasks, f"Expected {expected_tasks} tasks, got {todo_tasks_count}")

    def test_vuln_none_fields_crash_create_project(self):
        """
        VULN-M3-02: Unhandled NoneType Exceptions in create_project_from_playbook.
        When a playbook contains null/None for optional collections:
        - playbook['parameters'] = None -> crashes with TypeError ('NoneType' object is not iterable)
        - playbook['client_rules'] = None -> crashes with TypeError
        - playbook['tech_stack'] = None -> crashes with TypeError
        - task['day_offset'] = None -> crashes with TypeError (unsupported type for timedelta days)
        - task['subtasks'] = None -> crashes with TypeError
        """
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        # Case A: playbook with parameters = None
        pb_null_params = {
            "schema_version": "1.0",
            "id": "playbook_null_params",
            "slug": "null_params",
            "name": "Null Params Playbook",
            "safety_tier": "CONFIRM_ONCE",
            "trigger_patterns": ["/null_params"],
            "parameters": None,
            "task_sequence": [{"title": "Task 1", "day_offset": 0}],
            "execution_contract": {"type": "PROJECT_PLAYBOOK"},
        }
        with open(os.path.join(skills_dir, "playbook_null_params.json"), "w", encoding="utf-8") as f:
            json.dump(pb_null_params, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        res1 = sr.create_project_from_playbook("null_params", "Proj Null Params")
        self.assertTrue(res1.get("ok"), f"Failed to handle null parameters: {res1}")
        self.assertEqual(res1.get("project", {}).get("name"), "Proj Null Params")

        # Case B: task with day_offset = None
        pb_null_offset = {
            "schema_version": "1.0",
            "id": "playbook_null_offset",
            "slug": "null_offset",
            "name": "Null Offset Playbook",
            "safety_tier": "CONFIRM_ONCE",
            "trigger_patterns": ["/null_offset"],
            "parameters": [],
            "task_sequence": [{"title": "Task 1", "day_offset": None}],
            "execution_contract": {"type": "PROJECT_PLAYBOOK"},
        }
        with open(os.path.join(skills_dir, "playbook_null_offset.json"), "w", encoding="utf-8") as f:
            json.dump(pb_null_offset, f)

        res2 = sr.create_project_from_playbook("null_offset", "Proj Null Offset")
        self.assertTrue(res2.get("ok"), f"Failed to handle null day_offset: {res2}")
        self.assertEqual(len(res2.get("tasks", [])), 1)

    def test_vuln_empty_project_quality_gate_semantic_inversion(self):
        """
        VULN-M3-03: Quality Gate Blind Pass for Empty Projects.
        When a project has 0 tasks, 0 client rules, and 0 technical instincts:
        - task_completion_pct is evaluated to 0.0%
        - status_label is set to BLOCKED or UNSTARTED
        - readiness_score is never 100/100
        - recommendations requires defining tasks in To-Do first.
        """
        proj = {
            "id": "proj_unstarted_empty",
            "name": "Empty Unstarted Project",
            "client": "Client Z",
            "notes": [],
            "tech_stack": [],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(os.path.join(self.test_dir, "instincts.json"), "w", encoding="utf-8") as f:
            json.dump([], f)

        report = inspect_project_quality_gate("proj_unstarted_empty", data_dir=self.test_dir)
        self.assertNotEqual(report["readiness_score"], 100)
        self.assertIn(report["status_label"], ("BLOCKED", "UNSTARTED"))
        self.assertEqual(report["task_summary"]["total"], 0)
        self.assertEqual(report["task_summary"]["completion_pct"], 0.0)
        self.assertTrue(any("definire le attività di progetto" in r.lower() for r in report["recommendations"]))
        self.assertFalse(any("pronto per la consegna finale" in r.lower() for r in report["recommendations"]))

    def test_vuln_silent_wipeout_without_corrupt_bak(self):
        """
        VULN-M3-04: Silent Wipeout of Corrupt Files without .corrupt.bak (Regression of VULN-02).
        In create_project_from_playbook, if todo.json contains invalid syntax,
        the corrupted file is preserved as <filepath>.<timestamp>.corrupt.bak before re-initialization.
        """
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="base_pb")

        todo_file = os.path.join(self.test_dir, "todo.json")
        with open(todo_file, "w", encoding="utf-8") as f:
            f.write("{ CORRUPT DATA THAT CANNOT BE PARSED AS JSON ")

        res = sr.create_project_from_playbook("base_pb", "Overwritten Project")
        self.assertTrue(res.get("ok"))

        # Verify .corrupt.bak file was created
        bak_files = [f for f in os.listdir(self.test_dir) if "corrupt.bak" in f]
        self.assertGreater(len(bak_files), 0, "Expected .corrupt.bak file to be created when encountering invalid JSON")

    def test_vuln_ntfs_alternate_data_stream_in_custom_slug(self):
        """
        VULN-M3-05: Windows NTFS Alternate Data Stream on Unsanitized Custom Slugs.
        In evolve_project_to_playbook, clean_slug sanitizes slug even when explicitly passed,
        stripping characters like ':' to prevent NTFS Alternate Data Stream creation.
        """
        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook("proj_nave_intermarine", slug="test:stream")
        self.assertEqual(pb.get("slug"), "test_stream")

        skills_dir = os.path.join(self.test_dir, "skills")
        # On disk, the visible file is 'playbook_test_stream.json'
        files = os.listdir(skills_dir)
        self.assertIn("playbook_test_stream.json", files)
        self.assertNotIn("playbook_test", files)

        # A new SkillRegistry instance discovers this playbook from disk
        sr2 = SkillRegistry(data_dir=self.test_dir)
        pbs = sr2.list_playbooks()
        self.assertTrue(any(p.get("id") == "playbook_test_stream" for p in pbs))


# =============================================================================
# PART 2: FUNCTIONAL BOUNDARY, LEAP YEAR & QUALITY GATE VERIFICATION
# =============================================================================


class TestFunctionalBoundaries(IsolatedChallengerTestCase):
    """Functional boundary and corner tests for Milestone 3 implementation."""

    def test_evolve_nonexistent_project_fallback(self):
        """Evolve a non-existent project should synthesize a fallback and not crash."""
        sr = SkillRegistry(data_dir=self.test_dir)
        playbook = sr.evolve_project_to_playbook("proj_completely_unknown_xyz_99")
        self.assertIsNotNone(playbook)
        self.assertIn("Completely Unknown Xyz 99", playbook.get("name", ""))
        self.assertEqual(playbook.get("task_sequence"), [])
        valid, msg = validate_skill_schema(playbook)
        self.assertTrue(valid, f"Fallback playbook failed schema validation: {msg}")

    def test_evolve_identical_due_dates(self):
        """Tasks with identical due dates should all receive day_offset = 0."""
        proj = {"id": "proj_same_dates", "name": "Same Dates Project", "notes": [], "tech_stack": []}
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)

        todos = [
            {"id": "t1", "project_id": "proj_same_dates", "title": "Task 1", "due_date": "2026-11-05"},
            {"id": "t2", "project_id": "proj_same_dates", "title": "Task 2", "due_date": "2026-11-05"},
            {"id": "t3", "project_id": "proj_same_dates", "title": "Task 3", "due_date": "2026-11-05"},
        ]
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook("proj_same_dates")
        seq = pb.get("task_sequence", [])
        self.assertEqual(len(seq), 3)
        self.assertEqual([t["day_offset"] for t in seq], [0, 0, 0])

    def test_create_leap_year_date_arithmetic(self):
        """Verify leap year arithmetic across February 29 (e.g. 2024 leap year)."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="leap_test")

        res = sr.create_project_from_playbook(
            playbook_slug="leap_test",
            project_name="Leap Test Project",
            start_date="2024-02-28",
        )
        self.assertTrue(res.get("ok"))
        tasks = res.get("tasks", [])
        self.assertEqual(len(tasks), 4)

        due_dates = [t["due_date"] for t in tasks]
        self.assertEqual(due_dates, ["2024-02-28", "2024-02-29", "2024-03-01", "2024-03-03"])

        # Check Italian weekday labels
        labels = [t["due_label"] for t in tasks]
        self.assertEqual(labels, ["mer 28/02", "gio 29/02", "ven 01/03", "dom 03/03"])

    def test_create_month_and_year_boundary_arithmetic(self):
        """Verify year boundary arithmetic from Dec 30 across Jan 1."""
        sr = SkillRegistry(data_dir=self.test_dir)
        sr.evolve_project_to_playbook("proj_nave_intermarine", slug="year_test")

        res = sr.create_project_from_playbook(
            playbook_slug="year_test",
            project_name="Year Boundary Project",
            start_date="2026-12-30",
        )
        self.assertTrue(res.get("ok"))
        tasks = res.get("tasks", [])
        due_dates = [t["due_date"] for t in tasks]
        self.assertEqual(due_dates, ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-03"])

        labels = [t["due_label"] for t in tasks]
        self.assertEqual(labels, ["mer 30/12", "gio 31/12", "ven 01/01", "dom 03/01"])

    def test_quality_gate_score_clamping_and_status_labels(self):
        """Verify readiness_score is strictly clamped in [0, 100] across status thresholds."""
        proj = {
            "id": "proj_clamping_test",
            "name": "Clamping Test",
            "notes": ["Regola A"],
            "tech_stack": [],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)

        # 0% completion -> BLOCKED
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "t1", "project_id": "proj_clamping_test", "title": "Task 1", "status": "todo"}], f)
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "m1", "project_id": "proj_clamping_test", "text": "Regola A", "pinned": False}], f)
        with open(os.path.join(self.test_dir, "instincts.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "i1", "project_id": "proj_clamping_test", "trigger": "X", "action": "Y", "confidence": 0.4}], f)

        rep = inspect_project_quality_gate("proj_clamping_test", data_dir=self.test_dir)
        self.assertGreaterEqual(rep["readiness_score"], 0)
        self.assertLessEqual(rep["readiness_score"], 100)
        self.assertEqual(rep["status_label"], "BLOCKED")

        # 100% completion -> PASSED
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "t1", "project_id": "proj_clamping_test", "title": "Task 1", "status": "done"}], f)
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "m1", "project_id": "proj_clamping_test", "text": "Regola A", "pinned": True}], f)
        with open(os.path.join(self.test_dir, "instincts.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "i1", "project_id": "proj_clamping_test", "trigger": "X", "action": "Y", "confidence": 0.9}], f)

        rep2 = inspect_project_quality_gate("proj_clamping_test", data_dir=self.test_dir)
        self.assertEqual(rep2["readiness_score"], 100)
        self.assertEqual(rep2["status_label"], "PASSED")

    def test_evolve_project_with_zero_tasks(self):
        """Evolve a project that exists in projects.json but has 0 tasks in todo.json."""
        proj = {
            "id": "proj_zero_tasks",
            "name": "Progetto Vuoto",
            "client": "Cliente Zero",
            "tech_stack": ["Python", "Rust"],
            "notes": ["Regola A"],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump([], f)

        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook("proj_zero_tasks")
        self.assertEqual(pb.get("task_sequence"), [])
        self.assertEqual(pb.get("tech_stack"), ["Python", "Rust"])
        self.assertEqual(pb.get("client_rules"), ["Regola A"])

    def test_evolve_tasks_with_missing_and_mixed_due_dates(self):
        """Tasks with missing due dates should fall back to relative sequencing without crashing."""
        proj = {"id": "proj_mixed_dates", "name": "Mixed Dates Project", "notes": [], "tech_stack": []}
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)

        todos = [
            {"id": "t1", "project_id": "proj_mixed_dates", "title": "Task Dated", "due_date": "2026-11-01"},
            {"id": "t2", "project_id": "proj_mixed_dates", "title": "Task No Date 1"},
            {"id": "t3", "project_id": "proj_mixed_dates", "title": "Task Malformed Date", "due_date": "not-a-date"},
        ]
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook("proj_mixed_dates")
        seq = pb.get("task_sequence", [])
        self.assertEqual(len(seq), 3)
        for t in seq:
            self.assertIsInstance(t["day_offset"], int)
            self.assertGreaterEqual(t["day_offset"], 0)

    def test_evolve_special_characters_in_project_name(self):
        """Test project with emojis, unicode, quotes, and punctuation."""
        weird_id = "proj_test_name"
        proj = {
            "id": weird_id,
            "name": "Neko 🚀 Art: Sfondo \"Blu\" & Rosso / 100%",
            "client": "O'Connor & Sons <Test>",
            "tech_stack": ["Three.js", "C++"],
            "notes": ["Rule: 'quote' and \"double quote\""],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)

        sr = SkillRegistry(data_dir=self.test_dir)
        pb = sr.evolve_project_to_playbook(weird_id)
        self.assertIsNotNone(pb)
        self.assertIn("Neko 🚀 Art", pb.get("name", ""))
        self.assertTrue(re.match(r"^[a-z0-9_]+$", pb.get("slug", "")))

    def test_create_from_invalid_or_missing_slug(self):
        """Invalid slug must return error dictionary with ok=False without raising unhandled exceptions."""
        sr = SkillRegistry(data_dir=self.test_dir)
        res = sr.create_project_from_playbook("non_existent_playbook_xyz_999", "New Project")
        self.assertFalse(res.get("ok"))
        self.assertIn("non trovato", res.get("message", "").lower())

    def test_create_from_corrupted_playbook_json(self):
        """Corrupted JSON playbook file should be handled gracefully and return ok=False."""
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        corrupt_file = os.path.join(skills_dir, "playbook_corrupt_pb.json")
        with open(corrupt_file, "w", encoding="utf-8") as f:
            f.write("{{ INVALID JSON CORRUPTED DATA !!! }}")

        sr = SkillRegistry(data_dir=self.test_dir)
        res = sr.create_project_from_playbook("corrupt_pb", "New Project From Corrupt")
        self.assertFalse(res.get("ok"))

    def test_quality_gate_contradictory_rules_handling(self):
        """Test behavior when contradictory client rules exist (both marked verified if pinned)."""
        proj = {
            "id": "proj_contradictory",
            "name": "Contradictory Project",
            "notes": ["Regola Base"],
            "tech_stack": [],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)

        rules = [
            {"id": "m1", "project_id": "proj_contradictory", "text": "Logo deve essere sempre BLU", "pinned": True},
            {"id": "m2", "project_id": "proj_contradictory", "text": "Logo non deve MAI essere blu, usare ROSSO", "pinned": True},
        ]
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump(rules, f)

        rep = inspect_project_quality_gate("proj_contradictory", data_dir=self.test_dir)
        self.assertTrue(rep.get("ok"))
        chk = rep.get("client_rules_checklist", [])
        self.assertGreaterEqual(len(chk), 2)
        # Both contradictory rules are marked VERIFIED
        self.assertTrue(all(r["status"] == "VERIFIED" for r in chk))

    def test_quality_gate_missing_instincts(self):
        """Test quality gate when 0 technical instincts exist in instincts.json."""
        proj = {
            "id": "proj_no_instincts",
            "name": "No Instincts Project",
            "client": "Client Y",
            "notes": [],
            "tech_stack": ["UnknownTech"],
        }
        with open(os.path.join(self.test_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump([proj], f)
        with open(os.path.join(self.test_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "t1", "project_id": "proj_no_instincts", "title": "T1", "status": "done"}], f)
        with open(os.path.join(self.test_dir, "memory.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(os.path.join(self.test_dir, "instincts.json"), "w", encoding="utf-8") as f:
            json.dump([], f)

        rep = inspect_project_quality_gate("proj_no_instincts", data_dir=self.test_dir)
        self.assertTrue(rep.get("ok"))
        self.assertEqual(len(rep.get("technical_instincts_checklist", [])), 0)
        # 100% done tasks (50 pts) + 100% rules default (30 pts) + 100% instincts default (20 pts) = 100
        self.assertEqual(rep["readiness_score"], 100)


if __name__ == "__main__":
    unittest.main()

