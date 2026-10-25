"""
neko_bot.memory.models - CRDT LWW Data Models for Multi-Level Persistent Memory.

Supports:
1. Layer 1: SemanticFact (long-term facts, preferences, client rules, tech specs).
2. Layer 2: ProjectDeadline (projects, milestones, deadlines, dependencies, risk levels).
3. Layer 3: Episode (chronological traces of prompts, decisions, task completions, skill runs).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


def now_utc_iso() -> str:
    """Returns current UTC time in ISO-8601 string format with explicit UTC indicator."""
    return datetime.now(timezone.utc).isoformat()


def generate_memory_id(prefix: str = "mem") -> str:
    """Generates an 8-character hex ID with prefix (e.g. 'fact-a1b2c3d4')."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class SemanticFact:
    """
    Layer 1: Long-term declarative knowledge, user preferences, and client/work rules.
    CRDT LWW-Element-Set with tombstones.
    """
    id: str = field(default_factory=lambda: generate_memory_id("fact"))
    category: str = "fact"  # preference, client_rule, work_pattern, tech_spec, fact
    content: str = ""
    tags: List[str] = field(default_factory=list)
    importance: int = 3  # 1 to 5
    confidence: float = 1.0  # 0.0 to 1.0
    source: str = "cli"  # cli, conversation, inferred_from_task
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)
    access_count: int = 0
    last_accessed_at: Optional[str] = None
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = now_utc_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.tags is None:
            self.tags = []
        # Clamp importance between 1 and 5
        self.importance = max(1, min(5, int(self.importance or 3)))
        # Clamp confidence between 0.0 and 1.0
        self.confidence = max(0.0, min(1.0, float(self.confidence if self.confidence is not None else 1.0)))

    def mark_accessed(self) -> None:
        """Increments access count and updates last_accessed_at timestamp."""
        self.access_count += 1
        self.last_accessed_at = now_utc_iso()

    def to_dict(self) -> Dict[str, Any]:
        """Serializes model to JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticFact":
        """Deserializes model from dictionary gracefully ignoring unknown keys."""
        valid_keys = {
            "id", "category", "content", "tags", "importance", "confidence",
            "source", "created_at", "updated_at", "access_count", "last_accessed_at", "deleted"
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if "tags" in filtered and isinstance(filtered["tags"], str):
            filtered["tags"] = [t.strip() for t in filtered["tags"].split(",") if t.strip()]
        return cls(**filtered)


@dataclass
class ProjectDeadline:
    """
    Layer 2: Active project context, milestones, deadlines, and dependencies.
    CRDT LWW-Element-Set with tombstones.
    """
    id: str = field(default_factory=lambda: generate_memory_id("proj"))
    project_name: str = ""
    milestone: str = ""
    deadline_utc: Optional[str] = None  # ISO-8601 UTC
    dependencies: List[str] = field(default_factory=list)
    risk_level: str = "medium"  # low, medium, high, critical
    status: str = "active"  # planning, active, blocked, completed, archived
    time_budget_hours: Optional[float] = None
    tracked_hours: float = 0.0
    notes: str = ""
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = now_utc_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.dependencies is None:
            self.dependencies = []
        if self.risk_level not in ("low", "medium", "high", "critical"):
            self.risk_level = "medium"
        if self.status not in ("planning", "active", "blocked", "completed", "archived"):
            self.status = "active"

    def is_overdue(self) -> bool:
        """Returns True if deadline_utc is set, status is not completed/archived, and now > deadline."""
        if not self.deadline_utc or self.status in ("completed", "archived") or self.deleted:
            return False
        try:
            clean = str(self.deadline_utc).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > dt
        except Exception:
            return False

    def hours_remaining(self) -> Optional[float]:
        """Returns signed float hours until deadline, or None if no deadline."""
        if not self.deadline_utc:
            return None
        try:
            clean = str(self.deadline_utc).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = dt - datetime.now(timezone.utc)
            return delta.total_seconds() / 3600.0
        except Exception:
            return None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes model to JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectDeadline":
        """Deserializes model from dictionary gracefully ignoring unknown keys."""
        valid_keys = {
            "id", "project_name", "milestone", "deadline_utc", "dependencies",
            "risk_level", "status", "time_budget_hours", "tracked_hours",
            "notes", "created_at", "updated_at", "deleted"
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if "dependencies" in filtered and isinstance(filtered["dependencies"], str):
            filtered["dependencies"] = [d.strip() for d in filtered["dependencies"].split(",") if d.strip()]
        return cls(**filtered)


@dataclass
class Episode:
    """
    Layer 3: Chronological interaction trace (sessions, queries, decisions, completed tasks).
    CRDT LWW-Element-Set with tombstones.
    """
    id: str = field(default_factory=lambda: generate_memory_id("ep"))
    session_id: str = "default"
    timestamp: str = field(default_factory=now_utc_iso)
    event_type: str = "user_prompt"  # user_prompt, bot_action, task_completed, skill_executed, decision
    summary: str = ""
    full_text: str = ""
    context_tags: List[str] = field(default_factory=list)
    related_entities: Dict[str, List[str]] = field(default_factory=dict)
    outcome: str = "neutral"  # success, failure, neutral
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = now_utc_iso()
        if not self.created_at:
            self.created_at = self.timestamp
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.context_tags is None:
            self.context_tags = []
        if self.related_entities is None:
            self.related_entities = {}
        if self.outcome not in ("success", "failure", "neutral"):
            self.outcome = "neutral"

    def to_dict(self) -> Dict[str, Any]:
        """Serializes model to JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Episode":
        """Deserializes model from dictionary gracefully ignoring unknown keys."""
        valid_keys = {
            "id", "session_id", "timestamp", "event_type", "summary", "full_text",
            "context_tags", "related_entities", "outcome", "created_at", "updated_at", "deleted"
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if "context_tags" in filtered and isinstance(filtered["context_tags"], str):
            filtered["context_tags"] = [t.strip() for t in filtered["context_tags"].split(",") if t.strip()]
        return cls(**filtered)
