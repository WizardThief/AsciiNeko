"""
tests/test_challenger_m1_4_stress.py
====================================
Fresh Adversarial Verification and Stress Test Suite for Milestone 1:
1. Extreme radar stress (500-char path, 100 export items with 80-char names, 100-char branch name)
2. Dynamic SQLite parity (5 dynamic projects created via create_or_update_project and bound to folders)
3. Quality Gate robustness (readiness score recalculation on failed deliverables)
4. Non-dict HTTP API payloads ([], "hello", 123, None) to all workspace endpoints

Written by: challenger_m1_4
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

PROJECT_ROOT = os.path.abspath(r"C:\Users\AMD04\EPO\AsciiArt")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bridge.retrieval import TokenBudgetManager, estimate_tokens
from bridge.storage_manager import StorageManager
from bridge.ai_monitor import MemoryAndProjectsManager, CompanionRequestHandler
from bridge.skill_registry import inspect_project_quality_gate
from bridge.workspace_scanner import scan_workspace


class TestChallengerM14Stress(unittest.TestCase):
    """Adversarial stress testing across Radar, SQLite Parity, Quality Gate, and HTTP APIs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="challenger_m1_4_")
        self.data_dir = os.path.join(self.temp_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.workspaces_root = os.path.join(self.temp_dir, "workspaces")
        os.makedirs(self.workspaces_root, exist_ok=True)

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # MANDATE 1: EXTREME RADAR STRESS
    # =========================================================================

    def test_radar_500char_path_100_exports_100char_branch_token_ceiling(self):
        """
        MANDATE 1:
        Test format_workspace_radar with:
        - 500-character path
        - 100 export items with 80-character names
        - 100-character branch name
        Assert that estimate_tokens(radar_tag) <= 80 holds unconditionally.
        """
        mgr = TokenBudgetManager()

        # Build 500-character path: e.g. C:\very_long_path\...\
        subpart = "deep_directory_segment_for_testing_"  # 35 chars
        repetitions = (500 - 3) // len(subpart)
        remainder = (500 - 3) % len(subpart)
        path_500 = "C:\\" + (subpart * repetitions) + ("x" * remainder)
        self.assertGreaterEqual(len(path_500), 500, f"Path length is {len(path_500)}")

        # Build 100 export items with 80-character filenames
        exports_100 = []
        for i in range(100):
            # 80-character name ending with .glb
            prefix = f"export_asset_variant_{i:03d}_"
            suffix = ".glb"
            filler = "a" * (80 - len(prefix) - len(suffix))
            filename = f"{prefix}{filler}{suffix}"
            self.assertEqual(len(filename), 80, f"Export name length is {len(filename)}")
            exports_100.append({
                "path": filename,
                "size_mb": 12.34,
                "ext": ".glb",
            })
        self.assertEqual(len(exports_100), 100)

        # Build 100-character branch name
        branch_prefix = "feature/JIRA-99999-mass-production-release-candidate-"
        branch_100 = branch_prefix + ("b" * (100 - len(branch_prefix)))
        self.assertEqual(len(branch_100), 100, f"Branch length is {len(branch_100)}")

        summary = {
            "workspace_path": path_500,
            "total_files": 1234567,
            "total_size_mb": 987654.32,
            "key_exports": exports_100,
            "git_info": {
                "is_git_repo": True,
                "branch": branch_100,
                "dirty_count": 42,
            },
            "detected_stack": [
                "Blender",
                "GLB Export",
                "Rust",
                "Three.js",
                "Shaders / WebGPU",
                "Unreal Engine",
                "3ds Max",
            ],
        }

        radar_tag = mgr.format_workspace_radar(
            summary,
            workspace_path=path_500,
            max_tokens=80,
        )

        tok_estimate = estimate_tokens(radar_tag)

        # Output empirical proof
        print("\n[MANDATE 1 EMPIRICAL EVIDENCE]")
        print(f"Generated Tag:\n{radar_tag}")
        print(f"Tag Character Length: {len(radar_tag)}")
        print(f"Tag Token Estimate: {tok_estimate} (Max allowed: 80)")

        # Unconditional assertions
        self.assertTrue(radar_tag.startswith("<workspace_radar> "), "Tag must start with '<workspace_radar> '")
        self.assertTrue(radar_tag.endswith(" </workspace_radar>"), "Tag must end with ' </workspace_radar>'")
        self.assertLessEqual(
            tok_estimate,
            80,
            f"VIOLATION: Workspace radar token count ({tok_estimate}) exceeds 80 token budget!",
        )

    def test_radar_pathological_matrix_stress(self):
        """
        Adversarial stress matrix for format_workspace_radar across:
        - 1000 exports with 120-char names
        - Unicode paths with emojis and Chinese/Japanese characters
        - Varied max_tokens quotas (80, 50, 30, 20, 15, 11, 5)
        - None / empty input dicts
        """
        mgr = TokenBudgetManager()

        # 1. 1000 exports with 120-char names
        huge_exports = [
            {"path": f"exports/{'x'*100}_{i:04d}.glb", "size_mb": 1.0, "ext": ".glb"}
            for i in range(1000)
        ]
        summary_huge = {
            "workspace_path": "C:\\Projects\\" + "y" * 300,
            "total_files": 5000000,
            "total_size_mb": 1000000.0,
            "key_exports": huge_exports,
            "git_info": {"is_git_repo": True, "branch": "git_branch_" + "z" * 80},
            "detected_stack": ["Blender", "Rust", "Three.js", "Unreal Engine"],
        }

        for max_tok in (80, 50, 30, 20, 15):
            tag = mgr.format_workspace_radar(summary_huge, max_tokens=max_tok)
            toks = estimate_tokens(tag)
            self.assertLessEqual(
                toks,
                max_tok,
                f"Failed for max_tokens={max_tok}: got {toks} tokens with tag '{tag}'",
            )
            self.assertTrue(tag.startswith("<workspace_radar>"))

        # max_tokens < 12 should return empty string gracefully
        self.assertEqual(mgr.format_workspace_radar(summary_huge, max_tokens=10), "")
        self.assertEqual(mgr.format_workspace_radar(summary_huge, max_tokens=5), "")

        # 2. Unicode / emojis in path and branch
        unicode_summary = {
            "workspace_path": "C:\\Проекты\\船_🚢_Intermarine\\🚀_glTF_3D_リアルタイム_наука\\" + ("🌊" * 50),
            "total_files": 42,
            "total_size_mb": 100.5,
            "key_exports": [{"path": "модель_船_v1.glb", "size_mb": 5.0, "ext": ".glb"}],
            "git_info": {"is_git_repo": True, "branch": "ветка/фича-🚢-intermarine-главная"},
            "detected_stack": ["Blender", "GLB Export"],
        }
        tag_u = mgr.format_workspace_radar(unicode_summary, max_tokens=80)
        self.assertLessEqual(estimate_tokens(tag_u), 80)
        self.assertTrue(tag_u.startswith("<workspace_radar>"))

        # 3. None / empty inputs
        self.assertEqual(mgr.format_workspace_radar(None), "")
        self.assertEqual(mgr.format_workspace_radar({}), "")
        self.assertEqual(mgr.format_workspace_radar({"workspace_path": ""}), "")

    # =========================================================================
    # MANDATE 2: DYNAMIC SQLITE PARITY
    # =========================================================================

    def test_dynamic_sqlite_parity_5_projects(self):
        """
        MANDATE 2:
        Create 5 dynamic projects via MemoryAndProjectsManager.create_or_update_project,
        bind folders to them via bind_project_folder,
        query SQLite: SELECT id, workspace_path FROM projects.
        Assert that all 5 exist in SQLite with non-empty workspace paths.
        """
        mgr = MemoryAndProjectsManager(data_dir=self.data_dir)

        created_pids = []
        expected_mappings = {}

        # 1. Create 5 dynamic projects and bind distinct folders
        for i in range(1, 6):
            pname = f"Dynamic Milestone Project {i}"
            pclient = f"Client Alpha {i}"
            res_create = mgr.create_or_update_project(name=pname, client=pclient)
            self.assertTrue(res_create["ok"], f"Failed to create project {pname}")
            pid = res_create["project"]["id"]
            created_pids.append(pid)

            # Create workspace folder on disk with dummy assets
            ws_folder = os.path.join(self.workspaces_root, f"workspace_dyn_{i:02d}")
            os.makedirs(ws_folder, exist_ok=True)
            with open(os.path.join(ws_folder, f"model_{i}.glb"), "wb") as f:
                f.write(b"glb_data" * 50)
            with open(os.path.join(ws_folder, f"scene_{i}.blend"), "wb") as f:
                f.write(b"blend_data" * 50)

            # Bind folder
            res_bind = mgr.bind_project_folder(pid, ws_folder)
            self.assertTrue(res_bind["ok"], f"Failed to bind folder for {pid}")
            self.assertEqual(res_bind["workspace_path"], ws_folder)
            expected_mappings[pid] = ws_folder

        self.assertEqual(len(created_pids), 5)

        # 2. Query SQLite directly using raw SQL
        db_path = os.path.join(self.data_dir, "neko_storage.db")
        self.assertTrue(os.path.isfile(db_path), f"SQLite database {db_path} does not exist!")

        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, workspace_path FROM projects")
            rows = cursor.fetchall()
            db_dict = {row[0]: row[1] for row in rows}
        finally:
            conn.close()

        print("\n[MANDATE 2 EMPIRICAL EVIDENCE]")
        print(f"Total projects in SQLite: {len(db_dict)}")
        for pid in created_pids:
            print(f"Project ID: {pid} -> SQLite workspace_path: '{db_dict.get(pid)}'")

        # 3. Assert all 5 exist in SQLite with non-empty workspace paths
        for pid in created_pids:
            self.assertIn(
                pid,
                db_dict,
                f"Dynamic project {pid} missing from SQLite projects table!",
            )
            ws_in_db = db_dict[pid]
            self.assertTrue(
                bool(ws_in_db and ws_in_db.strip()),
                f"Dynamic project {pid} has empty workspace_path in SQLite: '{ws_in_db}'",
            )
            # Normalize paths for comparison (Windows casing / separators)
            self.assertEqual(
                os.path.normpath(ws_in_db),
                os.path.normpath(expected_mappings[pid]),
                f"Path mismatch in SQLite for {pid}: expected '{expected_mappings[pid]}', got '{ws_in_db}'",
            )

        # Also verify parity in projects.json
        proj_json_path = os.path.join(self.data_dir, "projects.json")
        self.assertTrue(os.path.isfile(proj_json_path))
        with open(proj_json_path, "r", encoding="utf-8") as f:
            projs_json = json.load(f)
        json_dict = {p["id"]: p.get("workspace_path") for p in projs_json if isinstance(p, dict)}
        for pid in created_pids:
            self.assertIn(pid, json_dict, f"Project {pid} missing from projects.json")
            self.assertEqual(
                os.path.normpath(json_dict[pid]),
                os.path.normpath(expected_mappings[pid]),
                f"Path mismatch in projects.json for {pid}",
            )

    def test_dynamic_sqlite_parity_rebind_and_concurrency(self):
        """
        Adversarial test:
        1. Re-bind a dynamic project to a different workspace and assert SQLite updates.
        2. Create and bind projects from 5 concurrent threads and assert zero SQLite locks or lost updates.
        """
        mgr = MemoryAndProjectsManager(data_dir=self.data_dir)

        # 1. Re-binding test
        res_create = mgr.create_or_update_project(name="Rebind Test Proj", client="Client Rebind")
        pid = res_create["project"]["id"]

        ws1 = os.path.join(self.workspaces_root, "ws_rebind_1")
        ws2 = os.path.join(self.workspaces_root, "ws_rebind_2")
        os.makedirs(ws1, exist_ok=True)
        os.makedirs(ws2, exist_ok=True)

        mgr.bind_project_folder(pid, ws1)
        mgr.bind_project_folder(pid, ws2)

        sm = StorageManager(data_dir=self.data_dir)
        db_projs = sm.get_all_projects()
        target = next((p for p in db_projs if p["id"] == pid), None)
        sm.close()

        self.assertIsNotNone(target)
        self.assertEqual(os.path.normpath(target["workspace_path"]), os.path.normpath(ws2))

        # 2. Concurrency test: 5 threads creating & binding dynamic projects simultaneously
        errors = []
        concurrent_pids = []

        def worker_fn(idx):
            try:
                local_mgr = MemoryAndProjectsManager(data_dir=self.data_dir)
                c_res = local_mgr.create_or_update_project(name=f"Concurrent Proj {idx}")
                if not c_res.get("ok"):
                    errors.append(f"Thread {idx} create failed")
                    return
                c_pid = c_res["project"]["id"]
                c_ws = os.path.join(self.workspaces_root, f"ws_concurrent_{idx}")
                os.makedirs(c_ws, exist_ok=True)
                b_res = local_mgr.bind_project_folder(c_pid, c_ws)
                if not b_res.get("ok"):
                    errors.append(f"Thread {idx} bind failed")
                    return
                concurrent_pids.append((c_pid, c_ws))
            except Exception as e:
                errors.append(f"Thread {idx} raised: {e}")

        threads = [threading.Thread(target=worker_fn, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent worker errors: {errors}")
        self.assertEqual(len(concurrent_pids), 5)

        # Check all 5 exist in SQLite
        db_path = os.path.join(self.data_dir, "neko_storage.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, workspace_path FROM projects")
        all_rows = dict(cursor.fetchall())
        conn.close()

        for c_pid, c_ws in concurrent_pids:
            self.assertIn(c_pid, all_rows)
            self.assertEqual(os.path.normpath(all_rows[c_pid]), os.path.normpath(c_ws))

    # =========================================================================
    # MANDATE 3: QUALITY GATE ROBUSTNESS
    # =========================================================================

    def test_quality_gate_score_decreases_proportionally_on_failed_deliverables(self):
        """
        MANDATE 3:
        Test multiple deliverables checks in Quality Gate.
        Verify readiness score decreases proportionally on failed deliverables checks
        and does NOT stay at 100.
        """
        # Scenario A: Project with valid folder and deliverables (.glb + .blend)
        ws_valid = os.path.join(self.workspaces_root, "qg_valid_ws")
        os.makedirs(ws_valid, exist_ok=True)
        with open(os.path.join(ws_valid, "model_final.glb"), "wb") as f:
            f.write(b"glb" * 50)
        with open(os.path.join(ws_valid, "model_final.blend"), "wb") as f:
            f.write(b"blend" * 50)

        # Scenario B: Project with missing / deleted folder (inaccessible)
        ws_broken = os.path.join(self.workspaces_root, "qg_nonexistent_ws_404")

        projects = [
            {
                "id": "proj_a_perfect",
                "name": "Project Perfect",
                "client": "Client Alpha",
                "notes": ["Logo deve essere Blu Marino"],
                "tech_stack": ["Blender", "Export GLB / Draco"],
                "workspace_path": ws_valid,
                "workspace_summary": {},
            },
            {
                "id": "proj_b_broken_ws",
                "name": "Project Broken Workspace",
                "client": "Client Alpha",
                "notes": ["Logo deve essere Blu Marino"],
                "tech_stack": ["Blender", "Export GLB / Draco"],
                "workspace_path": ws_broken,
                "workspace_summary": {},
            },
            {
                "id": "proj_c_no_rules_broken_ws",
                "name": "Project No Rules Broken Workspace",
                "client": "Client Beta",
                "notes": [],
                "tech_stack": ["Blender"],
                "workspace_path": ws_broken,
                "workspace_summary": {},
            },
            {
                "id": "proj_d_many_rules_broken_ws",
                "name": "Project Many Rules Broken Workspace",
                "client": "Client Gamma",
                "notes": [
                    "Regola 1: Formato 16:9",
                    "Regola 2: Risoluzione 4K",
                    "Regola 3: Colore Blu Marino",
                ],
                "tech_stack": ["Blender"],
                "workspace_path": ws_broken,
                "workspace_summary": {},
            },
        ]

        with open(os.path.join(self.data_dir, "projects.json"), "w", encoding="utf-8") as f:
            json.dump(projects, f)

        # Add 1 completed task to each project so task_score = 100
        todos = [
            {"id": "t1", "title": "Setup", "project": "Project Perfect", "status": "done"},
            {"id": "t2", "title": "Setup", "project": "Project Broken Workspace", "status": "done"},
            {"id": "t3", "title": "Setup", "project": "Project No Rules Broken Workspace", "status": "done"},
            {"id": "t4", "title": "Setup", "project": "Project Many Rules Broken Workspace", "status": "done"},
        ]
        with open(os.path.join(self.data_dir, "todo.json"), "w", encoding="utf-8") as f:
            json.dump(todos, f)

        # Inspect Quality Gates
        report_a = inspect_project_quality_gate("proj_a_perfect", data_dir=self.data_dir)
        report_b = inspect_project_quality_gate("proj_b_broken_ws", data_dir=self.data_dir)
        report_c = inspect_project_quality_gate("proj_c_no_rules_broken_ws", data_dir=self.data_dir)
        report_d = inspect_project_quality_gate("proj_d_many_rules_broken_ws", data_dir=self.data_dir)

        # Calculate rules score from client_rules_checklist
        def compute_rules_score(report):
            chk = report["client_rules_checklist"]
            if not chk:
                return 100.0
            v = sum(1 for c in chk if c["status"] == "VERIFIED")
            return round((v / len(chk)) * 100, 1)

        rules_score_a = compute_rules_score(report_a)
        rules_score_b = compute_rules_score(report_b)
        rules_score_c = compute_rules_score(report_c)
        rules_score_d = compute_rules_score(report_d)

        print("\n[MANDATE 3 EMPIRICAL EVIDENCE]")
        print(f"Scenario A (Valid WS) -> readiness_score: {report_a['readiness_score']}, rules_score: {rules_score_a}%")
        print(f"Scenario B (1 rule + Broken WS) -> readiness_score: {report_b['readiness_score']}, rules_score: {rules_score_b}%")
        print(f"Scenario C (0 rules + Broken WS) -> readiness_score: {report_c['readiness_score']}, rules_score: {rules_score_c}%")
        print(f"Scenario D (3 rules + Broken WS) -> readiness_score: {report_d['readiness_score']}, rules_score: {rules_score_d}%")

        # Assertions
        # 1. Valid workspace must be VERIFIED
        self.assertEqual(report_a["workspace_checklist"]["status"], "VERIFIED")
        self.assertEqual(report_a["readiness_score"], 100)
        self.assertEqual(report_a["status_label"], "PASSED")

        # 2. Broken workspaces must be FAILED
        self.assertEqual(report_b["workspace_checklist"]["status"], "FAILED")
        self.assertEqual(report_c["workspace_checklist"]["status"], "FAILED")
        self.assertEqual(report_d["workspace_checklist"]["status"], "FAILED")

        # 3. Readiness scores MUST NOT stay at 100
        self.assertLess(
            report_b["readiness_score"],
            100,
            f"Readiness score stayed at 100 despite failed workspace deliverables check! {report_b}",
        )
        self.assertLess(
            report_c["readiness_score"],
            100,
            f"Readiness score stayed at 100 in 0-rule scenario! {report_c}",
        )
        self.assertLess(
            report_d["readiness_score"],
            100,
            f"Readiness score stayed at 100 in multi-rule scenario! {report_d}",
        )

        # 4. Proportional decrease verification:
        # Scenario B has 1 client rule (VERIFIED) + 1 workspace check (FAILED) = 1/2 = 50% rules_score
        # Composite score = 0.50 * 100 + 0.30 * 50 + 0.20 * 100 = 50 + 15 + 20 = 85
        self.assertEqual(rules_score_b, 50.0)
        self.assertEqual(report_b["readiness_score"], 85)

        # Scenario C has 0 client rules + 1 workspace check (FAILED) = 0/1 = 0% rules_score
        # Composite score = 0.50 * 100 + 0.30 * 0 + 0.20 * 100 = 50 + 0 + 20 = 70
        self.assertEqual(rules_score_c, 0.0)
        self.assertEqual(report_c["readiness_score"], 70)

        # Scenario D has 3 client rules (VERIFIED) + 1 workspace check (FAILED) = 3/4 = 75% rules_score
        # Composite score = 0.50 * 100 + 0.30 * 75 + 0.20 * 100 = 50 + 22.5 + 20 = 92.5 -> 92 or 93
        self.assertEqual(rules_score_d, 75.0)
        self.assertIn(report_d["readiness_score"], (92, 93))

        # Hierarchy: Perfect (100) > 3 rules (92/93) > 1 rule (85) > 0 rules (70)
        self.assertGreater(report_a["readiness_score"], report_d["readiness_score"])
        self.assertGreater(report_d["readiness_score"], report_b["readiness_score"])
        self.assertGreater(report_b["readiness_score"], report_c["readiness_score"])

    # =========================================================================
    # MANDATE 4: NON-DICT HTTP PAYLOADS
    # =========================================================================

    def test_non_dict_http_payloads_400_and_zero_crashes(self):
        """
        MANDATE 4:
        Send non-dict payloads: [], "hello", 123, None
        to:
        - /api/projects/bind_folder
        - /api/projects/open_folder
        - /api/projects/scan_folder
        - /api/projects/quality_gate
        Verify HTTP 400 and zero server crashes.
        """
        # Launch test server on ephemeral port
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"

        endpoints = [
            "/api/projects/bind_folder",
            "/api/projects/open_folder",
            "/api/projects/scan_folder",
            "/api/projects/quality_gate",
        ]

        # Explicit non-dict payloads mandated by DISPATCH.md
        payload_test_cases = [
            ("empty_list", b"[]"),
            ("populated_list", b"[1, 2, 3]"),
            ("string", b'"hello"'),
            ("number_int", b"123"),
            ("number_float", b"45.67"),
            ("json_null", b"null"),
            ("boolean_true", b"true"),
            ("boolean_false", b"false"),
        ]

        print("\n[MANDATE 4 EMPIRICAL EVIDENCE]")
        tested_combinations = 0

        try:
            for endpoint in endpoints:
                for label, raw_payload in payload_test_cases:
                    req = urllib.request.Request(
                        f"{base_url}{endpoint}",
                        data=raw_payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )

                    try:
                        with urllib.request.urlopen(req, timeout=5.0) as resp:
                            self.fail(
                                f"Expected HTTP 400 for {endpoint} with payload '{label}', but got HTTP {resp.status}!"
                            )
                    except urllib.error.HTTPError as err:
                        self.assertEqual(
                            err.code,
                            400,
                            f"Expected HTTP 400 for {endpoint} with payload '{label}', got HTTP {err.code}",
                        )
                        body_bytes = err.read()
                        resp_json = json.loads(body_bytes.decode("utf-8"))
                        self.assertFalse(resp_json.get("ok", True))
                        self.assertIn("error", resp_json)
                        self.assertIn("oggetto json", resp_json["error"].lower())
                        tested_combinations += 1

            print(f"Successfully verified HTTP 400 across {tested_combinations} (endpoint, non-dict payload) combinations with zero crashes.")

            # Verify server is still completely responsive to valid requests
            status_req = urllib.request.Request(f"{base_url}/status")
            with urllib.request.urlopen(status_req, timeout=5.0) as resp:
                self.assertEqual(resp.status, 200)
                status_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue("code_version" in status_data or "ok" in status_data or "active_window" in status_data)

        finally:
            server.shutdown()
            server.server_close()

    # =========================================================================
    # PRODUCTION DATA HYGIENE
    # =========================================================================

    def test_production_data_hygiene_untouched(self):
        """
        Verify production directory C:\\Users\\AMD04\\EPO\\AsciiArt\\bridge\\data\\
        is completely clean of test artifacts.
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
        intermarine = next((p for p in projs if "intermarine" in p.get("id", "").lower()), None)
        self.assertIsNotNone(intermarine, "Intermarine project must remain preserved in production projects.json")


if __name__ == "__main__":
    unittest.main()
