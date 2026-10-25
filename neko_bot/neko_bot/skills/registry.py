"""
neko_bot.skills.registry - Thread-Safe CRDT LWW Skill Catalog and Storage Manager.

Manages catalog index and disk persistence under workspace/skills/:
- workspace/skills/registry.json (central catalog array)
- workspace/skills/workflows/<id>.json
- workspace/skills/tools/<id>.py
- workspace/skills/archive/<id>_v<version>.py
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.skills.models import (
    bump_version,
    canonical_json,
    now_utc_iso,
    parse_timestamp_safe,
)
from neko_bot.skills.sandbox import SkillSandbox, is_nested_quantifier_redos

logger = logging.getLogger("neko_bot.skills.registry")

_DIR_LOCKS: Dict[str, threading.RLock] = {}
_DIR_LOCKS_MUTEX = threading.Lock()


def _get_dir_lock(dir_path: Path) -> threading.RLock:
    key = str(dir_path.resolve()).lower()
    with _DIR_LOCKS_MUTEX:
        if key not in _DIR_LOCKS:
            _DIR_LOCKS[key] = threading.RLock()
        return _DIR_LOCKS[key]


@contextmanager
def _interprocess_lock(lock_file: Path, timeout: float = 30.0):
    """
    Cross-process file lock protecting disk catalog writes.
    Uses msvcrt.locking on Windows and fcntl.flock on POSIX.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if not lock_file.exists():
        try:
            with open(lock_file, "a+b") as init_f:
                init_f.write(b"\x00")
                init_f.flush()
        except Exception:
            pass

    f = open(lock_file, "a+b")
    start = time.time()
    locked = False
    delay = 0.002
    try:
        f.seek(0)
        while not locked:
            try:
                if os.name == "nt" and msvcrt:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                elif fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (OSError, IOError, PermissionError):
                if time.time() - start > timeout:
                    raise TimeoutError(f"Timed out waiting for file lock on {lock_file}")
                time.sleep(delay)
                delay = min(delay * 1.5, 0.05)
        yield
    finally:
        if locked:
            try:
                f.seek(0)
                if os.name == "nt" and msvcrt:
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                elif fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            f.close()
        except Exception:
            pass


def _write_text_atomic(file_path: Path, content: str, max_retries: int = 30) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(
        f"{file_path.name}.tmp.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}_{uuid.uuid4().hex[:6]}"
    )
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    for attempt in range(max_retries):
        try:
            os.replace(tmp_path, file_path)
            return
        except (PermissionError, OSError):
            if attempt == max_retries - 1:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass
                raise
            time.sleep(0.005 * (1.2 ** attempt))


