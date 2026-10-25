"""
neko_bot.cloud.mutation_queue - Thread-Safe, Process-Resilient Offline Mutation Journal.

Persists pending mutations locally in .neko/cloud_mutations.json.
Guarantees:
- Safe automatic directory creation.
- Atomic file writes using temp file + os.replace with Windows lock retry backoff.
- Graceful recovery from malformed/corrupted JSON.
- FIFO mutation ordering.
- Survives process restarts and client re-instantiations.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Union
import uuid

logger = logging.getLogger("neko_bot.cloud.mutation_queue")


def now_utc_iso() -> str:
    """Returns strict ISO-8601 UTC timestamp with 6-digit microsecond precision (27 chars)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class OfflineMutationQueue:
    """
    Thread-safe, process-persistent offline mutation queue.
    """

    def __init__(self, workspace_dir: Union[Path, str], filename: str = "cloud_mutations.json"):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.queue_file = self.workspace_dir / ".neko" / filename
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _read_queue(self) -> List[Dict[str, Any]]:
        """Reads queue from disk safely, recovering from file corruption."""
        if not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                data = json.loads(content)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                return []
        except Exception as e:
            logger.warning("Corrupted or unreadable queue file %s: %s (recovering cleanly)", self.queue_file, e)
            return []

    def _write_queue(self, queue: List[Dict[str, Any]], max_retries: int = 20) -> None:
        """Writes queue list atomically via temporary file and os.replace."""
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_name = f"{self.queue_file.name}.tmp.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}_{uuid.uuid4().hex[:6]}"
        tmp_path = self.queue_file.with_name(tmp_name)

        encoded = json.dumps(queue, indent=2, ensure_ascii=False)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(encoded)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass

        for attempt in range(max_retries):
            try:
                os.replace(tmp_path, self.queue_file)
                return
            except (PermissionError, OSError):
                if attempt == max_retries - 1:
                    try:
                        if tmp_path.exists():
                            tmp_path.unlink()
                    except Exception:
                        pass
                    raise
                time.sleep(0.005)

    def enqueue(
        self,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
        deleted: bool = False,
        device_id: str = "default_device",
        version: int = 1,
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Appends a mutation to the local journal and persists to disk.
        """
        with self._lock:
            queue = self._read_queue()
            now_ts = updated_at or payload.get("updated_at") or now_utc_iso()
            mut_id = hashlib.sha256(f"{entity_id}_{time.time_ns()}_{uuid.uuid4().hex[:8]}".encode()).hexdigest()[:16]

            mutation: Dict[str, Any] = {
                "id": entity_id,
                "mutation_id": mut_id,
                "device_id": device_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "version": payload.get("version", version),
                "updated_at": now_ts,
                "deleted": deleted,
                "data": payload,
                "payload": payload,
            }
            queue.append(mutation)
            self._write_queue(queue)
            return mutation

    def peek(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns pending mutations without removing them."""
        with self._lock:
            queue = self._read_queue()
            if limit is not None:
                return queue[:limit]
            return queue

    def acknowledge(self, mutation_ids: Optional[List[str]] = None) -> int:
        """
        Removes mutations matching mutation_ids (or all if None) upon confirmed sync.
        """
        with self._lock:
            queue = self._read_queue()
            if mutation_ids is None:
                count = len(queue)
                self._write_queue([])
                return count

            id_set = set(mutation_ids)
            remaining = [m for m in queue if m.get("mutation_id") not in id_set and m.get("id") not in id_set]
            drained_count = len(queue) - len(remaining)
            self._write_queue(remaining)
            return drained_count

    def remove_mutations(self, mutation_ids: List[str]) -> int:
        """Removes specifically acknowledged mutations from queue."""
        return self.acknowledge(mutation_ids)

    def clear(self) -> None:
        """Empties the queue completely."""
        with self._lock:
            self._write_queue([])

    def get_stats(self) -> Dict[str, Any]:
        """Returns stats required by test suites."""
        with self._lock:
            queue = self._read_queue()
            return {
                "pending_mutations": len(queue),
                "queue_file": str(self.queue_file),
            }
