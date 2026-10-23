"""
neko_bot.task.ranking - Deterministic Top-Task Ranking & Daily Summary Engine.

Implements Feature F11:
- Deterministic multi-criterion ranking:
  1. Overdue incomplete tasks first (sorted by how overdue they are).
  2. Incomplete tasks by priority (high > medium > low).
  3. Incomplete tasks by nearest upcoming deadline (due_date).
  4. Incomplete tasks without deadline by creation order (FIFO / oldest first).
  5. Filters out completed ('done') and tombstoned ('deleted') tasks.
- Exposes get_top_task() for integration with neko status --json.
- Exposes get_top_task_from_store() for offline safe loading.
- Exposes get_task_summary() for daily status rollups.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

logger = logging.getLogger("neko_bot.task.ranking")


def parse_datetime(val: Any) -> Optional[datetime]:
    """
    Safely parses an arbitrary date/time representation into a timezone-aware UTC datetime.
    Supports datetime objects, date strings (YYYY-MM-DD), and ISO-8601 strings.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    if not isinstance(val, str):
        return None

    s = val.strip()
    if not s:
        return None

    # Handle standard ISO-8601
    try:
        clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Handle date only: YYYY-MM-DD -> set to end of day 23:59:59 UTC
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            parts = [int(p) for p in s.split("-")]
            return datetime(parts[0], parts[1], parts[2], 23, 59, 59, tzinfo=timezone.utc)
        except Exception:
            pass

    # Handle YYYY-MM-DD HH:MM
    if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}$", s):
        try:
            d_part, t_part = s.split()
            y, m, d = [int(x) for x in d_part.split("-")]
            hh, mm = [int(x) for x in t_part.split(":")]
            return datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)
        except Exception:
            pass

    # Handle YYYY-MM-DD HH:MM:SS
    if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$", s):
        try:
            d_part, t_part = s.split()
            y, m, d = [int(x) for x in d_part.split("-")]
            hh, mm, ss = [int(x) for x in t_part.split(":")]
            return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def get_current_utc_time() -> datetime:
    """Returns current datetime in timezone-aware UTC."""
    return datetime.now(timezone.utc)


def extract_task_field(task: Any, field_name: str, default: Any = None) -> Any:
    """Extracts attribute or dictionary key transparently."""
    if isinstance(task, dict):
        return task.get(field_name, default)
    return getattr(task, field_name, default)


def is_task_active(task: Any) -> bool:
    """Returns True if task is neither completed nor tombstoned (deleted)."""
    deleted = bool(extract_task_field(task, "deleted", False))
    if deleted:
        return False

    state = extract_task_field(task, "state", None)
    if state is not None:
        state_str = state.value if hasattr(state, "value") else str(state).lower()
        if state_str in ("done", "completed"):
            return False

    completed = extract_task_field(task, "completed", None)
    if completed is True:
        return False

    return True


def task_ranking_key(
    task: Any,
    now: datetime,
    overdue_priority_first: bool = False,
) -> Tuple[Any, ...]:
    """
    Computes deterministic sort key for an active task.

    Ranking Hierarchy:
    1. Overdue incomplete tasks first (tier 0):
       Sorted by how overdue they are (oldest deadline first: due_ts ascending).
       Tie-breaker: priority weight, creation order, ID.
    2. Non-overdue tasks (tier 1):
       Sorted by priority (high > medium > low).
       Within same priority: nearest upcoming deadline (due_ts ascending).
       Unscheduled tasks: creation order (FIFO / oldest first).
       Tie-breaker: ID.
    """
    task_id = str(extract_task_field(task, "id", ""))
    prio_raw = extract_task_field(task, "priority", "medium")
    prio_str = prio_raw.value if hasattr(prio_raw, "value") else str(prio_raw).lower()
    prio_weights = {"high": 0, "medium": 1, "low": 2}
    prio_weight = prio_weights.get(prio_str, 1)

    due_raw = extract_task_field(task, "due_date", None)
    due_dt = parse_datetime(due_raw)

    created_raw = extract_task_field(task, "created_at", None)
    created_dt = parse_datetime(created_raw) or datetime.fromtimestamp(0, tz=timezone.utc)
    created_ts = created_dt.timestamp()

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    is_overdue = due_dt is not None and due_dt < now

    if is_overdue:
        # Tier 0: Overdue
        tier = 0
        due_ts = due_dt.timestamp()  # type: ignore[union-attr]
        if overdue_priority_first:
            return (tier, prio_weight, due_ts, created_ts, task_id)
        # Primary requirement: sorted by how overdue they are (due_ts ascending)
        return (tier, 0, due_ts, prio_weight, created_ts, task_id)
    else:
        # Tier 1: Non-overdue
        tier = 1
        has_deadline = 0 if due_dt is not None else 1
        due_ts = due_dt.timestamp() if due_dt is not None else float("inf")
        return (tier, prio_weight, has_deadline, due_ts, created_ts, task_id)