class SkillRegistry:
    """
    Thread-safe CRDT LWW Skill Registry managing disk persistence under workspace/skills/.
    """

    def __init__(
        self,
        workspace_dir: Optional[Union[Path, str]] = None,
        sandbox: Optional[SkillSandbox] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.config = config or Config.load()
        self.sandbox = sandbox or SkillSandbox()

        # Resolve paths
        if workspace_dir is not None:
            p = Path(workspace_dir).resolve()
            self.workspace_dir = p
            if p.name == "skills":
                self.skills_dir = p
            elif p.name == "workspace":
                self.skills_dir = p / "skills"
            elif (p / "workspace" / "skills").exists():
                self.skills_dir = p / "workspace" / "skills"
            elif (p / "skills").exists():
                self.skills_dir = p / "skills"
            else:
                self.skills_dir = p / "workspace" / "skills"
        else:
            self.workspace_dir = self.config.workspace_dir
            self.skills_dir = self.config.workspace_dir / "skills"

        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.workflows_dir = self.skills_dir / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir = self.skills_dir / "tools"
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.skills_dir / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        self.registry_file = self.skills_dir / "registry.json"
        self._lock = _get_dir_lock(self.skills_dir)
        self._skills: Dict[str, Dict[str, Any]] = {}
        self._purged_ids: Set[str] = set()
        self._mutated_ids: Set[str] = set()
        self._last_mtime: float = 0.0

        with self._lock:
            self._load_from_disk()

    @staticmethod
    def _read_json_with_retry(file_path: Path, max_retries: int = 30, delay: float = 0.005) -> Any:
        for attempt in range(max_retries):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (PermissionError, OSError, json.JSONDecodeError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)

    def _reload_if_modified(self) -> None:
        with self._lock:
            if self.registry_file.exists():
                try:
                    mtime = os.path.getmtime(self.registry_file)
                    if mtime > self._last_mtime:
                        self._load_from_disk()
                except (PermissionError, OSError):
                    pass

    def _load_from_disk(self) -> None:
        if not self.registry_file.exists():
            self._skills = {}
            self._last_mtime = 0.0
            return
        try:
            raw = self._read_json_with_retry(self.registry_file)
            items: List[Dict[str, Any]] = []
            if isinstance(raw, list):
                items = [s for s in raw if isinstance(s, dict)]
            elif isinstance(raw, dict):
                inner = raw.get("skills", raw)
                if isinstance(inner, list):
                    items = [s for s in inner if isinstance(s, dict)]
                elif isinstance(inner, dict):
                    items = [v for v in inner.values() if isinstance(v, dict)]

            skills_map: Dict[str, Dict[str, Any]] = {}
            for s in items:
                sid = s.get("id")
                if sid and sid not in self._purged_ids:
                    skills_map[sid] = s

            # Preserve any in-memory skills locally mutated by this instance
            for sid in getattr(self, "_mutated_ids", set()):
                if sid in self._purged_ids:
                    continue
                if sid in self._skills:
                    if sid not in skills_map:
                        skills_map[sid] = self._skills[sid]
                    elif self._should_win(self._skills[sid], skills_map[sid]):
                        skills_map[sid] = self._skills[sid]

            self._skills = skills_map
            self._last_mtime = os.path.getmtime(self.registry_file)
        except Exception as e:
            logger.warning("Error reading %s: %s (preserving state)", self.registry_file, e)

    def _save_registry(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.skills_dir / ".registry.lock"
        with _interprocess_lock(lock_file):
            disk_skills: Dict[str, Dict[str, Any]] = {}
            if self.registry_file.exists():
                try:
                    raw_disk = self._read_json_with_retry(self.registry_file)
                    disk_list = raw_disk if isinstance(raw_disk, list) else (
                        raw_disk.get("skills", []) if isinstance(raw_disk, dict) else []
                    )
                    if isinstance(disk_list, list):
                        for ds in disk_list:
                            if isinstance(ds, dict) and "id" in ds:
                                ds_id = ds["id"]
                                if ds_id not in self._purged_ids:
                                    disk_skills[ds_id] = ds
                except Exception as e:
                    logger.debug("Error merging disk registry before save: %s", e)

            # Apply local mutations from this instance
            for sid in self._mutated_ids:
                if sid in self._purged_ids:
                    disk_skills.pop(sid, None)
                    continue
                if sid in self._skills:
                    local_s = self._skills[sid]
                    if sid not in disk_skills:
                        disk_skills[sid] = local_s
                    else:
                        if self._should_win(local_s, disk_skills[sid]):
                            disk_skills[sid] = local_s

            for pid in self._purged_ids:
                disk_skills.pop(pid, None)

            self._skills = dict(disk_skills)
            payload = list(self._skills.values())
            atomic_write_json_file(self.registry_file, payload)
            if self.registry_file.exists():
                try:
                    self._last_mtime = os.path.getmtime(self.registry_file)
                except (PermissionError, OSError):
                    pass

    def register_skill(self, skill_def: Dict[str, Any], code_str: Optional[str] = None) -> Tuple[bool, str]:
        with self._lock:
            self._reload_if_modified()
            s_copy = dict(skill_def)
            if code_str:
                s_copy["code"] = code_str
                s_copy["code_str"] = code_str
            elif "code" in s_copy and "code_str" not in s_copy:
                s_copy["code_str"] = s_copy["code"]
            elif "code_str" in s_copy and "code" not in s_copy:
                s_copy["code"] = s_copy["code_str"]

            # Standardize type keys
            stype = s_copy.get("type") or s_copy.get("skill_type") or "tool"
            s_copy["type"] = "workflow" if "workflow" in str(stype).lower() else "tool"
            s_copy["skill_type"] = "workflow" if s_copy["type"] == "workflow" else "python_tool"

            # Filter/reject nested quantifier ReDoS patterns from trigger_patterns
            raw_patterns = s_copy.get("trigger_patterns")
            if raw_patterns and isinstance(raw_patterns, list):
                clean_patterns = []
                for p in raw_patterns:
                    if isinstance(p, str) and is_nested_quantifier_redos(p):
                        logger.warning("Rejected ReDoS trigger pattern '%s' for skill '%s'", p, s_copy.get("id"))
                    else:
                        clean_patterns.append(p)
                s_copy["trigger_patterns"] = clean_patterns

            # Security and validation gate: Verification Sandbox
            verified, msg = self.sandbox.verify_skill(s_copy)
            if not verified:
                return False, f"Verification failed: {msg}"

            sid = s_copy.get("id")
            if not sid:
                return False, "Skill missing id"

            self._purged_ids.discard(sid)
            now_utc = now_utc_iso()
            existing = self._skills.get(sid)

            if existing is not None:
                # Versioning
                old_ver = existing.get("version", "1.0.0")
                new_ver = s_copy.get("version")
                if not new_ver or new_ver == old_ver:
                    s_copy["version"] = bump_version(old_ver)
                # Archive old code if tool
                if existing.get("code"):
                    arch_path = self.archive_dir / f"{sid}_v{old_ver}.py"
                    _write_text_atomic(arch_path, existing["code"])
                s_copy["created_at"] = existing.get("created_at", now_utc)
                s_copy["execution_count"] = existing.get("execution_count", 0)
                s_copy["last_executed_at"] = existing.get("last_executed_at")
            else:
                s_copy["version"] = s_copy.get("version", "1.0.0")
                s_copy["created_at"] = s_copy.get("created_at", now_utc)
                s_copy["execution_count"] = 0
                s_copy["last_executed_at"] = None

            s_copy["status"] = "verified"
            s_copy["updated_at"] = now_utc
            s_copy["deleted"] = False

            # Persist code or workflow files
            if s_copy["type"] == "workflow":
                wf_path = self.workflows_dir / f"{sid}.json"
                atomic_write_json_file(wf_path, s_copy)
                s_copy["workflow_path"] = f"workflows/{sid}.json"
            else:
                tool_path = self.tools_dir / f"{sid}.py"
                _write_text_atomic(tool_path, s_copy.get("code", ""))
                s_copy["code_path"] = f"tools/{sid}.py"

            self._mutated_ids.add(sid)
            self._skills[sid] = s_copy
            self._save_registry()
            return True, "Registered successfully"

    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._reload_if_modified()
            s = self._skills.get(skill_id)
            if s and not s.get("deleted"):
                return s
            return None

    def list_skills(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_if_modified()
            if include_deleted:
                return list(self._skills.values())
            return [s for s in self._skills.values() if not s.get("deleted")]

    def delete_skill(self, skill_id: str, hard: bool = False) -> bool:
        with self._lock:
            self._reload_if_modified()
            if skill_id not in self._skills:
                return False
            if hard:
                self._purged_ids.add(skill_id)
                self._mutated_ids.discard(skill_id)
                self._skills.pop(skill_id, None)
                tool_path = self.tools_dir / f"{skill_id}.py"
                wf_path = self.workflows_dir / f"{skill_id}.json"
                if tool_path.exists():
                    try:
                        tool_path.unlink()
                    except Exception:
                        pass
                if wf_path.exists():
                    try:
                        wf_path.unlink()
                    except Exception:
                        pass
            else:
                self._skills[skill_id]["deleted"] = True
                self._skills[skill_id]["updated_at"] = now_utc_iso()
                self._mutated_ids.add(skill_id)
            self._save_registry()
            return True

    def match_skill(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not prompt or not isinstance(prompt, str):
            return None

        # 1. Copy candidates under lock to release lock immediately
        with self._lock:
            self._reload_if_modified()
            candidates = [
                (s, list(s.get("trigger_patterns", [])))
                for s in self._skills.values()
                if not s.get("deleted")
            ]

        # 2. Perform pattern matching OUTSIDE self._lock
        p_low = prompt.lower()
        for s, patterns in candidates:
            for pat in patterns:
                if not pat or not isinstance(pat, str):
                    continue
                pat_low = pat.lower()
                # Fast substring matching
                if pat_low in p_low:
                    return s

                # ReDoS guard: skip regex matching on nested quantifiers prone to catastrophic backtracking
                if is_nested_quantifier_redos(pat):
                    continue

                try:
                    if re.search(pat, prompt, re.IGNORECASE):
                        return s
                except (re.error, OverflowError):
                    pass
        return None

    def execute_skill(
        self,
        skill_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            self._reload_if_modified()
            skill = self.get_skill(skill_id)
            if not skill:
                return {"success": False, "result": {}, "error": f"Skill '{skill_id}' not found"}

            stype = skill.get("type", "tool")
            safe_inputs = inputs if inputs is not None else {}
            safe_context = context if context is not None else {}

            res: Dict[str, Any] = {}
            if stype == "workflow":
                steps = skill.get("steps", [])
                workflow_data = dict(safe_inputs)
                wf_success = True
                wf_error = None

                for idx, step in enumerate(steps):
                    action = step.get("action")
                    out_key = step.get("output_key", f"step_{idx}_out")
                    if action == "query_tasks":
                        try:
                            from neko_bot.task.store import TaskStore
                            ts = TaskStore(self.workspace_dir / "workspace")
                            tasks = [t.to_dict() for t in ts.list_tasks(include_completed=True)]
                            f_state = step.get("filter", {}).get("state") or step.get("params", {}).get("state")
                            if f_state:
                                tasks = [t for t in tasks if t.get("state") == f_state]
                            workflow_data[out_key] = tasks
                        except Exception:
                            workflow_data[out_key] = []
                    elif action == "format_template":
                        tmpl = step.get("template", "")
                        try:
                            workflow_data[out_key] = tmpl.format(**workflow_data)
                        except Exception:
                            workflow_data[out_key] = tmpl
                    elif action == "notify":
                        title = step.get("title", "Neko Bot")
                        msg = step.get("message", "{report}")
                        try:
                            formatted_msg = msg.format(**workflow_data)
                        except Exception:
                            formatted_msg = msg
                        workflow_data[out_key] = {"title": title, "message": formatted_msg, "delivered": True}
                    else:
                        workflow_data[out_key] = {"executed": True, "action": action}

                res = {
                    "success": wf_success,
                    "result": workflow_data if "report" in workflow_data else {"report": f"Workflow executed for {safe_inputs}", **workflow_data},
                    "error": wf_error,
                }
            else:
                code = skill.get("code") or skill.get("code_str") or ""
                timeout_sec = float(skill.get("timeout_sec", 5.0))
                res = self.sandbox.run_tool_in_sandbox(code, safe_inputs, safe_context, timeout_sec=timeout_sec)

            if res.get("success"):
                skill["execution_count"] = skill.get("execution_count", 0) + 1
                now_utc = now_utc_iso()
                skill["last_executed_at"] = now_utc
                skill["updated_at"] = now_utc
                self._mutated_ids.add(skill_id)
                self._save_registry()

            return res

    def export_crdt_entities(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._reload_if_modified()
            entities = []
            for s in self._skills.values():
                entities.append({
                    "entity_type": "skill",
                    "id": s["id"],
                    "entity_id": s["id"],
                    "version": s.get("version", "1.0.0"),
                    "updated_at": s.get("updated_at", now_utc_iso()),
                    "deleted": bool(s.get("deleted", False)),
                    "data": s,
                    "payload": s,
                })
            return entities

    def apply_crdt_mutation(self, mutation: Dict[str, Any]) -> bool:
        if not isinstance(mutation, dict):
            return False

        with self._lock:
            self._reload_if_modified()
            s_id = mutation.get("entity_id") or mutation.get("id")
            raw_payload = mutation.get("payload") or mutation.get("data")
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else dict(mutation)

            if not s_id and "id" in payload:
                s_id = payload["id"]
            if not s_id:
                return False

            # Sanitize s_id to prevent path traversal outside designated directory
            safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(s_id))
            if not safe_id:
                return False

            payload["id"] = s_id
            if "updated_at" in mutation and "updated_at" not in payload:
                payload["updated_at"] = mutation["updated_at"]
            if "deleted" in mutation and "deleted" not in payload:
                payload["deleted"] = mutation["deleted"]

            if not payload.get("updated_at"):
                payload["updated_at"] = now_utc_iso()

            local_skill = self._skills.get(s_id)
            if local_skill is None or self._should_win(payload, local_skill):
                self._skills[s_id] = payload
                self._mutated_ids.add(s_id)
                # Persist code or workflow files if provided, verifying boundary confinement
                if payload.get("code"):
                    tool_target = (self.tools_dir / f"{safe_id}.py").resolve()
                    if tool_target.is_relative_to(self.tools_dir.resolve()):
                        _write_text_atomic(tool_target, payload["code"])
                        payload["code_path"] = f"tools/{safe_id}.py"
                    else:
                        return False
                if payload.get("type") == "workflow":
                    wf_target = (self.workflows_dir / f"{safe_id}.json").resolve()
                    if wf_target.is_relative_to(self.workflows_dir.resolve()):
                        atomic_write_json_file(wf_target, payload)
                        payload["workflow_path"] = f"workflows/{safe_id}.json"
                    else:
                        return False

                self._save_registry()
                return True

            return False

    @classmethod
    def _should_win(cls, candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
        t_cand = parse_timestamp_safe(candidate.get("updated_at"))
        t_curr = parse_timestamp_safe(current.get("updated_at"))

        if t_cand > t_curr:
            return True
        if t_cand < t_curr:
            return False

        cand_del = bool(candidate.get("deleted", False))
        curr_del = bool(current.get("deleted", False))
        if cand_del != curr_del:
            return cand_del

        s_cand = canonical_json(candidate)
        s_curr = canonical_json(current)
        return s_cand > s_curr
