"""
neko_bot.task.store - Thread-safe CRDT LWW-Element-Set Task Store with Tombstones & Markdown Mirror.

Features:
- CRDT LWW-Element-Set:
  - Last-Write-Wins based on ISO-8601 UTC timestamps.
  - Deletions preserved as tombstones (deleted=True) with updated timestamps.
  - Conflict resolution: higher updated_at wins; on tie, tombstone wins; on tie, canonical JSON comparison.
- Atomic File Persistence:
  - tasks.json saved via atomic_write_json_file.
  - TASKS.md saved via atomic_write_text_file with Windows lock retry loop.
- Prefix Matching:
  - Case-insensitive short ID lookup.
  - Raises AmbiguousTaskPrefixError on multiple prefix matches.
- Automatic Markdown Mirror:
  - Clean GitHub-style task list grouped by project.
  - Completely excludes tombstones.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.task.models import Task, TaskPriority, TaskState, now_utc_iso

logger = logging.getLogger("neko_bot.task.store")


def atomic_write_text_file(file_path: Path, content: str, max_retries: int = 5) -> None:
    """Safely writes a text file atomically via os.replace with retry for Windows locks."""
    file_path = Path(file_path)
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
            time.sleep(0.01)


class AmbiguousTaskPrefixError(Exception):
    """Raised when a task ID prefix matches multiple tasks."""
    pass


class TaskStore:
    """
    Thread-safe CRDT LWW-Element-Set Task Store with tombstones and Markdown mirror.
    """

    def __init__(
        self,
        tasks_file_or_dir: Optional[Union[Path, str]] = None,
        tasks_md_file: Optional[Union[Path, str]] = None,
        config: Optional[Config] = None,
        auto_mirror: bool = True,
    ) -> None:
        self.config = config or Config.load()
        self.auto_mirror = auto_mirror
        self._lock = threading.RLock()
        self._tasks: Dict[str, Task] = {}
        self._last_mtime: float = 0.0

        # Resolve paths gracefully supporting directory or file paths
        if tasks_file_or_dir is None:
            self.tasks_file = self.config.tasks_file
            self.tasks_md_file = Path(tasks_md_file) if tasks_md_file else self.config.tasks_md_file
        else:
            p = Path(tasks_file_or_dir)
            if p.suffix.lower() == ".json":
                self.tasks_file = p
                self.tasks_md_file = Path(tasks_md_file) if tasks_md_file else (p.parent / "TASKS.md")
            else:
                self.tasks_file = p / "tasks.json"
                self.tasks_md_file = Path(tasks_md_file) if tasks_md_file else (p / "TASKS.md")

        self.load()

    def _reload_if_modified(self) -> None:
        """Reloads tasks from disk if tasks.json modification timestamp has changed."""
        try:
            if self.tasks_file.exists():
                mtime = self.tasks_file.stat().st_mtime
                if getattr(self, "_last_mtime", 0.0) != mtime:
                    self.load()
                    self._last_mtime = mtime
        except Exception:
            pass

    def load(self) -> None:
        """Loads tasks from disk. Replaces in-memory state."""
        with self._lock:
            if not self.tasks_file.exists():
                self._tasks = {}
                self._last_mtime = 0.0
                return

            try:
                self._last_mtime = self.tasks_file.stat().st_mtime
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                tasks_map: Dict[str, Task] = {}
                # Support direct map {id: task_dict} and wrapped format {"tasks": ...} or list
                items: Any = {}
                if isinstance(raw_data, dict):
                    if "tasks" in raw_data:
                        inner = raw_data["tasks"]
                        if isinstance(inner, dict):
                            items = inner
                        elif isinstance(inner, list):
                            items = {item.get("id"): item for item in inner if isinstance(item, dict) and "id" in item}
                    else:
                        items = raw_data
                elif isinstance(raw_data, list):
                    items = {item.get("id"): item for item in raw_data if isinstance(item, dict) and "id" in item}

                for task_id, task_dict in items.items():
                    if isinstance(task_dict, dict):
                        task = Task.from_dict(task_dict)
                        tasks_map[task.id] = task

                self._tasks = tasks_map
            except Exception as e:
                logger.error("Failed to load tasks from %s: %s. Preserving memory state.", self.tasks_file, e)

    def save(self) -> None:
        """Persists current state atomically to tasks.json and updates TASKS.md."""
        with self._lock:
            payload = {task_id: task.to_dict() for task_id, task in self._tasks.items()}
            atomic_write_json_file(self.tasks_file, payload)
            try:
                if self.tasks_file.exists():
                    self._last_mtime = self.tasks_file.stat().st_mtime
            except Exception:
                pass
            if self.auto_mirror:
                self.render_markdown_mirror()

    def add(self, item: Union[Task, str], **kwargs: Any) -> Task:
        """Adds a task or updates if newer. Accepts Task instance or title string."""
        with self._lock:
            self._reload_if_modified()
            if isinstance(item, Task):
                task = item
            else:
                task = Task.create(
                    title=str(item),
                    project=kwargs.get("project"),
                    priority=kwargs.get("priority", TaskPriority.MEDIUM),
                    due_date=kwargs.get("due_date"),
                    metadata=kwargs.get("metadata"),
                    custom_id=kwargs.get("custom_id"),
                )

            existing = self._tasks.get(task.id)
            if existing is None or self._should_win(task.to_dict(), existing.to_dict()):
                self._tasks[task.id] = task
                self.save()
            return self._tasks[task.id]

    def put(self, task: Task) -> Task:
        """Alias for add(task) to support direct TaskStore.put() calls."""
        return self.add(task)

    def create_task(
        self,
        title: str,
        project: Optional[str] = None,
        priority: Any = TaskPriority.MEDIUM,
        due_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        custom_id: Optional[str] = None,
    ) -> Task:
        """Convenience method to create and persist a new task."""
        task = Task.create(
            title=title,
            project=project,
            priority=priority,
            due_date=due_date,
            metadata=metadata,
            custom_id=custom_id,
        )
        return self.add(task)

    def add_task(
        self,
        title: str,
        project: Optional[str] = None,
        priority: Any = TaskPriority.MEDIUM,
        due_date: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        custom_id: Optional[str] = None,
    ) -> Task:
        """Alias matching CLI add_task convention."""
        meta = dict(metadata or {})
        if tags:
            meta["tags"] = tags
        return self.create_task(
            title=title,
            project=project,
            priority=priority,
            due_date=due_date,
            metadata=meta,
            custom_id=custom_id,
        )

    def get(self, task_id: str) -> Optional[Task]:
        """Retrieves task by exact ID."""
        with self._lock:
            self._reload_if_modified()
            return self._tasks.get(task_id)

    def get_task(self, task_id_or_prefix: str) -> Optional[Task]:
        """Convenience alias for get() / find_by_prefix()."""
        return self.get(task_id_or_prefix) or self.find_by_prefix(task_id_or_prefix, include_deleted=True)

    def find_by_prefix(self, prefix: str, include_deleted: bool = False) -> Optional[Task]:
        """
        Finds a single task matching prefix case-insensitively.
        Raises AmbiguousTaskPrefixError if multiple matches are found.
        """
        prefix_clean = prefix.strip().lower()
        with self._lock:
            self._reload_if_modified()
            # Check exact match first
            for tid, task in self._tasks.items():
                if tid.lower() == prefix_clean:
                    if not task.deleted or include_deleted:
                        return task

            matches: List[Task] = []
            for tid, task in self._tasks.items():
                if not include_deleted and task.deleted:
                    continue
                if tid.lower().startswith(prefix_clean):
                    matches.append(task)

            if not matches:
                return None
            if len(matches) == 1:
                return matches[0]

            raise AmbiguousTaskPrefixError(
                f"Prefix '{prefix}' matches multiple tasks: {[t.id for t in matches]}"
            )

    def update(
        self,
        task_id_or_prefix: str,
        title: Optional[str] = None,
        project: Optional[str] = None,
        priority: Optional[Any] = None,
        due_date: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
    ) -> Optional[Task]:
        """Updates task fields atomically."""
        with self._lock:
            task = self.find_by_prefix(task_id_or_prefix)
            if task is None:
                return None
            task.update(
                title=title,
                project=project,
                priority=priority,
                due_date=due_date,
                metadata_update=metadata_update,
            )
            self.save()
            return task

    def mark_done(self, task_id_or_prefix: str) -> Optional[Task]:
        """Marks a task as done."""
        with self._lock:
            task = self.find_by_prefix(task_id_or_prefix)
            if task is None:
                return None
            task.mark_done()
            self.save()
            return task

    def mark_todo(self, task_id_or_prefix: str) -> Optional[Task]:
        """Marks a task as todo."""
        with self._lock:
            task = self.find_by_prefix(task_id_or_prefix)
            if task is None:
                return None
            task.mark_todo()
            self.save()
            return task

    def delete(self, task_id_or_prefix: str, hard: bool = False) -> bool:
        """
        Deletes task.
        hard=False (default): Sets deleted=True with updated_at (CRDT tombstone).
        hard=True: Permanently deletes key from in-memory and disk store.
        """
        with self._lock:
            task = self.find_by_prefix(task_id_or_prefix, include_deleted=True)
            if task is None:
                return False
            if hard:
                del self._tasks[task.id]
            else:
                task.mark_deleted()
            self.save()
            return True

    def delete_task(self, task_id_or_prefix: str, hard: bool = False) -> bool:
        """Alias for delete()."""
        return self.delete(task_id_or_prefix, hard=hard)

    def mark_deleted(self, task_id_or_prefix: str) -> bool:
        """Convenience alias for delete(task_id_or_prefix, hard=False) (CRDT tombstone)."""
        return self.delete(task_id_or_prefix, hard=False)

    def list(
        self,
        project: Optional[str] = None,
        priority: Optional[Any] = None,
        state: Optional[Any] = None,
        include_done: bool = False,
        include_deleted: bool = False,
    ) -> List[Task]:
        """Queries tasks matching criteria."""
        with self._lock:
            self._reload_if_modified()
            results: List[Task] = []
            prio_enum = TaskPriority.from_str(priority) if priority is not None else None
            state_enum = TaskState.from_str(state) if state is not None else None

            for task in self._tasks.values():
                if not include_deleted and task.deleted:
                    continue
                if not include_done and task.state == TaskState.DONE:
                    continue
                if project is not None:
                    clean_p = project.lstrip("#").lower()
                    if not task.project or task.project.lower() != clean_p:
                        continue
                if prio_enum is not None and task.priority != prio_enum:
                    continue
                if state_enum is not None and task.state != state_enum:
                    continue
                results.append(task)
            return results

    def list_tasks(
        self,
        project: Optional[str] = None,
        priority: Optional[Any] = None,
        state: Optional[Any] = None,
        include_done: bool = False,
        include_deleted: bool = False,
    ) -> List[Task]:
        """Alias for list()."""
        return self.list(
            project=project,
            priority=priority,
            state=state,
            include_done=include_done,
            include_deleted=include_deleted,
        )

    def get_active_tasks(self) -> List[Task]:
        """Returns list of active (incomplete and non-deleted) tasks."""
        return self.list(include_done=False, include_deleted=False)

    # =========================================================================
    # CRDT LWW-Element-Set Reconciliation Protocol (PROJECT.md § 3)
    # =========================================================================

    def get_all_tasks_dict(self, include_deleted: bool = True) -> Dict[str, Dict[str, Any]]:
        """Returns dict of task_id -> task_dict for tasks.json conforming to ITaskStore."""
        with self._lock:
            self._reload_if_modified()
            res: Dict[str, Dict[str, Any]] = {}
            for tid, t in self._tasks.items():
                if include_deleted or not t.deleted:
                    res[tid] = t.to_dict()
            return res

    def reconcile_tasks(self, remote_tasks_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Performs CRDT LWW-Element-Set reconciliation against remote tasks dictionary.
        Returns the merged task dictionary.
        """
        with self._lock:
            changed = False
            for remote_id, remote_dict in remote_tasks_dict.items():
                if not isinstance(remote_dict, dict):
                    continue

                local_task = self._tasks.get(remote_id)
                if local_task is None:
                    # New remote element
                    self._tasks[remote_id] = Task.from_dict(remote_dict)
                    changed = True
                else:
                    # Conflict resolution between local and remote
                    if self._should_win(remote_dict, local_task.to_dict()):
                        self._tasks[remote_id] = Task.from_dict(remote_dict)
                        changed = True

            if changed:
                self.save()

            return self.get_all_tasks_dict(include_deleted=True)

    def merge(self, other_store: Union["TaskStore", Dict[str, Any], Path, str]) -> Dict[str, Task]:
        """High-level merge method accepting another TaskStore instance, dict, or file path."""
        with self._lock:
            if isinstance(other_store, TaskStore):
                remote_dict = other_store.get_all_tasks_dict(include_deleted=True)
            elif isinstance(other_store, (str, Path)):
                p = Path(other_store)
                if not p.exists():
                    return dict(self._tasks)
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                remote_dict = raw.get("tasks", raw) if isinstance(raw, dict) else {}
            elif isinstance(other_store, dict):
                remote_dict = other_store.get("tasks", other_store)
            else:
                raise TypeError(f"Unsupported store type for merge: {type(other_store)}")

            self.reconcile_tasks(remote_dict)
            return dict(self._tasks)

    @staticmethod
    def _parse_timestamp(ts: Optional[str]) -> float:
        """Parses ISO-8601 string to epoch timestamp safely."""
        if not ts:
            return 0.0
        try:
            clean = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt.timestamp()
        except Exception:
            return 0.0

    @classmethod
    def _should_win(cls, candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
        """
        Deterministic LWW tie-breaker:
        1. Compare updated_at timestamps.
        2. If equal, tombstone (deleted=True) wins.
        3. If both tombstone or both active, compare lexicographical canonical JSON strings.
        """
        t_cand = cls._parse_timestamp(candidate.get("updated_at"))
        t_curr = cls._parse_timestamp(current.get("updated_at"))

        if t_cand > t_curr:
            return True
        if t_cand < t_curr:
            return False

        # Timestamps are exactly equal - deterministic tie-breaking
        cand_del = bool(candidate.get("deleted", False))
        curr_del = bool(current.get("deleted", False))

        if cand_del != curr_del:
            return cand_del  # Tombstone wins tie-break

        # Canonical JSON string comparison
        json_cand = json.dumps(candidate, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        json_curr = json.dumps(current, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        return json_cand > json_curr

    # =========================================================================
    # Markdown Mirror Generator (workspace/TASKS.md)
    # =========================================================================

    def export_markdown(self) -> str:
        """Generates clean, human-readable Markdown mirror content."""
        with self._lock:
            active_tasks = [t for t in self._tasks.values() if not t.deleted and t.state == TaskState.TODO]
            done_tasks = [t for t in self._tasks.values() if not t.deleted and t.state == TaskState.DONE]

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            lines = [
                "# Tasks",
                "",
                f"> Auto-generated by neko_bot • Last updated: {now_str}",
                f"> Active: {len(active_tasks)} • Completed: {len(done_tasks)} • Total: {len(active_tasks) + len(done_tasks)}",
                "",
            ]

            # Group active tasks by project
            by_project: Dict[str, List[Task]] = {}
            for t in active_tasks:
                proj_name = t.project if t.project else "General"
                by_project.setdefault(proj_name, []).append(t)

            sorted_projects = sorted(
                by_project.keys(),
                key=lambda p: (1 if p.lower() == "general" else 0, p.lower())
            )

            prio_labels = {
                TaskPriority.HIGH: "`[HIGH]`",
                TaskPriority.MEDIUM: "`[MED]`",
                TaskPriority.LOW: "`[LOW]`",
            }

            for proj in sorted_projects:
                proj_tasks = by_project[proj]
                proj_tasks.sort(
                    key=lambda t: (
                        -t.priority.rank_weight,
                        t.due_date or "9999-99-99",
                        t.created_at,
                    )
                )

                header_icon = "📁" if proj.lower() == "general" else "🚀"
                lines.append(f"## {header_icon} {proj}")
                for t in proj_tasks:
                    p_badge = prio_labels.get(t.priority, "`[MED]`")
                    due_str = f" ⏰ {t.due_date}" if t.due_date else ""
                    lines.append(f"- [ ] {t.title} {p_badge}{due_str} `id:{t.id}`")
                lines.append("")

            # Completed section
            if done_tasks:
                lines.append("## ✅ Completed")
                done_tasks.sort(key=lambda t: t.completed_at or t.updated_at, reverse=True)
                for t in done_tasks:
                    p_badge = prio_labels.get(t.priority, "`[MED]`")
                    comp_date = t.completed_at[:10] if t.completed_at else ""
                    comp_str = f" (completed {comp_date})" if comp_date else ""
                    lines.append(f"- [x] {t.title} {p_badge}{comp_str} `id:{t.id}`")
                lines.append("")

            return "\n".join(lines).strip() + "\n"

    def render_markdown_mirror(self) -> None:
        """Renders and atomically flushes TASKS.md."""
        with self._lock:
            content = self.export_markdown()
            atomic_write_text_file(self.tasks_md_file, content)

    def purge_tombstones(self, older_than_days: int = 30) -> int:
        """Prunes ancient tombstones to keep storage compact."""
        with self._lock:
            now_epoch = time.time()
            cutoff = now_epoch - (older_than_days * 86400)
            to_remove = []
            for tid, t in self._tasks.items():
                if t.deleted and self._parse_timestamp(t.updated_at) < cutoff:
                    to_remove.append(tid)
            for tid in to_remove:
                del self._tasks[tid]
            if to_remove:
                self.save()
            return len(to_remove)

    # =========================================================================
    # Cloud CRDT LWW Synchronization Interface (Milestone 4)
    # =========================================================================

    def export_crdt_entities(self) -> List[Dict[str, Any]]:
        """Exports all tasks as uniform CRDT entities ready for cloud sync."""
        with self._lock:
            self._reload_if_modified()
            entities = []
            for t in self._tasks.values():
                d = t.to_dict()
                entities.append({
                    "entity_type": "task",
                    "id": t.id,
                    "entity_id": t.id,
                    "version": getattr(t, "version", 1),
                    "updated_at": t.updated_at,
                    "deleted": t.deleted,
                    "payload": d,
                    "data": d,
                })
            return entities

    def apply_crdt_mutation(self, mutation: Dict[str, Any]) -> bool:
        """Applies a remote CRDT task mutation using 3-level LWW rules."""
        if not isinstance(mutation, dict):
            return False

        with self._lock:
            self._reload_if_modified()
            raw_payload = mutation.get("payload")
            if raw_payload is None:
                raw_payload = mutation.get("data")
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else dict(mutation)
            tid = mutation.get("entity_id") or payload.get("id") or mutation.get("id")
            if not tid:
                return False

            payload["id"] = tid
            if "updated_at" in mutation and "updated_at" not in payload:
                payload["updated_at"] = mutation["updated_at"]
            if "deleted" in mutation and "deleted" not in payload:
                payload["deleted"] = mutation["deleted"]

            local_task = self._tasks.get(tid)
            if local_task is None or self._should_win(payload, local_task.to_dict()):
                self._tasks[tid] = Task.from_dict(payload)
                self.save()
                return True
            return False
