"""
neko_bot.skills.models - Data Models and CRDT Schemas for Autonomous Hybrid Skills.

Supports:
1. Declarative Workflows (multi-step action pipelines).
2. Executable Python Tools (sandboxed run(inputs, context) callables).
3. Self-Test Manifests and CRDT LWW-Element-Set Serialization.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional
import uuid


def now_utc_iso() -> str:
    """Returns current UTC time in fixed 27-character ISO-8601 format with microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_timestamp_safe(ts: Optional[str]) -> float:
    """Safely parses ISO-8601 UTC timestamp to POSIX epoch float."""
    if not ts:
        return 0.0
    try:
        clean = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def bump_version(current: str) -> str:
    """Increments the patch version of a semver string (e.g. 1.0.0 -> 1.0.1)."""
    parts = str(current or "1.0.0").strip().split(".")
    try:
        if len(parts) == 3:
            return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        elif len(parts) == 2:
            return f"{parts[0]}.{int(parts[1]) + 1}.0"
        elif len(parts) == 1:
            return f"{int(parts[0]) + 1}.0.0"
    except Exception:
        pass
    return f"{current}.1"


def generate_skill_id(skill_type: str = "tool") -> str:
    """Generates an 8-character hex ID with prefix (e.g. 'tool-a1b2c3d4' or 'workflow-c4d3e2f1')."""
    prefix = "workflow" if "workflow" in str(skill_type).lower() else "tool"
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def canonical_json(payload: Any) -> str:
    """Serializes payload into canonical JSON representation for deterministic tie-breaking."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


class SkillType(str, Enum):
    WORKFLOW = "workflow"
    TOOL = "tool"


class SkillStatus(str, Enum):
    DRAFT = "draft"
    VERIFIED = "verified"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class WorkflowStep:
    """A single declarative step in a multi-step workflow pipeline."""
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    filter: Optional[Dict[str, Any]] = None
    template: Optional[str] = None
    output_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"action": self.action}
        if self.params:
            d["params"] = self.params
        if self.filter:
            d["filter"] = self.filter
        if self.template:
            d["template"] = self.template
        if self.output_key:
            d["output_key"] = self.output_key
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowStep":
        return cls(
            action=str(data.get("action", "")),
            params=dict(data.get("params", {})),
            filter=data.get("filter"),
            template=data.get("template"),
            output_key=data.get("output_key"),
        )


@dataclass
class SkillSelfTest:
    """Self-test case executed inside the sandbox during skill verification."""
    name: str = "test_default"
    inputs: Dict[str, Any] = field(default_factory=dict)
    expected_output: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "inputs": self.inputs,
            "expected_output": self.expected_output,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillSelfTest":
        return cls(
            name=str(data.get("name", "test_default")),
            inputs=dict(data.get("inputs", {})),
            expected_output=dict(data.get("expected_output", {})),
            context=dict(data.get("context", {})),
        )


@dataclass
class SkillManifest:
    """
    Unified representation of an Autonomous Hybrid Skill.
    Can represent either a Declarative Workflow or an Executable Python Tool.
    Conforms to CRDT LWW-Element-Set with logical deletion (tombstones).
    """
    id: str = field(default_factory=lambda: generate_skill_id("tool"))
    name: str = ""
    version: str = "1.0.0"
    type: str = "tool"  # 'tool' or 'workflow'
    description: str = ""
    trigger_patterns: List[str] = field(default_factory=list)
    code: str = ""  # Python tool source code
    steps: List[Dict[str, Any]] = field(default_factory=list)  # Workflow steps
    self_tests: List[Dict[str, Any]] = field(default_factory=list)
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    status: str = "draft"  # draft, verified, failed, disabled
    execution_count: int = 0
    last_executed_at: Optional[str] = None
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = now_utc_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        # Normalize skill type
        raw_type = str(self.type or "tool").lower()
        if "workflow" in raw_type:
            self.type = "workflow"
        else:
            self.type = "tool"
        if not self.name:
            self.name = self.id.replace("-", "_")
        if self.trigger_patterns is None:
            self.trigger_patterns = []
        if self.steps is None:
            self.steps = []
        if self.self_tests is None:
            self.self_tests = []
        if self.input_schema is None:
            self.input_schema = {}
        if self.output_schema is None:
            self.output_schema = {}

    @property
    def is_tool(self) -> bool:
        return self.type == "tool"

    @property
    def is_workflow(self) -> bool:
        return self.type == "workflow"

    def mark_executed(self) -> None:
        """Increments execution count and updates execution timestamp."""
        self.execution_count += 1
        now = now_utc_iso()
        self.last_executed_at = now
        self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        """Serializes skill manifest to dictionary with dual-key aliases for full compatibility."""
        data = asdict(self)
        # Add standard aliases for broad test and CRDT compatibility
        data["skill_type"] = "python_tool" if self.type == "tool" else "workflow"
        data["code_str"] = self.code
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillManifest":
        """Deserializes skill manifest gracefully handling aliases."""
        raw = dict(data)
        # Reconcile aliases
        if "type" not in raw and "skill_type" in raw:
            st = str(raw["skill_type"]).lower()
            raw["type"] = "workflow" if "workflow" in st else "tool"
        if "code" not in raw and "code_str" in raw:
            raw["code"] = raw["code_str"]

        valid_fields = {
            "id", "name", "version", "type", "description", "trigger_patterns",
            "code", "steps", "self_tests", "input_schema", "output_schema",
            "status", "execution_count", "last_executed_at", "created_at",
            "updated_at", "deleted"
        }
        filtered = {k: v for k, v in raw.items() if k in valid_fields}
        return cls(**filtered)

    def to_crdt_entity(self) -> Dict[str, Any]:
        """Exports entity for CRDT synchronization with Cloud Server."""
        payload = self.to_dict()
        return {
            "entity_type": "skill",
            "id": self.id,
            "entity_id": self.id,
            "version": 1,
            "updated_at": self.updated_at,
            "deleted": self.deleted,
            "data": payload,
            "payload": payload,
        }
