"""
tests/test_git_sync_adversarial.py - Adversarial Stress & Verification Harness.

Adversarially challenges:
1. Merge driver space-in-path handling under full Git rebase merge invocations.
2. Offline 10s timeout resilience with unroutable blackhole remote and recovery.
3. Commutativity of partitioned multi-host time logs with underscores, multiple machines, and out-of-order merging.
4. CLI sync subcommands edge cases (daemon HTTP 500 error, malformed JSON, workspace with spaces).
"""

from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Generator
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from neko_bot.cli import main
from neko_bot.core.config import Config
from neko_bot.sync.engine import GitSyncEngine, SyncResult, SyncStatus
from neko_bot.sync.merge_driver import is_merge_driver_registered, register_merge_driver
from neko_bot.task.models import Task, TaskPriority
from neko_bot.task.store import TaskStore
from neko_bot.tracker.aggregator import TimeAggregator
from neko_bot.tracker.logger import TimeLogger


def robust_rmtree(path: Path) -> None:
    """Robust directory removal clearing Windows read-only flags on git packfiles."""
    def on_rm_error(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except Exception:
            pass

    if path.exists():
        shutil.rmtree(path, onerror=on_rm_error)


class TestAdversarialSpaceInPathMerge(unittest.TestCase):
    """
    Adversarial Challenge 1:
    End-to-end Git rebase merge invocation where bare remote, work repo, and home repo
    ALL reside in directories with spaces, parentheses, and ampersands.
    """

    def setUp(self) -> None:
        self.root_temp = Path(tempfile.mkdtemp(prefix="neko space & (special) dir_"))
        self.remote_dir = self.root_temp / "bare remote with spaces.git"
        self.work_dir = self.root_temp / "work clone with spaces (PC1)"
        self.home_dir = self.root_temp / "home clone with spaces (PC2)"

        # 1. Initialize bare remote
        subprocess.run(["git", "init", "--bare", str(self.remote_dir)], check=True, capture_output=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(self.remote_dir), check=True, capture_output=True)

        # 2. Bootstrap work node with initial commit
        self.work_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "WorkSpaceTester"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "work@neko.local"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.remote_dir)], cwd=str(self.work_dir), check=True, capture_output=True)

        # Bootstrap files
        ws_work = self.work_dir / "workspace"
        ws_work.mkdir(parents=True, exist_ok=True)
        (ws_work / "tasks.json").write_text("{}", encoding="utf-8")
        (ws_work / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
        register_merge_driver(self.work_dir)

        subprocess.run(["git", "add", "."], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial bootstrap layout"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(self.work_dir), check=True, capture_output=True)

        # 3. Clone to home node
        subprocess.run(["git", "clone", "-b", "main", str(self.remote_dir), str(self.home_dir)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "HomeSpaceTester"], cwd=str(self.home_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "home@neko.local"], cwd=str(self.home_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=str(self.home_dir), check=True, capture_output=True)
        register_merge_driver(self.home_dir)

    def tearDown(self) -> None:
        robust_rmtree(self.root_temp)

    def test_adversarial_space_in_path_end_to_end_merge(self) -> None:
        """
        Verify that Git invokes the custom merge driver properly during a concurrent
        conflict across repositories with spaces in directory and filenames.
        """
        self.assertTrue(is_merge_driver_registered(self.work_dir))
        self.assertTrue(is_merge_driver_registered(self.home_dir))

        # PC-WORK adds Task A
        engine_work = GitSyncEngine(repo_dir=self.work_dir, branch="main")
        store_work = TaskStore(self.work_dir / "workspace" / "tasks.json")
        store_work.add_task("Task from Work in Space Dir", custom_id="work_task_1", priority=TaskPriority.HIGH)
        res_w = engine_work.sync()
        self.assertTrue(res_w.success, f"Work sync failed: {res_w.error_message}")
        self.assertEqual(res_w.status, SyncStatus.SYNCED)

        # PC-HOME (without pulling) adds Task B
        engine_home = GitSyncEngine(repo_dir=self.home_dir, branch="main")
        store_home = TaskStore(self.home_dir / "workspace" / "tasks.json")
        store_home.add_task("Task from Home in Space Dir", custom_id="home_task_1", priority=TaskPriority.MEDIUM)

        # PC-HOME syncs: this performs git pull --rebase which executes merge driver!
        res_h = engine_home.sync()
        self.assertTrue(res_h.success, f"Home sync with merge driver failed: {res_h.error_message}")
        self.assertEqual(res_h.status, SyncStatus.SYNCED)

        # PC-WORK syncs to receive Home's reconciled commit
        res_w2 = engine_work.sync()
        self.assertTrue(res_w2.success)

        # Verify convergence: both tasks present in both stores with zero conflict markers
        store_work.load()
        store_home.load()

        self.assertIsNotNone(store_work.get("work_task_1"))
        self.assertIsNotNone(store_work.get("home_task_1"))
        self.assertIsNotNone(store_home.get("work_task_1"))
        self.assertIsNotNone(store_home.get("home_task_1"))

        content_w = (self.work_dir / "workspace" / "tasks.json").read_text(encoding="utf-8")
        content_h = (self.home_dir / "workspace" / "tasks.json").read_text(encoding="utf-8")
        self.assertNotIn("<<<<<<<", content_w)
        self.assertNotIn("<<<<<<<", content_h)
        self.assertEqual(json.loads(content_w), json.loads(content_h))


class TestAdversarialOfflineTimeout(unittest.TestCase):
    """
    Adversarial Challenge 2:
    Verify that an unreachable / blackhole remote drops out within network_timeout (<= 10s)
    returning SyncStatus.OFFLINE without hanging or raising unhandled exceptions, and
    restoration recovers cleanly.
    """

    def setUp(self) -> None:
        self.root_temp = Path(tempfile.mkdtemp(prefix="neko_offline_adv_"))
        self.remote_dir = self.root_temp / "remote.git"
        self.work_dir = self.root_temp / "work_repo"

        subprocess.run(["git", "init", "--bare", str(self.remote_dir)], check=True, capture_output=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(self.remote_dir), check=True, capture_output=True)

        self.work_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "OfflineTester"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "offline@neko.local"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.remote_dir)], cwd=str(self.work_dir), check=True, capture_output=True)

        ws = self.work_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "tasks.json").write_text("{}", encoding="utf-8")
        register_merge_driver(self.work_dir)

        subprocess.run(["git", "add", "."], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.work_dir), check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(self.work_dir), check=True, capture_output=True)

    def tearDown(self) -> None:
        robust_rmtree(self.root_temp)

    def test_adversarial_offline_blackhole_timeout_and_recovery(self) -> None:
        """
        Sets remote to a non-routable IP (192.0.2.1), triggers sync with network_timeout=3.0s,
        verifies it aborts within ~4s with SyncStatus.OFFLINE, and restores cleanly.
        """
        # Test RFC 5737 TEST-NET-1 non-routable IP
        blackhole_remote = "http://192.0.2.1:58999/repo.git"
        subprocess.run(["git", "remote", "set-url", "origin", blackhole_remote], cwd=str(self.work_dir), check=True, capture_output=True)

        engine = GitSyncEngine(repo_dir=self.work_dir, branch="main", network_timeout=3.0)
        store = TaskStore(self.work_dir / "workspace" / "tasks.json")
        store.add_task("Offline Queued Task", custom_id="off_adv_1")

        start_t = time.time()
        res = engine.sync()
        elapsed = time.time() - start_t

        # Must fail within ~4.5 seconds (timeout was 3.0s), NOT hang indefinitely
        self.assertLess(elapsed, 6.0, f"Sync took {elapsed}s, exceeded bounded timeout!")
        self.assertFalse(res.success)
        self.assertEqual(res.status, SyncStatus.OFFLINE)
        self.assertTrue(res.committed)
        self.assertIsNotNone(res.commit_sha)

        # Verify DAG retained the commit locally
        code, out, _ = engine._run_git(["log", "-1", "--pretty=%B"])
        self.assertEqual(code, 0)
        self.assertIn("neko auto-sync", out)

        # Restore valid remote URL
        subprocess.run(["git", "remote", "set-url", "origin", str(self.remote_dir)], cwd=str(self.work_dir), check=True, capture_output=True)

        # Re-sync
        res_recovered = engine.sync()
        self.assertTrue(res_recovered.success, f"Recovery sync failed: {res_recovered.error_message}")
        self.assertEqual(res_recovered.status, SyncStatus.SYNCED)
        self.assertTrue(res_recovered.pushed)


