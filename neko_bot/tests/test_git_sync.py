"""
tests/test_git_sync.py - Acceptance Criterion AC5 & Milestone 5 Test Suite.

Comprehensive E2E and Unit Test Suite verifying:
1. Feature F23: Strictly Scoped Git Auto-Sync (workspace isolation, repo detection).
2. Feature F24: Disjoint Multi-PC Time Logs (0.00% merge conflicts between PC-WORK and PC-HOME).
3. Feature F25: Custom Git Merge Driver & CRDT LWW-Element-Set Reconciliation (3-way merge, tombstones, TASKS.md mirror).
4. Feature F26: Offline Git Resilience (remote unreachable, timeout, local commit queueing).
5. Tier 4 Scenario 5: Multi-PC Work-to-Home Transition (Unreal on PC-WORK -> Blender on PC-HOME -> clean convergence).
"""

from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
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
from typing import Any, Dict, Generator, List, Optional
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

from neko_bot.cli import build_parser, cmd_sync, main
from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.daemon import DaemonController
from neko_bot.task.models import Task, TaskPriority, TaskState, now_utc_iso
from neko_bot.task.store import TaskStore
from neko_bot.tracker.aggregator import TimeAggregator
from neko_bot.tracker.logger import TimeLogger
from neko_bot.sync.engine import GitSyncEngine, SyncResult, SyncStatus
from neko_bot.sync.merge_driver import (
    auto_resolve_rebase_conflicts,
    is_merge_driver_registered,
    merge_driver_main,
    reconcile_3way,
    register_merge_driver,
)


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


@dataclass
class NodeEnvironment:
    """Represents an isolated PC node in the hermetic multi-PC cluster."""
    name: str  # "PC-WORK" or "PC-HOME"
    repo_dir: Path
    config: Config
    task_store: TaskStore
    time_logger: TimeLogger
    sync_engine: Any

    def run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Runs a git command inside this node's repository directory."""
        env = dict(os.environ)
        project_root = Path(__file__).resolve().parent.parent
        env["PYTHONPATH"] = str(project_root)
        env["COMPUTERNAME"] = self.name
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            env=env,
            check=check,
        )

    def write_time_record(
        self,
        app: str,
        project: str,
        duration_sec: float,
        category: str = "creative",
        target_date: Optional[str] = None,
    ) -> Path:
        """Appends a time record partitioned by this node's hostname."""
        date_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.time_logger.log_record(
            start_time=f"{date_str}T10:00:00Z",
            end_time=f"{date_str}T12:00:00Z",
            app=app,
            title=f"Working on {project}",
            project=project,
            duration_sec=duration_sec,
            category=category,
            timestamp=f"{date_str}T12:00:00Z",
        )

    def trigger_sync(self) -> Any:
        """Executes sync via GitSyncEngine or fallback git CLI sequence."""
        if self.sync_engine is not None:
            res = self.sync_engine.sync()
            self.task_store.load()
            return res

        # Fallback git CLI sync simulation if GitSyncEngine not instantiated
        self.run_git("add", "-A", "--", "workspace/tasks.json", "workspace/TASKS.md", "workspace/time_logs")
        status = self.run_git("status", "--porcelain")
        if status.stdout.strip():
            self.run_git("commit", "-m", f"neko auto-sync: {self.name}")

        pull_res = self.run_git("pull", "--rebase", "origin", "main", check=False)
        if pull_res.returncode != 0:
            if auto_resolve_rebase_conflicts is not None:
                auto_resolve_rebase_conflicts(self.config.workspace_dir)
                self.run_git("add", "workspace/tasks.json")
                self.run_git("rebase", "--continue", check=True)
            else:
                raise RuntimeError(f"Git rebase conflict on {self.name}: {pull_res.stderr}")

        self.run_git("push", "origin", "main", check=True)
        self.task_store.load()
        return None


@dataclass
class GitCluster:
    root_dir: Path
    remote_dir: Path
    work_node: NodeEnvironment
    home_node: NodeEnvironment


