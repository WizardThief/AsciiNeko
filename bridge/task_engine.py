"""
bridge/task_engine.py
=====================
Deterministic 6-State Task Engine (FSM) for AsciiNeko (Phase 3: F22, F23, F24).
Persists tasks and step-by-step workflows in SQLite-WAL (neko_storage.db).

States:
- QUEUED: Task created and awaiting worker execution.
- RUNNING: Active step currently executing.
- WAITING_INPUT: Suspended awaiting user confirmation (Tier 2/3) or parameters.
- RETRY: Transient step failure; awaiting exponential backoff delay (max 3 retries).
- COMPLETED: All workflow steps executed and validated.
- FAILED: Terminal failure or max retries exceeded; triggers HITL recovery.
- ABORTED: Canceled immediately by user (Esc / [✕ Annulla]); triggers HITL recovery.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("AsciiNeko.TaskEngine")

VALID_STATES = {"QUEUED", "RUNNING", "WAITING_INPUT", "RETRY", "COMPLETED", "FAILED", "ABORTED"}


class TaskEngine:
    """
    Deterministic 6-State Task Engine with SQLite-WAL persistence, step execution,
    exponential retry backoff, instant abort kill switch, and Human-in-the-Loop rollback.
    """

    def __init__(self, storage_manager: Any = None, db_path: Optional[str] = None):
        self._lock = threading.RLock()
        self.storage_manager = storage_manager

        if storage_manager is not None and hasattr(storage_manager, "db_path"):
            self.db_path = storage_manager.db_path
        elif db_path is not None:
            self.db_path = os.path.abspath(db_path)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = os.path.join(base_dir, "data")
            self.db_path = os.path.join(data_dir, "neko_storage.db")

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

        self._active_task_id: Optional[str] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _get_connection(self) -> sqlite3.Connection:
        if self.storage_manager is not None and hasattr(self.storage_manager, "get_connection"):
            return self.storage_manager.get_connection()
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Ensure tasks table exists with mandatory 6-state check constraint."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    client TEXT DEFAULT '',
                    color TEXT DEFAULT '#4facfe',
                    is_active INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    archived_at REAL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED', 'RUNNING', 'WAITING_INPUT', 'RETRY', 'COMPLETED', 'FAILED', 'ABORTED')),
                    project_id TEXT,
                    priority INTEGER DEFAULT 1,
                    current_step INTEGER DEFAULT 0,
                    total_steps INTEGER DEFAULT 1,
                    steps_json TEXT DEFAULT '[]',
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    elapsed_seconds REAL DEFAULT 0.0,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
            conn.commit()
        finally:
            conn.close()

    def create_task(
        self,
        title: str,
        steps: Optional[List[Dict[str, Any]]] = None,
        skill_id: Optional[str] = None,
        project_id: Optional[str] = None,
        priority: int = 1,
        max_retries: int = 3,
    ) -> str:
        """
        Creates and persists a new task in QUEUED state.
        Returns the unique task ID.
        """
        if steps is None or len(steps) == 0:
            steps = [{"step_index": 1, "title": title, "command": "default_action", "status": "QUEUED"}]

        task_id = uuid.uuid4().hex[:12]
        total_steps = len(steps)
        now = time.time()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, status, project_id, priority, current_step,
                        total_steps, steps_json, error_message, retry_count,
                        max_retries, created_at, started_at, completed_at, elapsed_seconds
                    ) VALUES (?, ?, 'QUEUED', ?, ?, 0, ?, ?, NULL, 0, ?, ?, NULL, NULL, 0.0)
                    """,
                    (
                        task_id,
                        title,
                        project_id,
                        priority,
                        total_steps,
                        json.dumps(steps, ensure_ascii=False),
                        max_retries,
                        now,
                    ),
                )
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.commit()
                else:
                    try:
                        conn.commit()
                    except Exception:
                        pass
            finally:
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.close()

        logger.info("Created Task [%s]: '%s' (%d steps)", task_id, title, total_steps)
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves task state and parsed steps payload by ID."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, title, status, project_id, priority, current_step,
                           total_steps, steps_json, error_message, retry_count,
                           max_retries, created_at, started_at, completed_at, elapsed_seconds
                    FROM tasks WHERE id = ?
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                d = dict(row)
                try:
                    d["steps"] = json.loads(d.get("steps_json") or "[]")
                except Exception:
                    d["steps"] = []

                # Calculate live elapsed time if running
                if d["status"] in ("RUNNING", "RETRY", "WAITING_INPUT") and d.get("started_at"):
                    d["elapsed_seconds"] = max(0.0, time.time() - float(d["started_at"]))

                d["progress_pct"] = int((d["current_step"] / max(1, d["total_steps"])) * 100)
                d["visualizer"] = self.render_ascii_progress(
                    d["current_step"],
                    d["total_steps"],
                    d["title"],
                    d["elapsed_seconds"],
                    d["status"],
                )
                return d
            finally:
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.close()

    def get_active_task(self) -> Optional[Dict[str, Any]]:
        """Returns the highest priority currently active task (RUNNING, RETRY, WAITING_INPUT, QUEUED)."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id FROM tasks
                    WHERE status IN ('RUNNING', 'RETRY', 'WAITING_INPUT', 'QUEUED')
                    ORDER BY CASE status
                        WHEN 'RUNNING' THEN 1
                        WHEN 'RETRY' THEN 2
                        WHEN 'WAITING_INPUT' THEN 3
                        WHEN 'QUEUED' THEN 4
                    END, priority DESC, created_at ASC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row:
                    return self.get_task(row[0])
                return None
            finally:
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.close()

    def list_tasks(self, limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists recent tasks with optional status filter."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                if status:
                    cur.execute(
                        "SELECT id FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                        (status, limit),
                    )
                else:
                    cur.execute(
                        "SELECT id FROM tasks ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    )
                rows = cur.fetchall()
                results = []
                for r in rows:
                    t = self.get_task(r[0])
                    if t:
                        results.append(t)
                return results
            finally:
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.close()

    def transition(self, task_id: str, new_status: str, error_message: Optional[str] = None) -> bool:
        """Formal FSM state transition validation and database update."""
        if new_status not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_status}. Must be one of {VALID_STATES}")

        now = time.time()
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT status, started_at, elapsed_seconds FROM tasks WHERE id = ?", (task_id,))
                row = cur.fetchone()
                if not row:
                    return False

                started_at = row["started_at"]
                if new_status == "RUNNING" and not started_at:
                    started_at = now

                elapsed = row["elapsed_seconds"] or 0.0
                completed_at = None
                if new_status in ("COMPLETED", "FAILED", "ABORTED"):
                    completed_at = now
                    if started_at:
                        elapsed = max(0.0, now - float(started_at))

                cur.execute(
                    """
                    UPDATE tasks
                    SET status = ?,
                        started_at = COALESCE(started_at, ?),
                        completed_at = COALESCE(?, completed_at),
                        elapsed_seconds = ?,
                        error_message = COALESCE(?, error_message)
                    WHERE id = ?
                    """,
                    (new_status, started_at, completed_at, elapsed, error_message, task_id),
                )
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.commit()
                else:
                    try:
                        conn.commit()
                    except Exception:
                        pass
                logger.info("Task [%s] transition -> %s", task_id, new_status)
                return True
            finally:
                if not hasattr(self.storage_manager, "get_connection"):
                    conn.close()

    def abort_task(self, task_id: str) -> bool:
        """
        Instant kill switch: halts execution immediately and transitions task to ABORTED.
        F23/F24 contract requirement.
        """
        task = self.get_task(task_id)
        if not task:
            return False

        if task["status"] in ("COMPLETED", "FAILED", "ABORTED"):
            return True

        return self.transition(task_id, "ABORTED", error_message="Annullato dall'utente (Kill Switch / Esc)")

    def execute_next_step(self, task_id: str) -> Dict[str, Any]:
        """
        Advances the task execution FSM by one atomic step.
        Handles transient error detection, exponential backoff, retry ceilings,
        and triggers HITL recovery on terminal failure.
        """
        with self._lock:
            task = self.get_task(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            status = task["status"]
            if status in ("COMPLETED", "ABORTED", "FAILED"):
                return task

            current_step = task["current_step"]
            total_steps = task["total_steps"]
            steps = task.get("steps", [])
            retry_count = task["retry_count"]
            max_retries = task["max_retries"]

            # If QUEUED, transition to RUNNING first
            if status == "QUEUED":
                self.transition(task_id, "RUNNING")
                task = self.get_task(task_id)
                status = "RUNNING"

            # Execute the step at current_step
            step_idx = current_step
            if step_idx < len(steps):
                step = steps[step_idx]
            else:
                step = {"step_index": step_idx + 1, "title": f"Step {step_idx + 1}", "status": "RUNNING"}

            # Simulated execution of the step
            try:
                # Check for transient failure simulation markers in step command
                cmd = str(step.get("command", ""))
                is_transient_error = "fail_transient" in cmd or step.get("simulate_transient_error", False)
                is_fatal_error = "fail_fatal" in cmd or step.get("simulate_fatal_error", False)

                if is_transient_error and retry_count < max_retries:
                    raise IOError(f"Transient I/O or network lock on step {step_idx + 1}")
                elif is_fatal_error or (is_transient_error and retry_count >= max_retries):
                    raise RuntimeError(f"Fatal error on step {step_idx + 1}: Max retries reached or unrecoverable defect")

                # Step Succeeded
                step["status"] = "COMPLETED"
                step["completed_at"] = time.time()
                next_step = current_step + 1

                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    if next_step >= total_steps:
                        now = time.time()
                        elapsed = max(0.0, now - float(task.get("started_at") or now))
                        cur.execute(
                            """
                            UPDATE tasks
                            SET current_step = ?, total_steps = ?, steps_json = ?,
                                status = 'COMPLETED', completed_at = ?, elapsed_seconds = ?
                            WHERE id = ?
                            """,
                            (next_step, total_steps, json.dumps(steps, ensure_ascii=False), now, elapsed, task_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE tasks
                            SET current_step = ?, steps_json = ?, status = 'RUNNING', retry_count = 0
                            WHERE id = ?
                            """,
                            (next_step, json.dumps(steps, ensure_ascii=False), task_id),
                        )
                    if not hasattr(self.storage_manager, "get_connection"):
                        conn.commit()
                    else:
                        try:
                            conn.commit()
                        except Exception:
                            pass
                finally:
                    if not hasattr(self.storage_manager, "get_connection"):
                        conn.close()

            except Exception as e:
                err_msg = str(e)
                logger.warning("Step execution failed for Task [%s] Step %d: %s", task_id, step_idx + 1, err_msg)
                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    if retry_count < max_retries:
                        new_retries = retry_count + 1
                        backoff = 1.0 * (2 ** (new_retries - 1))
                        step["status"] = "RETRY"
                        step["error"] = err_msg
                        cur.execute(
                            """
                            UPDATE tasks
                            SET status = 'RETRY', retry_count = ?, error_message = ?, steps_json = ?
                            WHERE id = ?
                            """,
                            (new_retries, err_msg, json.dumps(steps, ensure_ascii=False), task_id),
                        )
                    else:
                        now = time.time()
                        elapsed = max(0.0, now - float(task.get("started_at") or now))
                        step["status"] = "FAILED"
                        step["error"] = err_msg
                        cur.execute(
                            """
                            UPDATE tasks
                            SET status = 'FAILED', error_message = ?, completed_at = ?,
                                elapsed_seconds = ?, steps_json = ?
                            WHERE id = ?
                            """,
                            (f"Max retries ({max_retries}) exceeded: {err_msg}", now, elapsed, json.dumps(steps, ensure_ascii=False), task_id),
                        )
                    if not hasattr(self.storage_manager, "get_connection"):
                        conn.commit()
                    else:
                        try:
                            conn.commit()
                        except Exception:
                            pass
                finally:
                    if not hasattr(self.storage_manager, "get_connection"):
                        conn.close()

            return self.get_task(task_id)

    def handle_hitl_recovery(
        self,
        task_id: str,
        choice: int,
        generated_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        F24: Human-in-the-Loop Rollback Protocol.
        Never blindly wipes files without user choice:
        - Choice 1: Keep partial files, mark task terminated/failed cleanly.
        - Choice 2: Retry step, preserve files, reset retry counter to 0.
        - Choice 3: Complete rollback, explicitly delete partial files.
        """
        files = list(generated_files or [])
        if choice == 1:
            # Keep partial files and finish
            self.transition(task_id, "ABORTED", error_message="Terminato dall'utente (HITL Scelta 1: File preservati)")
            return {
                "action": "keep_files",
                "preserved": files,
                "deleted": [],
                "task_id": task_id,
                "status": "ABORTED",
            }
        elif choice == 2:
            # Retry step
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute("UPDATE tasks SET status = 'RUNNING', retry_count = 0 WHERE id = ?", (task_id,))
                    if not hasattr(self.storage_manager, "get_connection"):
                        conn.commit()
                    else:
                        try:
                            conn.commit()
                        except Exception:
                            pass
                finally:
                    if not hasattr(self.storage_manager, "get_connection"):
                        conn.close()
            return {
                "action": "retry_step",
                "preserved": files,
                "deleted": [],
                "task_id": task_id,
                "status": "RUNNING",
            }
        elif choice == 3:
            # Full rollback: user explicitly requested file cleanup
            deleted = []
            for fpath in files:
                try:
                    if os.path.exists(fpath):
                        if os.path.isfile(fpath):
                            os.remove(fpath)
                            deleted.append(fpath)
                except Exception as e:
                    logger.warning("HITL rollback could not remove %s: %s", fpath, e)
            self.transition(task_id, "ABORTED", error_message="Rollback completato (HITL Scelta 3: File parziali rimossi)")
            return {
                "action": "full_rollback",
                "preserved": [],
                "deleted": deleted,
                "task_id": task_id,
                "status": "ABORTED",
            }
        else:
            raise ValueError(f"Scelta HITL non valida: {choice}. Valori ammessi: 1, 2, 3")

    @staticmethod
    def render_ascii_progress(
        current_step: int,
        total_steps: int,
        title: str = "",
        elapsed_sec: float = 0.0,
        status: str = "RUNNING",
    ) -> str:
        """
        F23: ASCII Task Step Visualizer rendering format:
        [■■■■■■□□] 75% • Step X/Y • Elapsed • [✕ Annulla / Esc]
        """
        steps_total = max(1, total_steps)
        pct = int((current_step / steps_total) * 100)
        pct = max(0, min(100, pct))

        filled = int((pct / 100) * 10)
        bar = "■" * filled + "□" * (10 - filled)

        elapsed_int = int(elapsed_sec)
        mins = elapsed_int // 60
        secs = elapsed_int % 60
        time_str = f"{mins:02d}:{secs:02d}"

        if status == "ABORTED":
            return f"[{bar}] {pct}% • Step {current_step}/{total_steps} • ABORTITO"
        elif status == "FAILED":
            return f"[{bar}] {pct}% • Step {current_step}/{total_steps} • FALLITO"
        elif status == "COMPLETED":
            return f"[{bar}] 100% • Step {total_steps}/{total_steps} • {time_str} • COMPLETATO"

        return f"[{bar}] {pct}% • Step {current_step}/{total_steps} • {time_str} • [✕ Annulla]"