class TestAdversarialPartitionedTimeLogsCommutativity(unittest.TestCase):
    """
    Adversarial Challenge 3:
    Commutativity and underscore resilience for partitioned multi-host time logs.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_adv_timelogs_"))

    def tearDown(self) -> None:
        robust_rmtree(self.temp_dir)

    def test_partitioned_time_logs_commutativity_and_complex_hostnames(self) -> None:
        """
        Verify that:
        1. Hostnames containing multiple underscores (e.g., WORK_DESKTOP_GPU_01) are correctly parsed.
        2. Merging logs in Order 1 (Node A then B then C) yields the exact same aggregated metrics
           as Order 2 (Node C then B then A).
        """
        dir1 = self.temp_dir / "logs_order1"
        dir2 = self.temp_dir / "logs_order2"
        dir1.mkdir()
        dir2.mkdir()

        hosts = ["WORK_DESKTOP_GPU_01", "HOME_LAPTOP_RTX_3080", "OFFICE_MAC_M2"]
        dates = ["2026-10-21", "2026-10-22", "2026-10-23"]

        entries_by_host = {
            "WORK_DESKTOP_GPU_01": [
                {"timestamp": "2026-10-21T10:00:00Z", "start_time": "2026-10-21T09:00:00Z", "end_time": "2026-10-21T10:00:00Z", "app": "UnrealEditor.exe", "title": "Map1", "project": "GameDev", "duration_sec": 3600.0, "category": "creative", "hostname": "WORK_DESKTOP_GPU_01"},
                {"timestamp": "2026-10-22T14:00:00Z", "start_time": "2026-10-22T13:00:00Z", "end_time": "2026-10-22T14:00:00Z", "app": "3dsmax.exe", "title": "Building.max", "project": "ArchViz", "duration_sec": 3600.0, "category": "creative", "hostname": "WORK_DESKTOP_GPU_01"},
            ],
            "HOME_LAPTOP_RTX_3080": [
                {"timestamp": "2026-10-22T18:00:00Z", "start_time": "2026-10-22T17:00:00Z", "end_time": "2026-10-22T18:00:00Z", "app": "blender.exe", "title": "Props.blend", "project": "GameDev", "duration_sec": 1800.0, "category": "creative", "hostname": "HOME_LAPTOP_RTX_3080"},
                {"timestamp": "2026-10-23T20:00:00Z", "start_time": "2026-10-23T19:30:00Z", "end_time": "2026-10-23T20:00:00Z", "app": "Photoshop.exe", "title": "Textures.psd", "project": "GameDev", "duration_sec": 1800.0, "category": "creative", "hostname": "HOME_LAPTOP_RTX_3080"},
            ],
            "OFFICE_MAC_M2": [
                {"timestamp": "2026-10-23T11:00:00Z", "start_time": "2026-10-23T10:00:00Z", "end_time": "2026-10-23T11:00:00Z", "app": "Code.exe", "title": "neko_bot", "project": "Tooling", "duration_sec": 3600.0, "category": "development", "hostname": "OFFICE_MAC_M2"},
            ]
        }

        # Write in Order 1
        for h in hosts:
            for d in dates:
                records = [e for e in entries_by_host[h] if e["timestamp"].startswith(d)]
                if records:
                    f = dir1 / f"{h}_{d}.jsonl"
                    with open(f, "w", encoding="utf-8") as fp:
                        for r in records:
                            fp.write(json.dumps(r) + "\n")

        # Write in Reverse Order 2
        for h in reversed(hosts):
            for d in reversed(dates):
                records = [e for e in entries_by_host[h] if e["timestamp"].startswith(d)]
                if records:
                    f = dir2 / f"{h}_{d}.jsonl"
                    with open(f, "w", encoding="utf-8") as fp:
                        for r in records:
                            fp.write(json.dumps(r) + "\n")

        agg1 = TimeAggregator(dir1)
        rep1 = agg1.aggregate(start_date="2026-10-21", end_date="2026-10-23")

        agg2 = TimeAggregator(dir2)
        rep2 = agg2.aggregate(start_date="2026-10-21", end_date="2026-10-23")

        # Commutativity check
        self.assertEqual(rep1.total_seconds, rep2.total_seconds)
        self.assertEqual(rep1.total_seconds, 14400.0)  # 3600 + 3600 + 1800 + 1800 + 3600 = 14400s (4.0h)
        self.assertEqual(rep1.total_human, "4h 00m")
        self.assertEqual(rep1.by_hostname, rep2.by_hostname)
        self.assertEqual(rep1.by_project, rep2.by_project)
        self.assertEqual(rep1.by_app, rep2.by_app)
        self.assertEqual(sorted(rep1.machines), sorted(hosts))


class TestAdversarialCLISyncSubcommands(unittest.TestCase):
    """
    Adversarial Challenge 4:
    CLI sync robustness against daemon HTTP 500 error, malformed JSON, and paths with spaces.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_cli_adv_"))
        self.repo_dir = self.temp_dir / "repo with spaces"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "tester@neko.local"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=self.repo_dir, capture_output=True, check=True)

        ws = self.repo_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "tasks.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, capture_output=True, check=True)

    def tearDown(self) -> None:
        robust_rmtree(self.temp_dir)

    def test_cli_sync_daemon_http_500_fallback_to_in_process(self) -> None:
        """
        When daemon responds with HTTP 500 Internal Server Error,
        CLI must catch exception and seamlessly fall back to in-process sync.
        """
        err_resp = urllib.error.HTTPError(
            url="http://127.0.0.1:4040/command",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b"Internal daemon crash"),
        )
        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(True, 9999)):
            with patch("urllib.request.urlopen", side_effect=err_resp):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["sync", "--workspace", str(self.repo_dir)])
                self.assertEqual(ret, 0, "CLI must fall back to in-process execution on HTTP 500")
                self.assertIn("[Neko Sync]", buf.getvalue())

    def test_cli_sync_daemon_malformed_json_fallback_to_in_process(self) -> None:
        """
        When daemon returns non-JSON or truncated payload,
        CLI must catch JSONDecodeError and fall back to in-process sync.
        """
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<<<NON JSON ERROR HTML>>>"
        mock_resp.__enter__.return_value = mock_resp

        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(True, 9999)):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["sync", "--workspace", str(self.repo_dir)])
                self.assertEqual(ret, 0)
                self.assertIn("[Neko Sync]", buf.getvalue())

    def test_cli_sync_status_in_empty_git_repo(self) -> None:
        """
        Verifies `neko sync --status` in a brand-new Git repository with zero commits
        does not crash or throw branch rev-parse errors.
        """
        empty_repo = self.temp_dir / "empty_repo"
        empty_repo.mkdir()
        subprocess.run(["git", "init"], cwd=empty_repo, capture_output=True, check=True)

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--status", "--workspace", str(empty_repo)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn("Git Installed : Yes", out)
        self.assertIn("Is Repository : Yes", out)

        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            ret_json = main(["sync", "--status", "--json", "--workspace", str(empty_repo)])
        self.assertEqual(ret_json, 0)
        data = json.loads(buf_json.getvalue().strip())
        self.assertTrue(data["is_repo"])
        self.assertFalse(data["has_commits"])


if __name__ == "__main__":
    unittest.main()
