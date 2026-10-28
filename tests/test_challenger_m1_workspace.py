"""
tests/test_challenger_m1_workspace.py
=====================================
Empirical Adversarial Test Suite for Milestone 1 (F1-F8):
Workspace Scanner, Git Metadata, and Prompt Radar Boundedness.
Written by challenger_m1_1.
"""

import math
import os
import shutil
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = os.path.abspath(r"C:\Users\AMD04\EPO\AsciiArt")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bridge.workspace_scanner import WorkspaceScanner, scan_workspace
from bridge.retrieval import TokenBudgetManager, estimate_tokens
from bridge.storage_manager import StorageManager
from bridge.ai_monitor import MemoryAndProjectsManager


class TestChallengerWorkspaceScanner(unittest.TestCase):
    """Adversarial stress testing of WorkspaceScanner and TokenBudgetManager."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="challenger_m1_ws_")

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. SCANNER STRESS: Deep nested tree
    # --------------------------------------------------------------------------
    def test_deep_nested_tree_bounded_depth(self):
        """
        Verify scanner strictly bounds depth <= 3 and does NOT recurse beyond.
        Tree of depth 7:
        root/ (depth 1) -> f0.txt
          d1/ (depth 2) -> f1.txt
            d2/ (depth 3) -> f2.txt
              d3/ (depth 4) -> f3.txt
                d4/ (depth 5) -> f4.txt
                  d5/ (depth 6) -> f5.txt
                    d6/ (depth 7) -> f6.txt
        """
        cur = self.temp_dir
        with open(os.path.join(cur, "f0.txt"), "w") as f:
            f.write("depth 1")

        for level in range(1, 7):
            cur = os.path.join(cur, f"d{level}")
            os.makedirs(cur, exist_ok=True)
            with open(os.path.join(cur, f"f{level}.txt"), "w") as f:
                f.write(f"depth {level + 1}")

        # Scan with default max_depth=3
        res = scan_workspace(self.temp_dir, max_depth=3)
        self.assertTrue(res["exists"])
        self.assertEqual(
            res["total_files"],
            3,
            f"Expected exactly 3 files at depth <= 3, got {res['total_files']}",
        )

        scanned_paths = [rf["path"] for rf in res["recent_files"]]
        self.assertIn("f0.txt", scanned_paths)
        self.assertIn("d1/f1.txt", scanned_paths)
        self.assertIn("d1/d2/f2.txt", scanned_paths)

        # Ensure no file at depth >= 4 is scanned
        for level in range(3, 7):
            for sp in scanned_paths:
                self.assertNotIn(f"f{level}.txt", sp)

        # Test max_depth=1
        res1 = scan_workspace(self.temp_dir, max_depth=1)
        self.assertEqual(res1["total_files"], 1)

        # Test max_depth=2
        res2 = scan_workspace(self.temp_dir, max_depth=2)
        self.assertEqual(res2["total_files"], 2)

    # --------------------------------------------------------------------------
    # 1. SCANNER STRESS: Massive folder (1000 dummy files) and < 150ms speed
    # --------------------------------------------------------------------------
    def test_massive_folder_performance_under_150ms(self):
        """
        Create 1000 dummy files across allowed and skipped directories.
        Verify execution time is strictly < 150ms and skipped files excluded.
        """
        allowed_dirs = [
            self.temp_dir,
            os.path.join(self.temp_dir, "models"),
            os.path.join(self.temp_dir, "src"),
            os.path.join(self.temp_dir, "textures"),
            os.path.join(self.temp_dir, "scripts"),
        ]
        for ad in allowed_dirs:
            os.makedirs(ad, exist_ok=True)

        skipped_dirs = [
            os.path.join(self.temp_dir, "node_modules", "pkg"),
            os.path.join(self.temp_dir, "target", "debug"),
            os.path.join(self.temp_dir, "__pycache__"),
            os.path.join(self.temp_dir, ".venv", "Lib"),
            os.path.join(self.temp_dir, "Build", "Win64"),
        ]
        for sd in skipped_dirs:
            os.makedirs(sd, exist_ok=True)

        # 500 files in allowed dirs (100 in each)
        for i in range(500):
            target_dir = allowed_dirs[i % len(allowed_dirs)]
            with open(os.path.join(target_dir, f"asset_{i}.dat"), "wb") as f:
                f.write(b"data")

        # 500 files in skipped dirs (100 in each)
        for i in range(500):
            target_dir = skipped_dirs[i % len(skipped_dirs)]
            with open(os.path.join(target_dir, f"ignored_{i}.dat"), "wb") as f:
                f.write(b"skip")

        scanner = WorkspaceScanner(max_depth=3)
        start = time.perf_counter()
        summary = scanner.scan(self.temp_dir)
        elapsed_ms = (time.perf_counter() - start) * 1000

        self.assertLess(
            elapsed_ms,
            150.0,
            f"Scan must complete in < 150ms, took {elapsed_ms:.2f}ms",
        )
        self.assertEqual(
            summary["total_files"],
            500,
            f"Expected exactly 500 non-skipped files, got {summary['total_files']}",
        )

    # --------------------------------------------------------------------------
    # 1. SCANNER STRESS: Skipped directories
    # --------------------------------------------------------------------------
    def test_skipped_directories_thoroughly_excluded(self):
        """
        Verify .git, node_modules, target, __pycache__, .venv, Build, etc.
        are completely skipped and their contents excluded from total_files.
        """
        dirs_to_test = [
            ".git",
            "node_modules",
            "target",
            "__pycache__",
            ".venv",
            "venv",
            "env",
            "Build",
            "BUILD",
            "build",
            "Intermediate",
            "DerivedDataCache",
            ".idea",
            ".vscode",
            "dist",
            "bin",
            "obj",
        ]
        for d in dirs_to_test:
            dir_path = os.path.join(self.temp_dir, d, "nested")
            os.makedirs(dir_path, exist_ok=True)
            with open(os.path.join(dir_path, "dummy.txt"), "w") as f:
                f.write("must be skipped")

        summary = scan_workspace(self.temp_dir, max_depth=3)
        self.assertEqual(
            summary["total_files"],
            0,
            f"Skipped directories must have 0 files counted, got {summary['total_files']}",
        )
        self.assertEqual(len(summary["recent_files"]), 0)
        self.assertEqual(len(summary["key_exports"]), 0)

    # --------------------------------------------------------------------------
    # 1. SCANNER STRESS: Mixed Casing & Unusual File Names
    # --------------------------------------------------------------------------
    def test_asset_categorization_casing_and_unusual_names(self):
        """
        Test mixed uppercase/lowercase extensions (.BLEND, .Glb, .RS, .Png)
        and unusual filenames (spaces, unicode, dots, no extension).
        """
        files_to_create = [
            ("model_ship.BLEND", b"blend"),
            ("model_boat.Glb", b"glb"),
            ("mesh_car.GLB", b"glb2"),
            ("source_logic.RS", "fn main() {}".encode()),
            ("render_pass.Png", b"png"),
            ("highres_texture.PNG", b"png2"),
            ("building.MAX", b"max"),
            ("GameProject.Uproject", b"uproject"),
            ("compute.WGSL", b"wgsl"),
            ("Cargo.toml", "package".encode()),
            ("package.json", "{}".encode()),
            # Unusual names
            ("archive.tar.gz", b"gz"),
            ("LICENSE_NO_EXT", b"license"),
            (".hidden_dotfile", b"dot"),
            ("file with spaces and symbols #1 [final] (v2).Glb", b"glb_spaces"),
            ("nave_intermarine_船_🚢.blend", b"unicode_blend"),
        ]

        for fname, content in files_to_create:
            with open(os.path.join(self.temp_dir, fname), "wb") as f:
                f.write(content)

        summary = scan_workspace(self.temp_dir, max_depth=3)

        stack = summary["detected_stack"]
        self.assertIn("Blender", stack)
        self.assertIn("GLB Export", stack)
        self.assertIn("Rust", stack)
        self.assertIn("3ds Max", stack)
        self.assertIn("Unreal Engine", stack)
        self.assertIn("Shaders / WebGPU", stack)
        self.assertIn("Three.js", stack)

        # Check key exports (must find all 3 glb files regardless of case, plus Uproject)
        exports = [e["path"] for e in summary["key_exports"]]
        glb_exports = [e for e in summary["key_exports"] if e["ext"] == ".glb"]
        self.assertEqual(len(glb_exports), 3, f"Expected 3 GLB exports, found {len(glb_exports)}: {glb_exports}")
        self.assertTrue(any("model_boat.Glb" in e["path"] for e in glb_exports))
        self.assertTrue(any("mesh_car.GLB" in e["path"] for e in glb_exports))
        self.assertTrue(any("file with spaces and symbols" in e["path"] for e in glb_exports))
        self.assertTrue(any("GameProject.Uproject" in e for e in exports))

    # --------------------------------------------------------------------------
    # 2. GIT METADATA EDGE CASES
    # --------------------------------------------------------------------------
    def test_git_metadata_detached_head_raw_sha(self):
        """Detached HEAD (.git/HEAD contains raw 40-char SHA)."""
        git_dir = os.path.join(self.temp_dir, ".git")
        os.makedirs(git_dir, exist_ok=True)
        raw_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
            f.write(raw_sha + "\n")

        summary = scan_workspace(self.temp_dir)
        git_info = summary["git_info"]
        self.assertTrue(git_info["is_git_repo"])
        self.assertEqual(git_info["branch"], raw_sha[:7])

    def test_git_metadata_missing_refs_heads(self):
        """Missing .git/refs/heads/ directory does not cause error."""
        git_dir = os.path.join(self.temp_dir, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
            f.write("ref: refs/heads/main\n")

        summary = scan_workspace(self.temp_dir)
        self.assertTrue(summary["git_info"]["is_git_repo"])
        self.assertEqual(summary["git_info"]["branch"], "main")

    def test_git_metadata_not_a_git_repo(self):
        """Workspace not in a git repo returns graceful fallback."""
        summary = scan_workspace(self.temp_dir)
        git_info = summary["git_info"]
        self.assertFalse(git_info["is_git_repo"])
        self.assertEqual(git_info["branch"], "")
        self.assertEqual(git_info["dirty_count"], 0)

    def test_git_metadata_empty_head_file(self):
        """Corrupted 0-byte .git/HEAD file returns graceful fallback."""
        git_dir = os.path.join(self.temp_dir, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
            pass  # empty

        summary = scan_workspace(self.temp_dir)
        self.assertTrue(summary["git_info"]["is_git_repo"])
        self.assertIsInstance(summary["git_info"]["branch"], str)

    # --------------------------------------------------------------------------
    # 3. PROMPT RADAR BOUNDEDNESS (Adversarial stress test)
    # --------------------------------------------------------------------------
    def test_prompt_radar_pathological_summary_strictly_under_80_tokens(self):
        """
        Stress test TokenBudgetManager.format_workspace_radar with a pathological
        workspace summary:
        - 200 character path
        - 50 export assets with long paths
        - massive file count (999,999) and large size
        - 80 character git branch name
        - full detected stack
        Verify output token estimate NEVER exceeds 80 tokens.
        """
        mgr = TokenBudgetManager()
        long_dir_name = "x" * 200
        pathological_path = os.path.join("C:\\", long_dir_name)

        exports = [
            {
                "path": f"exports/variant_{i}/super_detailed_final_optimized_draco_model_asset_{i}.glb",
                "size_mb": 45.8,
                "ext": ".glb",
            }
            for i in range(50)
        ]

        summary = {
            "workspace_path": pathological_path,
            "total_files": 999999,
            "total_size_mb": 543210.99,
            "key_exports": exports,
            "git_info": {
                "is_git_repo": True,
                "branch": "feature/PROJ-12345-very-long-feature-branch-name-with-client-milestone-delivery-tags",
                "dirty_count": 99,
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
            summary, workspace_path=pathological_path, max_tokens=80
        )

        tok_estimate = estimate_tokens(radar_tag)

        print("\n[CHALLENGER EMPIRICAL TEST OUTPUT]")
        print("Generated Radar Tag:\n", radar_tag)
        print(f"Tag Character Length: {len(radar_tag)}")
        print(f"Tag Token Estimate: {tok_estimate} (Max allowed: 80)")

        # The contract explicitly specifies:
        # "inject a compact <workspace_radar> summary (<= 80 tokens)"
        # "verify output string length and token estimate NEVER exceeds 80 tokens."
        self.assertLessEqual(
            tok_estimate,
            80,
            f"FAIL: Workspace radar token count ({tok_estimate}) exceeds 80 tokens budget!",
        )


if __name__ == "__main__":
    unittest.main()