@contextmanager
def hermetic_git_cluster() -> Generator[GitCluster, None, None]:
    """
    Context manager creating a 100% hermetic multi-PC test cluster:
    - Bare local remote: `remote.git`
    - Node 1 clone: `PC-WORK`
    - Node 2 clone: `PC-HOME`
    - Fully initialized `workspace/tasks.json` and `workspace/time_logs`
    - Configured local Git identity and custom merge driver registration
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="neko_multi_pc_cluster_"))
    try:
        remote_dir = temp_dir / "remote.git"
        bootstrap_dir = temp_dir / "bootstrap"
        work_dir = temp_dir / "PC-WORK"
        home_dir = temp_dir / "PC-HOME"

        # 1. Initialize bare remote repository
        subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

        # 2. Bootstrap initial commit with main branch
        subprocess.run(["git", "clone", str(remote_dir), str(bootstrap_dir)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Bootstrap"], cwd=bootstrap_dir, check=True)
        subprocess.run(["git", "config", "user.email", "bootstrap@neko.local"], cwd=bootstrap_dir, check=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=bootstrap_dir, check=True)

        # Create initial workspace layout
        ws_dir = bootstrap_dir / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "time_logs").mkdir(parents=True, exist_ok=True)
        with open(ws_dir / "tasks.json", "w", encoding="utf-8") as f:
            json.dump({}, f)
        with open(ws_dir / "TASKS.md", "w", encoding="utf-8") as f:
            f.write("# Tasks\n\n> Auto-generated by neko_bot\n")

        # Configure .gitattributes for custom merge driver
        with open(bootstrap_dir / ".gitattributes", "w", encoding="utf-8") as f:
            f.write("workspace/tasks.json merge=neko-task-merge\n")
            f.write("workspace/TASKS.md merge=neko-task-merge\n")

        subprocess.run(["git", "add", "."], cwd=bootstrap_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial bootstrap layout"], cwd=bootstrap_dir, check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=bootstrap_dir, check=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(remote_dir), check=True)

        # 3. Helper to setup a cloned node
        def setup_node(node_name: str, node_dir: Path) -> NodeEnvironment:
            subprocess.run(["git", "clone", "-b", "main", str(remote_dir), str(node_dir)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", node_name], cwd=node_dir, check=True)
            subprocess.run(["git", "config", "user.email", f"{node_name.lower()}@neko.local"], cwd=node_dir, check=True)
            subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=node_dir, check=True)

            # Register custom merge driver in local .git/config
            driver_cmd = f'"{sys.executable}" -m neko_bot.sync.merge_driver "%O" "%A" "%B" "%P"'
            subprocess.run(["git", "config", "merge.neko-task-merge.name", "Neko Bot CRDT Task Merge Driver"], cwd=node_dir, check=True)
            subprocess.run(["git", "config", "merge.neko-task-merge.driver", driver_cmd], cwd=node_dir, check=True)

            # Initialize node config and directories
            cfg = Config.load(root_dir=node_dir)
            cfg.workspace_dir = node_dir / "workspace"
            cfg.neko_dir = node_dir / ".neko"
            cfg.ensure_dirs()

            task_store = TaskStore(tasks_file_or_dir=cfg.workspace_dir, config=cfg)
            time_logger = TimeLogger(logs_dir=cfg.time_logs_dir, hostname=node_name)

            engine = GitSyncEngine(repo_dir=node_dir, config=cfg, task_store=task_store)

            return NodeEnvironment(
                name=node_name,
                repo_dir=node_dir,
                config=cfg,
                task_store=task_store,
                time_logger=time_logger,
                sync_engine=engine,
            )

        work_node = setup_node("PC-WORK", work_dir)
        home_node = setup_node("PC-HOME", home_dir)

        yield GitCluster(
            root_dir=temp_dir,
            remote_dir=remote_dir,
            work_node=work_node,
            home_node=home_node,
        )
    finally:
        robust_rmtree(temp_dir)


class TestHermeticGitSyncFixture(unittest.TestCase):
    """Verifies hermetic cluster initialization, scoped git tracking, and repo detection."""

    def test_01_cluster_initialization(self) -> None:
        """Verifies bare remote and node clones are created with proper branches and git identities."""
        with hermetic_git_cluster() as cluster:
            self.assertTrue(cluster.remote_dir.exists())
            self.assertTrue(cluster.work_node.repo_dir.exists())
            self.assertTrue(cluster.home_node.repo_dir.exists())

            # Check branches point to main
            work_branch = cluster.work_node.run_git("branch", "--show-current").stdout.strip()
            home_branch = cluster.home_node.run_git("branch", "--show-current").stdout.strip()
            self.assertEqual(work_branch, "main")
            self.assertEqual(home_branch, "main")

            # Check local git identity isolation
            work_user = cluster.work_node.run_git("config", "user.name").stdout.strip()
            home_user = cluster.home_node.run_git("config", "user.name").stdout.strip()
            self.assertEqual(work_user, "PC-WORK")
            self.assertEqual(home_user, "PC-HOME")

    def test_02_strictly_scoped_git_tracking(self) -> None:
        """Verifies Feature F23: Git sync only stages workspace files, ignoring outside untracked files."""
        with hermetic_git_cluster() as cluster:
            # Create an external untracked file at the root of PC-WORK
            untracked_file = cluster.work_node.repo_dir / "external_asset.bin"
            untracked_file.write_text("untracked binary content", encoding="utf-8")

            # Mutate workspace tasks
            cluster.work_node.task_store.add_task("Workspace Task", project="test")

            # Trigger sync
            cluster.work_node.trigger_sync()

            # Verify external_asset.bin remains untracked on PC-WORK
            status = cluster.work_node.run_git("status", "--porcelain").stdout
            self.assertIn("?? external_asset.bin", status)

            # Sync PC-HOME and verify external_asset.bin was NOT pushed
            cluster.home_node.trigger_sync()
            self.assertFalse((cluster.home_node.repo_dir / "external_asset.bin").exists())

    def test_03_non_git_workspace_graceful_handling(self) -> None:
        """Verifies graceful handling when the workspace is not inside a git repository."""
        temp_dir = Path(tempfile.mkdtemp(prefix="neko_nongit_"))
        try:
            cfg = Config.load(root_dir=temp_dir)
            cfg.ensure_dirs()
            if GitSyncEngine is not None:
                engine = GitSyncEngine(repo_dir=temp_dir, config=cfg)
                self.assertFalse(engine.is_git_repo())
                res = engine.sync()
                self.assertFalse(res.success)
                self.assertIn("not a git repository", (res.error_message or "").lower())
        finally:
            robust_rmtree(temp_dir)


class TestDisjointTimeLogsSync(unittest.TestCase):
    """Verifies Acceptance Criterion AC5 & Feature F24: Machine-partitioned time log sync."""

    def test_04_disjoint_time_logs_concurrent_sync(self) -> None:
        """
        Verifies AC5:
        PC-WORK tracks 2h (7200s) in time_logs/PC-WORK_2026-10-23.jsonl.
        PC-HOME tracks 1.5h (5400s) in time_logs/PC-HOME_2026-10-23.jsonl.
        Both commit and sync. Both files must exist intact in both clones with 0 conflicts.
        """
        with hermetic_git_cluster() as cluster:
            test_date = "2026-10-23"

            # 1. PC-WORK logs 7200s for UnrealEditor.exe
            cluster.work_node.write_time_record(
                app="UnrealEditor.exe",
                project="CyberpunkCity",
                duration_sec=7200.0,
                category="creative",
                target_date=test_date,
            )

            # 2. PC-HOME logs 5400s for blender.exe
            cluster.home_node.write_time_record(
                app="blender.exe",
                project="CyberpunkProps",
                duration_sec=5400.0,
                category="creative",
                target_date=test_date,
            )

            # 3. PC-WORK syncs
            cluster.work_node.trigger_sync()

            # 4. PC-HOME syncs
            cluster.home_node.trigger_sync()

            # 5. PC-WORK syncs again to receive PC-HOME logs
            cluster.work_node.trigger_sync()

            # 6. Assertions on both clones
            for node in (cluster.work_node, cluster.home_node):
                work_log = node.config.time_logs_dir / f"PC-WORK_{test_date}.jsonl"
                home_log = node.config.time_logs_dir / f"PC-HOME_{test_date}.jsonl"

                self.assertTrue(work_log.exists(), f"{work_log.name} missing on {node.name}")
                self.assertTrue(home_log.exists(), f"{home_log.name} missing on {node.name}")

                # Verify records are parsed intact without corruption
                records_work = node.time_logger.read_records(target_date=test_date, hostname="PC-WORK")
                records_home = node.time_logger.read_records(target_date=test_date, hostname="PC-HOME")

                self.assertEqual(len(records_work), 1)
                self.assertEqual(records_work[0]["app"], "UnrealEditor.exe")
                self.assertEqual(records_work[0]["duration_sec"], 7200.0)

                self.assertEqual(len(records_home), 1)
                self.assertEqual(records_home[0]["app"], "blender.exe")
                self.assertEqual(records_home[0]["duration_sec"], 5400.0)

    def test_05_disjoint_time_logs_multiple_dates_and_hosts(self) -> None:
        """Verifies multi-day partitioned logs sync across machines without collisions."""
        with hermetic_git_cluster() as cluster:
            cluster.work_node.write_time_record("Photoshop.exe", "Art", 3600.0, target_date="2026-10-22")
            cluster.work_node.write_time_record("Photoshop.exe", "Art", 1800.0, target_date="2026-10-23")
            cluster.home_node.write_time_record("3dsmax.exe", "Modelling", 4000.0, target_date="2026-10-23")

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            for node in (cluster.work_node, cluster.home_node):
                all_logs = list(node.config.time_logs_dir.glob("*.jsonl"))
                self.assertEqual(len(all_logs), 3)

    def test_06_time_aggregation_after_sync(self) -> None:
        """Verifies TimeAggregator calculates unified metrics combining both synced logs."""
        with hermetic_git_cluster() as cluster:
            test_date = "2026-10-23"
            cluster.work_node.write_time_record("UnrealEditor.exe", "City", 7200.0, target_date=test_date)
            cluster.home_node.write_time_record("blender.exe", "Props", 5400.0, target_date=test_date)

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            # Aggregate today on PC-HOME
            agg_home = TimeAggregator(cluster.home_node.config.time_logs_dir)
            report = agg_home.aggregate(start_date=test_date, end_date=test_date)

            self.assertEqual(report.total_seconds, 12600.0)  # 7200 + 5400 = 3.5h
            self.assertEqual(report.by_project["City"], 7200.0)
            self.assertEqual(report.by_project["Props"], 5400.0)
            self.assertIn("PC-WORK", report.machines)
            self.assertIn("PC-HOME", report.machines)


class TestTaskCRDTReconciliationAndMergeDriver(unittest.TestCase):
    """Verifies Acceptance Criterion AC5 & Feature F25: CRDT LWW-Element-Set 3-way merge."""

    def test_07_merge_driver_cli_3way_reconciliation(self) -> None:
        """Directly verifies merge driver CLI execution (%O %A %B %P) via subprocess."""
        temp_dir = Path(tempfile.mkdtemp(prefix="neko_driver_test_"))
        try:
            base_file = temp_dir / "base.json"
            ours_file = temp_dir / "ours.json"
            theirs_file = temp_dir / "theirs.json"

            # Base version
            t_base = Task.create("Common Task", custom_id="task_1", priority="low").to_dict()
            t_base["updated_at"] = "2026-10-23T10:00:00Z"
            base_file.write_text(json.dumps({"task_1": t_base}), encoding="utf-8")

            # Ours version (edited at 10:05)
            t_ours = dict(t_base)
            t_ours["title"] = "Ours Modified Title"
            t_ours["updated_at"] = "2026-10-23T10:05:00Z"
            t_ours_new = Task.create("Ours New Task", custom_id="task_2").to_dict()
            ours_file.write_text(json.dumps({"task_1": t_ours, "task_2": t_ours_new}), encoding="utf-8")

            # Theirs version (edited at 10:10 - newer!)
            t_theirs = dict(t_base)
            t_theirs["title"] = "Theirs Newer Title"
            t_theirs["updated_at"] = "2026-10-23T10:10:00Z"
            t_theirs_new = Task.create("Theirs New Task", custom_id="task_3").to_dict()
            theirs_file.write_text(json.dumps({"task_1": t_theirs, "task_3": t_theirs_new}), encoding="utf-8")

            # Invoke driver CLI
            env = dict(os.environ)
            project_root = Path(__file__).resolve().parent.parent
            env["PYTHONPATH"] = str(project_root)

            cmd = [
                sys.executable,
                "-m",
                "neko_bot.sync.merge_driver",
                str(base_file),
                str(ours_file),
                str(theirs_file),
                "workspace/tasks.json",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0, f"Driver failed: {res.stderr}")

            # Verify resolved content in %A (ours_file)
            merged = json.loads(ours_file.read_text(encoding="utf-8"))
            self.assertIn("task_1", merged)
            self.assertIn("task_2", merged)
            self.assertIn("task_3", merged)
            # Newer write won
            self.assertEqual(merged["task_1"]["title"], "Theirs Newer Title")
        finally:
            robust_rmtree(temp_dir)

    def test_08_concurrent_task_additions_distinct(self) -> None:
        """
        Verifies AC5:
        PC-WORK adds Task A (#cyberpunk !alta).
        PC-HOME adds Task B (#blender !media).
        Both sync. Both clones must converge to identical tasks.json and TASKS.md with 0 conflicts.
        """
        with hermetic_git_cluster() as cluster:
            t_a = cluster.work_node.task_store.add_task("Task A", project="cyberpunk", priority="alta")
            t_b = cluster.home_node.task_store.add_task("Task B", project="blender", priority="media")

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            # Reload stores
            cluster.work_node.task_store.load()
            cluster.home_node.task_store.load()

            # 1. Zero conflict markers in tasks.json or TASKS.md
            for node in (cluster.work_node, cluster.home_node):
                tasks_raw = node.config.tasks_file.read_text(encoding="utf-8")
                md_raw = node.config.tasks_md_file.read_text(encoding="utf-8")
                self.assertNotIn("<<<<<<<", tasks_raw)
                self.assertNotIn("<<<<<<<", md_raw)

            # 2. Both clones converge to identical tasks.json dictionary
            dict_work = cluster.work_node.task_store.get_all_tasks_dict(include_deleted=True)
            dict_home = cluster.home_node.task_store.get_all_tasks_dict(include_deleted=True)
            self.assertEqual(dict_work, dict_home)

            # 3. Both clones have identical TASKS.md Markdown mirrors
            md_work = cluster.work_node.config.tasks_md_file.read_text(encoding="utf-8")
            md_home = cluster.home_node.config.tasks_md_file.read_text(encoding="utf-8")
            self.assertEqual(md_work, md_home)
            self.assertIn("Task A", md_work)
            self.assertIn("Task B", md_work)

    def test_09_concurrent_task_same_task_mutation_lww(self) -> None:
        """Verifies concurrent mutations of the same task converge via LWW timestamp."""
        with hermetic_git_cluster() as cluster:
            # Common initial task
            initial_task = cluster.work_node.task_store.add_task("Initial Title", custom_id="task_shared")
            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()

            # PC-WORK updates at T1
            t_work = cluster.work_node.task_store.get("task_shared")
            assert t_work is not None
            t_work.title = "Work Title"
            t_work.updated_at = "2026-10-23T12:00:00Z"
            cluster.work_node.task_store.save()

            # PC-HOME updates at T2 (T2 > T1)
            t_home = cluster.home_node.task_store.get("task_shared")
            assert t_home is not None
            t_home.title = "Home Newer Title"
            t_home.updated_at = "2026-10-23T12:05:00Z"
            cluster.home_node.task_store.save()

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            cluster.work_node.task_store.load()
            cluster.home_node.task_store.load()

            self.assertEqual(cluster.work_node.task_store.get("task_shared").title, "Home Newer Title")
            self.assertEqual(cluster.home_node.task_store.get("task_shared").title, "Home Newer Title")

    def test_10_concurrent_task_edit_and_completion_convergence(self) -> None:
        """Verifies concurrent edit vs completion converges deterministically."""
        with hermetic_git_cluster() as cluster:
            cluster.work_node.task_store.add_task("Bake Maps", custom_id="task_bake")
            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()

            # PC-WORK marks done at 12:00:00Z
            t_w = cluster.work_node.task_store.get("task_bake")
            assert t_w is not None
            t_w.mark_done()
            t_w.updated_at = "2026-10-23T12:00:00Z"
            cluster.work_node.task_store.save()

            # PC-HOME updates title & deadline at 12:05:00Z
            t_h = cluster.home_node.task_store.get("task_bake")
            assert t_h is not None
            t_h.title = "Bake Maps & Optimize"
            t_h.due_date = "2026-10-25"
            t_h.updated_at = "2026-10-23T12:05:00Z"
            cluster.home_node.task_store.save()

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            cluster.work_node.task_store.load()
            cluster.home_node.task_store.load()

            # Newer write (12:05:00Z) wins
            self.assertEqual(cluster.work_node.task_store.get("task_bake").title, "Bake Maps & Optimize")
            self.assertEqual(cluster.home_node.task_store.get("task_bake").title, "Bake Maps & Optimize")

    def test_11_tombstone_propagation_clean_deletion(self) -> None:
        """Verifies AC5: Tombstone propagation deletes task across both clones."""
        with hermetic_git_cluster() as cluster:
            t1 = cluster.work_node.task_store.add_task("Task To Delete", custom_id="del_me")
            t2 = cluster.work_node.task_store.add_task("Task To Keep", custom_id="keep_me")
            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()

            # PC-WORK soft-deletes task (tombstone)
            cluster.work_node.task_store.delete("del_me")

            # PC-HOME concurrently adds another task
            cluster.home_node.task_store.add_task("New Home Task", custom_id="home_new")

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            cluster.work_node.task_store.load()
            cluster.home_node.task_store.load()

            for node in (cluster.work_node, cluster.home_node):
                # Deleted task is tombstoned
                t = node.task_store.get("del_me")
                self.assertIsNotNone(t)
                self.assertTrue(t.deleted)

                # Excluded from active tasks
                active_ids = [task.id for task in node.task_store.get_active_tasks()]
                self.assertNotIn("del_me", active_ids)
                self.assertIn("keep_me", active_ids)
                self.assertIn("home_new", active_ids)

                # Excluded from TASKS.md Markdown mirror
                md_content = node.config.tasks_md_file.read_text(encoding="utf-8")
                self.assertNotIn("Task To Delete", md_content)
                self.assertIn("Task To Keep", md_content)
                self.assertIn("New Home Task", md_content)

    def test_12_tombstone_beats_edit_on_identical_timestamp(self) -> None:
        """Verifies CRDT tie-breaker: on equal timestamps, tombstone wins."""
        with hermetic_git_cluster() as cluster:
            cluster.work_node.task_store.add_task("Tie Task", custom_id="tie_task")
            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()

            equal_ts = "2026-10-23T15:00:00Z"

            # PC-WORK marks deleted with equal_ts
            t_w = cluster.work_node.task_store.get("tie_task")
            assert t_w is not None
            t_w.deleted = True
            t_w.updated_at = equal_ts
            cluster.work_node.task_store.save()

            # PC-HOME edits title with exact equal_ts
            t_h = cluster.home_node.task_store.get("tie_task")
            assert t_h is not None
            t_h.title = "Modified on Home"
            t_h.deleted = False
            t_h.updated_at = equal_ts
            cluster.home_node.task_store.save()

            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.work_node.trigger_sync()

            cluster.work_node.task_store.load()
            cluster.home_node.task_store.load()

            # Tombstone won the tie
            self.assertTrue(cluster.work_node.task_store.get("tie_task").deleted)
            self.assertTrue(cluster.home_node.task_store.get("tie_task").deleted)

    def test_13_markdown_mirror_clean_regeneration(self) -> None:
        """Verifies TASKS.md mirror is automatically regenerated and cleanly formatted."""
        with hermetic_git_cluster() as cluster:
            cluster.work_node.task_store.add_task("Unreal Task", project="unreal", priority="high")
            cluster.work_node.task_store.add_task("General Task", priority="low")
            cluster.work_node.trigger_sync()

            cluster.home_node.trigger_sync()
            md = cluster.home_node.config.tasks_md_file.read_text(encoding="utf-8")

            self.assertIn("# Tasks", md)
            self.assertIn("## 🚀 unreal", md)
            self.assertIn("## 📁 General", md)
            self.assertIn("`[HIGH]`", md)
            self.assertIn("`[LOW]`", md)


class TestOfflineGitResilience(unittest.TestCase):
    """Verifies Acceptance Criterion AC5 & Feature F26: Offline remote resilience."""

    def test_14_offline_remote_graceful_handling(self) -> None:
        """Verifies sync engine handles unreachable remote without unhandled crashes."""
        with hermetic_git_cluster() as cluster:
            # Point PC-WORK remote to non-existent directory
            invalid_remote = cluster.root_dir / "non_existent_remote.git"
            cluster.work_node.run_git("remote", "set-url", "origin", str(invalid_remote))

            # Make local changes
            cluster.work_node.task_store.add_task("Offline Task", custom_id="off_1")
            cluster.work_node.write_time_record("Photoshop.exe", "OfflineArt", 1200.0)

            if cluster.work_node.sync_engine is not None:
                res = cluster.work_node.sync_engine.sync()
                # Must return failure without crashing
                self.assertFalse(res.success)
                self.assertTrue(res.error_message is not None)

            # Local commit was created and local branch retains changes
            cluster.work_node.task_store.load()
            self.assertIsNotNone(cluster.work_node.task_store.get("off_1"))

    def test_15_recovery_and_push_after_reconnection(self) -> None:
        """Verifies local queued commits push cleanly after remote connectivity is restored."""
        with hermetic_git_cluster() as cluster:
            invalid_remote = cluster.root_dir / "offline_remote.git"
            valid_remote = cluster.remote_dir

            # Disconnect
            cluster.work_node.run_git("remote", "set-url", "origin", str(invalid_remote))
            cluster.work_node.task_store.add_task("Queued Task", custom_id="queued_1")

            if cluster.work_node.sync_engine is not None:
                cluster.work_node.sync_engine.sync()
            else:
                cluster.work_node.run_git("add", "-A", "--", "workspace/tasks.json", "workspace/TASKS.md", "workspace/time_logs")
                cluster.work_node.run_git("commit", "-m", "queued offline change", check=False)

            # Restore connectivity
            cluster.work_node.run_git("remote", "set-url", "origin", str(valid_remote))

            # Re-sync
            cluster.work_node.trigger_sync()

            # PC-HOME receives queued task
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()
            self.assertIsNotNone(cluster.home_node.task_store.get("queued_1"))


class TestTier4Scenario5WorkToHomeTransition(unittest.TestCase):
    """Verifies Tier 4 Scenario 5: Multi-PC Work-to-Home Transition real-world workflow."""

    def test_16_scenario_5_work_to_home_full_day_lifecycle(self) -> None:
        """
        Executes complete Tier 4 Scenario 5 narrative:
        1. User starts day at office on PC-WORK:
           - Tracks 2h 15m in Unreal (UnrealEditor.exe - CyberpunkCity).
           - Adds Task 1: Refactor shader graph #unreal !alta.
           - Adds Task 2: Compile lighting #unreal !media.
           - Completes Task 0 (Review PR #team).
           - Auto-syncs to bare Git remote.
        2. User commutes home, opens PC-HOME:
           - Daemon runs initial sync on startup.
           - Tracks 1h 45m in Blender (blender.exe - CyberpunkProps.blend).
           - Adds Task 3: UV unwrap drone #blender !alta.
           - Updates Task 1: Refactor shader graph and optimize VRAM, due 2026-10-24 18:00.
           - Deletes Task 2 (lighting compile not needed).
           - Auto-syncs to bare Git remote.
        3. Next morning, user returns to PC-WORK and syncs.
        4. Verifies 100% convergence across tasks, markdown mirrors, and time logs.
        """
        with hermetic_git_cluster() as cluster:
            test_date = "2026-10-23"

            # Setup initial state with Task 0
            t0 = cluster.work_node.task_store.add_task("Review PR", project="team", priority="media", custom_id="t0")
            cluster.work_node.trigger_sync()
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()

            # =================================================================
            # Phase 1: PC-WORK (Office Session)
            # =================================================================
            # Track 2h 15m (8100s) in Unreal
            cluster.work_node.write_time_record(
                app="UnrealEditor.exe",
                project="CyberpunkCity",
                duration_sec=8100.0,
                category="creative",
                target_date=test_date,
            )

            # Add Task 1 & Task 2
            cluster.work_node.task_store.add_task(
                "Refactor shader graph",
                project="unreal",
                priority="alta",
                custom_id="t1",
            )
            cluster.work_node.task_store.add_task(
                "Compile lighting",
                project="unreal",
                priority="media",
                custom_id="t2",
            )

            # Mark Task 0 completed
            cluster.work_node.task_store.mark_done("t0")

            # PC-WORK syncs before leaving office
            cluster.work_node.trigger_sync()

            # =================================================================
            # Phase 2: PC-HOME (Evening Session)
            # =================================================================
            # Daemon startup sync
            cluster.home_node.trigger_sync()
            cluster.home_node.task_store.load()

            # Verify initial sync received Phase 1 work
            self.assertTrue(cluster.home_node.task_store.get("t0").is_done())
            self.assertIsNotNone(cluster.home_node.task_store.get("t1"))
            self.assertIsNotNone(cluster.home_node.task_store.get("t2"))

            # Track 1h 45m (6300s) in Blender
            cluster.home_node.write_time_record(
                app="blender.exe",
                project="CyberpunkProps",
                duration_sec=6300.0,
                category="creative",
                target_date=test_date,
            )

            # Add Task 3
            cluster.home_node.task_store.add_task(
                "UV unwrap drone",
                project="blender",
                priority="alta",
                custom_id="t3",
            )

            # Update Task 1 (title & due date)
            cluster.home_node.task_store.update(
                "t1",
                title="Refactor shader graph and optimize VRAM",
                due_date="2026-10-24 18:00",
            )

            # Delete Task 2 (tombstone)
            cluster.home_node.task_store.delete("t2")

            # PC-HOME syncs
            cluster.home_node.trigger_sync()

            # =================================================================
            # Phase 3: PC-WORK (Next Morning)
            # =================================================================
            cluster.work_node.trigger_sync()
            cluster.work_node.task_store.load()

            # =================================================================
            # Phase 4: Full Multi-PC Verification Assertions
            # =================================================================
            # 1. Zero Git conflict markers in any file across both clones
            for node in (cluster.work_node, cluster.home_node):
                tasks_text = node.config.tasks_file.read_text(encoding="utf-8")
                md_text = node.config.tasks_md_file.read_text(encoding="utf-8")
                self.assertNotIn("<<<<<<<", tasks_text, f"Conflict markers in tasks.json on {node.name}")
                self.assertNotIn("<<<<<<<", md_text, f"Conflict markers in TASKS.md on {node.name}")

            # 2. Both clones converge to identical tasks.json dictionary
            tasks_work = cluster.work_node.task_store.get_all_tasks_dict(include_deleted=True)
            tasks_home = cluster.home_node.task_store.get_all_tasks_dict(include_deleted=True)
            self.assertEqual(tasks_work, tasks_home)

            # 3. Both clones have identical TASKS.md mirror
            self.assertEqual(
                cluster.work_node.config.tasks_md_file.read_text(encoding="utf-8"),
                cluster.home_node.config.tasks_md_file.read_text(encoding="utf-8"),
            )

            # 4. Task state convergence:
            # - Task 0 is completed
            self.assertTrue(cluster.work_node.task_store.get("t0").is_done())
            self.assertTrue(cluster.home_node.task_store.get("t0").is_done())

            # - Task 1 has updated title and due date
            t1_w = cluster.work_node.task_store.get("t1")
            self.assertEqual(t1_w.title, "Refactor shader graph and optimize VRAM")
            self.assertEqual(t1_w.due_date, "2026-10-24 18:00")

            # - Task 2 is tombstoned and not in active tasks
            t2_w = cluster.work_node.task_store.get("t2")
            self.assertTrue(t2_w.deleted)
            self.assertNotIn("t2", [t.id for t in cluster.work_node.task_store.get_active_tasks()])

            # - Task 3 is active and present
            t3_w = cluster.work_node.task_store.get("t3")
            self.assertIsNotNone(t3_w)
            self.assertEqual(t3_w.project, "blender")

            # 5. Intact per-machine time logs
            for node in (cluster.work_node, cluster.home_node):
                w_log = node.config.time_logs_dir / f"PC-WORK_{test_date}.jsonl"
                h_log = node.config.time_logs_dir / f"PC-HOME_{test_date}.jsonl"
                self.assertTrue(w_log.exists())
                self.assertTrue(h_log.exists())

            # 6. Cluster aggregated time report: 8100s (2h15m) + 6300s (1h45m) = 14400s (4.0h)
            agg = TimeAggregator(cluster.work_node.config.time_logs_dir)
            report = agg.aggregate(start_date=test_date, end_date=test_date)
            self.assertEqual(report.total_seconds, 14400.0)
            self.assertEqual(report.total_human, "4h 00m")
            self.assertEqual(report.by_app["UnrealEditor.exe"], 8100.0)
            self.assertEqual(report.by_app["blender.exe"], 6300.0)
            self.assertIn("PC-WORK", report.machines)
            self.assertIn("PC-HOME", report.machines)



class TestMergeDriverQuotingAndSyncStatus(unittest.TestCase):
    """Verifies Milestone 5 Iteration 2 fixes: merge driver quoting and SyncStatus.UP_TO_DATE."""

    def test_17_merge_driver_registration_has_quoted_placeholders(self) -> None:
        """Verifies register_merge_driver configures double quotes around %O %A %B %P."""
        temp_dir = Path(tempfile.mkdtemp(prefix="neko_quote_test_"))
        try:
            subprocess.run(["git", "init", str(temp_dir)], check=True, capture_output=True)
            ok = register_merge_driver(temp_dir)
            self.assertTrue(ok)
            self.assertTrue(is_merge_driver_registered(temp_dir))

            proc = subprocess.run(
                ["git", "config", "--get", "merge.neko-task-merge.driver"],
                cwd=str(temp_dir),
                capture_output=True,
                text=True,
                check=True,
            )
            driver_val = proc.stdout.strip()
            self.assertIn('"%O"', driver_val)
            self.assertIn('"%A"', driver_val)
            self.assertIn('"%B"', driver_val)
            self.assertIn('"%P"', driver_val)
        finally:
            robust_rmtree(temp_dir)

    def test_18_merge_driver_invocation_with_spaces_in_paths(self) -> None:
        """Verifies driver CLI executes without error when temporary directories contain spaces."""
        temp_dir = Path(tempfile.mkdtemp(prefix="neko space test_"))
        try:
            base = temp_dir / "base task.json"
            ours = temp_dir / "ours task.json"
            theirs = temp_dir / "theirs task.json"

            t0 = Task.create("Space Test", custom_id="s1").to_dict()
            base.write_text(json.dumps({"s1": t0}), encoding="utf-8")
            ours.write_text(json.dumps({"s1": t0}), encoding="utf-8")
            theirs.write_text(json.dumps({"s1": t0}), encoding="utf-8")

            ret = merge_driver_main(base, ours, theirs, "workspace/tasks.json")
            self.assertEqual(ret, 0)
        finally:
            robust_rmtree(temp_dir)

    def test_19_sync_up_to_date_reachable_on_clean_workspace(self) -> None:
        """Verifies that running sync on an already-synced repository returns SyncStatus.UP_TO_DATE."""
        with hermetic_git_cluster() as cluster:
            # First sync: PC-WORK adds task, syncs -> status == SYNCED
            cluster.work_node.task_store.add_task("Initial Task", custom_id="init_1")
            res1 = cluster.work_node.trigger_sync()
            self.assertTrue(res1.success)
            self.assertEqual(res1.status, SyncStatus.SYNCED)
            self.assertTrue(res1.committed)

            # Second sync immediately without changes -> status MUST be UP_TO_DATE
            res2 = cluster.work_node.trigger_sync()
            self.assertTrue(res2.success)
            self.assertEqual(res2.status, SyncStatus.UP_TO_DATE)
            self.assertFalse(res2.committed)
            self.assertFalse(res2.pulled)
            self.assertFalse(res2.pushed)
            self.assertIn("already up to date", res2.message.lower())

            # PC-HOME pulls the task -> status == SYNCED (pulled=True)
            res3 = cluster.home_node.trigger_sync()
            self.assertTrue(res3.success)
            self.assertEqual(res3.status, SyncStatus.SYNCED)
            self.assertTrue(res3.pulled)

            # PC-HOME immediately syncs again -> status MUST be UP_TO_DATE
            res4 = cluster.home_node.trigger_sync()
            self.assertTrue(res4.success)
            self.assertEqual(res4.status, SyncStatus.UP_TO_DATE)
            self.assertFalse(res4.pulled)
            self.assertFalse(res4.pushed)


class TestNekoSyncCLI(unittest.TestCase):
    """
    Acceptance Criterion AC5 / Milestone 5 CLI Test Suite.

    Verifies that the unified CLI command `neko sync`:
    1. Correctly formats and outputs repository status via `main(["sync", "--status"])`.
    2. Emits valid, structured JSON conforming to RepoStatus schema with `--status --json`.
    3. Emits valid, structured JSON conforming to SyncResult schema with `--json`.
    4. Parses and passes the `--force` flag to both GitSyncEngine and daemon IPC.
    5. Dispatches to background daemon when running (via loopback HTTP IPC), formatting output and exit codes.
    6. Falls back safely to direct in-process GitSyncEngine when daemon is stopped or IPC fails.
    7. Gracefully degrades in non-git directories with exit code 0 and informative messages.
    8. Operates end-to-end within the hermetic multi-PC cluster without raising exceptions.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_sync_cli_"))

        # 1. Hermetic Git repository
        self.repo_dir = self.temp_dir / "git_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "NekoSyncTester"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "sync_tester@neko.local"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=self.repo_dir, capture_output=True, check=True)

        # Setup initial commit & workspace
        ws = self.repo_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "tasks.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=self.repo_dir, capture_output=True, check=True)

        self.repo_config = Config.load(root_dir=self.repo_dir)
        self.repo_config.ensure_dirs()

        # 2. Non-git workspace
        self.non_git_dir = self.temp_dir / "non_git_workspace"
        self.non_git_dir.mkdir(parents=True, exist_ok=True)
        self.non_git_config = Config.load(root_dir=self.non_git_dir)
        self.non_git_config.ensure_dirs()

    def tearDown(self) -> None:
        robust_rmtree(self.temp_dir)

    def test_cli_sync_status_human_in_git_repo(self) -> None:
        """Verifies `neko sync --status` displays formatted repo diagnostics in a Git repo."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--status", "--workspace", str(self.repo_dir)])
        self.assertEqual(ret, 0, "neko sync --status must exit with 0")
        out = buf.getvalue()
        self.assertIn("NEKO BOT — GIT REPOSITORY STATUS", out)
        self.assertIn("Git Installed : Yes", out)
        self.assertIn("Is Repository : Yes", out)
        self.assertIn("Repo Root", out)
        self.assertIn("Branch", out)
        self.assertIn("Ahead/Behind", out)
        self.assertIn("Dirty State", out)

    def test_cli_sync_status_json_in_git_repo(self) -> None:
        """Verifies `neko sync --status --json` emits valid JSON conforming to RepoStatus."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--status", "--json", "--workspace", str(self.repo_dir)])
        self.assertEqual(ret, 0)
        data = json.loads(buf.getvalue().strip())
        self.assertTrue(data["git_installed"])
        self.assertTrue(data["is_repo"])
        self.assertIn("repo_root", data)
        self.assertIn("current_branch", data)
        self.assertIn("has_remote", data)
        self.assertIn("is_dirty", data)
        self.assertIn("ahead_commits", data)
        self.assertIn("behind_commits", data)
        self.assertIsInstance(data["staged_files"], list)
        self.assertIsInstance(data["unstaged_files"], list)

    def test_cli_sync_json_schema(self) -> None:
        """Verifies `neko sync --json` emits valid JSON matching SyncResult schema."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--json", "--workspace", str(self.repo_dir)])
        self.assertEqual(ret, 0, "Clean sync without remote returns exit code 0")
        output = buf.getvalue().strip()
        data = json.loads(output)
        expected_fields = [
            "success", "status", "message", "error_message",
            "committed", "pulled", "pushed", "commit_sha",
            "branch", "remote", "ahead_count", "behind_count",
            "timestamp", "details",
        ]
        for f in expected_fields:
            self.assertIn(f, data, f"Field '{f}' missing from sync JSON output")
        self.assertIsInstance(data["success"], bool)
        self.assertIsInstance(data["status"], str)
        self.assertIsInstance(data["message"], str)
        self.assertIsInstance(data["committed"], bool)
        self.assertIsInstance(data["pulled"], bool)
        self.assertIsInstance(data["pushed"], bool)
        self.assertIsInstance(data["details"], dict)

    def test_cli_sync_force_flag_in_process(self) -> None:
        """Verifies `--force` argument is correctly parsed and passed to GitSyncEngine."""
        with patch.object(GitSyncEngine, "sync") as mock_sync:
            mock_sync.return_value = SyncResult(
                success=True,
                status=SyncStatus.SYNCED,
                message="Forced sync ok",
                pushed=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                ret = main(["sync", "--force", "--workspace", str(self.repo_dir)])
            self.assertEqual(ret, 0)
            mock_sync.assert_called_once_with(force_push=True)
            self.assertIn("[Neko Sync] Success (synced): Forced sync ok", buf.getvalue())

    def test_cli_sync_daemon_running_ipc_success(self) -> None:
        """Verifies that when daemon is running, cmd_sync communicates via loopback HTTP IPC."""
        mock_ipc_result = {
            "success": True,
            "result": {
                "success": True,
                "status": "synced",
                "message": "Synced via background daemon.",
                "committed": True,
                "pulled": True,
                "pushed": True,
            },
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_ipc_result).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(True, 4242)):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["sync", "--workspace", str(self.repo_dir)])
                self.assertEqual(ret, 0)
                self.assertIn("[Neko Sync] (synced) Synced via background daemon.", buf.getvalue())
                self.assertTrue(mock_urlopen.called)
                req = mock_urlopen.call_args[0][0]
                self.assertIn("/command", req.full_url)
                body = json.loads(req.data.decode("utf-8"))
                self.assertEqual(body["action"], "sync")
                self.assertFalse(body["force"])

    def test_cli_sync_daemon_running_json_output(self) -> None:
        """Verifies `neko sync --json` when daemon is running passes through daemon JSON result."""
        mock_ipc_result = {
            "success": True,
            "result": {
                "success": True,
                "status": "synced",
                "message": "Synced via background daemon.",
                "committed": True,
                "pulled": True,
                "pushed": True,
                "commit_sha": "abc1234",
                "ahead_count": 0,
                "behind_count": 0,
                "timestamp": "2026-10-23T15:00:00Z",
                "details": {},
            },
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_ipc_result).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(True, 4242)):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["sync", "--json", "--workspace", str(self.repo_dir)])
                self.assertEqual(ret, 0)
                data = json.loads(buf.getvalue().strip())
                self.assertEqual(data["status"], "synced")
                self.assertEqual(data["commit_sha"], "abc1234")
                self.assertTrue(data["success"])

    def test_cli_sync_daemon_running_with_force(self) -> None:
        """Verifies `neko sync --force` when daemon is running sends force=True in IPC payload."""
        mock_ipc_result = {
            "success": True,
            "result": {
                "success": True,
                "status": "synced",
                "message": "Force synced via daemon.",
            },
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_ipc_result).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(True, 4242)):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["sync", "--force", "--workspace", str(self.repo_dir)])
                self.assertEqual(ret, 0)
                req = mock_urlopen.call_args[0][0]
                body = json.loads(req.data.decode("utf-8"))
                self.assertTrue(body["force"])

    def test_cli_sync_daemon_running_ipc_failure_falls_back_to_in_process(self) -> None:
        """Verifies that if daemon IPC raises an exception (stale PID / dead port), in-process sync runs."""
        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(True, 4242)):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
                with patch.object(GitSyncEngine, "sync") as mock_in_process_sync:
                    mock_in_process_sync.return_value = SyncResult(
                        success=True,
                        status=SyncStatus.SYNCED,
                        message="Fallback in-process sync succeeded",
                    )
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        ret = main(["sync", "--workspace", str(self.repo_dir)])
                    self.assertEqual(ret, 0)
                    mock_in_process_sync.assert_called_once_with(force_push=False)
                    self.assertIn("Fallback in-process sync succeeded", buf.getvalue())

    def test_cli_sync_daemon_stopped_runs_in_process(self) -> None:
        """Verifies that when daemon is stopped, cmd_sync directly runs in-process GitSyncEngine."""
        with patch("neko_bot.core.daemon.DaemonController.is_running", return_value=(False, None)):
            with patch("urllib.request.urlopen") as mock_urlopen:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["sync", "--workspace", str(self.repo_dir)])
                self.assertEqual(ret, 0)
                mock_urlopen.assert_not_called()
                self.assertIn("[Neko Sync]", buf.getvalue())

    def test_cli_sync_non_git_directory_human_output(self) -> None:
        """Verifies error handling and graceful degradation in a non-git directory (human text)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--workspace", str(self.non_git_dir)])
        self.assertEqual(ret, 0, "Non-git directory auto-sync degrades gracefully with exit code 0")
        out = buf.getvalue()
        self.assertIn("Workspace is not a Git repository. Auto-sync skipped.", out)

    def test_cli_sync_non_git_directory_json_output(self) -> None:
        """Verifies `neko sync --json` in a non-git directory returns valid JSON with not_a_repo status."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--json", "--workspace", str(self.non_git_dir)])
        self.assertEqual(ret, 0, "Non-git workspace exits with code 0 per graceful degradation contract")
        data = json.loads(buf.getvalue().strip())
        self.assertFalse(data["success"])
        self.assertEqual(data["status"], "not_a_repo")
        self.assertIn("not a Git repository", data["message"])

    def test_cli_sync_non_git_directory_status(self) -> None:
        """Verifies `neko sync --status` in a non-git directory reports Is Repository: No."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["sync", "--status", "--workspace", str(self.non_git_dir)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn("Is Repository : No", out)

        # Also test --status --json
        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            ret_json = main(["sync", "--status", "--json", "--workspace", str(self.non_git_dir)])
        self.assertEqual(ret_json, 0)
        data = json.loads(buf_json.getvalue().strip())
        self.assertFalse(data["is_repo"])

    def test_cli_sync_end_to_end_cluster_roundtrip(self) -> None:
        """Verifies end-to-end multi-PC sync execution via CLI main() using hermetic cluster."""
        with hermetic_git_cluster() as cluster:
            # Add a task on PC-WORK
            cluster.work_node.task_store.add_task(
                title="Bake AO textures in Blender #art !alta",
                project="art",
                priority=TaskPriority.HIGH,
            )
            # Invoke CLI sync on PC-WORK
            buf_work = io.StringIO()
            with redirect_stdout(buf_work):
                ret_work = main(["sync", "--workspace", str(cluster.work_node.repo_dir)])
            self.assertEqual(ret_work, 0, f"Work node CLI sync failed: {buf_work.getvalue()}")

            # Invoke CLI sync on PC-HOME
            buf_home = io.StringIO()
            with redirect_stdout(buf_home):
                ret_home = main(["sync", "--workspace", str(cluster.home_node.repo_dir)])
            self.assertEqual(ret_home, 0, f"Home node CLI sync failed: {buf_home.getvalue()}")

            # Verify PC-HOME loaded the new task
            cluster.home_node.task_store.load()
            titles = [t.title for t in cluster.home_node.task_store.get_active_tasks()]
            self.assertIn("Bake AO textures in Blender #art !alta", titles)


if __name__ == "__main__":
    unittest.main()
