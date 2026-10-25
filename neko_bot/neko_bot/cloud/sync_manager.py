"""
neko_bot.cloud.sync_manager - High-Level Cloud Synchronization Manager.

Fulfills PROJECT.md CloudSyncManager contract and coordinates:
- OfflineMutationQueue (.neko/cloud_mutations.json).
- CloudClient REST transport (:8767).
- Persistent sequence watermark (.neko/cloud_state.json).
- Multi-store CRDT LWW dispatch (TaskStore, MemoryStore, SkillRegistry, TimeLogger).
- Real-time telemetry export for .neko/status.json and HUD companion.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Union

from neko_bot.cloud.client import CloudClient, CloudConnectionError
from neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso
from neko_bot.core.config import Config, atomic_write_json_file

logger = logging.getLogger("neko_bot.cloud.sync_manager")


class CloudSyncManager:
    """
    Client Sync Manager satisfying CloudSyncManagerContract and production requirements.
    """

    def __init__(
        self,
        workspace_dir: Union[Path, str],
        server_url: str = "http://127.0.0.1:8767",
        device_id: str = "workstation_local",
        token: str = "default_token",
        task_store: Optional[Any] = None,
        memory_store: Optional[Any] = None,
        skill_registry: Optional[Any] = None,
        time_logger: Optional[Any] = None,
        config: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.workspace_dir = Path(workspace_dir).resolve()
        self._server_url = str(server_url).rstrip("/")
        self.device_id = device_id
        self.token = token
        self.config = config

        # Store references
        self._task_store = task_store
        self._memory_store = memory_store
        self._skill_registry = skill_registry
        self._time_logger = time_logger

        # Local journal and client transport
        self.queue = OfflineMutationQueue(self.workspace_dir)
        self.queue_file = self.queue.queue_file  # Explicitly required by test suites
        self.client = CloudClient(server_url=self._server_url, device_id=self.device_id, token=self.token)

        # Persistent state tracking
        self.state_file = self.workspace_dir / ".neko" / "cloud_state.json"
        self._lock = threading.RLock()
        self.last_sequence = self._load_last_sequence()
        self.last_sync_time: Optional[str] = None
        self.last_sync_status: str = "offline"

    @property
    def server_url(self) -> str:
        return self._server_url

    @server_url.setter
    def server_url(self, val: str) -> None:
        self._server_url = str(val).rstrip("/")
        if hasattr(self, "client") and self.client:
            self.client.server_url = self._server_url

    def _load_last_sequence(self) -> int:
        """Loads sequence watermark from .neko/cloud_state.json."""
        if not self.state_file.exists():
            return 0
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return int(data.get("last_sequence", 0))
        except Exception:
            return 0

    def _save_state(self) -> None:
        """Atomically persists sequence watermark and sync state."""
        state = {
            "device_id": self.device_id,
            "server_url": self.server_url,
            "last_sequence": self.last_sequence,
            "last_sync_time": self.last_sync_time,
            "last_sync_status": self.last_sync_status,
        }
        try:
            atomic_write_json_file(self.state_file, state)
        except Exception as e:
            logger.debug("Error saving cloud state: %s", e)

    def enqueue_mutation(
        self,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
        deleted: bool = False,
    ) -> Dict[str, Any]:
        """
        Enqueues a mutation locally. Survives offline periods and restarts.
        """
        return self.queue.enqueue(
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            deleted=deleted,
            device_id=self.device_id,
        )

    def get_queue_stats(self) -> Dict[str, Any]:
        """Returns queue statistics."""
        return self.queue.get_stats()

    def reconcile(self) -> Dict[str, Any]:
        """
        Executes bidirectional reconciliation:
        1. Pushes local queued mutations.
        2. Pulls remote delta mutations.
        3. Dispatches remote mutations to local stores.
        4. Updates local sequence watermark.
        """
        with self._lock:
            self.client.server_url = self._server_url
            pending = self.queue.peek()
            try:
                data = self.client.reconcile(mutations=pending, since_sequence=self.last_sequence)
                new_seq = data.get("server_sequence") or data.get("new_server_sequence") or self.last_sequence
                self.last_sequence = int(new_seq)

                # Acknowledge and remove only the mutations that were transmitted
                sent_ids = [m.get("mutation_id") for m in pending if m.get("mutation_id")]
                if sent_ids:
                    self.queue.acknowledge(sent_ids)
                else:
                    self.queue.acknowledge()

                # Extract pulled mutations
                pulled = data.get("pulled_mutations") or data.get("remote_mutations") or []

                # Apply remote mutations to local stores
                self._apply_remote_mutations(pulled)

                self.last_sync_time = now_utc_iso()
                self.last_sync_status = "synced"
                self._save_state()

                return {
                    "status": "synced",
                    "sync_status": "synced",
                    "success": True,
                    "pulled_count": len(pulled),
                    "pulled_mutations": pulled,
                    "remote_mutations": pulled,
                    "server_sequence": self.last_sequence,
                    "new_server_sequence": self.last_sequence,
                }
            except CloudConnectionError as e:
                self.last_sync_status = "offline"
                self._save_state()
                return {
                    "status": "offline",
                    "sync_status": "offline",
                    "success": False,
                    "error": str(e),
                    "pending_mutations": len(pending),
                    "pending": len(pending),
                }
            except Exception as e:
                self.last_sync_status = "error"
                self._save_state()
                return {
                    "status": "error",
                    "sync_status": "error",
                    "success": False,
                    "error": str(e),
                    "pending_mutations": len(pending),
                    "pending": len(pending),
                }

    def _apply_remote_mutations(self, mutations: List[Dict[str, Any]]) -> None:
        """Applies remote mutations to local stores using 3-level CRDT LWW."""
        for m in mutations:
            etype = m.get("entity_type", "")
            data = m.get("payload") or m.get("data") or {}

            # 1. Task store (only if store instance was explicitly injected)
            if etype == "task" and self._task_store is not None:
                self._apply_task_mutation(m, data)
            # 2. Memory store (only if store instance was explicitly injected)
            elif etype in ("memory", "memory_fact", "memory_project", "memory_episode", "fact", "project", "episode") and self._memory_store is not None:
                self._apply_memory_mutation(m, data)
            # 3. Skill registry (only if registry instance was explicitly injected)
            elif etype == "skill" and self._skill_registry is not None:
                self._apply_skill_mutation(m, data)
            # 4. Time log (only if logger instance was explicitly injected)
            elif etype == "time_log" and self._time_logger is not None:
                self._apply_time_log_mutation(m, data)

    def _apply_task_mutation(self, mutation: Dict[str, Any], data: Dict[str, Any]) -> None:
        try:
            store = self._task_store
            if store is None:
                return

            if hasattr(store, "apply_crdt_mutation"):
                store.apply_crdt_mutation(mutation)
            elif hasattr(store, "reconcile_tasks"):
                tid = mutation.get("entity_id") or data.get("id")
                if tid:
                    store.reconcile_tasks({tid: data})
        except Exception as e:
            logger.debug("Failed applying task mutation: %s", e)

    def _apply_memory_mutation(self, mutation: Dict[str, Any], data: Dict[str, Any]) -> None:
        try:
            store = self._memory_store
            if store is None:
                return
            if hasattr(store, "apply_crdt_mutation"):
                store.apply_crdt_mutation(mutation)
        except Exception as e:
            logger.debug("Failed applying memory mutation: %s", e)

    def _apply_skill_mutation(self, mutation: Dict[str, Any], data: Dict[str, Any]) -> None:
        try:
            registry = self._skill_registry
            if registry is None:
                return
            if hasattr(registry, "apply_crdt_mutation"):
                registry.apply_crdt_mutation(mutation)
        except Exception as e:
            logger.debug("Failed applying skill mutation: %s", e)

    def _apply_time_log_mutation(self, mutation: Dict[str, Any], data: Dict[str, Any]) -> None:
        try:
            time_logs_dir = (self.workspace_dir / "workspace" / "time_logs").resolve()
            time_logs_dir.mkdir(parents=True, exist_ok=True)
            ent_id = mutation.get("entity_id", "remote_log")
            safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(ent_id))
            target_file = (time_logs_dir / f"{safe_id}.json").resolve()
            if target_file.is_relative_to(time_logs_dir):
                atomic_write_json_file(target_file, data)
        except Exception as e:
            logger.debug("Failed applying time log mutation: %s", e)

    def get_telemetry(self) -> Dict[str, Any]:
        """Generates telemetry block for .neko/status.json."""
        stats = self.get_queue_stats()
        pending = stats["pending_mutations"]
        return {
            "status": self.last_sync_status,
            "sync_status": self.last_sync_status,
            "connected": self.last_sync_status == "synced",
            "last_sync_time": self.last_sync_time,
            "last_sync": self.last_sync_time,
            "pending_mutations": pending,
            "pending": pending,
            "cloud_server_url": self.server_url,
            "server": self.server_url,
            "device_id": self.device_id,
        }
