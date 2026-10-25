"""
cloud/storage/sqlite_adapter.py - SQLite Storage Adapter with WAL Mode & Monotonic Sequencing.

Implements BaseStorageAdapter using Python's standard sqlite3 module.
Features:
- PRAGMA journal_mode = WAL for concurrent reader/writer isolation
- PRAGMA busy_timeout = 5000ms for thread contention mitigation
- Atomic sequence generation via AUTOINCREMENT journal table (sync_mutations)
- Natural compound primary key (entity_type, entity_id) on sync_entities
- Full 3-tier CRDT LWW tie-breaking: UTC timestamp -> Tombstone -> Canonical JSON
- Thread-safe via internal RLock protecting all connection accesses
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from cloud.storage.base import BaseStorageAdapter


def _canonical_json(data: Any) -> str:
    """Helper for deterministic canonical JSON serialization."""
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _parse_ts(ts: Optional[Union[str, float, int]]) -> float:
    """Helper to convert ISO timestamp string to POSIX epoch float."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str) or not ts.strip():
        return 0.0
    try:
        clean = ts.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(clean).timestamp()
    except Exception:
        return 0.0


class SQLiteStorageAdapter(BaseStorageAdapter):
    """
    Production-grade SQLite Storage Adapter for Neko Cloud Server.
    Provides local, hermetic, zero-dependency storage with WAL mode.
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self.initialize()

    @property
    def adapter_name(self) -> str:
        return "SQLiteStorageAdapter"

    def _apply_pragmas(self) -> None:
        """Configures SQLite connection for high concurrency and data integrity."""
        with self._lock:
            c = self.conn.cursor()
            c.execute("PRAGMA journal_mode = WAL;")
            c.execute("PRAGMA busy_timeout = 5000;")
            c.execute("PRAGMA foreign_keys = ON;")
            c.execute("PRAGMA synchronous = NORMAL;")

    def initialize(self) -> None:
        """Initializes tables and indexes."""
        with self._lock:
            c = self.conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS sync_devices (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                client_version TEXT NOT NULL,
                registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
                auth_token_hash TEXT NOT NULL,
                last_sequence INTEGER DEFAULT 0
            );
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS sync_mutations (
                server_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                mutation_id TEXT UNIQUE NOT NULL,
                device_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                deleted BOOLEAN NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS sync_entities (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                deleted BOOLEAN NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                server_sequence INTEGER NOT NULL,
                PRIMARY KEY (entity_type, entity_id)
            );
            """)

            # Performance indexes for delta queries and sync operations
            c.execute("CREATE INDEX IF NOT EXISTS idx_entities_seq ON sync_entities(server_sequence);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON sync_entities(entity_type);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_entities_device ON sync_entities(device_id);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mutations_seq ON sync_mutations(server_sequence);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mutations_device ON sync_mutations(device_id);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mutations_entity ON sync_mutations(entity_type, entity_id);")
            self.conn.commit()

    def register_device(
        self,
        device_id: str,
        device_name: str,
        client_version: str,
        token_hash: str
    ) -> bool:
        """Registers or updates a client workstation."""
        with self._lock:
            c = self.conn.cursor()
            c.execute("""
            INSERT INTO sync_devices (device_id, device_name, client_version, auth_token_hash, last_seen_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(device_id) DO UPDATE SET
                device_name = excluded.device_name,
                client_version = excluded.client_version,
                auth_token_hash = excluded.auth_token_hash,
                last_seen_at = CURRENT_TIMESTAMP;
            """, (device_id, device_name, client_version, token_hash))
            self.conn.commit()
            return True

    def authenticate_device(self, device_id: str, token: str) -> bool:
        """Authenticates a device against stored SHA-256 hash."""
        with self._lock:
            c = self.conn.cursor()
            row = c.execute(
                "SELECT auth_token_hash FROM sync_devices WHERE device_id = ?",
                (device_id,)
            ).fetchone()
            if not row:
                return False
            expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if row["auth_token_hash"] == expected_hash:
                c.execute(
                    "UPDATE sync_devices SET last_seen_at = CURRENT_TIMESTAMP WHERE device_id = ?",
                    (device_id,)
                )
                self.conn.commit()
                return True
            return False

    def apply_mutation(self, mutation: Dict[str, Any]) -> Tuple[bool, int]:
        """
        Applies a mutation under 3-tier CRDT LWW rules.
        Returns: (applied: bool, server_sequence: int).
        """
        with self._lock:
            c = self.conn.cursor()
            ent_type = str(mutation.get("entity_type", ""))
            ent_id = str(mutation.get("entity_id") or mutation.get("id", ""))
            dev_id = str(mutation.get("device_id", "unknown"))

            upd = str(mutation.get("updated_at") or datetime.now(timezone.utc).isoformat())
            if _parse_ts(upd) > time.time() + 60.0:
                return False, 0

            raw_v = mutation.get("version", 1)
            try:
                v = int(raw_v)
            except (ValueError, TypeError):
                try:
                    v = int(str(raw_v).split(".")[0])
                except Exception:
                    v = 1

            del_flag = 1 if mutation.get("deleted", False) else 0

            raw_payload = mutation.get("payload")
            if raw_payload is None:
                raw_payload = mutation.get("data", {})
            payload_str = _canonical_json(raw_payload)
            c_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

            # Query existing state for CRDT LWW tie-break
            row = c.execute(
                "SELECT updated_at, deleted, payload, server_sequence FROM sync_entities WHERE entity_type = ? AND entity_id = ?",
                (ent_type, ent_id)
            ).fetchone()

            if row:
                existing_seq = row["server_sequence"]
                t_exist = _parse_ts(row["updated_at"])
                t_incoming = _parse_ts(upd)
                existing_del = bool(row["deleted"])
                incoming_del = bool(del_flag)
                existing_payload = row["payload"]

                # 1. Level 1: Timestamp dominance
                if t_exist > t_incoming:
                    return False, existing_seq
                elif abs(t_exist - t_incoming) < 1e-9:
                    # 2. Level 2: Tombstone wins tie-break
                    if existing_del and not incoming_del:
                        return False, existing_seq
                    elif existing_del == incoming_del:
                        # 3. Level 3: Canonical JSON lexicographical comparison
                        if existing_payload >= payload_str:
                            return False, existing_seq
                        # incoming payload_str > existing_payload -> incoming wins!
                    # not existing_del and incoming_del -> incoming tombstone wins!
                # t_incoming > t_exist -> incoming wins!

            # Incoming mutation won tie-break (or entity is brand new)
            mut_id = mutation.get("mutation_id")
            if not mut_id:
                mut_id = hashlib.sha256(f"{ent_type}_{ent_id}_{time.time_ns()}".encode()).hexdigest()[:16]

            # 1. Journal mutation and generate auto-increment server_sequence
            c.execute("""
            INSERT OR REPLACE INTO sync_mutations (
                mutation_id, device_id, entity_type, entity_id, version, updated_at, deleted, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (mut_id, dev_id, ent_type, ent_id, v, upd, del_flag, payload_str))

            seq = c.lastrowid
            if seq is None or seq == 0:
                seq_row = c.execute(
                    "SELECT server_sequence FROM sync_mutations WHERE mutation_id = ?",
                    (mut_id,)
                ).fetchone()
                seq = seq_row["server_sequence"] if seq_row else 1

            # 2. Upsert entity into sync_entities with the assigned server_sequence
            c.execute("""
            INSERT INTO sync_entities (
                entity_type, entity_id, device_id, version, updated_at, deleted, payload, content_hash, server_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                device_id = excluded.device_id,
                version = excluded.version,
                updated_at = excluded.updated_at,
                deleted = excluded.deleted,
                payload = excluded.payload,
                content_hash = excluded.content_hash,
                server_sequence = excluded.server_sequence;
            """, (ent_type, ent_id, dev_id, v, upd, del_flag, payload_str, c_hash, seq))

            self.conn.commit()
            return True, seq

    def pull_mutations(
        self,
        since_sequence: int,
        exclude_device: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Queries mutations newer than since_sequence."""
        with self._lock:
            c = self.conn.cursor()
            query_since = since_sequence if since_sequence < 0 else max(0, since_sequence)
            query = "SELECT * FROM sync_entities WHERE server_sequence > ?"
            params: List[Any] = [query_since]

            if exclude_device:
                query += " AND device_id != ?"
                params.append(exclude_device)
            query += " ORDER BY server_sequence ASC"

            rows = c.execute(query, params).fetchall()
            max_seq = max(since_sequence, 0)
            mutations: List[Dict[str, Any]] = []

            for r in rows:
                seq = r["server_sequence"]
                max_seq = max(max_seq, seq)
                try:
                    decoded = json.loads(r["payload"])
                except Exception:
                    decoded = {}

                mutations.append({
                    "id": r["entity_id"],
                    "entity_id": r["entity_id"],
                    "entity_type": r["entity_type"],
                    "device_id": r["device_id"],
                    "version": r["version"],
                    "updated_at": r["updated_at"],
                    "deleted": bool(r["deleted"]),
                    "data": decoded,
                    "payload": decoded,
                    "server_sequence": seq,
                })

            return mutations, max_seq

    def get_status(self) -> Dict[str, Any]:
        """Returns cloud storage status telemetry."""
        with self._lock:
            c = self.conn.cursor()
            seq_row = c.execute("SELECT MAX(server_sequence) as max_seq FROM sync_entities").fetchone()
            cnt_row = c.execute("SELECT COUNT(*) as cnt FROM sync_entities WHERE deleted = 0").fetchone()
            dev_row = c.execute("SELECT COUNT(*) as dev_cnt FROM sync_devices").fetchone()

            max_seq = (seq_row["max_seq"] or 0) if seq_row else 0
            active_cnt = (cnt_row["cnt"] or 0) if cnt_row else 0
            active_devs = (dev_row["dev_cnt"] or 0) if dev_row else 0

            return {
                "server_sequence": max_seq,
                "active_entities": active_cnt,
                "active_devices": active_devs,
                "adapter": self.adapter_name,
                "status": "healthy"
            }

    def close(self) -> None:
        """Closes SQLite connection."""
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
