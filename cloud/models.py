"""
cloud/models.py - Unified Data Models, DDL Schemas, and Serialization Utilities.

Defines the core entity structures, CRDT metadata, SQL DDL schemas for SQLite and PostgreSQL,
and canonical JSON serialization functions for deterministic tie-breaking.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Canonical Serialization & Timestamp Helpers
# ---------------------------------------------------------------------------

def canonical_json(data: Any) -> str:
    """
    Serializes a Python object to canonical JSON string with sorted keys,
    strict ASCII escaping, and no superfluous whitespace.
    Guarantees deterministic byte-for-byte serialization across platforms and Python versions.
    """
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def compute_content_hash(data: Any) -> str:
    """Computes SHA-256 digest of canonical JSON payload."""
    if isinstance(data, (dict, list)):
        serialized = canonical_json(data)
    elif isinstance(data, str):
        serialized = data
    else:
        serialized = str(data)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_iso_timestamp(ts: Optional[Union[str, float, int]]) -> float:
    """
    Parses an ISO-8601 timestamp string into POSIX epoch seconds (float).
    Handles UTC 'Z' suffixes and offset notations (+00:00).
    Returns 0.0 on malformed input or None.
    """
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


def format_iso_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Formats a datetime object to strict 27-character ISO-8601 UTC string:
    'YYYY-MM-DDTHH:MM:SS.ffffffZ'.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Core Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Device:
    """Represents a registered client workstation."""
    device_id: str
    device_name: str
    client_version: str
    auth_token_hash: str
    registered_at: str = field(default_factory=format_iso_timestamp)
    last_seen_at: str = field(default_factory=format_iso_timestamp)
    last_sequence: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "client_version": self.client_version,
            "registered_at": self.registered_at,
            "last_seen_at": self.last_seen_at,
            "last_sequence": self.last_sequence,
        }


@dataclass
class SyncEntity:
    """Represents a synchronized ecosystem entity (CRDT LWW record)."""
    entity_type: str
    entity_id: str
    device_id: str
    version: int
    updated_at: str
    deleted: bool
    payload: Dict[str, Any]
    content_hash: str
    server_sequence: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.entity_id,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "device_id": self.device_id,
            "version": self.version,
            "updated_at": self.updated_at,
            "deleted": self.deleted,
            "data": self.payload,
            "payload": self.payload,
            "content_hash": self.content_hash,
            "server_sequence": self.server_sequence,
        }


@dataclass
class SyncMutation:
    """Represents an atomic mutation journal entry."""
    mutation_id: str
    device_id: str
    entity_type: str
    entity_id: str
    version: int
    updated_at: str
    deleted: bool
    payload: Dict[str, Any]
    server_sequence: int
    applied_at: str = field(default_factory=format_iso_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "device_id": self.device_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "version": self.version,
            "updated_at": self.updated_at,
            "deleted": self.deleted,
            "data": self.payload,
            "payload": self.payload,
            "server_sequence": self.server_sequence,
            "applied_at": self.applied_at,
        }


# ---------------------------------------------------------------------------
# SQL DDL Schemas
# ---------------------------------------------------------------------------

SQLITE_SCHEMA_DDL = """
-- SQLite WAL Schema for Neko Cloud Server
CREATE TABLE IF NOT EXISTS sync_devices (
    device_id TEXT PRIMARY KEY,
    device_name TEXT NOT NULL,
    client_version TEXT NOT NULL,
    registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    auth_token_hash TEXT NOT NULL,
    last_sequence INTEGER DEFAULT 0
);

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

CREATE INDEX IF NOT EXISTS idx_entities_seq ON sync_entities(server_sequence);
CREATE INDEX IF NOT EXISTS idx_entities_type ON sync_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_device ON sync_entities(device_id);
CREATE INDEX IF NOT EXISTS idx_mutations_seq ON sync_mutations(server_sequence);
CREATE INDEX IF NOT EXISTS idx_mutations_device ON sync_mutations(device_id);
CREATE INDEX IF NOT EXISTS idx_mutations_entity ON sync_mutations(entity_type, entity_id);
"""

POSTGRES_SCHEMA_DDL = """
-- PostgreSQL 13+ / Supabase Schema for Neko Cloud Server
CREATE TABLE IF NOT EXISTS sync_devices (
    device_id VARCHAR(64) PRIMARY KEY,
    device_name VARCHAR(128) NOT NULL,
    client_version VARCHAR(32) NOT NULL,
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    auth_token_hash VARCHAR(128) NOT NULL,
    last_sequence BIGINT DEFAULT 0
);

CREATE SEQUENCE IF NOT EXISTS sync_global_sequence START WITH 1 INCREMENT BY 1;

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

CREATE INDEX IF NOT EXISTS idx_entities_seq ON sync_entities(server_sequence);
CREATE INDEX IF NOT EXISTS idx_entities_type ON sync_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_device ON sync_entities(device_id);
CREATE INDEX IF NOT EXISTS idx_mutations_seq ON sync_mutations(server_sequence);
CREATE INDEX IF NOT EXISTS idx_mutations_device ON sync_mutations(device_id);
CREATE INDEX IF NOT EXISTS idx_mutations_entity ON sync_mutations(entity_type, entity_id);
"""
