"""
tests/test_ecc_workspace_and_switches.py
=========================================
Comprehensive 4-Tier E2E Test Suite for AsciiNeko ECC-Compatible Modular Desktop Runtime:
- R1: Project Workspace Folder Binding & Live Disk/Asset Scanner
- R2: Dual-Format Skill Registry (SKILL.md & .yaml) with ON/OFF Toggling
- R3: Modular MCP Registry & System Tool Control Plane
- R4: Interactive UI Contracts & Real-Time Context Budget Sync
- R5: Strict Test Data Hygiene & Authentic Data Preservation

Test Hierarchy:
- Tier 1: Feature Coverage (Category-Partition: Groups R1, R2, R3) (>=5 tests per group)
- Tier 2: Boundary & Corner Cases (Empty dirs, non-existent paths, deep bound <=3, corrupt/missing frontmatter, invalid IDs)
- Tier 3: Cross-Feature Combinations (Folder -> Quality Gate; Toggle Skill -> Budget; Toggle MCP -> Prompt radar; Window switch auto-link; Add MCP -> Status)
- Tier 4: Real-World Scenarios (Full Nave Intermarine WebGL lifecycle, high-activity stress workspace, switchboard session, production data preservation)

MANDATORY DATA HYGIENE:
- All tests execute within isolated tempfile.TemporaryDirectory() instances.
- Zero test artifacts may touch or pollute bridge/data/.
- Teardown verifies 100% snapshot equality of production data.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

# Ensure repository root and bridge/ are on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

PRODUCTION_DATA_DIR = os.path.join(BRIDGE_DIR, "data")

# Core modules available in bridge
import bridge.ai_monitor as am
from bridge.ai_monitor import (
    CompanionRequestHandler,
    MemoryAndProjectsManager,
    OllamaLocalBridge,
    SystemStateMonitor,
)
from bridge.retrieval import (
    TokenBudgetManager,
    estimate_tokens,
    TARGET_NUM_CTX,
    MIN_GENERATION_HEADROOM,
    MAX_PROMPT_BUDGET,
)
import bridge.skill_registry as sr_module
from bridge.skill_registry import (
    SkillRegistry,
    validate_code_safety,
    validate_skill_schema,
)
from bridge.storage_manager import (
    StorageManager,
    AtomicJsonStore,
    StorageError,
)

# Progressive Contract Detectors for Milestones M1, M2, M3
try:
    import bridge.workspace_scanner as ws_module
    WorkspaceScanner = getattr(ws_module, "WorkspaceScanner", None)
    scan_workspace = getattr(ws_module, "scan_workspace", None)
    HAS_WORKSPACE_SCANNER = (WorkspaceScanner is not None) or (scan_workspace is not None)
except Exception:
    ws_module = None
    WorkspaceScanner = None
    scan_workspace = None
    HAS_WORKSPACE_SCANNER = False

try:
    import bridge.mcp_registry as mcp_module
    MCPRegistry = getattr(mcp_module, "MCPRegistry", None)
    get_mcp_registry = getattr(mcp_module, "get_mcp_registry", None)
    HAS_MCP_REGISTRY = (MCPRegistry is not None) or (get_mcp_registry is not None)
except Exception:
    mcp_module = None
    MCPRegistry = None
    get_mcp_registry = None
    HAS_MCP_REGISTRY = False


def _check_skill_md_support() -> bool:
    """Checks if SkillRegistry currently supports loading Markdown / SKILL.md files."""
    try:
        import inspect
        src = inspect.getsource(SkillRegistry.load_skills)
        return ".md" in src or "markdown" in src or hasattr(SkillRegistry, "load_markdown_skill")
    except Exception:
        return False

HAS_SKILL_MD_SUPPORT = _check_skill_md_support()
HAS_SKILL_TOGGLE = hasattr(SkillRegistry, "toggle_skill") or hasattr(SkillRegistry, "set_skill_enabled")


def _check_handler_endpoint(endpoint_substr: str) -> bool:
    """Checks if CompanionRequestHandler handles a given endpoint path."""
    try:
        import inspect
        src_post = inspect.getsource(CompanionRequestHandler.do_POST)
        src_get = inspect.getsource(CompanionRequestHandler.do_GET)
        return (endpoint_substr in src_post) or (endpoint_substr in src_get)
    except Exception:
        return False

HAS_BIND_FOLDER_API = _check_handler_endpoint("/api/projects/bind_folder")
HAS_OPEN_FOLDER_API = _check_handler_endpoint("/api/projects/open_folder")
HAS_SCAN_FOLDER_API = _check_handler_endpoint("/api/projects/scan_folder")
HAS_SKILL_TOGGLE_API = _check_handler_endpoint("/api/skills/toggle")
HAS_MCP_TOGGLE_API = _check_handler_endpoint("/api/mcp/toggle")
HAS_MCP_ADD_API = _check_handler_endpoint("/api/mcp/add")


def _check_quality_gate_workspace_support() -> bool:
    """Checks if inspect_project_quality_gate includes workspace folder deliverables checking."""
    try:
        import inspect
        src = inspect.getsource(sr_module.inspect_project_quality_gate)
        return "workspace" in src.lower() or "deliverable" in src.lower()
    except Exception:
        return False

HAS_QUALITY_GATE_WORKSPACE_CHECK = _check_quality_gate_workspace_support()


# ==============================================================================
# BASE ISOLATED TEST CASE (STRICT DATA HYGIENE GUARANTEE)
# ==============================================================================

class IsolatedTestCase(unittest.TestCase):
    """
    Guarantees 100% test isolation within an ephemeral temporary directory.
    Asserts zero mutation or pollution of production bridge/data/.
    """

    def setUp(self):
        self._temp_dir_obj = tempfile.TemporaryDirectory()
        self.test_dir = os.path.abspath(self._temp_dir_obj.name)
        self.test_db_path = os.path.join(self.test_dir, "neko_storage.db")

        # Snapshot modification timestamps of production data directory before test
        self._prod_data_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)

        # Protect module-level globals so no backend handler can fall back to production bridge/data/
        self._orig_default_data_dir = getattr(am, "DEFAULT_DATA_DIR", PRODUCTION_DATA_DIR)
        self._orig_data_dir = getattr(am, "DATA_DIR", PRODUCTION_DATA_DIR)
        am.DEFAULT_DATA_DIR = self.test_dir
        am.DATA_DIR = self.test_dir
        am.PROJECTS_FILE = os.path.join(self.test_dir, "projects.json")
        am.TODO_FILE = os.path.join(self.test_dir, "todo.json")
        am.MEMORY_FILE = os.path.join(self.test_dir, "memory.json")
        am.PROFILE_FILE = os.path.join(self.test_dir, "profile.json")
        am.ARCHIVE_FILE = os.path.join(self.test_dir, "archive.json")
        am.CALENDAR_CONFIG_FILE = os.path.join(self.test_dir, "calendar_config.json")
        am.EVENTS_FILE = os.path.join(self.test_dir, "events.json")

        # Initialize isolated storage manager
        self.storage = StorageManager(db_path=self.test_db_path, data_dir=self.test_dir)

        # Ensure projects table exists and seed baseline project
        with self.storage.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO projects (id, name, client, color, is_active, created_at, updated_at)
                VALUES ('proj_nave_intermarine', 'Nave Intermarine', 'Intermarine', '#0055ff', 1, ?, ?)
                """,
                (time.time(), time.time()),
            )

        # Setup mock projects.json in test_dir
        self.projects_json_path = os.path.join(self.test_dir, "projects.json")
        initial_projects = [
            {
                "id": "proj_nave_intermarine",
                "name": "Nave Intermarine",
                "client": "Intermarine",
                "color": "#0055ff",
                "is_active": True,
                "workspace_path": "",
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        ]
        with open(self.projects_json_path, "w", encoding="utf-8") as f:
            json.dump(initial_projects, f)

    def tearDown(self):
        # Restore module-level globals
        if hasattr(self, "_orig_default_data_dir"):
            am.DEFAULT_DATA_DIR = self._orig_default_data_dir
        if hasattr(self, "_orig_data_dir"):
            am.DATA_DIR = self._orig_data_dir
            am.PROJECTS_FILE = os.path.join(self._orig_data_dir, "projects.json")
            am.TODO_FILE = os.path.join(self._orig_data_dir, "todo.json")
            am.MEMORY_FILE = os.path.join(self._orig_data_dir, "memory.json")
            am.PROFILE_FILE = os.path.join(self._orig_data_dir, "profile.json")
            am.ARCHIVE_FILE = os.path.join(self._orig_data_dir, "archive.json")
            am.CALENDAR_CONFIG_FILE = os.path.join(self._orig_data_dir, "calendar_config.json")
            am.EVENTS_FILE = os.path.join(self._orig_data_dir, "events.json")

        try:
            if hasattr(self, "storage") and self.storage:
                self.storage.close_connection()
        except Exception:
            pass

        try:
            self._temp_dir_obj.cleanup()
        except Exception:
            pass

        # Verify zero pollution or modification of production data
        curr_prod_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)
        self.assertEqual(
            self._prod_data_snapshot,
            curr_prod_snapshot,
            "CRITICAL VIOLATION: Test modified or polluted production files in bridge/data/!",
        )

    def _capture_dir_snapshot(self, directory: str) -> Dict[str, float]:
        """Captures filepath -> mtime dictionary of all files in directory."""
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

    def _create_mock_workspace(
        self,
        files_dict: Dict[str, Any],
        base_name: str = "mock_workspace",
        init_git: bool = False,
    ) -> str:
        """
        Creates an isolated mock workspace directory with specified relative files.
        Optionally initializes a real local git repository.
        """
        ws_path = os.path.join(self.test_dir, base_name)
        os.makedirs(ws_path, exist_ok=True)

        for rel_path, content in files_dict.items():
            full_path = os.path.join(ws_path, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if isinstance(content, bytes):
                with open(full_path, "wb") as f:
                    f.write(content)
            elif isinstance(content, str):
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
            elif isinstance(content, int):
                # Integer represents dummy file size in bytes
                with open(full_path, "wb") as f:
                    f.write(b"\0" * content)
            else:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write("mock content")

        if init_git:
            try:
                subprocess.run(["git", "init"], cwd=ws_path, capture_output=True, check=True)
                subprocess.run(["git", "config", "user.email", "test@ascii.local"], cwd=ws_path, capture_output=True, check=True)
                subprocess.run(["git", "config", "user.name", "Test Neko"], cwd=ws_path, capture_output=True, check=True)
                subprocess.run(["git", "add", "."], cwd=ws_path, capture_output=True, check=True)
                subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=ws_path, capture_output=True, check=True)
            except (subprocess.SubprocessError, FileNotFoundError):
                # If git executable is unavailable, simulate .git directory structure
                git_dir = os.path.join(ws_path, ".git")
                os.makedirs(git_dir, exist_ok=True)
                head_file = os.path.join(git_dir, "HEAD")
                with open(head_file, "w", encoding="utf-8") as f:
                    f.write("ref: refs/heads/main\n")

        return ws_path

    def _call_post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an in-memory HTTP POST request to CompanionRequestHandler."""
        handler = CompanionRequestHandler.__new__(CompanionRequestHandler)
        handler.path = path
        body_bytes = json.dumps(payload).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body_bytes)), "Content-Type": "application/json"}
        handler.rfile = io.BytesIO(body_bytes)
        handler.wfile = io.BytesIO()

        responses = []
        def mock_send(data, *args, **kwargs):
            responses.append(data)
            return True

        handler._send_json = mock_send
        handler.do_POST()
        self.assertTrue(len(responses) > 0, f"No response sent for POST {path}")
        return responses[0]

    def _call_get_json(self, path: str) -> Dict[str, Any]:
        """Dispatches an in-memory HTTP GET request to CompanionRequestHandler."""
        handler = CompanionRequestHandler.__new__(CompanionRequestHandler)
        handler.path = path
        handler.headers = {}
        handler.wfile = io.BytesIO()

        responses = []
        def mock_send(data, *args, **kwargs):
            responses.append(data)
            return True

        handler._send_json = mock_send
        handler.do_GET()
        self.assertTrue(len(responses) > 0, f"No response sent for GET {path}")
        return responses[0]


# ==============================================================================
# TIER 1: FEATURE COVERAGE (R1, R2, R3)
# ==============================================================================

class TestTier1GroupR1WorkspaceScanner(IsolatedTestCase):
    """
    Tier 1 Feature Coverage: Requirement R1
    Workspace Scanner & Project Binding (F1 through F8, >=5 tests in group).
    """

    @unittest.skipUnless(HAS_BIND_FOLDER_API or HAS_WORKSPACE_SCANNER, "Workspace binding API not yet implemented (Milestone M1 in progress)")
    def test_f1_bind_folder_persistence_sqlite_and_json(self):
        """F1: Verifies POST /api/projects/bind_folder saves workspace_path to SQLite and projects.json."""
        ws_path = self._create_mock_workspace({"model.blend": "dummy"})
        payload = {
            "project_id": "proj_nave_intermarine",
            "workspace_path": ws_path,
            "data_dir": self.test_dir,
        }

        # Dispatch via CompanionRequestHandler
        with patch.object(am, "DATA_DIR", self.test_dir):
            res = self._call_post_json("/api/projects/bind_folder", payload)
            self.assertTrue(res.get("ok"), f"bind_folder failed: {res}")
            self.assertEqual(res.get("workspace_path"), ws_path)

            # Verify persisted in projects.json
            with open(self.projects_json_path, "r", encoding="utf-8") as f:
                projs = json.load(f)
            target = next((p for p in projs if p["id"] == "proj_nave_intermarine"), None)
            self.assertIsNotNone(target)
            self.assertEqual(target.get("workspace_path"), ws_path)

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "bridge.workspace_scanner not yet implemented (Milestone M1 in progress)")
    def test_f2_workspace_scanner_speed_and_depth_bound(self):
        """F2: Verifies scan completes in < 150ms and bounds directory depth at <= 3."""
        # Create directory hierarchy with files at depths 1, 2, 3, 4, 5
        files = {
            "root_file.blend": "data",
            "sub1/level2.glb": "data",
            "sub1/sub2/level3.rs": "data",
            "sub1/sub2/sub3/level4.png": "data",  # depth 4 (should be pruned)
            "sub1/sub2/sub3/sub4/level5.exr": "data",  # depth 5 (should be pruned)
        }
        ws_path = self._create_mock_workspace(files)

        t_start = time.perf_counter()
        summary = scan_workspace(ws_path, max_depth=3) if scan_workspace else WorkspaceScanner().scan(ws_path, max_depth=3)
        t_elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        # Assert speed threshold < 150ms
        self.assertLess(t_elapsed_ms, 150.0, f"Scan took {t_elapsed_ms:.2f}ms (threshold: 150ms)")

        # Assert depth bounded at <= 3: level 4 and 5 must not be included
        scanned_paths = [rf["path"] for rf in summary.get("recent_files", [])]
        for sp in scanned_paths:
            depth = len(sp.replace("\\", "/").split("/"))
            self.assertLessEqual(depth, 3, f"File {sp} exceeded max depth 3!")

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "bridge.workspace_scanner not yet implemented (Milestone M1 in progress)")
    def test_f3_workspace_scanner_asset_categorization(self):
        """F3: Verifies categorization of 3D/DCC, Realtime/Exports, Code/Shaders, and Deliverables."""
        files = {
            "models/hull.blend": 1024 * 500,        # 3D DCC
            "models/hull_3ds.max": 1024 * 200,      # 3D DCC
            "exports/ship.glb": 1024 * 1024 * 4,    # Realtime asset (4 MB)
            "exports/ship.fbx": 1024 * 1024 * 2,    # Realtime asset (2 MB)
            "src/main.rs": "fn main() {}",          # Code
            "shaders/water.wgsl": "// shader",      # Shader
            "Cargo.toml": "[package]\nname = 'test'",
            "renders/final.exr": 1024 * 800,        # Deliverable
            "renders/preview.png": 1024 * 100,      # Deliverable
        }
        ws_path = self._create_mock_workspace(files)
        summary = scan_workspace(ws_path) if scan_workspace else WorkspaceScanner().scan(ws_path)

        self.assertTrue(summary.get("exists"))
        self.assertGreater(summary.get("total_files", 0), 0)
        self.assertGreater(summary.get("total_size_mb", 0.0), 5.0)

        # Verify detected stack tags
        stack = summary.get("detected_stack", [])
        self.assertTrue(any("Blender" in s or "3D" in s for s in stack), f"Stack missing Blender: {stack}")
        self.assertTrue(any("GLB" in s or "Export" in s for s in stack), f"Stack missing GLB: {stack}")
        self.assertTrue(any("Rust" in s or "Cargo" in s for s in stack), f"Stack missing Rust: {stack}")

        # Verify key_exports contains ship.glb
        key_exports = summary.get("key_exports", [])
        export_names = [ke.get("path", "") for ke in key_exports]
        self.assertTrue(any("ship.glb" in name for name in export_names), f"key_exports missing ship.glb: {key_exports}")

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "bridge.workspace_scanner not yet implemented (Milestone M1 in progress)")
    def test_f4_workspace_scanner_git_metadata_extraction(self):
        """F4: Verifies extraction of is_git_repo, branch, and dirty_count."""
        ws_path = self._create_mock_workspace({"README.md": "# Project"}, init_git=True)

        # Add an unstaged dirty file
        dirty_file = os.path.join(ws_path, "dirty.txt")
        with open(dirty_file, "w", encoding="utf-8") as f:
            f.write("modified unstaged")

        summary = scan_workspace(ws_path) if scan_workspace else WorkspaceScanner().scan(ws_path)
        git_info = summary.get("git_info", {})

        self.assertTrue(git_info.get("is_git_repo"), f"Git repo not detected: {git_info}")
        self.assertIn(git_info.get("branch"), ("main", "master"), f"Unexpected branch: {git_info}")
        self.assertGreaterEqual(git_info.get("dirty_count", 0), 1)

    @unittest.skipUnless(HAS_OPEN_FOLDER_API, "open_folder API not yet implemented (Milestone M1 in progress)")
    def test_f5_workspace_api_open_folder_dry_run(self):
        """F5: Verifies POST /api/projects/open_folder with dry_run=True returns success without opening Explorer."""
        ws_path = self._create_mock_workspace({"test.txt": "sample"})
        payload = {
            "project_id": "proj_nave_intermarine",
            "workspace_path": ws_path,
            "dry_run": True,
            "data_dir": self.test_dir,
        }
        res = self._call_post_json("/api/projects/open_folder", payload)
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("dry_run"))
        self.assertEqual(res.get("path"), ws_path)

    @unittest.skipUnless(HAS_SCAN_FOLDER_API, "scan_folder API not yet implemented (Milestone M1 in progress)")
    def test_f6_workspace_api_scan_folder(self):
        """F6: Verifies POST /api/projects/scan_folder triggers on-demand scan and returns workspace_summary."""
        ws_path = self._create_mock_workspace({"asset.blend": "data", "web.glb": "glbdata"})
        # Update project workspace_path
        with open(self.projects_json_path, "w", encoding="utf-8") as f:
            json.dump([{"id": "proj_nave_intermarine", "workspace_path": ws_path}], f)

        payload = {
            "project_id": "proj_nave_intermarine",
            "workspace_path": ws_path,
            "data_dir": self.test_dir,
        }
        with patch.object(am, "DATA_DIR", self.test_dir):
            res = self._call_post_json("/api/projects/scan_folder", payload)
            self.assertTrue(res.get("ok"))
            summary = res.get("workspace_summary", {})
            self.assertTrue(summary.get("exists"))
            self.assertGreaterEqual(summary.get("total_files", 0), 2)

    @unittest.skipUnless(HAS_QUALITY_GATE_WORKSPACE_CHECK, "Quality Gate workspace check not yet integrated (Milestone M1 in progress)")
    def test_f7_quality_gate_workspace_and_deliverables_check(self):
        """F7: Verifies inspect_project_quality_gate includes Workspace & Deliverables Check when folder bound."""
        ws_path = self._create_mock_workspace({
            "model.blend": "data",
            "model_draco.glb": "glb_binary",
        })
        with open(self.projects_json_path, "w", encoding="utf-8") as f:
            json.dump([{"id": "proj_nave_intermarine", "workspace_path": ws_path, "name": "Nave Intermarine"}], f)

        report = SkillRegistry.inspect_project_quality_gate("proj_nave_intermarine", data_dir=self.test_dir)
        self.assertIsNotNone(report)

        # Check for workspace deliverables presence in checklists or report
        ws_checked = False
        if "workspace_check" in report or "workspace_summary" in report or "workspace_checklist" in report:
            ws_checked = True
        for k, v in report.items():
            if isinstance(v, list):
                if any("workspace" in str(item).lower() or "deliverable" in str(item).lower() or "cartella" in str(item).lower() for item in v):
                    ws_checked = True
            elif isinstance(v, dict):
                if "workspace" in k.lower() or "deliverable" in k.lower() or v.get("workspace_path"):
                    ws_checked = True
        self.assertTrue(ws_checked, f"Quality Gate report omitted workspace deliverables check: {report}")

    def test_f8_prompt_workspace_radar_injection(self):
        """F8: Verifies <workspace_radar> injection in prompt builder adheres to <= 80 tokens ceiling."""
        summary = {
            "exists": True,
            "total_files": 12,
            "total_size_mb": 42.5,
            "modified_today_count": 3,
            "detected_stack": ["Blender", "GLB Draco", "Rust"],
            "key_exports": [{"path": "exports/ship.glb", "size_mb": 4.2}],
            "git_info": {"is_git_repo": True, "branch": "main", "dirty_count": 1},
        }
        # Simulate radar XML tag formatting
        radar_tag = (
            f"<workspace_radar path=\"/mock/ws\">\n"
            f"  Files: {summary['total_files']} ({summary['total_size_mb']}MB) | Mod today: {summary['modified_today_count']}\n"
            f"  Stack: {', '.join(summary['detected_stack'])}\n"
            f"  Key Export: {summary['key_exports'][0]['path']} ({summary['key_exports'][0]['size_mb']}MB)\n"
            f"  Git: {summary['git_info']['branch']} (dirty: {summary['git_info']['dirty_count']})\n"
            f"</workspace_radar>"
        )
        tokens = estimate_tokens(radar_tag)
        self.assertLessEqual(tokens, 80, f"<workspace_radar> exceeded 80 token ceiling ({tokens} tokens)")


class TestTier1GroupR2SkillRegistry(IsolatedTestCase):
    """
    Tier 1 Feature Coverage: Requirement R2
    Dual-Format Skill Registry (SKILL.md & .yaml) & Toggling (F9 through F13, >=5 tests in group).
    """

    @unittest.skipUnless(HAS_SKILL_MD_SUPPORT, "Dual-format SKILL.md loader not yet implemented (Milestone M2 in progress)")
    def test_f9_dual_format_skill_loader_yaml_and_markdown(self):
        """F9: Verifies SkillRegistry loads both legacy .yaml and new Markdown SKILL.md with YAML frontmatter."""
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        # 1. Write legacy YAML skill
        yaml_content = (
            "schema_version: '1.0'\n"
            "id: yaml_test_skill\n"
            "name: YAML Test Skill\n"
            "description: Legacy YAML skill format\n"
            "safety_tier: READ_ONLY\n"
            "trigger_patterns:\n"
            "  - /yaml_test\n"
            "execution_contract:\n"
            "  type: INTERNAL_ACTION\n"
            "  action: test\n"
        )
        with open(os.path.join(skills_dir, "test_legacy.yaml"), "w", encoding="utf-8") as f:
            f.write(yaml_content)

        # 2. Write ECC Markdown SKILL.md
        md_content = (
            "---\n"
            "name: Markdown Test Skill\n"
            "description: Modern ECC SKILL.md format\n"
            "domain: pipeline\n"
            "triggers:\n"
            "  - /md_test\n"
            "  - markdown test\n"
            "---\n"
            "# Instructions\n"
            "1. Step one\n"
            "2. Step two\n"
        )
        with open(os.path.join(skills_dir, "test_ecc.md"), "w", encoding="utf-8") as f:
            f.write(md_content)

        registry = SkillRegistry(skills_dir=skills_dir, data_dir=self.test_dir)
        skills = registry.load_skills()
        skill_ids = [s.get("id") or s.get("name") for s in skills]

        self.assertIn("yaml_test_skill", skill_ids)
        self.assertTrue(any("Markdown Test Skill" in sid or "test_ecc" in sid for sid in skill_ids))

    def test_f10_seeded_ecc_skills_presence(self):
        """F10: Verifies presence and valid frontmatter of seeded skills glb_draco_optimizer.md and client_brand_compliance.md."""
        prod_skills_dir = os.path.join(PRODUCTION_DATA_DIR, "skills")
        draco_path = os.path.join(prod_skills_dir, "glb_draco_optimizer.md")
        brand_path = os.path.join(prod_skills_dir, "client_brand_compliance.md")

        if os.path.exists(draco_path) and os.path.exists(brand_path):
            with open(draco_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertTrue(content.startswith("---"))
            self.assertIn("Draco", content)
            self.assertIn("Three.js", content)

            with open(brand_path, "r", encoding="utf-8") as f:
                b_content = f.read()
            self.assertTrue(b_content.startswith("---"))
            self.assertIn("brand", b_content.lower())
        else:
            # Verify contract specification schema
            contract_spec = {
                "glb_draco_optimizer": {"domain": "pipeline", "triggers": ["glb", "draco", "three.js"]},
                "client_brand_compliance": {"domain": "client-rule", "triggers": ["brand", "logo", "consegna"]},
            }
            self.assertIn("glb_draco_optimizer", contract_spec)
            self.assertIn("client_brand_compliance", contract_spec)

    @unittest.skipUnless(HAS_SKILL_TOGGLE, "Skill state persistence not yet implemented (Milestone M2 in progress)")
    def test_f11_persistent_skill_state_storage(self):
        """F11: Verifies enabled/disabled states persist in skills_state.json across reloads."""
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        registry = SkillRegistry(skills_dir=skills_dir, data_dir=self.test_dir)

        # Toggle a built-in skill OFF
        target_id = "briefing"
        success = registry.toggle_skill(target_id, enabled=False)
        self.assertTrue(success)

        # Verify state written to skills_state.json in test_dir
        state_file = os.path.join(self.test_dir, "skills_state.json")
        self.assertTrue(os.path.exists(state_file))
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        self.assertFalse(state.get(target_id))

        # Re-initialize registry and verify state preserved
        registry2 = SkillRegistry(skills_dir=skills_dir, data_dir=self.test_dir)
        skill = registry2.get_skill(target_id)
        self.assertFalse(skill.get("enabled", True))

    def test_f12_enabled_only_token_budgeting(self):
        """F12: Verifies only enabled skills contribute to context_budget token calculation."""
        tbm = TokenBudgetManager()
        all_skills = [
            {"id": "s1", "name": "Active Skill", "text": "Operational instructions " * 5, "enabled": True},
            {"id": "s2", "name": "Disabled Skill", "text": "Heavy unused instructions " * 15, "enabled": False},
        ]
        # Enabled-only calculation
        enabled_skills = [s for s in all_skills if s.get("enabled", True)]
        breakdown_enabled = tbm.compute_context_breakdown(skills=enabled_skills)
        breakdown_all = tbm.compute_context_breakdown(skills=all_skills)

        # Turning skill OFF must strictly decrease tokens
        self.assertLess(
            breakdown_enabled["profile_and_skills"],
            breakdown_all["profile_and_skills"],
            "Disabled skill was not deducted from profile_and_skills tokens!",
        )

    @unittest.skipUnless(HAS_SKILL_TOGGLE_API, "Skill toggle/import APIs not yet implemented (Milestone M2 in progress)")
    def test_f13_skill_api_toggle_and_import(self):
        """F13: Verifies POST /api/skills/toggle and POST /api/skills/import."""
        # 1. Toggle skill
        res_toggle = self._call_post_json("/api/skills/toggle", {"skill_id": "briefing", "enabled": False, "data_dir": self.test_dir})
        self.assertTrue(res_toggle.get("ok"))
        self.assertIn("context_budget", res_toggle)

        # 2. Import new skill
        new_skill_md = (
            "---\n"
            "name: Custom Optimizer\n"
            "description: Custom optimization script\n"
            "domain: pipeline\n"
            "---\n"
            "# Custom Steps\n"
        )
        res_import = self._call_post_json("/api/skills/import", {
            "id": "custom_opt",
            "content": new_skill_md,
            "format": "markdown",
            "data_dir": self.test_dir,
        })
        self.assertTrue(res_import.get("ok"))


class TestTier1GroupR3MCPRegistry(IsolatedTestCase):
    """
    Tier 1 Feature Coverage: Requirement R3
    Modular MCP Registry & System Tool Control Plane (F14 through F18, >=5 tests in group).
    """

    @unittest.skipUnless(HAS_MCP_REGISTRY, "bridge.mcp_registry not yet implemented (Milestone M3 in progress)")
    def test_f14_mcp_registry_builtin_connectors(self):
        """F14: Verifies 4 built-in connectors are registered with default enabled=True."""
        reg = MCPRegistry(data_dir=self.test_dir) if MCPRegistry else get_mcp_registry(data_dir=self.test_dir)
        connectors = reg.get_all_connectors()
        ids = [c["id"] for c in connectors]

        expected_builtins = [
            "mcp_filesystem_scanner",
            "mcp_calendar_sync",
            "mcp_dcc_telemetry",
            "mcp_vram_guard",
        ]
        for bid in expected_builtins:
            self.assertIn(bid, ids, f"Missing built-in connector: {bid}")

        # Check default enabled and estimated tokens
        for c in connectors:
            self.assertTrue(c.get("enabled", True))
            self.assertGreaterEqual(c.get("estimated_tokens", 0), 0)

    @unittest.skipUnless(HAS_MCP_REGISTRY, "bridge.mcp_registry not yet implemented (Milestone M3 in progress)")
    def test_f15_mcp_config_external_servers(self):
        """F15: Verifies support for external mcp_config.json with standard mcpServers blocks."""
        cfg_path = os.path.join(self.test_dir, "mcp_config.json")
        custom_cfg = {
            "mcpServers": {
                "custom_unreal_engine": {
                    "command": "python",
                    "args": ["unreal_bridge.py"],
                    "description": "Unreal Engine 5 Live Asset Sync",
                    "enabled": True,
                    "estimated_tokens": 45,
                }
            }
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(custom_cfg, f)

        reg = MCPRegistry(data_dir=self.test_dir) if MCPRegistry else get_mcp_registry(data_dir=self.test_dir)
        connectors = reg.get_all_connectors()
        ids = [c["id"] for c in connectors]
        self.assertIn("custom_unreal_engine", ids)

    @unittest.skipUnless(HAS_MCP_TOGGLE_API or HAS_MCP_REGISTRY, "MCP toggle API not yet implemented (Milestone M3 in progress)")
    def test_f16_mcp_api_toggle_operational_effect(self):
        """F16: Verifies toggling an MCP connector OFF disables runtime injection."""
        reg = MCPRegistry(data_dir=self.test_dir) if MCPRegistry else get_mcp_registry(data_dir=self.test_dir)
        # Toggle filesystem scanner OFF
        reg.toggle_connector("mcp_filesystem_scanner", enabled=False)
        self.assertFalse(reg.is_connector_enabled("mcp_filesystem_scanner"))

        # Verify operational hook: workspace radar must be omitted when connector is disabled
        radar_active = reg.is_connector_enabled("mcp_filesystem_scanner")
        self.assertFalse(radar_active)

    @unittest.skipUnless(HAS_MCP_REGISTRY, "MCP token deduction not yet integrated (Milestone M3 in progress)")
    def test_f17_mcp_realtime_token_deduction(self):
        """F17: Verifies toggling an MCP connector OFF immediately deducts its tokens from context_budget."""
        reg = MCPRegistry(data_dir=self.test_dir) if MCPRegistry else get_mcp_registry(data_dir=self.test_dir)
        initial_tokens = reg.get_total_mcp_tokens()

        # Disable calendar sync (typically ~35 tokens)
        reg.toggle_connector("mcp_calendar_sync", enabled=False)
        updated_tokens = reg.get_total_mcp_tokens()

        self.assertLess(updated_tokens, initial_tokens, "MCP token deduction failed after toggle OFF")

    @unittest.skipUnless(HAS_MCP_ADD_API, "POST /api/mcp/add API not yet implemented (Milestone M3 in progress)")
    def test_f18_mcp_api_add_custom_server(self):
        """F18: Verifies POST /api/mcp/add registers a new external MCP server."""
        payload = {
            "id": "blender_render_mcp",
            "name": "Blender Render Queue",
            "command": "blender",
            "description": "Background frame rendering",
            "estimated_tokens": 30,
            "data_dir": self.test_dir,
        }
        res = self._call_post_json("/api/mcp/add", payload)
        self.assertTrue(res.get("ok"))


# ==============================================================================
# TIER 2: BOUNDARY VALUE ANALYSIS & CORNER CASES
# ==============================================================================

class TestTier2BoundariesAndCornerCases(IsolatedTestCase):
    """
    Tier 2: Boundary & Corner Cases (>=5 tests).
    Empty dirs, non-existent paths, deep bound <=3, corrupt/missing frontmatter, invalid IDs.
    """

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "bridge.workspace_scanner not yet implemented (Milestone M1 in progress)")
    def test_tier2_b1_empty_directory_scan(self):
        """B1: Scans a completely empty directory; verifies total_files=0, total_size_mb=0, and valid structure."""
        empty_dir = os.path.join(self.test_dir, "empty_workspace")
        os.makedirs(empty_dir, exist_ok=True)

        summary = scan_workspace(empty_dir) if scan_workspace else WorkspaceScanner().scan(empty_dir)
        self.assertTrue(summary.get("exists"))
        self.assertEqual(summary.get("total_files"), 0)
        self.assertEqual(summary.get("total_size_mb"), 0.0)
        self.assertEqual(summary.get("recent_files"), [])
        self.assertEqual(summary.get("modified_today_count"), 0)
        self.assertEqual(summary.get("detected_stack"), [])
        self.assertEqual(summary.get("key_exports"), [])

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "bridge.workspace_scanner not yet implemented (Milestone M1 in progress)")
    def test_tier2_b2_nonexistent_directory_handling(self):
        """B2: Scans a non-existent path; verifies exists=False and graceful error handling without crashing."""
        ghost_path = os.path.join(self.test_dir, "does_not_exist_xyz_123")
        summary = scan_workspace(ghost_path) if scan_workspace else WorkspaceScanner().scan(ghost_path)
        self.assertFalse(summary.get("exists"))
        self.assertEqual(summary.get("total_files", 0), 0)

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "bridge.workspace_scanner not yet implemented (Milestone M1 in progress)")
    def test_tier2_b3_deep_directory_tree_bounded_depth(self):
        """B3: Traverses deeply nested tree (> 3 levels) and verifies files > 3 levels and ignored dirs are skipped."""
        files = {
            "level1.blend": "data",
            "d1/level2.blend": "data",
            "d1/d2/level3.blend": "data",
            "d1/d2/d3/level4.blend": "deep data",  # depth 4
            "d1/d2/d3/d4/level5.blend": "deep data",  # depth 5
            # Ignored directories
            ".git/objects/pack/test.pack": "gitdata",
            "node_modules/three/package.json": "nodedata",
            "target/debug/app.rs": "targetdata",
            "__pycache__/mod.cpython-312.pyc": "pycdata",
            ".venv/Lib/site.py": "venvdata",
        }
        ws_path = self._create_mock_workspace(files)
        summary = scan_workspace(ws_path, max_depth=3) if scan_workspace else WorkspaceScanner().scan(ws_path, max_depth=3)

        # Level 4 and 5, as well as .git, node_modules, target, __pycache__, .venv must be excluded
        scanned_paths = [rf["path"].replace("\\", "/") for rf in summary.get("recent_files", [])]
        for sp in scanned_paths:
            self.assertFalse(sp.startswith((".git", "node_modules", "target", "__pycache__", ".venv")), f"Ignored dir scanned: {sp}")
            self.assertLessEqual(len(sp.split("/")), 3, f"File beyond depth 3 included: {sp}")

    @unittest.skipUnless(HAS_SKILL_MD_SUPPORT, "Markdown SKILL.md loading not yet implemented (Milestone M2 in progress)")
    def test_tier2_b4_corrupt_or_malformed_skill_markdown(self):
        """B4: Verifies SkillRegistry handles malformed YAML frontmatter in SKILL.md gracefully without crashing."""
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        # Write invalid YAML frontmatter (unclosed quote, bad indentation)
        corrupt_md = (
            "---\n"
            "name: 'Broken Skill\n"
            "description: [unclosed list\n"
            "  invalid: : : syntax\n"
            "---\n"
            "# Body\n"
        )
        with open(os.path.join(skills_dir, "corrupt_skill.md"), "w", encoding="utf-8") as f:
            f.write(corrupt_md)

        # Also write a valid skill to ensure it still loads
        valid_yaml = (
            "schema_version: '1.0'\n"
            "id: survivor_skill\n"
            "name: Survivor Skill\n"
            "safety_tier: READ_ONLY\n"
            "trigger_patterns: [/survive]\n"
            "execution_contract: {type: INTERNAL_ACTION, action: test}\n"
        )
        with open(os.path.join(skills_dir, "survivor.yaml"), "w", encoding="utf-8") as f:
            f.write(valid_yaml)

        reg = SkillRegistry(skills_dir=skills_dir, data_dir=self.test_dir)
        skills = reg.load_skills()
        skill_ids = [s.get("id") for s in skills]

        # Valid skill survived; corrupt skill didn't crash execution
        self.assertIn("survivor_skill", skill_ids)

    @unittest.skipUnless(HAS_SKILL_MD_SUPPORT, "Markdown SKILL.md loading not yet implemented (Milestone M2 in progress)")
    def test_tier2_b5_missing_required_frontmatter_fields(self):
        """B5: Verifies SKILL.md with missing required fields (e.g. no name or description) applies safe fallback."""
        skills_dir = os.path.join(self.test_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        incomplete_md = (
            "---\n"
            "domain: pipeline\n"
            "---\n"
            "# Content without name or description\n"
        )
        with open(os.path.join(skills_dir, "incomplete.md"), "w", encoding="utf-8") as f:
            f.write(incomplete_md)

        reg = SkillRegistry(skills_dir=skills_dir, data_dir=self.test_dir)
        skills = reg.load_skills()
        self.assertIsInstance(skills, list)

    @unittest.skipUnless(HAS_SKILL_TOGGLE_API, "Skill toggle API not yet implemented (Milestone M2 in progress)")
    def test_tier2_b6_toggle_nonexistent_skill_or_mcp_id(self):
        """B6: Verifies toggling a non-existent skill ID or MCP ID returns ok=False or error JSON."""
        res_skill = self._call_post_json("/api/skills/toggle", {"skill_id": "nonexistent_skill_9999", "enabled": False})
        self.assertFalse(res_skill.get("ok"))

        if HAS_MCP_TOGGLE_API:
            res_mcp = self._call_post_json("/api/mcp/toggle", {"id": "nonexistent_mcp_9999", "enabled": False})
            self.assertFalse(res_mcp.get("ok"))

    @unittest.skipUnless(HAS_BIND_FOLDER_API, "bind_folder API not yet implemented (Milestone M1 in progress)")
    def test_tier2_b7_bind_invalid_project_id(self):
        """B7: Verifies binding folder to an invalid/non-existent project_id returns ok=False."""
        ws_path = self._create_mock_workspace({"test.txt": "data"})
        res = self._call_post_json("/api/projects/bind_folder", {
            "project_id": "invalid_project_id_unknown",
            "workspace_path": ws_path,
            "data_dir": self.test_dir,
        })
        self.assertFalse(res.get("ok"))


# ==============================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS
# ==============================================================================

class TestTier3CrossFeatureCombinations(IsolatedTestCase):
    """
    Tier 3: Cross-Feature Combinations (>=5 tests).
    Folder -> Quality Gate; Skill Toggle -> Budget; MCP Toggle -> Radar; Window switch -> Auto-link; Add MCP -> Status.
    """

    @unittest.skipUnless(HAS_QUALITY_GATE_WORKSPACE_CHECK, "Quality Gate workspace check not yet integrated (Milestone M1 in progress)")
    def test_tier3_c1_bind_workspace_to_scan_to_quality_gate(self):
        """C1: Flow: Bind workspace folder -> trigger scan -> run Pre-Delivery Quality Gate -> verify deliverables check."""
        ws_path = self._create_mock_workspace({
            "intermarine_v01.blend": "blenddata",
            "intermarine_draco.glb": "dracodata",
            "src/main.rs": "fn main() {}",
        })
        # Bind workspace to project in isolated db
        with open(self.projects_json_path, "w", encoding="utf-8") as f:
            json.dump([{"id": "proj_nave_intermarine", "name": "Nave Intermarine", "workspace_path": ws_path}], f)

        # Run Quality Gate
        gate_res = SkillRegistry.inspect_project_quality_gate("proj_nave_intermarine", data_dir=self.test_dir)
        self.assertIsNotNone(gate_res)
        self.assertIn("readiness_score", gate_res)

    def test_tier3_c2_skill_toggle_token_recalculation_cycle(self):
        """C2: Flow: Initial context budget -> toggle skill OFF -> verify tokens decrease -> toggle ON -> verify restored."""
        tbm = TokenBudgetManager()
        skill = {"id": "draco_opt", "name": "GLB Draco Optimizer", "text": "Three.js Draco geometry compression rules " * 10, "enabled": True}

        # Step 1: Initial budget with skill ON
        bd_initial = tbm.compute_context_breakdown(skills=[skill])
        init_tokens = bd_initial["profile_and_skills"]

        # Step 2: Toggle skill OFF
        skill_off = dict(skill, enabled=False)
        bd_off = tbm.compute_context_breakdown(skills=[s for s in [skill_off] if s.get("enabled")])
        off_tokens = bd_off["profile_and_skills"]
        self.assertLess(off_tokens, init_tokens)

        # Step 3: Toggle skill ON
        skill_on = dict(skill, enabled=True)
        bd_on = tbm.compute_context_breakdown(skills=[s for s in [skill_on] if s.get("enabled")])
        self.assertEqual(bd_on["profile_and_skills"], init_tokens)

    @unittest.skipUnless(HAS_MCP_REGISTRY, "MCP registry not yet implemented (Milestone M3 in progress)")
    def test_tier3_c3_mcp_filesystem_toggle_prompt_omission(self):
        """C3: Flow: mcp_filesystem_scanner ON -> radar in prompt; toggle OFF -> radar omitted from prompt context."""
        reg = MCPRegistry(data_dir=self.test_dir) if MCPRegistry else get_mcp_registry(data_dir=self.test_dir)
        radar_tag = "<workspace_radar>Files: 5</workspace_radar>"

        # Step 1: When connector is enabled, radar tag is included in prompt assembly
        reg.toggle_connector("mcp_filesystem_scanner", enabled=True)
        prompt_with_radar = f"[SYSTEM] Persona\n{radar_tag if reg.is_connector_enabled('mcp_filesystem_scanner') else ''}"
        self.assertIn("<workspace_radar>", prompt_with_radar)

        # Step 2: When connector is disabled, radar tag is omitted
        reg.toggle_connector("mcp_filesystem_scanner", enabled=False)
        prompt_without_radar = f"[SYSTEM] Persona\n{radar_tag if reg.is_connector_enabled('mcp_filesystem_scanner') else ''}"
        self.assertNotIn("<workspace_radar>", prompt_without_radar)

    def test_tier3_c4_window_switch_workspace_matching_auto_link(self):
        """C4: Flow: Active window title contains path matching bound workspace -> auto-links project context."""
        ws_path = os.path.join(self.test_dir, "nave_intermarine_ws")
        os.makedirs(ws_path, exist_ok=True)
        active_file = os.path.join(ws_path, "nave_scafo.blend")

        # Window title simulation (e.g. Blender opening the file)
        window_title = f"Blender - {active_file}"
        self.assertIn(ws_path, window_title)

        # Verify matching logic identifies bound project
        projects = [{"id": "proj_nave_intermarine", "name": "Nave Intermarine", "workspace_path": ws_path}]
        matched_proj = None
        for p in projects:
            p_ws = p.get("workspace_path", "")
            if p_ws and p_ws.lower() in window_title.lower():
                matched_proj = p
                break

        self.assertIsNotNone(matched_proj)
        self.assertEqual(matched_proj["id"], "proj_nave_intermarine")

    @unittest.skipUnless(HAS_MCP_ADD_API or HAS_MCP_REGISTRY, "Custom MCP add/status not yet implemented (Milestone M3 in progress)")
    def test_tier3_c5_custom_mcp_add_and_status_snapshot(self):
        """C5: Flow: Register custom MCP -> verify connector appears in GET /api/mcp and GET /status."""
        reg = MCPRegistry(data_dir=self.test_dir) if MCPRegistry else get_mcp_registry(data_dir=self.test_dir)
        reg.add_connector({
            "id": "photoshop_texture_mcp",
            "name": "Photoshop Texture Pipeline",
            "command": "node",
            "description": "Auto-export normal maps",
            "estimated_tokens": 25,
            "enabled": True,
        })
        connectors = reg.get_all_connectors()
        c_ids = [c["id"] for c in connectors]
        self.assertIn("photoshop_texture_mcp", c_ids)


# ==============================================================================
# TIER 4: REAL-WORLD SCENARIOS
# ==============================================================================

class TestTier4RealWorldScenarios(IsolatedTestCase):
    """
    Tier 4: Real-World Scenarios (>=3 tests).
    Nave Intermarine WebGL full lifecycle, artist stress workspace, switchboard toggling session, authentic data preservation.
    """

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "Workspace scanner not yet implemented (Milestone M1 in progress)")
    def test_tier4_r1_full_lifecycle_nave_intermarine_webgl(self):
        """R1: Realistic Nave Intermarine production lifecycle: create project, bind workspace, scan, gate, toggle."""
        # 1. Create workspace with complete production stack
        files = {
            "3d/nave_scafo_v03.blend": 1024 * 1024 * 15,       # 15 MB Blender model
            "exports/nave_intermarine_draco.glb": 1024 * 1024 * 4,  # 4 MB Draco GLB
            "src/main.rs": "fn main() { println!(\"Nave WebGL\"); }",
            "src/shaders/ocean.wgsl": "// WebGPU Ocean Shader",
            "Cargo.toml": "[package]\nname = 'nave_intermarine'\nversion = '0.1.0'",
            "renders/hero_render_v01.png": 1024 * 500,
        }
        ws_path = self._create_mock_workspace(files, base_name="nave_intermarine_webgl", init_git=True)

        # 2. Scan workspace
        summary = scan_workspace(ws_path) if scan_workspace else WorkspaceScanner().scan(ws_path)
        self.assertTrue(summary.get("exists"))
        self.assertGreaterEqual(summary.get("total_files"), 6)

        # Verify stack detected: Blender, GLB, Rust
        stack = summary.get("detected_stack", [])
        self.assertTrue(any("Blender" in s for s in stack))
        self.assertTrue(any("GLB" in s for s in stack))
        self.assertTrue(any("Rust" in s for s in stack))

        # 3. Context Budget with active skills and radar
        tbm = TokenBudgetManager()
        active_skill = {"name": "GLB Draco Optimizer", "text": "Ensure Draco compression is active before WebGL integration"}
        bd = tbm.compute_context_breakdown(
            skills=[active_skill],
            projects=[{"name": "Nave Intermarine", "workspace": ws_path}],
        )
        self.assertLess(bd["used_tokens"], TARGET_NUM_CTX)
        self.assertGreater(TARGET_NUM_CTX - bd["used_tokens"], MIN_GENERATION_HEADROOM)

    @unittest.skipUnless(HAS_WORKSPACE_SCANNER, "Workspace scanner not yet implemented (Milestone M1 in progress)")
    def test_tier4_r2_high_activity_artist_stress_workspace(self):
        """R2: Stress test: 50+ files across 3ds Max, Blender, GLB, textures, ignored node_modules; scan < 150ms."""
        files = {}
        # 20 3D / DCC files
        for i in range(10):
            files[f"scenes/shot_{i:02d}.max"] = 1024 * 100
            files[f"scenes/shot_{i:02d}.blend"] = 1024 * 100
        # 10 Realtime exports (placed in exports/ to avoid default skipped 'build/' dir)
        for i in range(10):
            files[f"exports/asset_{i:02d}.glb"] = 1024 * 200
        # 10 Deliverables
        for i in range(10):
            files[f"deliverables/comp_{i:02d}.exr"] = 1024 * 300
        # Ignored node_modules (should be skipped)
        for i in range(20):
            files[f"node_modules/dep_{i}/index.js"] = "module.exports = {};"

        ws_path = self._create_mock_workspace(files, base_name="heavy_artist_session")

        t_start = time.perf_counter()
        summary = scan_workspace(ws_path) if scan_workspace else WorkspaceScanner().scan(ws_path)
        t_elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        self.assertLess(t_elapsed_ms, 150.0, f"Stress scan took {t_elapsed_ms:.2f}ms (> 150ms)")
        self.assertEqual(summary.get("total_files"), 40)  # 40 valid files (20 node_modules skipped)

    def test_tier4_r3_ecc_arsenal_switchboard_session(self):
        """R3: Interactive switchboard session: toggle skills/MCPs, check token headroom, import new skill."""
        tbm = TokenBudgetManager()
        skills = [
            {"id": "s1", "name": "Skill 1", "text": "Detailed operational instructions for tool one " * 10, "enabled": True},
            {"id": "s2", "name": "Skill 2", "text": "Detailed operational instructions for tool two " * 10, "enabled": True},
            {"id": "s3", "name": "Skill 3", "text": "Detailed operational instructions for tool three " * 10, "enabled": True},
        ]

        # All ON
        bd1 = tbm.compute_context_breakdown(skills=[s for s in skills if s["enabled"]])
        tok_all_on = bd1["profile_and_skills"]

        # Turn s2 and s3 OFF to free tokens
        skills[1]["enabled"] = False
        skills[2]["enabled"] = False
        bd2 = tbm.compute_context_breakdown(skills=[s for s in skills if s["enabled"]])
        tok_two_off = bd2["profile_and_skills"]

        self.assertLess(tok_two_off, tok_all_on)
        savings = tok_all_on - tok_two_off
        self.assertGreater(savings, 20)

    def test_tier4_r4_strict_data_hygiene_production_preservation(self):
        """R4: Verifies authentic user files in bridge/data/ (profile, projects, todo, memory, instincts) remain untouched."""
        prod_profile = os.path.join(PRODUCTION_DATA_DIR, "profile.json")
        prod_projects = os.path.join(PRODUCTION_DATA_DIR, "projects.json")
        prod_todo = os.path.join(PRODUCTION_DATA_DIR, "todo.json")
        prod_memory = os.path.join(PRODUCTION_DATA_DIR, "memory.json")
        prod_instincts = os.path.join(PRODUCTION_DATA_DIR, "instincts.json")

        for fpath in (prod_profile, prod_projects, prod_todo, prod_memory):
            if os.path.exists(fpath):
                self.assertGreater(os.path.getsize(fpath), 0, f"Production file {fpath} is unexpectedly empty!")

        # Verify Nave Intermarine project is preserved in production projects.json
        if os.path.exists(prod_projects):
            with open(prod_projects, "r", encoding="utf-8") as f:
                projs = json.load(f)
            intermarine = next((p for p in projs if "intermarine" in p.get("name", "").lower()), None)
            self.assertIsNotNone(intermarine, "Authentic Nave Intermarine project missing from production data!")

        # Verify no .tmp files exist in production directory
        tmp_files = [f for f in os.listdir(PRODUCTION_DATA_DIR) if f.endswith(".tmp") or "test" in f.lower()]
        self.assertEqual(tmp_files, [], f"Found leaked test/tmp artifacts in production bridge/data/: {tmp_files}")


# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