def rank_tasks(
    tasks: Iterable[Any],
    reference_time: Optional[datetime] = None,
    overdue_priority_first: bool = False,
) -> List[Any]:
    """
    Ranks an iterable of tasks deterministically.
    Excludes all completed and deleted (tombstoned) tasks.
    """
    now = reference_time or get_current_utc_time()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    active_tasks = [t for t in tasks if is_task_active(t)]
    active_tasks.sort(key=lambda t: task_ranking_key(t, now, overdue_priority_first))
    return active_tasks


def format_top_task_dict(task: Any) -> Dict[str, Any]:
    """Converts a top task to the contract schema dictionary for external consumers."""
    if task is None:
        return {}

    if hasattr(task, "to_status_dict") and callable(task.to_status_dict):
        return task.to_status_dict()

    task_id = str(extract_task_field(task, "id", ""))
    title = str(extract_task_field(task, "title", ""))
    project = extract_task_field(task, "project", "") or ""
    prio_raw = extract_task_field(task, "priority", "medium")
    priority = prio_raw.value if hasattr(prio_raw, "value") else str(prio_raw).lower()
    due_date = extract_task_field(task, "due_date", "") or ""

    return {
        "id": task_id,
        "title": title,
        "project": project,
        "priority": priority,
        "due_date": due_date,
    }


def get_top_task(
    tasks_or_store: Any,
    reference_time: Optional[datetime] = None,
    as_dict: bool = True,
    overdue_priority_first: bool = False,
) -> Optional[Union[Dict[str, Any], Any]]:
    """
    Returns the single top-ranked active task, or None if no active tasks exist.
    Accepts TaskStore, list/iterable of tasks, or dictionary.
    """
    if hasattr(tasks_or_store, "list"):
        task_list = tasks_or_store.list(include_done=True, include_deleted=True)
    elif hasattr(tasks_or_store, "get_all_tasks_dict"):
        task_list = list(tasks_or_store.get_all_tasks_dict().values())
    elif isinstance(tasks_or_store, dict):
        if "tasks" in tasks_or_store and isinstance(tasks_or_store["tasks"], list):
            task_list = tasks_or_store["tasks"]
        else:
            task_list = list(tasks_or_store.values())
    elif hasattr(tasks_or_store, "__iter__"):
        task_list = list(tasks_or_store)
    else:
        task_list = []

    ranked = rank_tasks(task_list, reference_time=reference_time, overdue_priority_first=overdue_priority_first)
    if not ranked:
        return None
    top = ranked[0]
    return format_top_task_dict(top) if as_dict else top


