"""
neko_bot.task - Local-First Task Management Subsystem.

Features:
- Models: Task, TaskPriority, TaskState
- Store: TaskStore, CRDT LWW-Element-Set with tombstones and Markdown mirror
- Parser: parse_task_input, parse_italian_task, ItalianTaskParser
- Ranking: rank_tasks, get_top_task, get_top_task_from_store, get_task_summary
"""

from neko_bot.task.models import Task, TaskPriority, TaskState, now_utc_iso
from neko_bot.task.parser import (
    ItalianTaskParser,
    ParsedTask,
    parse_deterministic,
    parse_italian_task,
    parse_task_input,
)
from neko_bot.task.ranking import (
    format_top_task_dict,
    get_task_summary,
    get_top_task,
    get_top_task_from_store,
    rank_tasks,
)
from neko_bot.task.store import (
    AmbiguousTaskPrefixError,
    TaskStore,
    atomic_write_text_file,
)

__all__ = [
    "Task",
    "TaskPriority",
    "TaskState",
    "now_utc_iso",
    "TaskStore",
    "AmbiguousTaskPrefixError",
    "atomic_write_text_file",
    "ParsedTask",
    "ItalianTaskParser",
    "parse_deterministic",
    "parse_italian_task",
    "parse_task_input",
    "rank_tasks",
    "get_top_task",
    "get_top_task_from_store",
    "get_task_summary",
    "format_top_task_dict",
]
