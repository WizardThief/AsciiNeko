"""
neko_bot.notifications.queue - Local JSON Notification Queue for AsciiNeko & CLI.

Features:
- Thread-safe in-memory cache synchronized with .neko/notifications.json via atomic_write_json_file.
- Dual-compatibility serialization: writes both "notifications" and "items" keys for 100% backward compatibility.
- Auto-prune by TTL (default 86400s / 24h) and capacity cap (50 items max, read items evicted first).
- Synchronization helper sync_to_status() updating .neko/status.json notification_queue.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from neko_bot.core.config import Config, atomic_write_json_file, get_config

logger = logging.getLogger("neko_bot.notifications.queue")


class NotificationCategory(str, Enum):
    BRIEFING = "briefing"
    TIMER = "timer"
    TASK = "task"
    SYSTEM = "system"

    @classmethod
    def from_str(cls, val: Optional[Any]) -> "NotificationCategory":
        if val is None:
            return cls.SYSTEM
        if isinstance(val, cls):
            return val
        v = str(val).strip().lower()
        if v in ("briefing", "morning", "daily", "evening"):
            return cls.BRIEFING
        if v in ("timer", "break", "pomodoro"):
            return cls.TIMER
        if v in ("task", "todo"):
            return cls.TASK
        return cls.SYSTEM


class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"

    @classmethod
    def from_str(cls, val: Optional[Any]) -> "NotificationLevel":
        if val is None:
            return cls.INFO
        if isinstance(val, cls):
            return val
        v = str(val).strip().lower()
        if v in ("alert", "error", "critical", "danger", "urgent"):
            return cls.ALERT
        if v in ("warning", "warn"):
            return cls.WARNING
        return cls.INFO


def now_utc_iso() -> str:
    """Returns current UTC timestamp formatted as ISO-8601 with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class NotificationItem:
    """Dataclass representing an individual notification entry."""
    id: str
    title: str
    message: str
    timestamp: str = field(default_factory=now_utc_iso)
    category: str = "system"
    level: str = "info"
    read: bool = False
    ttl_seconds: int = 86400  # Default 24 hours
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, current_ts: Optional[float] = None) -> bool:
        if self.ttl_seconds <= 0:
            return False
        now_ts = current_ts if current_ts is not None else time.time()
        try:
            clean_ts = self.timestamp.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            return (now_ts - dt.timestamp()) > self.ttl_seconds
        except Exception:
            return False

    def mark_read(self) -> None:
        self.read = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp,
            "category": str(self.category),
            "level": str(self.level),
            "read": bool(self.read),
            "ttl_seconds": int(self.ttl_seconds),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NotificationItem":
        cat = NotificationCategory.from_str(data.get("category")).value
        lvl = NotificationLevel.from_str(data.get("level")).value
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            title=str(data.get("title") or ""),
            message=str(data.get("message") or ""),
            timestamp=str(data.get("timestamp") or now_utc_iso()),
            category=cat,
            level=lvl,
            read=bool(data.get("read", False)),
            ttl_seconds=int(data.get("ttl_seconds", 86400)),
            metadata=dict(data.get("metadata") or {}),
        )


