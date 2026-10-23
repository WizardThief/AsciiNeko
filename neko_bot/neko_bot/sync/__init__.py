"""
neko_bot.sync - Multi-PC Git Auto-Sync and Deterministic CRDT Reconciliation.
"""

from neko_bot.sync.engine import (
    GitSyncEngine,
    RepoStatus,
    SyncResult,
    SyncStatus,
)
from neko_bot.sync.merge_driver import (
    auto_resolve_rebase_conflicts,
    is_merge_driver_registered,
    merge_driver_main,
    reconcile_3way,
    reconcile_files,
    register_merge_driver,
)

__all__ = [
    "GitSyncEngine",
    "SyncResult",
    "SyncStatus",
    "RepoStatus",
    "merge_driver_main",
    "reconcile_files",
    "reconcile_3way",
    "register_merge_driver",
    "is_merge_driver_registered",
    "auto_resolve_rebase_conflicts",
]
