"""
bridge/storage_manager.py
=========================
ACID Storage Manager for AsciiNeko Golden Architecture (Phase 1).
Provides:
- SQLite-WAL connection management and full schema DDL
- Transactional Atomic JSON Reader/Writer with Windows msvcrt locking and .corrupt.bak protection
- Non-destructive idempotent legacy data migration
- Integrated Memory Retrieval Service & Token Budget Manager contracts
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bridge.retrieval import (
    MemoryRetrievalService,
    TimeDecayScorer,
    DeduplicationEngine,
    TokenBudgetManager,
    estimate_tokens,
    FTS5_TABLE_DDL,
    FTS5_TRIGGERS_DDL,
    FTS5_REBUILD_SQL,
)

logger = logging.getLogger("AsciiNeko.StorageManager")

# Conditionally import Windows msvcrt for OS-level file locking
try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False


class StorageError(IOError):
    """Base exception for all storage errors (inherits from IOError for test compatibility)."""
    pass


class StorageLockError(StorageError):
    """Raised when acquiring a file lock or replacing an atomic file times out."""
    pass


class StorageCorruptError(StorageError):
    """Raised when reading corrupted JSON data to prevent silent wipeout."""
    pass


class StorageManager:
    """
    Centralized ACID storage manager combining:
    1. SQLite-WAL connection with automatic PRAGMA initialization, FTS5 BM25 index, and relational schemas.
    2. Transactional Atomic JSON Reader/Writer with Windows msvcrt locking, exponential backoff, and .corrupt.bak generation.
    3. Non-destructive legacy data migration from bridge/data/*.json to neko_storage.db.
    4. Full conformance to PROJECT.md interface contracts.
    """

    DDL_SCHEMA = """
    -- 1. Memories
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        category TEXT DEFAULT 'Generale',
        project_id TEXT,
        importance REAL DEFAULT 0.5,
        pinned INTEGER DEFAULT 0,
        frequency INTEGER DEFAULT 1,
        source TEXT DEFAULT 'user',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);
    CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories(pinned);
    CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);

    -- 2. Memories FTS5 Virtual Table & Triggers
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        text,
        category,
        project_id UNINDEXED,
        content='memories',
        content_rowid='rowid'
    );

    CREATE TRIGGER IF NOT EXISTS trg_memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, text, category, project_id)
        VALUES (new.rowid, new.text, new.category, new.project_id);
    END;

    CREATE TRIGGER IF NOT EXISTS trg_memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, text, category, project_id)
        VALUES ('delete', old.rowid, old.text, old.category, old.project_id);
    END;

    CREATE TRIGGER IF NOT EXISTS trg_memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, text, category, project_id)
        VALUES ('delete', old.rowid, old.text, old.category, old.project_id);
        INSERT INTO memories_fts(rowid, text, category, project_id)
        VALUES (new.rowid, new.text, new.category, new.project_id);
    END;

    -- 3. Projects
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

    -- 4. Project Notes
    CREATE TABLE IF NOT EXISTS project_notes (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_proj_notes_pid ON project_notes(project_id);

    -- 5. Project Pins
    CREATE TABLE IF NOT EXISTS project_pins (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        label TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    -- 6. Tasks
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
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

    -- 7. Skills
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        safety_tier TEXT NOT NULL CHECK(safety_tier IN ('READ_ONLY', 'CONFIRM_ONCE', 'DESTRUCTIVE_REQUIRE_AUTH')),
        trigger_patterns_json TEXT NOT NULL,
        parameters_json TEXT DEFAULT '[]',
        execution_contract_json TEXT NOT NULL,
        validation_rules_json TEXT DEFAULT '{}',
        is_builtin INTEGER DEFAULT 0,
        usage_count INTEGER DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    -- 8. Time Logs
    CREATE TABLE IF NOT EXISTS time_logs (
        id TEXT PRIMARY KEY,
        hostname TEXT NOT NULL,
        application TEXT NOT NULL,
        window_title TEXT,
        project_id TEXT,
        project_name TEXT,
        file_name TEXT,
        start_time REAL NOT NULL,
        end_time REAL NOT NULL,
        duration_seconds REAL NOT NULL,
        is_afk INTEGER DEFAULT 0,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_timelogs_host ON time_logs(hostname);
    CREATE INDEX IF NOT EXISTS idx_timelogs_app ON time_logs(application);
    CREATE INDEX IF NOT EXISTS idx_timelogs_time ON time_logs(start_time, end_time);

    -- 9. Archive
    CREATE TABLE IF NOT EXISTS archive (
        id TEXT PRIMARY KEY,
        original_type TEXT NOT NULL CHECK(original_type IN ('project', 'task', 'memory')),
        payload_json TEXT NOT NULL,
        archived_at REAL NOT NULL,
        notes TEXT
    );

    -- 10. Chat History
    CREATE TABLE IF NOT EXISTS chat_history (
        id TEXT PRIMARY KEY,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
        text TEXT NOT NULL,
        time_label TEXT NOT NULL,
        timestamp REAL NOT NULL,
        model TEXT,
        actions_json TEXT DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_history(timestamp);
    """

    def __init__(self, db_path: Optional[str] = None, data_dir: Optional[str] = None):
        """
        Initialize the StorageManager.
        Supports:
          StorageManager(db_path, data_dir)
          StorageManager(db_path=..., data_dir=...)
          StorageManager(data_dir=...)
        """
        self._lock = threading.RLock()
        self.lock = self._lock  # Alias for contract test compatibility
        self._local = threading.local()

        # Handle directory and db_path resolution
        if db_path and not data_dir:
            self.db_path = os.path.abspath(db_path)
            self.data_dir = os.path.dirname(self.db_path)
        elif data_dir and not db_path:
            self.data_dir = os.path.abspath(data_dir)
            self.db_path = os.path.join(self.data_dir, "neko_storage.db")
        elif data_dir and db_path:
            self.db_path = os.path.abspath(db_path)
            self.data_dir = os.path.abspath(data_dir)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.data_dir = os.path.join(base_dir, "data")
            self.db_path = os.path.join(self.data_dir, "neko_storage.db")

        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.retrieval_service = MemoryRetrievalService(
            db_connection_provider=self.get_connection
        )

        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """
        Returns a thread-local SQLite connection with WAL mode and pragmas configured.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            # Execute mandatory pragmas
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA cache_size = -2000;")
            conn.execute("PRAGMA temp_store = MEMORY;")
            self._local.conn = conn
        return conn

    def close_connection(self):
        """Closes thread-local connection if open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def init_db(self):
        """Executes full DDL schema in a safe transaction."""
        with self.transaction() as conn:
            conn.executescript(self.DDL_SCHEMA)

    @contextmanager
    def transaction(self):
        """
        Thread-safe context manager for database transactions.
        Commits on normal exit, rolls back on exception.
        """
        with self._lock:
            conn = self.get_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def set_data_dir(self, new_dir: str):
        """
        Updates the active data directory and database path dynamically.
        """
        with self._lock:
            self.close_connection()
            self.data_dir = os.path.abspath(new_dir)
            os.makedirs(self.data_dir, exist_ok=True)
            self.db_path = os.path.join(self.data_dir, "neko_storage.db")
            self.init_db()

    # =========================================================================
    # TRANSACTIONAL ATOMIC JSON WRITER & READER
    # =========================================================================

    def atomic_save_json(
        self,
        path: str,
        data: Any,
        max_retries: int = 5,
        base_delay: float = 0.05,
    ) -> bool:
        """
        Thread-safe and process-safe atomic JSON writer.
        Uses uuid4 temp file, Windows msvcrt locking, fsync, and exponential backoff retry.
        """
        abs_path = os.path.abspath(path)
        dir_name = os.path.dirname(abs_path)
        os.makedirs(dir_name, exist_ok=True)

        tmp_path = f"{abs_path}.{uuid.uuid4().hex}.tmp"

        with self._lock:
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    if HAS_MSVCRT:
                        try:
                            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        except (OSError, IOError):
                            pass

                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                    if HAS_MSVCRT:
                        try:
                            f.seek(0)
                            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                        except (OSError, IOError):
                            pass

                # Atomic replace with exponential backoff retry
                last_err = None
                for attempt in range(max_retries):
                    try:
                        os.replace(tmp_path, abs_path)
                        return True
                    except (PermissionError, OSError) as e:
                        last_err = e
                        if attempt == max_retries - 1:
                            break
                        jitter = random.uniform(0.0, 0.02)
                        sleep_time = (base_delay * (2 ** attempt)) + jitter
                        time.sleep(sleep_time)

                raise StorageLockError(
                    f"Failed to replace {abs_path} after {max_retries} attempts: {last_err}"
                )
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def atomic_load_json(
        self,
        path: str,
        default: Any = None,
        max_retries: int = 5,
        base_delay: float = 0.05,
    ) -> Any:
        """
        Thread-safe atomic JSON reader.
        Retries on file lock contention; on corruption saves .corrupt.bak and raises StorageCorruptError.
        """
        abs_path = os.path.abspath(path)

        with self._lock:
            if not os.path.isfile(abs_path):
                return default

            content = None
            last_err = None
            for attempt in range(max_retries):
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    break
                except (UnicodeDecodeError, UnicodeError) as decode_err:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    corrupt_path = f"{abs_path}.{ts}.corrupt.bak"
                    try:
                        shutil.copy2(abs_path, corrupt_path)
                        logger.error(
                            "CORRUPT JSON FILE DETECTED: %s -> Preserved to %s",
                            abs_path,
                            corrupt_path,
                        )
                    except Exception as copy_err:
                        logger.error(
                            "Failed to copy corrupt file %s: %s", abs_path, copy_err
                        )
                    raise StorageCorruptError(
                        f"CORRUPT JSON DETECTED: Corrupt JSON payload in {abs_path}: {decode_err}"
                    ) from decode_err
                except (PermissionError, OSError) as e:
                    last_err = e
                    if attempt == max_retries - 1:
                        raise StorageLockError(
                            f"Failed to read {abs_path} due to file lock: {last_err}"
                        )
                    jitter = random.uniform(0.0, 0.01)
                    time.sleep((base_delay * (2 ** attempt)) + jitter)

            if not content or not content.strip():
                return default

            try:
                return json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError, UnicodeError) as decode_err:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                corrupt_path = f"{abs_path}.{ts}.corrupt.bak"
                try:
                    shutil.copy2(abs_path, corrupt_path)
                    logger.error(
                        "CORRUPT JSON FILE DETECTED: %s -> Preserved to %s",
                        abs_path,
                        corrupt_path,
                    )
                except Exception as copy_err:
                    logger.error(
                        "Failed to copy corrupt file %s: %s", abs_path, copy_err
                    )

                raise StorageCorruptError(
                    f"CORRUPT JSON DETECTED: Corrupt JSON payload in {abs_path}: {decode_err}"
                ) from decode_err

    # =========================================================================
    # NON-DESTRUCTIVE IDEMPOTENT MIGRATION
    # =========================================================================

    def migrate_legacy_data(self, source_dir: Optional[str] = None) -> Dict[str, int]:
        """
        Reads existing legacy JSON files in source_dir (default: active data_dir)
        and populates SQLite tables idempotently without deleting or modifying original files.
        """
        s_dir = os.path.abspath(source_dir or self.data_dir)
        counts = {
            "memories": 0,
            "tasks": 0,
            "projects": 0,
            "project_notes": 0,
            "project_pins": 0,
            "archive": 0,
            "chat_history": 0,
        }

        # 1. Migrate memory.json
        mem_path = os.path.join(s_dir, "memory.json")
        if os.path.isfile(mem_path):
            try:
                data = self.atomic_load_json(mem_path, [])
                if isinstance(data, list):
                    with self.transaction() as conn:
                        for item in data:
                            if not isinstance(item, dict):
                                continue
                            m_text = (item.get("text") or "").strip()
                            if not m_text:
                                continue
                            m_id = item.get("id") or f"mem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
                            m_cat = item.get("category") or "Generale"
                            m_proj = item.get("project_id")
                            m_imp = float(item.get("importance", 0.5))
                            m_pin = int(item.get("pinned", 0))
                            m_freq = int(item.get("frequency", 1))
                            m_src = item.get("source") or "legacy"

                            # Parse timestamps
                            ts = item.get("timestamp")
                            if ts is None:
                                created_str = item.get("created_at")
                                try:
                                    dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M")
                                    ts = dt.timestamp()
                                except Exception:
                                    ts = time.time()
                            created_at = float(ts)
                            updated_at = float(item.get("updated_at") or created_at)

                            # Idempotent insert: skip if id or exact text already exists
                            cur = conn.execute(
                                "SELECT 1 FROM memories WHERE id = ? OR LOWER(text) = LOWER(?)",
                                (m_id, m_text),
                            )
                            if cur.fetchone() is None:
                                conn.execute(
                                    """
                                    INSERT INTO memories (id, text, category, project_id, importance, pinned, frequency, source, created_at, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (m_id, m_text, m_cat, m_proj, m_imp, m_pin, m_freq, m_src, created_at, updated_at),
                                )
                                counts["memories"] += 1
            except Exception as e:
                logger.warning("Error migrating memory.json: %s", e)

        # 2. Migrate todo.json
        todo_path = os.path.join(s_dir, "todo.json")
        if os.path.isfile(todo_path):
            try:
                todos = self.atomic_load_json(todo_path, [])
                if isinstance(todos, list):
                    with self.transaction() as conn:
                        for item in todos:
                            if isinstance(item, str):
                                t_title = item.strip()
                                if not t_title:
                                    continue
                                t_id = f"task_{uuid.uuid4().hex[:12]}"
                                t_status = "QUEUED"
                                t_prio = 1
                                t_cur_step = 0
                                t_tot_step = 1
                                t_steps_json = "[]"
                                t_created = time.time()
                            elif isinstance(item, dict):
                                t_title = (item.get("title") or item.get("text") or "").strip()
                                if not t_title:
                                    continue
                                t_id = item.get("id") or f"task_{uuid.uuid4().hex[:12]}"
                                t_status = item.get("status") if item.get("status") in (
                                    'QUEUED', 'RUNNING', 'WAITING_INPUT', 'RETRY', 'COMPLETED', 'FAILED', 'ABORTED'
                                ) else "QUEUED"
                                prio_val = item.get("priority", 1)
                                if isinstance(prio_val, str):
                                    t_prio = 2 if prio_val.lower() == "high" else (0 if prio_val.lower() == "low" else 1)
                                else:
                                    try:
                                        t_prio = int(prio_val)
                                    except Exception:
                                        t_prio = 1
                                t_cur_step = int(item.get("current_step", 0))
                                t_tot_step = int(item.get("total_steps", 1))
                                t_steps_json = json.dumps(item.get("steps", []))
                                cr = item.get("created_at")
                                try:
                                    t_created = float(cr)
                                except (ValueError, TypeError):
                                    t_created = time.time()
                                t_project_id = item.get("project_id")
                            else:
                                continue

                            cur = conn.execute(
                                "SELECT 1 FROM tasks WHERE id = ? OR (LOWER(title) = LOWER(?) AND status = 'QUEUED')",
                                (t_id, t_title),
                            )
                            if cur.fetchone() is None:
                                conn.execute(
                                    """
                                    INSERT INTO tasks (id, title, status, project_id, priority, current_step, total_steps, steps_json, created_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (t_id, t_title, t_status, t_project_id, t_prio, t_cur_step, t_tot_step, t_steps_json, t_created),
                                )
                                counts["tasks"] += 1
            except Exception as e:
                logger.warning("Error migrating todo.json: %s", e)

        # 3. Migrate projects.json
        proj_path = os.path.join(s_dir, "projects.json")
        if os.path.isfile(proj_path):
            try:
                projs = self.atomic_load_json(proj_path, [])
                if isinstance(projs, list):
                    with self.transaction() as conn:
                        for p in projs:
                            if not isinstance(p, dict):
                                continue
                            p_id = p.get("id") or f"proj_{uuid.uuid4().hex[:12]}"
                            p_name = (p.get("name") or "").strip()
                            if not p_name:
                                continue
                            p_client = (p.get("client") or "").strip()
                            p_color = p.get("color") or "#4facfe"
                            p_is_active = 1 if p.get("status") == "active" else 0

                            ts = p.get("last_ts")
                            if ts is None:
                                try:
                                    dt = datetime.strptime(p.get("created_at", ""), "%Y-%m-%d %H:%M")
                                    ts = dt.timestamp()
                                except Exception:
                                    ts = time.time()
                            p_created = float(ts)
                            p_updated = float(ts)

                            cur = conn.execute("SELECT 1 FROM projects WHERE id = ?", (p_id,))
                            if cur.fetchone() is None:
                                conn.execute(
                                    """
                                    INSERT INTO projects (id, name, client, color, is_active, created_at, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (p_id, p_name, p_client, p_color, p_is_active, p_created, p_updated),
                                )
                                counts["projects"] += 1

                            # Migrate notes
                            for idx, note in enumerate(p.get("notes", [])):
                                n_text = (note or "").strip()
                                if not n_text:
                                    continue
                                n_id = f"pnote_{p_id}_{idx}"
                                cur_n = conn.execute(
                                    "SELECT 1 FROM project_notes WHERE id = ? OR (project_id = ? AND note = ?)",
                                    (n_id, p_id, n_text)
                                )
                                if cur_n.fetchone() is None:
                                    conn.execute(
                                        "INSERT INTO project_notes (id, project_id, note, created_at) VALUES (?, ?, ?, ?)",
                                        (n_id, p_id, n_text, p_updated),
                                    )
                                    counts["project_notes"] += 1

                            # Migrate pins
                            for idx, pin in enumerate(p.get("pins", [])):
                                if not isinstance(pin, dict):
                                    continue
                                pin_path = (pin.get("path") or "").strip()
                                if not pin_path:
                                    continue
                                pin_id = pin.get("id") or f"pin_{p_id}_{idx}"
                                pin_lbl = pin.get("label") or os.path.basename(pin_path)
                                cur_p = conn.execute("SELECT 1 FROM project_pins WHERE id = ?", (pin_id,))
                                if cur_p.fetchone() is None:
                                    conn.execute(
                                        "INSERT INTO project_pins (id, project_id, label, path, created_at) VALUES (?, ?, ?, ?, ?)",
                                        (pin_id, p_id, pin_lbl, pin_path, p_updated),
                                    )
                                    counts["project_pins"] += 1
            except Exception as e:
                logger.warning("Error migrating projects.json: %s", e)

        # 4. Migrate archive.json
        arch_path = os.path.join(s_dir, "archive.json")
        if os.path.isfile(arch_path):
            try:
                arch_items = self.atomic_load_json(arch_path, [])
                if isinstance(arch_items, list):
                    with self.transaction() as conn:
                        for a in arch_items:
                            if not isinstance(a, dict):
                                continue
                            a_id = a.get("id") or f"arch_{uuid.uuid4().hex[:12]}"
                            cur = conn.execute("SELECT 1 FROM archive WHERE id = ?", (a_id,))
                            if cur.fetchone() is None:
                                a_notes = a.get("client") or a.get("name") or ""
                                a_ts = time.time()
                                if a.get("archived_at"):
                                    try:
                                        a_ts = datetime.strptime(a["archived_at"], "%Y-%m-%d %H:%M").timestamp()
                                    except Exception:
                                        pass
                                conn.execute(
                                    """
                                    INSERT INTO archive (id, original_type, payload_json, archived_at, notes)
                                    VALUES (?, 'project', ?, ?, ?)
                                    """,
                                    (a_id, json.dumps(a, ensure_ascii=False), a_ts, a_notes),
                                )
                                counts["archive"] += 1
            except Exception as e:
                logger.warning("Error migrating archive.json: %s", e)

        # 5. Migrate chat_history.json
        chat_path = os.path.join(s_dir, "chat_history.json")
        if os.path.isfile(chat_path):
            try:
                chats = self.atomic_load_json(chat_path, [])
                if isinstance(chats, list):
                    with self.transaction() as conn:
                        for c in chats:
                            if not isinstance(c, dict):
                                continue
                            c_id = c.get("id") or f"chat_{uuid.uuid4().hex[:12]}"
                            c_role = c.get("role") or "user"
                            if c_role not in ('user', 'assistant', 'system'):
                                c_role = "user"
                            c_text = c.get("text") or ""
                            c_time = c.get("time") or datetime.now().strftime("%H:%M")
                            c_ts = float(c.get("timestamp") or time.time())
                            c_model = c.get("model") or ""
                            c_actions = json.dumps(c.get("actions", []), ensure_ascii=False)

                            cur = conn.execute("SELECT 1 FROM chat_history WHERE id = ?", (c_id,))
                            if cur.fetchone() is None:
                                conn.execute(
                                    """
                                    INSERT INTO chat_history (id, role, text, time_label, timestamp, model, actions_json)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (c_id, c_role, c_text, c_time, c_ts, c_model, c_actions),
                                )
                                counts["chat_history"] += 1
            except Exception as e:
                logger.warning("Error migrating chat_history.json: %s", e)

        logger.info("Non-destructive migration completed: %s", counts)
        return counts

    # =========================================================================
    # PROJECT.MD CONTRACT METHODS & RETRIEVAL INTEGRATION
    # =========================================================================

    def upsert_memory(
        self,
        text: str,
        category: str = "Generale",
        project_id: Optional[str] = None,
        importance: float = 0.5,
        pinned: bool = False,
        source: str = "user",
    ) -> str:
        """
        Hybrid deduplication (threshold >= 0.85): updates timestamp & frequency if duplicate,
        inserts new row if unique. Returns memory_id.
        """
        with self._lock:
            mid, _ = self.retrieval_service.upsert_memory(
                text=text,
                category=category,
                project_id=project_id,
                importance=importance,
                pinned=pinned,
                source=source,
            )
            return mid

    def add_memory(
        self,
        text: str,
        category: str = "Generale",
        project_id: Optional[str] = None,
        importance: float = 0.5,
        pinned: int = 0,
        source: str = "user",
    ) -> Dict[str, Any]:
        """Inserts memory and returns record dictionary."""
        with self._lock:
            clean_text = (text or "").strip()
            if not clean_text:
                raise ValueError("Memory text cannot be empty.")
            now = time.time()
            mem_id = f"mem_{int(now * 1000)}_{uuid.uuid4().hex[:6]}"
            with self.transaction() as conn:
                if project_id:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                            (project_id, project_id, now, now),
                        )
                    except Exception:
                        pass
                conn.execute(
                    """
                    INSERT INTO memories (id, text, category, project_id, importance, pinned, frequency, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (mem_id, clean_text, category, project_id, importance, pinned, source, now, now),
                )
            return {
                "id": mem_id,
                "text": clean_text,
                "category": category,
                "project_id": project_id,
                "importance": importance,
                "pinned": pinned,
                "frequency": 1,
                "source": source,
                "created_at": now,
                "updated_at": now,
            }

    insert_memory = add_memory

    def get_memories(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all memories, optionally filtered by project_id."""
        conn = self.get_connection()
        if project_id:
            rows = conn.execute(
                "SELECT * FROM memories WHERE project_id = ? OR project_id IS NULL ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def pin_memory(self, memory_id: str, pinned: bool = True) -> bool:
        """Sets the pinned flag (pinned=1 exempts memory from time decay, lambda=0)."""
        val = 1 if pinned else 0
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE memories SET pinned = ?, updated_at = ? WHERE id = ?",
                (val, time.time(), memory_id),
            )
            return cur.rowcount > 0

    def delete_memory(self, memory_id: str) -> bool:
        """Deletes a memory by ID."""
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    def search_memories(
        self,
        query: str,
        project_id: Optional[str] = None,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        """
        FTS5 BM25 combined with time-decay scoring.
        Returns ranked list of memory dictionaries.
        """
        with self._lock:
            return self.retrieval_service.search_memories(
                query=query,
                active_project_id=project_id,
                limit=limit,
            )

    def build_context_prompt(
        self,
        query: str,
        active_project: Optional[str] = None,
        active_dcc: Optional[str] = None,
    ) -> Tuple[str, int]:
        """
        Builds formatted context prompt string within num_ctx: 8192 budget (<= 2450 tokens prompt).
        Guarantees 100% preservation of pinned rules and >= 5742 tokens free headroom.
        Returns (prompt_str, estimated_tokens).
        """
        with self._lock:
            conn = self.get_connection()
            pinned_rows = conn.execute(
                "SELECT text FROM memories WHERE pinned = 1 ORDER BY updated_at DESC"
            ).fetchall()
            pinned_traits = [r["text"] for r in pinned_rows]

            # Dynamic search
            dynamic_results = self.search_memories(query=query, project_id=active_project, limit=10)
            dynamic_mems = [m["text"] for m in dynamic_results if not m.get("pinned")]

            return self.retrieval_service.budget_manager.build_context_prompt(
                query=query,
                active_project=active_project,
                active_dcc=active_dcc,
                pinned_traits=pinned_traits,
                dynamic_memories=dynamic_mems,
            )

    def get_chat_history(self, limit: int = 60) -> List[Dict[str, Any]]:
        """Retrieves recent chat history records."""
        conn = self.get_connection()
        rows = conn.execute(
            "SELECT * FROM chat_history ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        results = [dict(r) for r in reversed(rows)]
        for r in results:
            if "actions_json" in r and r["actions_json"]:
                try:
                    r["actions"] = json.loads(r["actions_json"])
                except Exception:
                    r["actions"] = []
            else:
                r["actions"] = []
        return results

    def append_chat_turn(
        self,
        user_text: str,
        reply_text: str,
        model: str = "",
        actions: Optional[List[Dict]] = None,
    ) -> Tuple[str, str]:
        """Appends user and assistant dialogue entries atomically."""
        now = time.time()
        now_str = datetime.now().strftime("%H:%M")
        u_id = f"u_{int(now * 1000)}"
        a_id = f"a_{int(now * 1000) + 1}"
        actions_str = json.dumps(actions or [], ensure_ascii=False)

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO chat_history (id, role, text, time_label, timestamp, model, actions_json)
                VALUES (?, 'user', ?, ?, ?, '', '[]')
                """,
                (u_id, user_text, now_str, now),
            )
            conn.execute(
                """
                INSERT INTO chat_history (id, role, text, time_label, timestamp, model, actions_json)
                VALUES (?, 'assistant', ?, ?, ?, ?, ?)
                """,
                (a_id, reply_text, now_str, now, model, actions_str),
            )
        return u_id, a_id

    def clear_chat_history(self) -> bool:
        """Deletes all chat history rows."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM chat_history")
            return True


# Global Module Singleton Accessor
_global_storage_manager: Optional[StorageManager] = None
_global_storage_lock = threading.Lock()


def get_storage_manager(data_dir: Optional[str] = None) -> StorageManager:
    """Returns the process-wide StorageManager singleton."""
    global _global_storage_manager
    with _global_storage_lock:
        if _global_storage_manager is None:
            _global_storage_manager = StorageManager(data_dir=data_dir)
        elif data_dir and os.path.abspath(data_dir) != os.path.abspath(_global_storage_manager.data_dir):
            _global_storage_manager.set_data_dir(data_dir)
        return _global_storage_manager
