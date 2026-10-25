"""
neko_bot.cloud forwarding stub package.
"""

from pathlib import Path
import sys

# Extend package search path to include root cloud/ directory if present
_root_cloud = Path(__file__).resolve().parent.parent.parent / "cloud"
if _root_cloud.exists() and str(_root_cloud) not in __path__:
    __path__.append(str(_root_cloud))

try:
    from neko_bot.neko_bot.cloud.client import CloudClient, CloudClientError, CloudConnectionError
    from neko_bot.neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso
    from neko_bot.neko_bot.cloud.sync_manager import CloudSyncManager
except ImportError:
    from neko_bot.cloud.client import CloudClient, CloudClientError, CloudConnectionError  # type: ignore
    from neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso  # type: ignore
    from neko_bot.cloud.sync_manager import CloudSyncManager  # type: ignore

try:
    from cloud.server import CloudServer
    from cloud.storage.base import BaseStorageAdapter
    from cloud.storage.sqlite_adapter import SQLiteStorageAdapter
    from cloud.storage.postgres_adapter import PostgresStorageAdapter
except ImportError:
    CloudServer = None  # type: ignore
    BaseStorageAdapter = None  # type: ignore
    SQLiteStorageAdapter = None  # type: ignore
    PostgresStorageAdapter = None  # type: ignore

__all__ = [
    "BaseStorageAdapter",
    "CloudClient",
    "CloudClientError",
    "CloudConnectionError",
    "CloudServer",
    "CloudSyncManager",
    "OfflineMutationQueue",
    "PostgresStorageAdapter",
    "SQLiteStorageAdapter",
    "now_utc_iso",
]
