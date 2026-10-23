"""
neko_bot.sync.engine - Background Git Auto-Synchronization Engine.

Features:
- Strictly scoped workspace operations (workspace/tasks.json, TASKS.md, time_logs/)
- Automatic Git working tree detection and graceful non-git degradation
- Zero-hang network timeouts and terminal prompt suppression
- Native Git DAG local commit queueing when offline
- In-process rebase conflict auto-resolver fallback (CRDT LWW-Element-Set)
- Concurrency protection via threading.RLock and Git index.lock retry backoff
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from neko_bot.core.config import Config
from neko_bot.sync.merge_driver import (
    auto_resolve_rebase_conflicts,
    find_repo_root,
    is_merge_driver_registered,
    register_merge_driver,
)

logger = logging.getLogger("neko_bot.sync.engine")


class SyncStatus(str, Enum):
    SYNCED = "synced"
    UP_TO_DATE = "up_to_date"
    OFFLINE = "offline"
    NO_REMOTE = "no_remote"
    NOT_A_REPO = "not_a_repo"
    NO_GIT_BINARY = "no_git_binary"
    CONFLICT_RESOLVED = "conflict_resolved"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    LOCKED = "locked"
    ERROR = "error"


@dataclass
class SyncResult:
    success: bool
    status: SyncStatus
    message: str
    error_message: Optional[str] = None
    committed: bool = False
    pulled: bool = False
    pushed: bool = False
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    remote: Optional[str] = None
    ahead_count: int = 0
    behind_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.success and self.error_message is None:
            self.error_message = self.message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value if isinstance(self.status, SyncStatus) else str(self.status),
            "message": self.message,
            "error_message": self.error_message,
            "committed": self.committed,
            "pulled": self.pulled,
            "pushed": self.pushed,
            "commit_sha": self.commit_sha,
            "branch": self.branch,
            "remote": self.remote,
            "ahead_count": self.ahead_count,
            "behind_count": self.behind_count,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class RepoStatus:
    git_installed: bool
    is_repo: bool
    repo_root: Optional[Path] = None
    current_branch: Optional[str] = None
    remote_name: Optional[str] = None
    remote_url: Optional[str] = None
    has_remote: bool = False
    has_commits: bool = False
    is_dirty: bool = False
    ahead_commits: int = 0
    behind_commits: int = 0
    staged_files: List[str] = field(default_factory=list)
    unstaged_files: List[str] = field(default_factory=list)


class GitSyncEngine:
    """
    Orchestrates scoped Git synchronization across multiple machines.
    """

    def __init__(
        self,
        repo_dir: Optional[Union[Path, str]] = None,
        config: Optional[Config] = None,
        workspace_dir: Optional[Union[Path, str]] = None,
        remote: Optional[str] = None,
        branch: Optional[str] = None,
        network_timeout: float = 10.0,
        local_timeout: float = 5.0,
        task_store: Optional[Any] = None,
    ) -> None:
        self.repo_dir = Path(repo_dir).resolve() if repo_dir else None
        self.config = config or Config.load(root_dir=self.repo_dir)
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else self.config.workspace_dir.resolve()
        self.remote = remote or getattr(self.config, "git_remote", "origin")
        self.branch = branch or getattr(self.config, "git_branch", "main")
        self.network_timeout = network_timeout
        self.local_timeout = local_timeout
        self.task_store = task_store
        self._lock = threading.RLock()
        self._last_result: Optional[SyncResult] = None
        self._hostname = os.getenv("COMPUTERNAME", socket.gethostname())

    # --- Subprocess Execution Wrapper ---
    def _run_git(
        self,
        args: List[str],
        cwd: Optional[Path] = None,
        timeout: Optional[float] = None,
        retries_on_lock: int = 3,
    ) -> Tuple[int, str, str]:
        """
        Executes Git command safely:
        - Prevents interactive credential hangs (GIT_TERMINAL_PROMPT=0, GIT_ASKPASS="")
        - Hides background windows on Windows (CREATE_NO_WINDOW)
        - Handles index.lock contention via exponential backoff
        - Returns (returncode, stdout, stderr)
        """
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = ""
        env["SSH_ASKPASS"] = ""
        py_editor = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
        env["GIT_EDITOR"] = py_editor
        env["GIT_SEQUENCE_EDITOR"] = py_editor
        project_root = Path(__file__).resolve().parent.parent.parent
        env["PYTHONPATH"] = str(project_root)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        target_cwd = cwd or self.get_repo_root() or self.workspace_dir
        cmd = ["git"] + args
        eff_timeout = timeout if timeout is not None else self.local_timeout

        for attempt in range(retries_on_lock + 1):
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(target_cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    creationflags=creationflags,
                )
                try:
                    stdout, stderr = proc.communicate(timeout=eff_timeout)
                except subprocess.TimeoutExpired:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                    else:
                        proc.kill()
                    try:
                        proc.communicate(timeout=1.0)
                    except Exception:
                        pass
                    logger.warning("Git command timed out after %ss: %s", eff_timeout, " ".join(args))
                    return -1, "", f"Git command timed out after {eff_timeout}s: {' '.join(args)}"

                stdout = stdout or ""
                stderr = stderr or ""

                if "index.lock" in stderr and attempt < retries_on_lock:
                    time.sleep(0.1 * (2 ** attempt))
                    continue

                return proc.returncode, stdout.strip(), stderr.strip()

            except FileNotFoundError:
                logger.warning("Git binary not found in system PATH.")
                return -2, "", "Git binary not found in PATH"
            except Exception as e:
                logger.warning("Unexpected error running git command: %s", e)
                return -3, "", str(e)

        return -1, "", "Exceeded lock retries"

    # --- Inspection & Discovery ---
    def is_git_available(self) -> bool:
        """Returns True if git executable is available in PATH."""
        code, out, _ = self._run_git(["--version"], timeout=2.0)
        return code == 0

    def get_repo_root(self) -> Optional[Path]:
        """Discovers top-level repository root directory."""
        if self.repo_dir:
            if (self.repo_dir / ".git").exists():
                return self.repo_dir
            code, out, _ = self._run_git(["rev-parse", "--show-toplevel"], cwd=self.repo_dir, timeout=2.0)
            if code == 0 and out.strip():
                return Path(out.strip()).resolve()
            return None

        # Check workspace_dir
        if (self.workspace_dir / ".git").exists():
            return self.workspace_dir
        code, out, _ = self._run_git(["rev-parse", "--show-toplevel"], cwd=self.workspace_dir, timeout=2.0)
        if code == 0 and out.strip():
            return Path(out.strip()).resolve()

        return find_repo_root(self.workspace_dir)

    def is_git_repo(self) -> bool:
        """Returns True if the designated directory is part of an active Git repository."""
        if not self.is_git_available():
            return False

        if self.repo_dir:
            p = self.repo_dir.resolve()
            if (p / ".git").exists():
                return True
            code, out, _ = self._run_git(["rev-parse", "--show-toplevel"], cwd=p, timeout=2.0)
            if code == 0 and out.strip():
                return Path(out.strip()).resolve() == p
            return False

        root = self.get_repo_root()
        return root is not None

    def is_repo(self) -> bool:
        """Alias for is_git_repo()."""
        return self.is_git_repo()

    def get_current_branch(self) -> Optional[str]:
        """Returns active branch name or None."""
        code, out, _ = self._run_git(["branch", "--show-current"])
        if code == 0 and out:
            return out
        code, out, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if code == 0 and out and out != "HEAD":
            return out
        return None

    def has_commits(self) -> bool:
        """Returns True if current branch has at least one commit (HEAD exists)."""
        code, _, _ = self._run_git(["rev-parse", "--verify", "HEAD"])
        return code == 0

    def get_scoped_pathspecs(self) -> List[str]:
        """
        Computes pathspecs strictly bounded to productivity files in workspace_dir.
        Guarantees operations never stage or commit outside the workspace.
        """
        root = self.get_repo_root()
        if not root:
            return ["workspace/tasks.json", "workspace/TASKS.md", "workspace/time_logs"]

        try:
            rel_ws = self.workspace_dir.resolve().relative_to(root.resolve()).as_posix()
            if rel_ws in ("", "."):
                return ["tasks.json", "TASKS.md", "time_logs"]
            return [
                f"{rel_ws}/tasks.json",
                f"{rel_ws}/TASKS.md",
                f"{rel_ws}/time_logs",
            ]
        except ValueError:
            return ["workspace/tasks.json", "workspace/TASKS.md", "workspace/time_logs"]

    def get_repo_status(self) -> RepoStatus:
        """Returns structured diagnostic status of the Git workspace."""
        git_avail = self.is_git_available()
        if not git_avail:
            return RepoStatus(git_installed=False, is_repo=False)

        root = self.get_repo_root()
        is_r = self.is_git_repo()
        if not is_r or not root:
            return RepoStatus(git_installed=True, is_repo=False)

        branch = self.get_current_branch()
        has_c = self.has_commits()

        # Remote check
        code_rem, out_rem, _ = self._run_git(["remote", "get-url", self.remote])
        remote_url = out_rem if code_rem == 0 else None
        has_rem = remote_url is not None

        # Dirty state & file list
        code_stat, out_stat, _ = self._run_git(["status", "--porcelain"])
        staged: List[str] = []
        unstaged: List[str] = []
        is_dirty = False
        if code_stat == 0 and out_stat:
            is_dirty = True
            for line in out_stat.splitlines():
                if len(line) >= 3:
                    x, y, path = line[0], line[1], line[3:].strip()
                    if x in ("M", "A", "D", "R", "C"):
                        staged.append(path)
                    if y in ("M", "D") or x == "?":
                        unstaged.append(path)

        # Ahead/behind commits
        ahead = 0
        behind = 0
        if has_rem and branch and has_c:
            code_rev, out_rev, _ = self._run_git(
                ["rev-list", "--left-right", "--count", f"{branch}...{self.remote}/{branch}"]
            )
            if code_rev == 0 and out_rev:
                parts = out_rev.split()
                if len(parts) == 2:
                    try:
                        ahead = int(parts[0])
                        behind = int(parts[1])
                    except ValueError:
                        pass

        return RepoStatus(
            git_installed=True,
            is_repo=True,
            repo_root=root,
            current_branch=branch,
            remote_name=self.remote,
            remote_url=remote_url,
            has_remote=has_rem,
            has_commits=has_c,
            is_dirty=is_dirty,
            ahead_commits=ahead,
            behind_commits=behind,
            staged_files=staged,
            unstaged_files=unstaged,
        )

    # --- Core Synchronization Phases ---
    def stage_and_commit_local(self, commit_msg: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Stages workspace productivity files only and commits if changes exist.
        Returns (committed_bool, commit_sha_or_none).
        """
        scoped = self.get_scoped_pathspecs()
        for pathspec in scoped:
            # Stage only within scoped pathspecs
            self._run_git(["add", "-A", "--", pathspec])

        # Check if anything is staged
        code_diff, _, _ = self._run_git(["diff", "--cached", "--quiet"])
        if code_diff == 0:
            # Nothing staged
            return False, None

        msg = commit_msg or f"neko auto-sync: {self._hostname}"
        code_cmt, out_cmt, err_cmt = self._run_git(["commit", "-m", msg], timeout=self.local_timeout)
        if code_cmt != 0:
            logger.warning("Commit failed: %s", err_cmt or out_cmt)
            return False, None

        code_sha, out_sha, _ = self._run_git(["rev-parse", "HEAD"])
        sha = out_sha[:8] if code_sha == 0 else None
        return True, sha

    def fetch_remote(self) -> Tuple[bool, str]:
        """Probes and fetches from the configured remote."""
        code, out, err = self._run_git(
            ["fetch", self.remote, self.branch],
            timeout=self.network_timeout,
        )
        if code == 0:
            return True, out
        return False, err or out

    def pull_rebase(self) -> Tuple[bool, str]:
        """
        Executes git pull --rebase with automatic CRDT conflict resolution fallback.
        """
        code, out, err = self._run_git(
            ["pull", "--rebase", self.remote, self.branch],
            timeout=self.network_timeout,
        )
        combined = f"{out}\n{err}".strip() if (out or err) else ""
        if code == 0:
            return True, combined or "Pull completed successfully."

        # Rebase conflict occurred. Attempt CRDT auto-resolution.
        logger.info("Git pull --rebase produced conflicts. Invoking CRDT auto-resolver fallback...")
        resolved = auto_resolve_rebase_conflicts(self.get_repo_root())
        if resolved:
            return True, "Rebase conflicts successfully resolved via CRDT reconciliation."

        # If resolution failed, abort rebase to preserve local working tree state
        self._run_git(["rebase", "--abort"])
        return False, combined or "Git pull --rebase failed."

    def push_remote(self, force: bool = False) -> Tuple[bool, str]:
        """Pushes queued local commits to remote branch."""
        args = ["push"]
        if force:
            args.append("-f")
        args.extend([self.remote, self.branch])

        code, out, err = self._run_git(args, timeout=self.network_timeout)
        combined = f"{out}\n{err}".strip() if (out or err) else ""
        if code == 0:
            return True, combined or "Push completed successfully."
        return False, combined or "Git push failed."

    # --- High-Level Sync Orchestrator ---
    def sync(self, force_push: bool = False) -> SyncResult:
        """
        Executes complete, strictly scoped synchronization workflow:
        1. Preconditions: checks git availability and repo status.
        2. Ensures custom merge driver is registered.
        3. Stages and commits local changes (workspace scoped only).
        4. Probes remote via fetch. If unreachable/timeout -> returns OFFLINE (commits queued).
        5. Pulls with rebase, invoking CRDT merge driver & fallback auto-resolver.
        6. Pushes local commits to remote.
        7. Refreshes in-memory store.
        """
        with self._lock:
            # 1. Preconditions
            if not self.is_git_available():
                res = SyncResult(
                    success=False,
                    status=SyncStatus.NO_GIT_BINARY,
                    message="Git is not installed or not in PATH. Auto-sync skipped.",
                    error_message="Git binary not found",
                )
                self._last_result = res
                return res

            if not self.is_git_repo():
                res = SyncResult(
                    success=False,
                    status=SyncStatus.NOT_A_REPO,
                    message="Workspace is not a Git repository. Auto-sync skipped.",
                    error_message="Not a git repository",
                )
                self._last_result = res
                return res

            repo_root = self.get_repo_root()
            if repo_root and not is_merge_driver_registered(repo_root):
                try:
                    register_merge_driver(repo_root)
                except Exception as e:
                    logger.debug("Failed to auto-register merge driver: %s", e)

            # Ensure branch matches active checkout if not explicitly overridden
            active_b = self.get_current_branch()
            if active_b and (self.config is None or getattr(self.config, "git_branch", None) is None):
                self.branch = active_b

            # 2. Stage & commit local workspace changes
            committed, commit_sha = self.stage_and_commit_local()

            # 3. Check Remote
            code_rem, out_rem, _ = self._run_git(["remote", "get-url", self.remote])
            if code_rem != 0:
                # No remote configured: local commits stay safe locally
                res = SyncResult(
                    success=True,
                    status=SyncStatus.NO_REMOTE,
                    message="No git remote configured. Local workspace changes committed locally.",
                    committed=committed,
                    commit_sha=commit_sha,
                )
                self._last_result = res
                return res

            # 4. Probe remote via fetch
            fetch_ok, fetch_err = self.fetch_remote()
            if not fetch_ok:
                res = SyncResult(
                    success=False,
                    status=SyncStatus.OFFLINE,
                    message=f"Git remote unreachable (offline): {fetch_err}",
                    error_message=fetch_err or "Remote unreachable",
                    committed=committed,
                    commit_sha=commit_sha,
                )
                self._last_result = res
                return res

            # Determine behind count and capture HEAD before pull
            behind_count = 0
            ahead_count = 0
            if self.has_commits():
                code_cnt, out_cnt, _ = self._run_git(["rev-list", "--count", "HEAD..FETCH_HEAD"])
                if code_cnt == 0 and out_cnt.strip().isdigit():
                    behind_count = int(out_cnt.strip())
                else:
                    code_rev, out_rev, _ = self._run_git(
                        ["rev-list", "--left-right", "--count", f"{self.branch}...{self.remote}/{self.branch}"]
                    )
                    if code_rev == 0 and out_rev:
                        parts = out_rev.split()
                        if len(parts) == 2 and parts[1].isdigit():
                            behind_count = int(parts[1])

            code_head_before, head_before, _ = self._run_git(["rev-parse", "HEAD"])

            # 5. Pull with rebase
            pull_ok, pull_msg = self.pull_rebase()
            if not pull_ok:
                res = SyncResult(
                    success=False,
                    status=SyncStatus.CONFLICT_UNRESOLVED,
                    message=f"Unresolved Git rebase conflict: {pull_msg}",
                    error_message=pull_msg,
                    committed=committed,
                    commit_sha=commit_sha,
                    behind_count=behind_count,
                )
                self._last_result = res
                return res

            code_head_after, head_after, _ = self._run_git(["rev-parse", "HEAD"])

            # Determine if new commits were actually pulled
            pulled_commits = False
            lower_pull = (pull_msg or "").lower()
            if behind_count > 0:
                pulled_commits = True
            elif code_head_before == 0 and code_head_after == 0 and head_before != head_after:
                pulled_commits = True
            elif code_head_before != 0 and code_head_after == 0:
                pulled_commits = True
            elif "conflict" in lower_pull or "fast-forward" in lower_pull or "rebased and updated" in lower_pull:
                pulled_commits = True
            elif "up to date" in lower_pull or "up-to-date" in lower_pull:
                pulled_commits = False

            # Check ahead count before push
            if self.has_commits():
                code_ah, out_ah, _ = self._run_git(["rev-list", "--count", "FETCH_HEAD..HEAD"])
                if code_ah == 0 and out_ah.strip().isdigit():
                    ahead_count = int(out_ah.strip())
                else:
                    code_rev, out_rev, _ = self._run_git(
                        ["rev-list", "--left-right", "--count", f"{self.branch}...{self.remote}/{self.branch}"]
                    )
                    if code_rev == 0 and out_rev:
                        parts = out_rev.split()
                        if len(parts) == 2 and parts[0].isdigit():
                            ahead_count = int(parts[0])

            # 6. Push local commits to remote
            push_ok, push_msg = self.push_remote(force=force_push)
            if not push_ok:
                res = SyncResult(
                    success=False,
                    status=SyncStatus.OFFLINE,
                    message=f"Failed to push to remote: {push_msg}",
                    error_message=push_msg,
                    committed=committed,
                    pulled=pulled_commits,
                    commit_sha=commit_sha,
                    ahead_count=ahead_count,
                    behind_count=behind_count,
                )
                self._last_result = res
                return res

            # Determine if commits were actually pushed
            pushed_commits = False
            lower_push = (push_msg or "").lower()
            if "everything up-to-date" in lower_push or "everything up to date" in lower_push:
                pushed_commits = False
            elif committed or ahead_count > 0:
                pushed_commits = True
            elif "->" in lower_push:
                pushed_commits = True

            # 7. Reload store if present
            if self.task_store is not None:
                try:
                    self.task_store.load()
                except Exception as e:
                    logger.debug("Task store reload error after sync: %s", e)

            if committed or pulled_commits or pushed_commits:
                status = SyncStatus.SYNCED
                message = "Workspace successfully synchronized with Git remote."
            else:
                status = SyncStatus.UP_TO_DATE
                message = "Workspace is already up to date with Git remote."

            res = SyncResult(
                success=True,
                status=status,
                message=message,
                committed=committed,
                pulled=pulled_commits,
                pushed=pushed_commits,
                commit_sha=commit_sha,
                branch=self.branch,
                remote=self.remote,
                ahead_count=0 if push_ok else ahead_count,
                behind_count=0 if pull_ok else behind_count,
            )
            self._last_result = res
            return res
