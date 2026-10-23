"""
neko_bot.task.models - Data models and enums for Task Management.

Provides:
- TaskPriority: Enum with Italian string mapping and ranking weights.
- TaskState: Enum representing task lifecycle states.
- Task: Dataclass implementing CRDT LWW-Element representation with tombstones.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import uuid


class TaskPriority(str, Enum):
    """Priority levels with string-enum compatibility."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def from_str(cls, val: Optional[Any]) -> "TaskPriority":
        if val is None:
            return cls.MEDIUM
        if isinstance(val, cls):
            return val
        v = str(val).strip().lower()
        if v in ("high", "alta", "urgente", "urgent", "3", "h", "!alta", "!urgente"):
            return cls.HIGH
        if v in ("low", "bassa", "1", "l", "!bassa"):
            return cls.LOW
        return cls.MEDIUM

    @property
    def rank_weight(self) -> int:
        """Weight for priority comparison (higher integer = higher priority)."""
        if self == TaskPriority.HIGH:
            return 3
        if self == TaskPriority.MEDIUM:
            return 2
        return 1


class TaskState(str, Enum):
    """Lifecycle states of a task."""
    TODO = "todo"
    DONE = "done"

    @classmethod
    def from_str(cls, val: Optional[Any]) -> "TaskState":
        if val is None:
            return cls.TODO
        if isinstance(val, cls):
            return val
        v = str(val).strip().lower()
        if v in ("done", "completed", "fatto", "completato", "x", "true", "1"):
            return cls.DONE
        return cls.TODO


def now_utc_iso() -> str:
    """Returns current UTC timestamp formatted in ISO-8601 with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Task:
    """
    CRDT LWW-Element representation of a task with tombstones.
    """
    id: str
    title: str
    project: Optional[str] = None
    priority: TaskPriority = TaskPriority.MEDIUM
    state: TaskState = TaskState.TODO
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)
    due_date: Optional[str] = None
    deleted: bool = False
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.priority, str) and not isinstance(self.priority, TaskPriority):
            self.priority = TaskPriority.from_str(self.priority)
        if isinstance(self.state, str) and not isinstance(self.state, TaskState):
            self.state = TaskState.from_str(self.state)

    @classmethod
    def create(
        cls,
        title: str,
        project: Optional[str] = None,
        priority: Any = TaskPriority.MEDIUM,
        due_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        custom_id: Optional[str] = None,
    ) -> "Task":
        """Factory creating a new active task with 8-char short hex UUID."""
        now = now_utc_iso()
        task_id = custom_id or uuid.uuid4().hex[:8]
        clean_project = project.lstrip("#").strip() if project else None
        prio = TaskPriority.from_str(priority)
        return cls(
            id=task_id,
            title=title.strip(),
            project=clean_project or None,
            priority=prio,
            state=TaskState.TODO,
            created_at=now,
            updated_at=now,
            due_date=due_date,
            deleted=False,
            completed_at=None,
            metadata=dict(metadata or {}),
        )

    def mark_done(self) -> None:
        """Transitions state to DONE and touches updated_at."""
        now = now_utc_iso()
        self.state = TaskState.DONE
        self.completed_at = now
        self.updated_at = now

    def mark_todo(self) -> None:
        """Transitions state back to TODO and touches updated_at."""
        self.state = TaskState.TODO
        self.completed_at = None
        self.updated_at = now_utc_iso()

    def mark_deleted(self) -> None:
        """Applies CRDT tombstone and touches updated_at."""
        self.deleted = True
        self.updated_at = now_utc_iso()

    def update(
        self,
        title: Optional[str] = None,
        project: Optional[str] = None,
        priority: Optional[Any] = None,
        due_date: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mutates task fields and updates the LWW timestamp."""
        changed = False
        if title is not None and title.strip() != self.title:
            self.title = title.strip()
            changed = True
        if project is not None:
            clean_proj = project.lstrip("#").strip() or None
            if clean_proj != self.project:
                self.project = clean_proj
                changed = True
        if priority is not None:
            prio = TaskPriority.from_str(priority)
            if prio != self.priority:
                self.priority = prio
                changed = True
        if due_date != self.due_date:
            self.due_date = due_date
            changed = True
        if metadata_update:
            self.metadata.update(metadata_update)
            changed = True

        if changed:
            self.updated_at = now_utc_iso()

    def is_done(self) -> bool:
        """Returns True if task is marked completed."""
        return self.state == TaskState.DONE

    def is_active(self) -> bool:
        """Returns True if task is not deleted and not completed."""
        return not self.deleted and self.state != TaskState.DONE

    def to_dict(self) -> Dict[str, Any]:
        """Serializes task to JSON-compatible dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "project": self.project,
            "priority": self.priority.value if isinstance(self.priority, TaskPriority) else str(self.priority),
            "state": self.state.value if isinstance(self.state, TaskState) else str(self.state),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "due_date": self.due_date,
            "deleted": self.deleted,
            "completed_at": self.completed_at,
            "metadata": dict(self.metadata),
        }

    def to_status_dict(self) -> Dict[str, Any]:
        """Formats minimal payload conforming to PROJECT.md § 2 status top_task schema."""
        return {
            "id": self.id,
            "title": self.title,
            "project": self.project or "",
            "priority": self.priority.value if isinstance(self.priority, TaskPriority) else str(self.priority),
            "due_date": self.due_date or "",
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Safely deserializes task from JSON dictionary."""
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            project=data.get("project") or None,
            priority=TaskPriority.from_str(data.get("priority")),
            state=TaskState.from_str(data.get("state", "todo")),
            created_at=data.get("created_at") or now_utc_iso(),
            updated_at=data.get("updated_at") or now_utc_iso(),
            due_date=data.get("due_date") or None,
            deleted=bool(data.get("deleted", False)),
            completed_at=data.get("completed_at") or None,
            metadata=dict(data.get("metadata", {})),
        )
