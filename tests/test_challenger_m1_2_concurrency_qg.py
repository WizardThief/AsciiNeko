"""
tests/test_challenger_m1_2_concurrency_qg.py
============================================
Adversarial Verification Suite for Milestone 1 (F1-F8):
Storage Concurrency, Bind Folder API Robustness, Quality Gate Edge Cases, and Hook Safety.

Written by: challenger_m1_2
Target Codebase: C:\\Users\\AMD04\\EPO\\AsciiArt
Strict Isolation: All tests use tempfile.TemporaryDirectory() with ZERO pollution of bridge/data/.
"""

import http.server
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from typing import Any, Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(r"C:\Users\AMD04\EPO\AsciiArt")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bridge.workspace_scanner import WorkspaceScanner, scan_workspace
from bridge.storage_manager import StorageManager, get_storage_manager
from bridge.ai_monitor import MemoryAndProjectsManager, CompanionRequestHandler
from bridge.skill_registry import SkillRegistry, inspect_project_quality_gate
from bridge.hooks_runner import HooksRunner


class TestChallengerM1ConcurrencyAndQualityGate(unittest.TestCase):
    """
    Adversarial verification testing covering:
    1. Storage & API Concurrency Stress
    2. Quality Gate Edge Cases
    3. Hook Safety & Window Matching with Unicode/Edge Inputs
    4. Production Data Hygiene
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="challenger_m1_2_")
        self.data_dir = os.path.join(self.temp_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.workspaces_root = os.path.join(self.temp_dir, "workspaces")
        os.makedirs(self.workspaces_root, exist_ok=True)

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # SECTION 1: API & CONCURRENCY STRESS
    # =========================================================================

    def test_concurrent_folder_binding_10_threads_zero_lost_updates(self):
        """
        Adversarial Test 1.1:
        Concurrently bind 10 distinct folders across 10 threads using MemoryAndProjectsManager.
        Verify zero SQLite locked errors, zero lost updates, and 100% consistency
        between projects.json and SQLite projects table.
        """
        # 1. Initialize data_dir with 10 distinct projects (including proj_nave_intermarine)
        projects = []
        folder_paths = {}

        # First project is canonical Nave Intermarine
        p_intermarine_id = "proj_nave_intermarine"
        ws_intermarine = os.path.join(self.workspaces_root, "ws_intermarine")
        os.makedirs(ws_intermarine, exist_ok=True)
        with open(os.path.join(ws_intermarine, "ship.blend"), "wb") as f:
            f.write(b"blend" * 100)
        with open(os.path.join(ws_intermarine, "ship.glb"), "wb") as f:
            f.write(b"glb" * 100)
        folder_paths[p_intermarine_id] = ws_intermarine
        projects.append({
            "id": p_intermarine_id,
            "name": "Nave Intermarine",
            "client": "Intermarine",
            "status": "active",
            "notes": ["Logo sempre blu marino"],
            "tech_stack": ["Blender", "Export GLB / Draco"],
            "workspace_path": "",
            "workspace_summary": {},
            "created_at": "2026-10-28 10:00",
            "updated_at": "2026-10-28 10:00",
            "last_ts": time.time(),
        })

        # 9 more distinct projects to make exactly 10
        for i in range(1, 10):
            pid = f"proj_stress_{i:02d}"
            pname = f"Stress Project {i:02d}"
            ws_folder = os.path.join(self.workspaces_root, f"ws_{i:02d}")
            os.makedirs(ws_folder, exist_ok=True)

            # Create distinct asset files in each folder
            with open(os.path.join(ws_folder, f"asset_{i}.blend"), "wb") as f:
                f.write(b"blend" * 100)
            if i % 2 == 0:
                with open(os.path.join(ws_folder, f"export_{i}.glb"), "wb") as f:
                    f.write(b"glb" * 200)

            folder_paths[pid] = ws_folder
            projects.append({
                "id": pid,
                "name": pname,
                "client": f"Client_{i}",
                "status": "active",
                "notes": [f"Note {i}"],
                "tech_stack": ["Blender"],
                "workspace_path": "",
                "workspace_summary": {},
                "created_at": "2026-10-28 10:00",
                "updated_at": "2026-10-28 10:00",
                "last_ts": time.time(),
            })

        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)

        # Initialize SQLite database and migrate legacy projects
        sm = StorageManager(data_dir=self.data_dir)
        sm.migrate_legacy_data(self.data_dir)

        # Manager instance shared across threads
        mgr = MemoryAndProjectsManager(data_dir=self.data_dir)

        errors: List[Exception] = []
        results: Dict[str, Any] = {}
        lock = threading.Lock()

        def worker(pid: str, path: str):
            try:
                # Small randomized delay to maximize contention window
                time.sleep(0.005 * (hash(pid) % 4))
                res = mgr.bind_project_folder(pid, path)
                with lock:
                    results[pid] = res
            except Exception as ex:
                with lock:
                    errors.append(ex)

        threads = []
        for pid, path in folder_paths.items():
            t = threading.Thread(target=worker, args=(pid, path))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # Assert no thread crashed with unhandled exception or SQLite lock
        self.assertEqual(len(errors), 0, f"Concurrent binding threw errors: {errors}")
        self.assertEqual(len(results), 10, "All 10 binding calls must complete")

        for pid, res in results.items():
            self.assertTrue(res.get("ok"), f"Binding result for {pid} failed: {res}")
            self.assertEqual(res.get("workspace_path"), folder_paths[pid])

        # Verify projects.json persistence: ZERO lost updates
        with open(os.path.join(self.data_dir, "projects.json"), "r", encoding="utf-8") as f:
            saved_json = json.load(f)

        self.assertEqual(len(saved_json), 10, "projects.json must retain all 10 projects")
        for p in saved_json:
            pid = p["id"]
            expected_path = folder_paths[pid]
            self.assertEqual(
                p.get("workspace_path"),
                expected_path,
                f"Lost update in projects.json for {pid}: expected {expected_path}, got {p.get('workspace_path')}"
            )
            self.assertIn("workspace_summary", p)
            self.assertTrue(p["workspace_summary"].get("exists"))
            self.assertGreater(p["workspace_summary"].get("total_files", 0), 0)

        # Verify SQLite table consistency: 100% parity with projects.json
        sqlite_projects = sm.get_all_projects()
        self.assertEqual(len(sqlite_projects), 10, "SQLite projects table must contain 10 projects")
        sqlite_map = {p["id"]: p for p in sqlite_projects}

        for pid, expected_path in folder_paths.items():
            db_p = sqlite_map.get(pid)
            self.assertIsNotNone(db_p, f"Project {pid} missing from SQLite")
            self.assertEqual(
                db_p["workspace_path"],
                expected_path,
                f"SQLite workspace_path mismatch for {pid}: expected {expected_path}, got {db_p['workspace_path']}"
            )
            self.assertIsInstance(db_p["workspace_summary"], dict)
            self.assertTrue(db_p["workspace_summary"].get("exists"))

        sm.close()

    def test_concurrent_storage_manager_update_workspace(self):
        """
        Adversarial Test 1.2:
        Concurrently call sm.update_project_workspace on 10 distinct projects
        from multiple threads across independent StorageManager instances.
        Verify zero SQLite locked errors and clean data consistency.
        """
        projects = []
        for i in range(10):
            projects.append({
                "id": f"proj_sm_{i:02d}",
                "name": f"SM Project {i:02d}",
                "workspace_path": "",
                "workspace_summary": {},
            })

        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)

        sm_init = StorageManager(data_dir=self.data_dir)
        sm_init.migrate_legacy_data(self.data_dir)
        sm_init.close()

        errors = []
        lock = threading.Lock()

        def sm_worker(idx: int):
            try:
                # Use independent StorageManager instance per thread
                local_sm = StorageManager(data_dir=self.data_dir)
                pid = f"proj_sm_{idx:02d}"
                ws_path = os.path.join(self.workspaces_root, f"sm_ws_{idx:02d}")
                summary = {"total_files": idx + 1, "detected_stack": ["Rust"]}
                ok = local_sm.update_project_workspace(pid, ws_path, summary)
                local_sm.close()
                if not ok:
                    with lock:
                        errors.append(f"update_project_workspace returned False for {pid}")
            except Exception as ex:
                with lock:
                    errors.append(ex)

        threads = [threading.Thread(target=sm_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Errors during concurrent SM updates: {errors}")

        # Check SQLite projects
        sm_verify = StorageManager(data_dir=self.data_dir)
        all_projs = sm_verify.get_all_projects()
        self.assertEqual(len(all_projs), 10)
        for p in all_projs:
            self.assertTrue(p["workspace_path"].startswith(self.workspaces_root))
            self.assertEqual(p["workspace_summary"].get("detected_stack"), ["Rust"])
        sm_verify.close()

    def test_rapid_successive_binds_same_project(self):
        """
        Adversarial Test 1.3:
        20 rapid successive binds on the SAME project with different paths.
        Verify memory, projects.json, and SQLite remain consistent without race condition wipeout.
        """
        # Use canonical Nave Intermarine which is pre-registered in SQLite
        mgr = MemoryAndProjectsManager(data_dir=self.data_dir)
        pid = "proj_nave_intermarine"

        last_path = ""
        for i in range(20):
            ws_folder = os.path.join(self.workspaces_root, f"rapid_ws_{i}")
            os.makedirs(ws_folder, exist_ok=True)
            with open(os.path.join(ws_folder, f"file_{i}.txt"), "w") as f:
                f.write(f"content {i}")
            res = mgr.bind_project_folder(pid, ws_folder)
            self.assertTrue(res["ok"])
            last_path = ws_folder

        # Verify in-memory state
        target_in_mem = next(p for p in mgr.projects if p["id"] == pid)
        self.assertEqual(target_in_mem["workspace_path"], last_path)

        # Verify projects.json persistence
        with open(os.path.join(self.data_dir, "projects.json"), "r", encoding="utf-8") as f:
            saved = json.load(f)
        saved_target = next(p for p in saved if p["id"] == pid)
        self.assertEqual(saved_target["workspace_path"], last_path)
        self.assertEqual(saved_target["workspace_summary"]["total_files"], 1)

        # Verify SQLite table
        sm = StorageManager(data_dir=self.data_dir)
        db_projs = sm.get_all_projects()
        db_target = next(p for p in db_projs if p["id"] == pid)
        self.assertEqual(db_target["workspace_path"], last_path)
        self.assertEqual(db_target["workspace_summary"]["total_files"], 1)
        sm.close()

    def test_bind_folder_api_validation_edge_cases(self):
        """
        Adversarial Test 1.4:
        Test POST /api/projects/bind_folder against:
        - Non-existent directories
        - Path pointing to a file (not a folder)
        - Empty string and whitespace-only
        - Malformed/invalid JSON payload
        - Non-dict JSON payload (list, number, string)
        - Unknown project_id
        """
        # Pre-seed a project in self.data_dir
        mgr = MemoryAndProjectsManager(data_dir=self.data_dir)
        mgr.create_or_update_project(name="Validation Proj", client="ValClient")
        valid_pid = mgr.projects[0]["id"]

        valid_folder = os.path.join(self.workspaces_root, "valid_ws")
        os.makedirs(valid_folder, exist_ok=True)
        dummy_file = os.path.join(self.workspaces_root, "not_a_folder.txt")
        with open(dummy_file, "w") as f:
            f.write("I am a file")

        # Launch HTTP server on ephemeral port
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. Non-existent directory
            non_existent = os.path.join(self.workspaces_root, "does_not_exist_xyz_999")
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=json.dumps({"project_id": valid_pid, "workspace_path": non_existent, "data_dir": self.data_dir}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)
            resp_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertFalse(resp_body["ok"])
            self.assertIn("non trovata", resp_body["error"].lower())

            # 2. Path pointing to a file (not a directory)
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=json.dumps({"project_id": valid_pid, "workspace_path": dummy_file, "data_dir": self.data_dir}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)
            resp_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertFalse(resp_body["ok"])
            self.assertIn("non è una cartella", resp_body["error"].lower())

            # 3. Empty string
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=json.dumps({"project_id": valid_pid, "workspace_path": "", "data_dir": self.data_dir}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)
            resp_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertFalse(resp_body["ok"])

            # 4. Whitespace string
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=json.dumps({"project_id": valid_pid, "workspace_path": "   \t  ", "data_dir": self.data_dir}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)

            # 5. Malformed JSON payload
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=b"{\"broken_json: unclosed",
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)

            # 6. Non-dict JSON payload (array: [1, 2, 3])
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=b"[1, 2, 3]",
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)

            # 7. Unknown project_id with non-empty workspace
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=json.dumps({"project_id": "proj_completely_unknown_999", "workspace_path": valid_folder, "data_dir": self.data_dir}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5.0)
            self.assertEqual(ctx.exception.code, 400)
            resp_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertFalse(resp_body["ok"])
            self.assertIn("non trovato", resp_body["error"].lower())

            # 8. Valid binding must succeed with 200
            req = urllib.request.Request(
                f"{base_url}/api/projects/bind_folder",
                data=json.dumps({"project_id": valid_pid, "workspace_path": valid_folder, "data_dir": self.data_dir}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                self.assertEqual(resp.status, 200)
                resp_body = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(resp_body["ok"])
                self.assertEqual(resp_body["workspace_path"], valid_folder)

        finally:
            server.shutdown()
            server.server_close()

    # =========================================================================
    # SECTION 2: QUALITY GATE ROBUSTNESS
    # =========================================================================

    def test_quality_gate_project_without_workspace_path(self):
        """
        Adversarial Test 2.1:
        Project without workspace_path (empty string or missing attribute).
        Verify inspect_project_quality_gate succeeds cleanly without KeyError or crash,
        and workspace_checklist is None.
        """
        projects = [{
            "id": "proj_no_ws",
            "name": "Project Without Workspace",
            "client": "ClientA",
            "notes": ["Logo sempre blu"],
            "tech_stack": ["Blender"],
            "workspace_path": "",
            "workspace_summary": {},
        }]
        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f)

        todos = [
            {"id": "t1", "title": "Task 1", "project": "Project Without Workspace", "status": "done"},
        ]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report = inspect_project_quality_gate("proj_no_ws", data_dir=self.data_dir)
        self.assertTrue(report["ok"])
        self.assertEqual(report["project_id"], "proj_no_ws")
        self.assertIsNone(report.get("workspace_checklist"))
        self.assertNotIn("workspace_summary", report)
        self.assertGreaterEqual(report["readiness_score"], 0)
        self.assertIn("status_label", report)

    def test_quality_gate_deleted_or_nonexistent_workspace_folder(self):
        """
        Adversarial Test 2.2:
        Project with workspace_path pointing to a deleted or non-existent folder.
        Verify Quality Gate reports folder missing in checklist without crashing,
        status is FAILED, and actionable recommendation is provided.
        """
        deleted_folder = os.path.join(self.workspaces_root, "deleted_client_ws")
        # Ensure it does not exist
        if os.path.exists(deleted_folder):
            shutil.rmtree(deleted_folder)

        projects = [{
            "id": "proj_missing_folder",
            "name": "Nave Fantasma",
            "client": "Intermarine",
            "notes": ["Logo ufficiale = Blu Marino"],
            "tech_stack": ["Blender", "Export GLB / Draco"],
            "workspace_path": deleted_folder,
            "workspace_summary": {},
        }]
        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f)

        todos = [
            {"id": "t1", "title": "Cleanup modello in Blender", "project": "Nave Fantasma", "status": "done"},
            {"id": "t2", "title": "Export GLB ottimizzato con Draco", "project": "Nave Fantasma", "status": "todo"},
        ]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report = inspect_project_quality_gate("proj_missing_folder", data_dir=self.data_dir)
        self.assertTrue(report["ok"])
        self.assertIsNotNone(report.get("workspace_checklist"))

        ws_chk = report["workspace_checklist"]
        self.assertEqual(ws_chk["status"], "FAILED")
        self.assertFalse(ws_chk["accessible"])
        self.assertEqual(ws_chk["total_files"], 0)

        # Check client_rules_checklist contains workspace deliverables check with FAILED status
        ws_rule = next((c for c in report["client_rules_checklist"] if "Workspace & Deliverables Check" in c.get("rule", "")), None)
        self.assertIsNotNone(ws_rule, "client_rules_checklist must contain Workspace & Deliverables Check")
        self.assertEqual(ws_rule["status"], "FAILED")

        # Check recommendations contains inaccessible folder warning
        rec_found = any("non risulta accessibile" in r for r in report["recommendations"])
        self.assertTrue(rec_found, f"Recommendations must report folder inaccessible: {report['recommendations']}")

    def test_quality_gate_workspace_only_git_zero_deliverables(self):
        """
        Adversarial Test 2.3:
        Project with workspace_path having only .git (no files) or zero deliverables.
        Verify Quality Gate handles zero deliverables gracefully without crashing,
        reports 0 deliverables detected, and scores appropriately.
        """
        empty_git_ws = os.path.join(self.workspaces_root, "empty_git_ws")
        git_dir = os.path.join(empty_git_ws, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")

        projects = [{
            "id": "proj_zero_deliv",
            "name": "Zero Deliverables Project",
            "client": "ClientZ",
            "notes": ["Regola A"],
            "tech_stack": ["Rust", "Three.js"],
            "workspace_path": empty_git_ws,
            "workspace_summary": {},
        }]
        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f)

        todos = [
            {"id": "t1", "title": "Attività 1", "project": "Zero Deliverables Project", "status": "todo"},
        ]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report = inspect_project_quality_gate("proj_zero_deliv", data_dir=self.data_dir)
        self.assertTrue(report["ok"])
        ws_chk = report["workspace_checklist"]
        self.assertTrue(ws_chk["accessible"])
        self.assertEqual(ws_chk["total_files"], 0, "Files inside .git must be excluded")
        self.assertEqual(len(ws_chk["key_exports"]), 0)
        self.assertTrue(ws_chk["git_info"]["is_git_repo"])
        self.assertEqual(ws_chk["git_info"]["branch"], "main")

        # Check rule description mentions 'nessun file export rilevato'
        ws_rule = next(c for c in report["client_rules_checklist"] if "Workspace & Deliverables Check" in c.get("rule", ""))
        self.assertIn("nessun file export rilevato", ws_rule["rule"])

    # =========================================================================
    # SECTION 3: HOOK SAFETY & ACTIVE WINDOW MATCHING
    # =========================================================================

    def test_hook_window_matching_edge_cases(self):
        """
        Adversarial Test 3.1:
        Test match_window_to_tasks against:
        - Empty window title and empty app_name
        - Non-existent file paths
        - Non-DCC applications (Notepad, Spotify, Calculator)
        - Unicode, emojis, Japanese characters, special punctuation in window title and filename
        - Verify zero crashes and proper active project resolution
        """
        # Set up a project with workspace
        ws_folder = os.path.join(self.workspaces_root, "unicode_ws")
        os.makedirs(ws_folder, exist_ok=True)

        # Create files with Unicode, emoji, and special characters
        unicode_filename = "⛵_Intermarine_Caffè_Möbius_テスト.blend"
        with open(os.path.join(ws_folder, unicode_filename), "wb") as f:
            f.write(b"data")

        projects = [{
            "id": "proj_unicode_intermarine",
            "name": "Nave Intermarine Special",
            "workspace_path": ws_folder,
        }]
        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False)

        todos = [{
            "id": "t_special_1",
            "title": "Modellazione 3D in Blender per Intermarine",
            "project": "Nave Intermarine Special",
            "status": "todo",
        }]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f, ensure_ascii=False)

        mem_mgr = MemoryAndProjectsManager(data_dir=self.data_dir)
        runner = HooksRunner(data_dir=self.data_dir, mem_proj_manager=mem_mgr)

        try:
            # 1. Empty window title & app_name
            res = runner.match_window_to_tasks({})
            self.assertIsNone(res)
            res = runner.match_window_to_tasks({"app_name": "", "window_title": ""})
            self.assertIsNone(res)
            res = runner.match_window_to_tasks({"app_name": "desktop", "window_title": "Desktop"})
            self.assertIsNone(res)

            # 2. Non-DCC app (e.g. Spotify, Calculator)
            res = runner.match_window_to_tasks({
                "app_name": "Spotify",
                "window_title": "Spotify Free - Daft Punk",
                "is_dcc": False,
            })
            self.assertIsNone(res)

            # 3. Non-existent path in window_info
            res = runner.match_window_to_tasks({
                "app_name": "Blender",
                "window_title": "Blender - Z:\\non_existent_drive\\fake_123.blend",
                "file_name": "fake_123.blend",
                "is_dcc": True,
            })
            # Should not crash, returns suggestion or None
            self.assertTrue(res is None or isinstance(res, dict))

            # 4. Unicode / emoji title matching the bound workspace file
            window_info_unicode = {
                "app_name": "Blender",
                "window_title": f"Blender - {unicode_filename}",
                "file_name": unicode_filename,
                "is_dcc": True,
            }
            sugg = runner.match_window_to_tasks(window_info_unicode)
            self.assertIsNotNone(sugg, "Must match task for Unicode window title")
            self.assertEqual(sugg["task_id"], "t_special_1")
            self.assertEqual(sugg["detected_app"].lower(), "blender")

            # Verify active project was auto-linked in mem_proj_manager
            self.assertEqual(mem_mgr.profile.get("active_project_id"), "proj_unicode_intermarine")

        finally:
            runner.shutdown()

    def test_rapid_successive_window_switches_stress(self):
        """
        Adversarial Test 3.2:
        Simulate 50 rapid successive window switch events alternating between
        DCC apps, workspace folders, and background desktop noise.
        Verify zero thread deadlocks, zero lock contention exceptions,
        and consistent state.
        """
        ws_a = os.path.join(self.workspaces_root, "ws_a")
        ws_b = os.path.join(self.workspaces_root, "ws_b")
        os.makedirs(ws_a, exist_ok=True)
        os.makedirs(ws_b, exist_ok=True)
        with open(os.path.join(ws_a, "ship.blend"), "w") as f:
            f.write("a")
        with open(os.path.join(ws_b, "main.rs"), "w") as f:
            f.write("b")

        projects = [
            {"id": "proj_a", "name": "Project A", "workspace_path": ws_a},
            {"id": "proj_b", "name": "Project B", "workspace_path": ws_b},
        ]
        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f)

        todos = [
            {"id": "t_a", "title": "Lavoro A in Blender", "project": "Project A", "status": "todo"},
            {"id": "t_b", "title": "Sviluppo B in Rust Code", "project": "Project B", "status": "todo"},
        ]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        mem_mgr = MemoryAndProjectsManager(data_dir=self.data_dir)
        runner = HooksRunner(data_dir=self.data_dir, mem_proj_manager=mem_mgr)

        events = [
            {"app_name": "Blender", "window_title": "Blender - ship.blend", "file_name": "ship.blend", "is_dcc": True},
            {"app_name": "Code", "window_title": "Visual Studio Code - main.rs", "file_name": "main.rs", "is_dcc": True},
            {"app_name": "Chrome", "window_title": "Google Chrome - Stack Overflow", "is_dcc": False},
            {"app_name": "3dsmax", "window_title": "3ds Max 2026 - Untitled.max", "file_name": "Untitled.max", "is_dcc": True},
            {"app_name": "desktop", "window_title": "Desktop", "is_dcc": False},
        ]

        try:
            for cycle in range(10):
                for ev in events:
                    res = runner.match_window_to_tasks(ev)
                    # Every call must execute cleanly and not raise
                    if ev["app_name"] == "Blender":
                        self.assertIsNotNone(res)
                        self.assertEqual(res["task_id"], "t_a")
                        self.assertEqual(mem_mgr.profile.get("active_project_id"), "proj_a")
                    elif ev["app_name"] == "Code":
                        self.assertIsNotNone(res)
                        self.assertEqual(res["task_id"], "t_b")
                        self.assertEqual(mem_mgr.profile.get("active_project_id"), "proj_b")
                    elif ev["app_name"] in ("Chrome", "desktop"):
                        self.assertIsNone(res)

        finally:
            runner.shutdown()

    # =========================================================================
    # SECTION 4: DATA HYGIENE
    # =========================================================================

    def test_production_data_hygiene(self):
        """
        Adversarial Test 4.1:
        Verify production directory C:\\Users\\AMD04\\EPO\\AsciiArt\\bridge\\data\\
        has ZERO temporary test files, ZERO test projects, and valid JSON files.
        """
        prod_data_dir = os.path.abspath(r"C:\Users\AMD04\EPO\AsciiArt\bridge\data")
        self.assertTrue(os.path.isdir(prod_data_dir), "Production data dir must exist")

        entries = os.listdir(prod_data_dir)
        for fname in entries:
            self.assertFalse(fname.startswith("neko_test_"), f"Test folder leaked in prod: {fname}")
            self.assertFalse(fname.startswith("challenger_"), f"Challenger folder leaked in prod: {fname}")
            self.assertFalse(fname.endswith(".tmp"), f"Temporary file leaked in prod: {fname}")
            self.assertFalse(".corrupt.bak" in fname, f"Corrupt backup file leaked in prod: {fname}")

        # Check projects.json in prod
        proj_file = os.path.join(prod_data_dir, "projects.json")
        self.assertTrue(os.path.isfile(proj_file))
        with open(proj_file, "r", encoding="utf-8") as f:
            projs = json.load(f)
        self.assertIsInstance(projs, list)
        self.assertGreater(len(projs), 0)
        # Verify Diego's Intermarine project is intact
        intermarine = next((p for p in projs if "intermarine" in p.get("id", "").lower()), None)
        self.assertIsNotNone(intermarine, "Intermarine project must remain preserved in production projects.json")
        self.assertIn("workspace_path", intermarine)
        self.assertIn("workspace_summary", intermarine)

class TestChallengerM1EmpiricalDefects(unittest.TestCase):
    """
    Dedicated empirical reproduction tests demonstrating concrete defects:
    - Defect 1: Workspace Radar token ceiling exceeded (F8 violation)
    - Defect 2: Quality Gate readiness_score decoupling from workspace failure (F7 flaw)
    - Defect 3: Dynamic projects fail to persist into SQLite projects table (F1 parity gap)
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="challenger_defects_")
        self.data_dir = os.path.join(self.temp_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_defect_1_workspace_radar_token_budget_overflow(self):
        """
        Defect 1 Reproduction:
        TokenBudgetManager.format_workspace_radar fails to enforce <= 80 tokens
        when paths, export filenames, or git branches are long.
        """
        from bridge.retrieval import TokenBudgetManager, estimate_tokens
        mgr = TokenBudgetManager()
        summary = {
            "workspace_path": "C:/Projects/" + "x" * 120,
            "total_files": 9999,
            "total_size_mb": 500.0,
            "key_exports": [{"path": f"model_export_asset_{i}.glb"} for i in range(10)],
            "git_info": {"is_git_repo": True, "branch": "feature/PROJ-12345-very-long-branch-name"},
            "detected_stack": ["Blender", "GLB Export", "Rust"],
        }
        radar_tag = mgr.format_workspace_radar(summary)
        tok_count = estimate_tokens(radar_tag)
        # Specification F8 explicitly requires <= 80 tokens ceiling
        self.assertLessEqual(
            tok_count,
            80,
            f"[DEFECT-1 F8 VIOLATION] Workspace radar token count ({tok_count}) exceeds 80 tokens budget ceiling! Tag: {radar_tag}"
        )

    def test_defect_2_quality_gate_readiness_score_decoupling(self):
        """
        Defect 2 Reproduction:
        inspect_project_quality_gate calculates rules_score before appending the
        Workspace & Deliverables Check, meaning a FAILED workspace check has
        zero impact on rules_score or readiness_score.
        """
        # Project A with valid existing workspace
        ws_valid = os.path.join(self.temp_dir, "valid_ws")
        os.makedirs(ws_valid, exist_ok=True)
        with open(os.path.join(ws_valid, "model.glb"), "w") as f:
            f.write("glb")

        # Project B with broken non-existent workspace
        ws_broken = os.path.join(self.temp_dir, "non_existent_ws")

        projects = [
            {
                "id": "proj_valid",
                "name": "Project Valid",
                "workspace_path": ws_valid,
                "workspace_summary": {},
                "notes": ["Rule 1"],
                "tech_stack": ["Blender"],
            },
            {
                "id": "proj_broken",
                "name": "Project Broken",
                "workspace_path": ws_broken,
                "workspace_summary": {},
                "notes": ["Rule 1"],
                "tech_stack": ["Blender"],
            },
        ]
        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f)

        todos = [
            {"id": "t1", "title": "Task 1", "project": "Project Valid", "status": "done"},
            {"id": "t2", "title": "Task 1", "project": "Project Broken", "status": "done"},
        ]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        report_valid = inspect_project_quality_gate("proj_valid", data_dir=self.data_dir)
        report_broken = inspect_project_quality_gate("proj_broken", data_dir=self.data_dir)

        self.assertEqual(report_valid["workspace_checklist"]["status"], "VERIFIED")
        self.assertEqual(report_broken["workspace_checklist"]["status"], "FAILED")

        # Expectation: A project with FAILED workspace deliverables check must have a lower rules_score / readiness_score
        # Actual behavior: rules_score is identical (100.0) and readiness_score is identical (100) because rules_score was calculated before workspace check!
        rules_score_valid = next(c for c in report_valid["client_rules_checklist"] if c.get("check_type") == "workspace_deliverables")
        rules_score_broken = next(c for c in report_broken["client_rules_checklist"] if c.get("check_type") == "workspace_deliverables")

        # Extract verified ratios
        v_count_valid = sum(1 for c in report_valid["client_rules_checklist"] if c["status"] == "VERIFIED")
        v_count_broken = sum(1 for c in report_broken["client_rules_checklist"] if c["status"] == "VERIFIED")

        self.assertGreater(
            report_valid["readiness_score"],
            report_broken["readiness_score"],
            f"[DEFECT-2 F7 FLAW] Quality gate readiness score is decoupled from workspace check failure! "
            f"Valid score: {report_valid['readiness_score']}, Broken score: {report_broken['readiness_score']}"
        )

    def test_defect_3_dynamic_project_sqlite_parity_missing(self):
        """
        Defect 3 Reproduction:
        When a project is created dynamically via MemoryAndProjectsManager.create_or_update_project
        and bind_project_folder is called, update_project_workspace fails silently because
        it uses SQL UPDATE on a non-existent row. The project is never persisted in SQLite.
        """
        mgr = MemoryAndProjectsManager(data_dir=self.data_dir)
        res_create = mgr.create_or_update_project(name="Dynamic Studio Proj", client="StudioClient")
        self.assertTrue(res_create["ok"])
        pid = res_create["project"]["id"]

        ws_folder = os.path.join(self.temp_dir, "dynamic_ws")
        os.makedirs(ws_folder, exist_ok=True)
        res_bind = mgr.bind_project_folder(pid, ws_folder)
        self.assertTrue(res_bind["ok"])

        # Check SQLite database
        sm = StorageManager(data_dir=self.data_dir)
        db_projs = sm.get_all_projects()
        db_pids = [p["id"] for p in db_projs]
        sm.close()

        self.assertIn(
            pid,
            db_pids,
            f"[DEFECT-3 F1 PARITY GAP] Dynamically created project {pid} missing from SQLite projects table after bind_project_folder! SQLite contains: {db_pids}"
        )


if __name__ == "__main__":
    unittest.main()