def get_top_task_from_store(
    workspace_dir_or_file: Optional[Union[Path, str]] = None,
    config: Optional[Any] = None,
    reference_time: Optional[datetime] = None,
    overdue_priority_first: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Safely reads tasks from tasks.json, ranks them, and returns top task dict.
    Returns None if file does not exist, is empty, corrupt, or contains no active tasks.
    """
    if workspace_dir_or_file is None:
        if config is not None:
            tasks_path = config.tasks_file
        else:
            from neko_bot.core.config import Config
            tasks_path = Config.load().tasks_file
    else:
        p = Path(workspace_dir_or_file)
        if p.suffix.lower() == ".json":
            tasks_path = p
        else:
            tasks_path = p / "tasks.json"

    if not tasks_path.exists():
        return None

    try:
        with open(tasks_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return None
            data = json.loads(raw)
    except Exception as e:
        logger.debug("Failed to read tasks.json for ranking: %s", e)
        return None

    task_list: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        if "tasks" in data and isinstance(data["tasks"], list):
            task_list = [t for t in data["tasks"] if isinstance(t, dict)]
        elif "tasks" in data and isinstance(data["tasks"], dict):
            task_list = [v for v in data["tasks"].values() if isinstance(v, dict)]
        else:
            task_list = [v for v in data.values() if isinstance(v, dict)]
    elif isinstance(data, list):
        task_list = [t for t in data if isinstance(t, dict)]

    return get_top_task(task_list, reference_time=reference_time, as_dict=True, overdue_priority_first=overdue_priority_first)


def get_task_summary(
    tasks_or_store: Any,
    reference_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculates structured daily summary metrics across tasks.
    Accepts TaskStore, list/iterable of tasks, or dictionary.
    """
    now = reference_time or get_current_utc_time()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today_date_str = now.strftime("%Y-%m-%d")

    if hasattr(tasks_or_store, "list"):
        tasks = tasks_or_store.list(include_done=True, include_deleted=True)
    elif hasattr(tasks_or_store, "get_all_tasks_dict"):
        tasks = list(tasks_or_store.get_all_tasks_dict().values())
    elif isinstance(tasks_or_store, dict):
        if "tasks" in tasks_or_store and isinstance(tasks_or_store["tasks"], list):
            tasks = tasks_or_store["tasks"]
        else:
            tasks = list(tasks_or_store.values())
    elif hasattr(tasks_or_store, "__iter__"):
        tasks = list(tasks_or_store)
    else:
        tasks = []

    total_active = 0
    overdue_count = 0
    due_today_count = 0
    high_priority_count = 0
    completed_today_count = 0
    by_project: Dict[str, int] = {}
    active_items: List[Any] = []

    for task in tasks:
        deleted = bool(extract_task_field(task, "deleted", False))
        if deleted:
            continue

        state = extract_task_field(task, "state", None)
        state_str = state.value if hasattr(state, "value") else str(state).lower() if state else "todo"
        completed = extract_task_field(task, "completed", False) or state_str in ("done", "completed")

        if completed:
            completed_at_raw = extract_task_field(task, "completed_at", None) or extract_task_field(task, "updated_at", None)
            comp_dt = parse_datetime(completed_at_raw)
            if comp_dt and comp_dt.strftime("%Y-%m-%d") == today_date_str:
                completed_today_count += 1
            continue

        # Task is active
        total_active += 1
        active_items.append(task)

        prio_raw = extract_task_field(task, "priority", "medium")
        prio_str = prio_raw.value if hasattr(prio_raw, "value") else str(prio_raw).lower()
        if prio_str == "high":
            high_priority_count += 1

        proj = extract_task_field(task, "project", None)
        if proj:
            clean_proj = str(proj).lstrip("#").strip()
            if clean_proj:
                by_project[clean_proj] = by_project.get(clean_proj, 0) + 1

        due_raw = extract_task_field(task, "due_date", None)
        due_dt = parse_datetime(due_raw)
        if due_dt:
            if due_dt < now:
                overdue_count += 1
            if due_dt.strftime("%Y-%m-%d") == today_date_str:
                due_today_count += 1

    top_task = get_top_task(active_items, reference_time=now, as_dict=True)

    return {
        "total_active": total_active,
        "overdue_count": overdue_count,
        "due_today_count": due_today_count,
        "high_priority_count": high_priority_count,
        "completed_today_count": completed_today_count,
        "by_project": by_project,
        "top_task": top_task,
    }
