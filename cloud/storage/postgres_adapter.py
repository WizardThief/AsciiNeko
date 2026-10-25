"""
cloud/storage/postgres_adapter.py - PostgreSQL 13+ / Supabase Storage Adapter.

Implements BaseStorageAdapter for PostgreSQL/Supabase production deployments.
Features:
- DDL schema compatible with PostgreSQL 13+ and Supabase JSONB extensions
- Global sequence tracking via PostgreSQL sequence generator (`sync_global_sequence`)
- Upsert semantics via `ON CONFLICT (entity_type, entity_id) DO UPDATE SET`
- Support for `psycopg2` and `psycopg` driver backends with dynamic import
- Parameterized SQL execution (%s) against SQL injection
- 3-tier CRDT LWW tie-breaking: UTC timestamp -> Tombstone -> Canonical JSON
- Monotonic sequence generation and watermark-based tombstone purging
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from cloud.storage.base import BaseStorageAdapter

logger = logging.getLogger("neko_cloud.postgres")


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


class PostgresStorageAdapter(BaseStorageAdapter):
    """
    Genuine PostgreSQL Storage Adapter for Scalable Multi-Workstation Production Deployments.
    Works with psycopg2 or psycopg3 backends for cloud PostgreSQL or Supabase.
    """

    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or os.environ.get("DATABASE_URL", "postgresql://localhost:5432/neko_cloud_db")
        self._lock = threading.RLock()
        self._conn = None
        self._driver_module = self._detect_driver()
        if self._driver_module is not None:
            try:
                self._get_connection()
                self.initialize()
            except Exception as e:
                logger.warning("PostgreSQL driver detected but connection failed on init: %s", e)

    @property
    def adapter_name(self) -> str:
        return "PostgresStorageAdapter"

    def _detect_driver(self) -> Optional[Any]:
        try:
            import psycopg2  # type: ignore
            return psycopg2
        except ImportError:
            try:
                import psycopg  # type: ignore
                return psycopg
            except ImportError:
                return None

    def _get_connection(self) -> Any:
        with self._lock:
            if self._conn is not None:
                return self._conn
            driver = self._driver_module or self._detect_driver()
            if not driver:
                raise ImportError("PostgreSQL driver (psycopg2 or psycopg) is required for execution.")
            try:
                self._conn = driver.connect(self.dsn)
                return self._conn
            except Exception as e:
                raise RuntimeError(f"Failed to connect to PostgreSQL at {self.dsn}: {e}") from e

    def initialize(self) -> None:
        """Executes genuine DDL schema creation."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_devices (
                device_id VARCHAR(64) PRIMARY KEY,
                device_name VARCHAR(128) NOT NULL,
                client_version VARCHAR(32) NOT NULL,
                registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                auth_token_hash VARCHAR(128) NOT NULL,
                last_sequence BIGINT DEFAULT 0
            );
            """)

            cur.execute("""
            CREATE SEQUENCE IF NOT EXISTS sync_global_sequence START WITH 1 INCREMENT BY 1;
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_mutations (
                server_sequence BIGINT PRIMARY KEY DEFAULT nextval('sync_global_sequence'),
                mutation_id VARCHAR(64) UNIQUE NOT NULL,
                device_id VARCHAR(64) NOT NULL,
                entity_type VARCHAR(32) NOT NULL,
                entity_id VARCHAR(128) NOT NULL,
                version BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                deleted BOOLEAN NOT NULL DEFAULT FALSE,
                payload JSONB NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_entities (
                entity_type VARCHAR(32) NOT NULL,
                entity_id VARCHAR(128) NOT NULL,
                device_id VARCHAR(64) NOT NULL,
                version BIGINT NOT NULL DEFAULT 1,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                deleted BOOLEAN NOT NULL DEFAULT FALSE,
                payload JSONB NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                server_sequence BIGINT NOT NULL,
                PRIMARY KEY (entity_type, entity_id)
            );
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_seq ON sync_entities(server_sequence);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON sync_entities(entity_type);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_device ON sync_entities(device_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mutations_seq ON sync_mutations(server_sequence);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mutations_device ON sync_mutations(device_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mutations_entity ON sync_mutations(entity_type, entity_id);")

            conn.commit()

    def register_device(
        self,
        device_id: str,
        device_name: str,
        client_version: str,
        token_hash: str
    ) -> bool:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO sync_devices (device_id, device_name, client_version, auth_token_hash, last_seen_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (device_id) DO UPDATE SET
                device_name = EXCLUDED.device_name,
                client_version = EXCLUDED.client_version,
                auth_token_hash = EXCLUDED.auth_token_hash,
                last_seen_at = CURRENT_TIMESTAMP;
            """, (device_id, device_name, client_version, token_hash))
            conn.commit()
            return True

    def authenticate_device(self, device_id: str, token: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT auth_token_hash FROM sync_devices WHERE device_id = %s;",
                (device_id,)
            )
            row = cur.fetchone()
            if not row:
                return False
            expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            actual_hash = row[0] if isinstance(row, (tuple, list)) else row.get("auth_token_hash")
            if actual_hash == expected_hash:
                cur.execute(
                    "UPDATE sync_devices SET last_seen_at = CURRENT_TIMESTAMP WHERE device_id = %s;",
                    (device_id,)
                )
                conn.commit()
                return True
            return False

    def apply_mutation(self, mutation: Dict[str, Any]) -> Tuple[bool, int]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()

            ent_type = str(mutation.get("entity_type", ""))
            ent_id = str(mutation.get("entity_id") or mutation.get("id", ""))
            dev_id = str(mutation.get("device_id", "unknown"))

            # Clock skew guardrail: reject timestamps > 60s in future
            upd = str(mutation.get("updated_at") or datetime.now(timezone.utc).isoformat())
            if _parse_ts(upd) > time.time() + 60.0:
                return False, 0

            # Version resilience: support semver strings
            raw_v = mutation.get("version", 1)
            try:
                v = int(raw_v)
            except (ValueError, TypeError):
                try:
                    v = int(str(raw_v).split(".")[0])
                except Exception:
                    v = 1

            del_flag = bool(mutation.get("deleted", False))

            raw_payload = mutation.get("payload")
            if raw_payload is None:
                raw_payload = mutation.get("data", {})
            payload_str = _canonical_json(raw_payload)
            c_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

            # Query existing entity for 3-tier CRDT LWW tie-breaking
            cur.execute(
                "SELECT updated_at, deleted, payload, server_sequence FROM sync_entities WHERE entity_type = %s AND entity_id = %s;",
                (ent_type, ent_id)
            )
            row = cur.fetchone()
            if row:
                if isinstance(row, (tuple, list)):
                    exist_upd, exist_del, exist_payload, exist_seq = row[0], row[1], row[2], row[3]
                else:
                    exist_upd = row.get("updated_at")
                    exist_del = row.get("deleted")
                    exist_payload = row.get("payload")
                    exist_seq = row.get("server_sequence")

                t_exist = _parse_ts(exist_upd.isoformat() if hasattr(exist_upd, "isoformat") else str(exist_upd))
                t_incoming = _parse_ts(upd)
                existing_del = bool(exist_del)
                incoming_del = bool(del_flag)

                if isinstance(exist_payload, dict):
                    existing_payload_str = _canonical_json(exist_payload)
                else:
                    existing_payload_str = str(exist_payload)

                # 1. Level 1: Timestamp dominance
                if t_exist > t_incoming:
                    return False, exist_seq
                elif abs(t_exist - t_incoming) < 1e-9:
                    # 2. Level 2: Tombstone wins tie-break
                    if existing_del and not incoming_del:
                        return False, exist_seq
                    elif existing_del == incoming_del:
                        # 3. Level 3: Canonical JSON lexicographical comparison
                        if existing_payload_str >= payload_str:
                            return False, exist_seq

            # Incoming mutation won tie-break
            mut_id = mutation.get("mutation_id")
            if not mut_id:
                mut_id = hashlib.sha256(f"{ent_type}_{ent_id}_{time.time_ns()}".encode()).hexdigest()[:16]

            # 1. Journal mutation and assign server_sequence
            cur.execute("""
            INSERT INTO sync_mutations (
                mutation_id, device_id, entity_type, entity_id, version, updated_at, deleted, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (mutation_id) DO UPDATE SET
                device_id = EXCLUDED.device_id,
                entity_type = EXCLUDED.entity_type,
                entity_id = EXCLUDED.entity_id,
                version = EXCLUDED.version,
                updated_at = EXCLUDED.updated_at,
                deleted = EXCLUDED.deleted,
                payload = EXCLUDED.payload
            RETURNING server_sequence;
            """, (mut_id, dev_id, ent_type, ent_id, v, upd, del_flag, payload_str))

            seq_row = cur.fetchone()
            seq = seq_row[0] if seq_row else 1

            # 2. Upsert entity into sync_entities
            cur.execute("""
            INSERT INTO sync_entities (
                entity_type, entity_id, device_id, version, updated_at, deleted, payload, content_hash, server_sequence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_type, entity_id) DO UPDATE SET
                device_id = EXCLUDED.device_id,
                version = EXCLUDED.version,
                updated_at = EXCLUDED.updated_at,
                deleted = EXCLUDED.deleted,
                payload = EXCLUDED.payload,
                content_hash = EXCLUDED.content_hash,
                server_sequence = EXCLUDED.server_sequence;
            """, (ent_type, ent_id, dev_id, v, upd, del_flag, payload_str, c_hash, seq))

            conn.commit()
            return True, seq

    def pull_mutations(
        self,
        since_sequence: int,
        exclude_device: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()

            query_since = since_sequence if since_sequence < 0 else max(0, since_sequence)
            query = """
            SELECT entity_type, entity_id, device_id, version, updated_at, deleted, payload, server_sequence
            FROM sync_entities WHERE server_sequence > %s
            """
            params: List[Any] = [query_since]

            if exclude_device:
                query += " AND device_id != %s"
                params.append(exclude_device)
            query += " ORDER BY server_sequence ASC;"

            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            max_seq = max(since_sequence, 0)
            mutations: List[Dict[str, Any]] = []

            for r in rows:
                if isinstance(r, (tuple, list)):
                    e_type, e_id, d_id, ver, u_at, is_del, p_load, s_seq = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
                else:
                    e_type = r.get("entity_type")
                    e_id = r.get("entity_id")
                    d_id = r.get("device_id")
                    ver = r.get("version")
                    u_at = r.get("updated_at")
                    is_del = r.get("deleted")
                    p_load = r.get("payload")
                    s_seq = r.get("server_sequence")

                max_seq = max(max_seq, s_seq)
                if isinstance(p_load, dict):
                    decoded = p_load
                elif isinstance(p_load, str):
                    try:
                        decoded = json.loads(p_load)
                    except Exception:
                        decoded = {}
                else:
                    decoded = {}

                iso_upd = u_at.isoformat() if hasattr(u_at, "isoformat") else str(u_at)
                mutations.append({
                    "id": e_id,
                    "entity_id": e_id,
                    "entity_type": e_type,
                    "device_id": d_id,
                    "version": ver,
                    "updated_at": iso_upd,
                    "deleted": bool(is_del),
                    "data": decoded,
                    "payload": decoded,
                    "server_sequence": s_seq,
                })

            return mutations, max_seq

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()

            cur.execute("SELECT COALESCE(MAX(server_sequence), 0) FROM sync_entities;")
            max_seq = cur.fetchone()[0] or 0

            cur.execute("SELECT COUNT(*) FROM sync_entities WHERE deleted = FALSE;")
            active_cnt = cur.fetchone()[0] or 0

            cur.execute("SELECT COUNT(*) FROM sync_devices;")
            dev_cnt = cur.fetchone()[0] or 0

            return {
                "server_sequence": max_seq,
                "active_entities": active_cnt,
                "active_devices": dev_cnt,
                "adapter": self.adapter_name,
                "status": "ready"
            }

    def purge_tombstones(self, days_threshold: int = 90) -> int:
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
            WITH min_seq AS (
                SELECT COALESCE(MIN(last_sequence), 0) AS min_s
                FROM sync_devices
                WHERE last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
            )
            DELETE FROM sync_entities
            WHERE deleted = TRUE
              AND updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
              AND server_sequence <= (SELECT min_s FROM min_seq);
            """, (days_threshold,))
            deleted_count = cur.rowcount if hasattr(cur, "rowcount") else 0
            conn.commit()
            return max(0, deleted_count)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