class NotificationQueue:
    """
    Persistent JSON Notification Queue stored at .neko/notifications.json.
    Supports atomic writes, TTL pruning, FIFO capacity limit, and status.json syncing.
    """

    def __init__(
        self,
        file_path: Optional[Path] = None,
        status_file: Optional[Path] = None,
        max_capacity: int = 50,
        auto_save: bool = True,
        on_change: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> None:
        cfg = get_config()
        self.file_path = Path(file_path) if file_path is not None else cfg.notifications_file
        self.status_file = Path(status_file) if status_file is not None else cfg.status_file
        self.max_capacity = max(1, int(max_capacity))
        self.auto_save = auto_save
        self.on_change = on_change
        self._items: List[NotificationItem] = []
        self._lock = threading.RLock()
        self.load()

    def _notify_change(self) -> None:
        if self.on_change:
            try:
                self.on_change([item.to_dict() for item in self._items])
            except Exception as e:
                logger.debug("NotificationQueue on_change callback error: %s", e)

    def _prune_expired_internal(self) -> int:
        now_ts = time.time()
        initial_len = len(self._items)
        self._items = [item for item in self._items if not item.is_expired(now_ts)]
        return initial_len - len(self._items)

    def _enforce_capacity_internal(self) -> None:
        """Evicts oldest read items first; if still over capacity, evicts oldest FIFO."""
        if len(self._items) <= self.max_capacity:
            return

        # 1. Evict oldest read items
        excess = len(self._items) - self.max_capacity
        new_items: List[NotificationItem] = []
        evicted = 0
        for item in self._items:
            if item.read and evicted < excess:
                evicted += 1
                continue
            new_items.append(item)
        self._items = new_items

        # 2. If still over capacity, evict strictly oldest FIFO
        if len(self._items) > self.max_capacity:
            self._items = self._items[-self.max_capacity:]

    def enqueue(
        self,
        title: str,
        message: str,
        category: str = "system",
        level: str = "info",
        ttl_seconds: int = 86400,
        notification_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationItem:
        with self._lock:
            self._prune_expired_internal()

            item = NotificationItem(
                id=notification_id or uuid.uuid4().hex[:8],
                title=title.strip(),
                message=message.strip(),
                timestamp=now_utc_iso(),
                category=NotificationCategory.from_str(category).value,
                level=NotificationLevel.from_str(level).value,
                read=False,
                ttl_seconds=ttl_seconds,
                metadata=dict(metadata or {}),
            )
            self._items.append(item)
            self._enforce_capacity_internal()

            if self.auto_save:
                self.save()

            self._notify_change()
            return item

    def peek(
        self,
        unread_only: bool = False,
        limit: Optional[int] = None,
        category: Optional[str] = None,
    ) -> List[NotificationItem]:
        with self._lock:
            res = self._items
            if unread_only:
                res = [item for item in res if not item.read]
            if category:
                cat_val = NotificationCategory.from_str(category).value
                res = [item for item in res if item.category == cat_val]
            if limit and limit > 0:
                res = res[-limit:]
            return [NotificationItem.from_dict(item.to_dict()) for item in res]

    def list_notifications(
        self,
        unread_only: bool = False,
        limit: Optional[int] = None,
        category: Optional[str] = None,
    ) -> List[NotificationItem]:
        """Convenience alias for peek."""
        return self.peek(unread_only=unread_only, limit=limit, category=category)

    def get(self, notification_id: str) -> Optional[NotificationItem]:
        with self._lock:
            for item in self._items:
                if item.id == notification_id:
                    return NotificationItem.from_dict(item.to_dict())
            return None

    def mark_read(self, notification_id: str) -> bool:
        with self._lock:
            for item in self._items:
                if item.id == notification_id:
                    item.mark_read()
                    if self.auto_save:
                        self.save()
                    self._notify_change()
                    return True
            return False

    def mark_as_read(self, notification_id: str) -> bool:
        """Alias for mark_read."""
        return self.mark_read(notification_id)

    def mark_all_read(self, category: Optional[str] = None) -> int:
        with self._lock:
            cat_val = NotificationCategory.from_str(category).value if category else None
            count = 0
            for item in self._items:
                if not item.read and (cat_val is None or item.category == cat_val):
                    item.mark_read()
                    count += 1
            if count > 0:
                if self.auto_save:
                    self.save()
                self._notify_change()
            return count

    def prune(self) -> int:
        with self._lock:
            pruned = self._prune_expired_internal()
            if pruned > 0:
                if self.auto_save:
                    self.save()
                self._notify_change()
            return pruned

    def prune_expired(self) -> int:
        """Alias for prune."""
        return self.prune()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            if self.auto_save:
                self.save()
            self._notify_change()

    def delete(self, notification_id: str) -> bool:
        with self._lock:
            initial = len(self._items)
            self._items = [item for item in self._items if item.id != notification_id]
            if len(self._items) < initial:
                if self.auto_save:
                    self.save()
                self._notify_change()
                return True
            return False

    def load(self) -> None:
        with self._lock:
            if not self.file_path.exists():
                self._items = []
                return
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    self._items = []
                    return
                # Dual compatibility: support both "notifications" and "items"
                raw_list = data.get("notifications", data.get("items", []))
                if isinstance(raw_list, list):
                    self._items = [NotificationItem.from_dict(d) for d in raw_list if isinstance(d, dict)]
                else:
                    self._items = []
            except Exception as e:
                logger.warning("Corrupt notifications file at %s: %s. Resetting.", self.file_path, e)
                self._items = []
                if self.auto_save:
                    self.save()

    def save(self) -> None:
        with self._lock:
            items_dict = [item.to_dict() for item in self._items]
            payload = {
                "notifications": items_dict,
                "items": items_dict,  # Dual-compatibility alias for daemon.py line 613
                "updated_at": now_utc_iso(),
                "total": len(self._items),
                "unread": sum(1 for item in self._items if not item.read),
            }
            atomic_write_json_file(self.file_path, payload)

    def sync_to_status(self, status_file: Optional[Path] = None) -> None:
        """Flushes the current active notification queue into .neko/status.json."""
        target = Path(status_file) if status_file is not None else self.status_file
        if not target.exists():
            return
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data["notification_queue"] = [item.to_dict() for item in self._items]
                data["updated_at"] = now_utc_iso()
                atomic_write_json_file(target, data)
        except Exception as e:
            logger.debug("Failed to sync notification queue to status file %s: %s", target, e)
