"""
neko_bot.cli - Unified Command-Line Interface Harness for neko_bot.

Fast startup (< 15ms) built on standard library argparse.
Supports:
  neko status [--json]
  neko daemon {start,stop,status,restart,run} [--foreground] [--port PORT]
  neko do "<prompt>"
  neko task {add,list,done,delete,top,summary}
"""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from neko_bot.core.config import Config
from neko_bot.core.daemon import DaemonController, run_daemon
from neko_bot.core.router import get_default_router

DEFAULT_PORT = 8766


def print_human_status(status: Dict[str, Any]) -> None:
    """Renders human-readable status table to stdout."""
    health = str(status.get("daemon_health", "unknown")).upper()
    pid = status.get("daemon_pid")
    pid_str = f" (PID: {pid})" if pid else ""
    res_mode = str(status.get("resource_mode", "normal")).upper()
    app = status.get("active_application") or "None (Desktop)"
    proj = status.get("active_project") or "None"
    seconds = int(status.get("today_tracked_seconds", 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    time_str = f"{hours}h {minutes:02d}m ({seconds}s)"
    afk = "AFK" if status.get("is_afk") else "ACTIVE"
    idle = int(status.get("idle_seconds", 0))

    top_task = status.get("top_task")
    if isinstance(top_task, dict):
        prio = str(top_task.get("priority", "normal")).upper()
        title = top_task.get("title", "Untitled")
        project = top_task.get("project", "")
        proj_str = f" (#{project})" if project else ""
        top_str = f"[{prio}] {title}{proj_str}"
    else:
        top_str = "None"

    notifs = len(status.get("notification_queue", []))

    lines = [
        "=" * 60,
        "                      NEKO BOT STATUS                       ",
        "=" * 60,
        f"  Daemon Health:    {health}{pid_str}",
        f"  Resource Mode:    {res_mode}",
        f"  Active Window:    {app} (Project: {proj})",
        f"  Today Tracked:    {time_str}",
        f"  User State:       {afk} (Idle: {idle}s)",
        f"  Top Task:         {top_str}",
        f"  Notifications:    {notifs} pending in queue",
        "=" * 60,
    ]
    print("\n".join(lines))


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko status [--json]`."""
    controller = DaemonController(config)
    status_data = controller.get_status_payload()

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(status_data, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        print_human_status(status_data)
    return 0


def cmd_daemon(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko daemon {start,stop,status,restart,run}`."""
    subaction = args.subaction
    controller = DaemonController(config)

    if subaction == "status":
        running, pid = controller.is_running()
        if running:
            print(f"Daemon is running (PID: {pid}, port: {config.port}).")
            return 0
        else:
            print("Daemon is stopped.")
            return 1

    elif subaction == "start":
        foreground = getattr(args, "foreground", False)
        ok, msg = controller.start(foreground=foreground)
        print(msg)
        return 0 if ok else 1

    elif subaction == "run":
        run_daemon(port=config.port, root_dir=config.project_root)
        return 0

    elif subaction == "stop":
        ok, msg = controller.stop()
        print(msg)
        return 0 if ok else 1

    elif subaction == "restart":
        ok, msg = controller.restart()
        print(msg)
        return 0 if ok else 1

    return 0


# --- Task Management Command Handlers (Milestone 2) ---

def cmd_task_add(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko task add \"<text>\" [--project P] [--priority PRIO] [--due DUE]`."""
    from neko_bot.task.models import TaskPriority
    from neko_bot.task.parser import parse_task_input
    from neko_bot.task.store import TaskStore

    parsed = parse_task_input(args.text, config=config)

    # CLI flag overrides
    project = args.project if getattr(args, "project", None) else parsed.project
    priority_arg = getattr(args, "priority", None)
    if priority_arg:
        priority = TaskPriority.from_str(priority_arg)
    else:
        priority = parsed.priority

    due_date = getattr(args, "due", None) if getattr(args, "due", None) else parsed.due_date

    store = TaskStore(config.tasks_file, config.tasks_md_file)
    task = store.add_task(
        title=parsed.title,
        project=project,
        priority=priority,
        due_date=due_date,
        tags=parsed.tags,
        metadata={"raw_prompt": args.text, "parsed_by": parsed.parsed_by},
    )

    proj_str = f" (#{task.project})" if task.project and task.project != "default" else ""
    due_str = f" | Due: {task.due_date}" if task.due_date else ""
    prio_str = str(task.priority.value if hasattr(task.priority, "value") else task.priority).upper()

    print(f"Task added: [{prio_str}] {task.title}{proj_str}{due_str} (ID: {task.id[:8]})")
    return 0


def cmd_task_list(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko task list [--all] [--project P] [--priority PRIO] [--json]`."""
    from neko_bot.task.store import TaskStore

    store = TaskStore(config.tasks_file, config.tasks_md_file)
    tasks = store.list_tasks(
        include_done=getattr(args, "all", False),
        project=getattr(args, "project", None),
        priority=getattr(args, "priority", None),
    )

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps([t.to_dict() for t in tasks], indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    if not tasks:
        print("No tasks found.")
        return 0

    lines = [
        "=" * 80,
        "                                NEKO TASK LIST                                  ",
        "=" * 80,
        f"  {'ID':<10} {'STATUS':<8} {'PRIO':<8} {'PROJECT':<12} {'DUE DATE':<20} {'TITLE':<20}",
        "-" * 80,
    ]
    for t in tasks:
        status_box = "[X]" if t.state == "done" or t.is_done() else "[ ]"
        prio = str(t.priority.value if hasattr(t.priority, "value") else t.priority).upper()
        proj = t.project or "default"
        due = t.due_date[:16] if t.due_date else "---"
        title = t.title
        lines.append(f"  {t.id[:8]:<10} {status_box:<8} {prio:<8} {proj:<12} {due:<20} {title}")

    pending_count = sum(1 for t in tasks if not (t.state == "done" or t.is_done()))
    completed_count = len(tasks) - pending_count
    lines.append("=" * 80)
    lines.append(f"  Total: {len(tasks)} tasks ({pending_count} pending, {completed_count} completed)")
    print("\n".join(lines))
    return 0


def cmd_task_done(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko task done <task_id_or_prefix>`."""
    from neko_bot.task.store import AmbiguousTaskPrefixError, TaskStore

    store = TaskStore(config.tasks_file, config.tasks_md_file)
    try:
        task = store.mark_done(args.task_id)
        if task is None:
            sys.stderr.write(f"Error: Task '{args.task_id}' not found.\n")
            return 1
        print(f"Task completed: [{task.id[:8]}] {task.title}")
        return 0
    except AmbiguousTaskPrefixError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


def cmd_task_delete(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko task delete <task_id_or_prefix>`."""
    from neko_bot.task.store import AmbiguousTaskPrefixError, TaskStore

    store = TaskStore(config.tasks_file, config.tasks_md_file)
    try:
        task = store.find_by_prefix(args.task_id, include_deleted=True)
        if task is None:
            sys.stderr.write(f"Error: Task '{args.task_id}' not found.\n")
            return 1
        store.delete(task.id)
        print(f"Task deleted: [{task.id[:8]}] {task.title}")
        return 0
    except AmbiguousTaskPrefixError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


def cmd_task_top(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko task top [--json]`."""
    from neko_bot.task.ranking import format_top_task_dict, get_top_task
    from neko_bot.task.store import TaskStore

    store = TaskStore(config.tasks_file, config.tasks_md_file)
    top = get_top_task(store, as_dict=False)

    if getattr(args, "json", False):
        top_dict = format_top_task_dict(top) if top else None
        sys.stdout.write(json.dumps(top_dict, indent=2, ensure_ascii=False) + "\n")
        return 0

    if not top:
        print("No pending tasks found. All caught up!")
        return 0

    prio = str(top.priority.value if hasattr(top.priority, "value") else top.priority).upper()
    proj = f" (#{top.project})" if top.project and top.project != "default" else ""
    print(f"[TOP TASK] [{prio}] {top.title}{proj}")
    print(f"  ID:       {top.id[:8]}")
    print(f"  Priority: {prio}")
    print(f"  Project:  {top.project or 'default'}")
    print(f"  Due Date: {top.due_date or 'None'}")
    return 0


def cmd_task_summary(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko task summary [--json]`."""
    from neko_bot.task.ranking import get_task_summary
    from neko_bot.task.store import TaskStore

    store = TaskStore(config.tasks_file, config.tasks_md_file)
    summary = get_task_summary(store)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        return 0

    lines = [
        "=" * 60,
        "                    NEKO TASK SUMMARY                       ",
        "=" * 60,
        f"  Active Tasks:      {summary['total_active']}",
        f"  Overdue Tasks:     {summary['overdue_count']}",
        f"  Due Today:         {summary['due_today_count']}",
        f"  High Priority:     {summary['high_priority_count']}",
        f"  Completed Today:   {summary['completed_today_count']}",
    ]
    if summary["by_project"]:
        lines.append("  By Project:")
        for proj, count in summary["by_project"].items():
            lines.append(f"    - {proj}: {count}")
    top = summary.get("top_task")
    if top:
        prio = str(top.get("priority", "")).upper()
        title = top.get("title", "")
        lines.append(f"  Top Task:          [{prio}] {title}")
    lines.append("=" * 60)
    print("\n".join(lines))
    return 0


def cmd_do(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko do \"<prompt>\"` with smart routing for tasks, status, and summary."""
    prompt = args.prompt.strip()

    # Smart routing for status and summary commands
    if prompt.lower() in ("status", "stato"):
        return cmd_status(args, config)
    if prompt.lower() in ("summary", "sommario", "riassunto", "briefing"):
        return cmd_task_summary(args, config)

    # Detect task creation intent
    is_task = False
    task_keywords = ["#", "!alta", "!media", "!bassa", "!urgente", "domani", "oggi", "stasera", "dopodomani", "entro", "alle", "task", "todo", "ricorda", "fare"]
    lower_prompt = prompt.lower()
    if any(k in lower_prompt for k in task_keywords):
        is_task = True

    # Try sending to running daemon first
    url = f"http://{config.host}:{config.port}/command"
    payload = json.dumps({"action": "do", "prompt": prompt}).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res = data.get("result", {})
            if isinstance(res, dict) and "data" in res:
                item = res["data"]
                if isinstance(item, dict) and "title" in item:
                    print(f"Task accepted: {item.get('title')} [Priority: {item.get('priority')}]")
                else:
                    print(str(item))
            else:
                print(str(data.get("response", "Executed.")))
            return 0
    except Exception:
        pass

    # If prompt is a task, create it directly in the TaskStore
    if is_task:
        from neko_bot.task.parser import parse_task_input
        from neko_bot.task.store import TaskStore

        parsed = parse_task_input(prompt, config=config)
        store = TaskStore(config.tasks_file, config.tasks_md_file)
        task = store.add_task(
            title=parsed.title,
            project=parsed.project,
            priority=parsed.priority,
            due_date=parsed.due_date,
            tags=parsed.tags,
            metadata={"raw_prompt": prompt, "parsed_by": parsed.parsed_by},
        )
        prio_val = task.priority.value if hasattr(task.priority, "value") else str(task.priority)
        print(f"Task accepted: {task.title} [Priority: {prio_val}]")
        return 0

    # In-process deterministic routing fallback
    router = get_default_router(config)
    result = router.route_prompt(prompt)
    data = result.get("data")
    if isinstance(data, dict) and "title" in data:
        print(f"Task accepted: {data.get('title')} [Priority: {data.get('priority')}]")
    else:
        print(str(data if data is not None else f"Executed: {prompt}"))
    return 0


def print_time_today_table(report: Any) -> None:
    from neko_bot.tracker.aggregator import format_duration

    lines = [
        "=" * 80,
        f"                           NEKO TIME TODAY ({report.start_date})                         ",
        "=" * 80,
        f"  {'PROJECT':<20} {'CATEGORY':<14} {'TIME (H:M)':<14} {'SECONDS':<12} {'% DAY':<8}",
        "-" * 80,
    ]
    tot = report.total_seconds if report.total_seconds > 0 else 1.0
    if not report.by_project:
        lines.append("  No time tracked today yet.")
    else:
        for proj, sec in report.by_project.items():
            pct = (sec / tot) * 100.0
            h_str = report.by_project_human.get(proj, format_duration(sec))
            cat = "creative" if "creative" in report.by_category else "dev"
            lines.append(f"  {proj:<20} {cat:<14} {h_str:<14} {int(sec):<12} {pct:>5.1f}%")

    lines.append("=" * 80)
    top_p = list(report.by_project.keys())[0] if report.by_project else "None"
    machines_str = ", ".join(report.machines) if report.machines else "None"
    lines.append(f"  Total Active Time:   {report.total_human} ({int(report.total_seconds)}s)")
    lines.append(f"  Idle Subtracted:     {report.idle_subtracted_human} ({int(report.idle_subtracted_total)}s)")
    lines.append(f"  Top Project:         {top_p}")
    lines.append(f"  Active Machines:     {machines_str}")
    lines.append("=" * 80)
    print("\n".join(lines))


def print_time_week_table(report: Any) -> None:
    from neko_bot.tracker.aggregator import format_duration

    lines = [
        "=" * 80,
        f"               NEKO 7-DAY TIME ROLLUP ({report.start_date} to {report.end_date})               ",
        "=" * 80,
        f"  {'DATE':<14} {'HOURS':<14} {'SECONDS':<14} {'STATUS':<20}",
        "-" * 80,
    ]
    if not report.by_date:
        lines.append("  No time logs recorded in this 7-day period.")
    else:
        for dt_str, sec in report.by_date.items():
            h_str = format_duration(sec)
            status_desc = f"{int(sec // 3600)}h active" if sec > 0 else "off / inactive"
            lines.append(f"  {dt_str:<14} {h_str:<14} {int(sec):<14} {status_desc:<20}")

    lines.append("=" * 80)
    days_count = len(report.by_date) if report.by_date else 7
    avg_sec = report.total_seconds / days_count if days_count > 0 else 0
    top_cat = list(report.by_category.keys())[0] if report.by_category else "None"
    lines.append(f"  Weekly Total:        {report.total_human} ({int(report.total_seconds)}s)")
    lines.append(f"  Daily Average:       {format_duration(avg_sec)}")
    lines.append(f"  Top Category:        {top_cat}")
    lines.append("=" * 80)
    print("\n".join(lines))


def print_project_time_table(data: Dict[str, Any]) -> None:
    from neko_bot.tracker.aggregator import format_duration

    proj = data.get("project", "Unknown")
    lines = [
        "=" * 80,
        f"                       PROJECT TIME REPORT: {proj}                       ",
        "=" * 80,
        f"  Total Tracked:       {data.get('total_human', '0h 00m')} ({int(data.get('total_seconds', 0))}s)",
        f"  Date Range:          {data.get('start_date', '')} to {data.get('end_date', '')}",
        f"  Idle Subtracted:     {format_duration(data.get('idle_subtracted_total', 0))}",
        "-" * 80,
        "  BY APPLICATION:",
    ]
    by_app = data.get("by_app", {})
    tot = data.get("total_seconds", 1.0) or 1.0
    if not by_app:
        lines.append("    (No application logs recorded)")
    else:
        for app, sec in by_app.items():
            pct = (sec / tot) * 100.0
            lines.append(f"    - {app:<24} {format_duration(sec):<10} ({int(sec)}s) [{pct:>5.1f}%]")

    lines.append("-" * 80)
    lines.append("  BY MACHINE:")
    by_host = data.get("by_hostname", {})
    for host, sec in by_host.items():
        lines.append(f"    - {host:<24} {format_duration(sec):<10} ({int(sec)}s)")
    lines.append("=" * 80)
    print("\n".join(lines))


def print_full_time_report_table(report: Any) -> None:
    from neko_bot.tracker.aggregator import format_duration

    lines = [
        "=" * 80,
        f"                    NEKO CONSOLIDATED TIME REPORT ({report.start_date} to {report.end_date})                    ",
        "=" * 80,
        f"  Total Active Work:   {report.total_human} ({int(report.total_seconds)}s)",
        f"  Idle Subtracted:     {report.idle_subtracted_human} ({int(report.idle_subtracted_total)}s)",
        f"  Log Entries:         {report.entry_count}",
        f"  Machines:            {', '.join(report.machines) if report.machines else 'None'}",
        "-" * 80,
        "  PROJECTS BREAKDOWN:",
    ]
    tot = report.total_seconds if report.total_seconds > 0 else 1.0
    for p, sec in report.by_project.items():
        lines.append(f"    - {p:<24} {format_duration(sec):<10} ({(sec/tot)*100:>5.1f}%)")
    lines.append("-" * 80)
    lines.append("  CATEGORIES BREAKDOWN:")
    for c, sec in report.by_category.items():
        lines.append(f"    - {c:<24} {format_duration(sec):<10} ({(sec/tot)*100:>5.1f}%)")
    lines.append("-" * 80)
    lines.append("  APPLICATIONS BREAKDOWN:")
    for a, sec in report.by_app.items():
        lines.append(f"    - {a:<24} {format_duration(sec):<10} ({(sec/tot)*100:>5.1f}%)")
    lines.append("=" * 80)
    print("\n".join(lines))


def cmd_time_today(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko time today [--json]`."""
    from neko_bot.tracker.aggregator import TimeAggregator

    agg = TimeAggregator(config.time_logs_dir)
    report = agg.aggregate_today()

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    print_time_today_table(report)
    return 0


def cmd_time_week(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko time week [--json] [--end DATE]`."""
    from neko_bot.tracker.aggregator import TimeAggregator

    agg = TimeAggregator(config.time_logs_dir)
    report = agg.aggregate_week(end_date=getattr(args, "end", None))

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    print_time_week_table(report)
    return 0


def cmd_time_project(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko time project <name> [--json] [--from DATE] [--to DATE]`."""
    from neko_bot.tracker.aggregator import TimeAggregator

    agg = TimeAggregator(config.time_logs_dir)
    data = agg.aggregate_project(
        project_name=args.name,
        start_date=getattr(args, "from_date", None),
        end_date=getattr(args, "to_date", None),
    )

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    print_project_time_table(data)
    return 0


def cmd_time_report(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko time report [--json] [--from DATE] [--to DATE] [--project P]`."""
    from neko_bot.tracker.aggregator import TimeAggregator

    agg = TimeAggregator(config.time_logs_dir)
    report = agg.aggregate(
        start_date=getattr(args, "from_date", None),
        end_date=getattr(args, "to_date", None),
        project_filter=getattr(args, "project", None),
    )

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    print_full_time_report_table(report)
    return 0


def cmd_quick(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko quick` launching the floating quick launcher window."""
    from neko_bot.ui.quick_bar import QuickBar
    app = QuickBar(config=config)
    return app.run()


def cmd_briefing(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko briefing [--evening] [--json] [--language {it,en}] [--deterministic]`."""
    from neko_bot.briefing.generator import BriefingGenerator
    gen = BriefingGenerator(config=config)
    lang = getattr(args, "language", "it") or "it"
    force_det = getattr(args, "deterministic", False)
    if getattr(args, "evening", False):
        res = gen.generate_evening_wrapup(force_deterministic=force_det, language=lang)
    else:
        res = gen.generate_morning_briefing(force_deterministic=force_det, language=lang)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(res.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    sys.stdout.write(f"\n{res.title}\n{'=' * len(res.title)}\n{res.body}\n\n")
    sys.stdout.flush()
    return 0


def cmd_sync(args: argparse.Namespace, config: Optional[Config] = None) -> int:
    """Handles `neko sync [--json] [--force] [--status]`."""
    if config is None:
        root_dir = Path(args.workspace).resolve() if getattr(args, "workspace", None) else None
        config = Config.load(root_dir=root_dir)
        if getattr(args, "port", None):
            config.port = args.port

    from neko_bot.sync.engine import GitSyncEngine, SyncStatus

    engine = GitSyncEngine(config=config)

    if getattr(args, "status", False):
        status = engine.get_repo_status()
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps(asdict(status), indent=2, default=str) + "\n")
            sys.stdout.flush()
            return 0
        sys.stdout.write("\n" + "=" * 55 + "\n")
        sys.stdout.write("  🐾 NEKO BOT — GIT REPOSITORY STATUS\n")
        sys.stdout.write("=" * 55 + "\n")
        sys.stdout.write(f"  Git Installed : {'Yes' if status.git_installed else 'No'}\n")
        sys.stdout.write(f"  Is Repository : {'Yes' if status.is_repo else 'No'}\n")
        if status.is_repo:
            sys.stdout.write(f"  Repo Root     : {status.repo_root}\n")
            sys.stdout.write(f"  Branch        : {status.current_branch or 'N/A'}\n")
            sys.stdout.write(f"  Remote        : {status.remote_name or 'None'} ({status.remote_url or 'N/A'})\n")
            sys.stdout.write(f"  Ahead/Behind  : +{status.ahead_commits} / -{status.behind_commits}\n")
            sys.stdout.write(f"  Dirty State   : {'Yes' if status.is_dirty else 'Clean'}\n")
        sys.stdout.write("=" * 55 + "\n\n")
        sys.stdout.flush()
        return 0

    # If daemon is running, try HTTP IPC first
    controller = DaemonController(config)
    running, _ = controller.is_running()
    if running:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "sync", "force": getattr(args, "force", False)}).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                res_dict = data.get("result") or {}
                status_str = res_dict.get("status", "synced")
                if getattr(args, "json", False):
                    sys.stdout.write(json.dumps(res_dict, indent=2, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
                else:
                    msg = res_dict.get("message", "Synced via daemon.")
                    if status_str == "not_a_repo":
                        sys.stdout.write("Workspace is not a Git repository. Auto-sync skipped.\n")
                    elif status_str == "no_git_binary":
                        sys.stdout.write("Git is not installed or not in PATH. Auto-sync skipped.\n")
                    elif status_str == "offline":
                        sys.stdout.write(f"[Neko Sync] Offline: {msg}\n")
                    elif data.get("success"):
                        sys.stdout.write(f"[Neko Sync] ({status_str}) {msg}\n")
                    else:
                        sys.stdout.write(f"[Neko Sync] Error ({status_str}): {msg}\n")
                    sys.stdout.flush()
                return 0 if (data.get("success") or status_str in ("not_a_repo", "no_git_binary", "no_remote")) else 1
        except Exception:
            pass  # Fall back to in-process GitSyncEngine

    # Otherwise run in-process
    res = engine.sync(force_push=getattr(args, "force", False))
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(res.to_dict(), indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        if res.status == SyncStatus.NOT_A_REPO:
            sys.stdout.write("Workspace is not a Git repository. Auto-sync skipped.\n")
        elif res.status == SyncStatus.NO_GIT_BINARY:
            sys.stdout.write("Git is not installed or not in PATH. Auto-sync skipped.\n")
        elif res.status == SyncStatus.OFFLINE:
            sys.stdout.write(f"[Neko Sync] Offline: {res.message}\n")
        elif res.success:
            sys.stdout.write(f"[Neko Sync] Success ({res.status.value}): {res.message}\n")
        else:
            sys.stdout.write(f"[Neko Sync] Error ({res.status.value}): {res.message}\n")
        sys.stdout.flush()

    return 0 if (res.success or res.status in (SyncStatus.NOT_A_REPO, SyncStatus.NO_GIT_BINARY, SyncStatus.NO_REMOTE)) else 1


def build_parser() -> argparse.ArgumentParser:
    """Builds top-level CLI argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=str, default=None, help="Path to workspace root")
    common.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Daemon IPC port (default: {DEFAULT_PORT})")

    parser = argparse.ArgumentParser(
        prog="neko",
        description="neko_bot: Local-First AI Productivity Bot & Background Daemon",
        parents=[common],
    )
    parser.add_argument("-v", "--version", action="version", version="neko_bot 0.1.0")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # status command
    p_status = subparsers.add_parser("status", parents=[common], help="Display bot health, active window, tracked time, top task")
    p_status.add_argument("--json", action="store_true", help="Output machine-readable JSON payload")
    p_status.set_defaults(handler=cmd_status)

    # daemon command
    p_daemon = subparsers.add_parser("daemon", parents=[common], help="Manage background daemon lifecycle")
    p_daemon.add_argument(
        "subaction",
        choices=["start", "stop", "status", "restart", "run"],
        help="Lifecycle action",
    )
    p_daemon.add_argument("--foreground", action="store_true", help="Run daemon in current console")
    p_daemon.set_defaults(handler=cmd_daemon)

    # do command
    p_do = subparsers.add_parser("do", parents=[common], help="Execute natural-language request or task")
    p_do.add_argument("prompt", type=str, help="Natural language prompt")
    p_do.set_defaults(handler=cmd_do)

    # task command
    p_task = subparsers.add_parser("task", parents=[common], help="Manage tasks and project to-dos")
    task_sub = p_task.add_subparsers(dest="task_action", metavar="<action>")

    # task add "<text>"
    p_tadd = task_sub.add_parser("add", parents=[common], help="Add a task via natural language or flags")
    p_tadd.add_argument("text", type=str, help="Task description with optional #tags, !priorities, and deadlines")
    p_tadd.add_argument("--project", type=str, default=None, help="Explicit project name")
    p_tadd.add_argument("--priority", type=str, default=None, help="Explicit priority (high, medium, low)")
    p_tadd.add_argument("--due", type=str, default=None, help="Explicit due date (YYYY-MM-DD [HH:MM])")
    p_tadd.set_defaults(handler=cmd_task_add)

    # task list
    p_tlist = task_sub.add_parser("list", parents=[common], help="List active tasks")
    p_tlist.add_argument("--all", action="store_true", help="Include completed tasks")
    p_tlist.add_argument("--project", type=str, default=None, help="Filter by project")
    p_tlist.add_argument("--priority", type=str, default=None, help="Filter by priority")
    p_tlist.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_tlist.set_defaults(handler=cmd_task_list)

    # task done <task_id>
    p_tdone = task_sub.add_parser("done", parents=[common], help="Mark a task completed by ID or short prefix")
    p_tdone.add_argument("task_id", type=str, help="Task ID or prefix")
    p_tdone.set_defaults(handler=cmd_task_done)

    # task delete <task_id>
    p_tdel = task_sub.add_parser("delete", parents=[common], help="Delete a task (tombstone) by ID or short prefix")
    p_tdel.add_argument("task_id", type=str, help="Task ID or prefix")
    p_tdel.set_defaults(handler=cmd_task_delete)

    # task top
    p_ttop = task_sub.add_parser("top", parents=[common], help="Show current highest priority top task")
    p_ttop.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_ttop.set_defaults(handler=cmd_task_top)

    # task summary
    p_tsum = task_sub.add_parser("summary", parents=[common], help="Display daily task summary metrics")
    p_tsum.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_tsum.set_defaults(handler=cmd_task_summary)

    # time command
    p_time = subparsers.add_parser("time", parents=[common], help="Tracked project hours and daily/weekly aggregations")
    time_sub = p_time.add_subparsers(dest="time_action", metavar="<action>")

    # time today
    p_ttoday = time_sub.add_parser("today", parents=[common], help="Display today's tracked hours by project and category")
    p_ttoday.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_ttoday.set_defaults(handler=cmd_time_today)

    # time week
    p_tweek = time_sub.add_parser("week", parents=[common], help="Display 7-day time rollup")
    p_tweek.add_argument("--end", dest="end", type=str, default=None, help="End date for 7-day rollup (YYYY-MM-DD)")
    p_tweek.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_tweek.set_defaults(handler=cmd_time_week)

    # time project <name>
    p_tproj = time_sub.add_parser("project", parents=[common], help="Display detailed time history for a specific project")
    p_tproj.add_argument("name", type=str, help="Project name (e.g. CyberpunkCity)")
    p_tproj.add_argument("--from", dest="from_date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    p_tproj.add_argument("--to", dest="to_date", type=str, default=None, help="End date (YYYY-MM-DD)")
    p_tproj.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_tproj.set_defaults(handler=cmd_time_project)

    # time report
    p_treport = time_sub.add_parser("report", parents=[common], help="Generate comprehensive time report")
    p_treport.add_argument("--from", dest="from_date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    p_treport.add_argument("--to", dest="to_date", type=str, default=None, help="End date (YYYY-MM-DD)")
    p_treport.add_argument("--project", type=str, default=None, help="Filter by project")
    p_treport.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_treport.set_defaults(handler=cmd_time_report)

    # quick command
    p_quick = subparsers.add_parser("quick", parents=[common], help="Launch floating quick-input bar")
    p_quick.set_defaults(handler=cmd_quick)

    # briefing command
    p_briefing = subparsers.add_parser("briefing", parents=[common], help="Generate and display daily or morning briefing")
    p_briefing.add_argument("--evening", action="store_true", help="Generate evening wrap-up instead of morning briefing")
    p_briefing.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_briefing.add_argument("--language", choices=["it", "en"], default="it", help="Language for briefing")
    p_briefing.add_argument("--deterministic", action="store_true", help="Force deterministic template without LLM")
    p_briefing.set_defaults(handler=cmd_briefing)

    # sync command
    p_sync = subparsers.add_parser("sync", parents=[common], help="Synchronize workspace tasks and time logs via Git")
    p_sync.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_sync.add_argument("--force", action="store_true", help="Force push if remote is out of sync")
    p_sync.add_argument("--status", action="store_true", help="Inspect sync and repository status without executing pull/push")
    p_sync.set_defaults(handler=cmd_sync)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI main entry point."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    if not args.command:
        parser.print_help()
        return 0

    root_dir = Path(args.workspace).resolve() if args.workspace else None
    config = Config.load(root_dir=root_dir)
    if args.port:
        config.port = args.port

    handler = getattr(args, "handler", None)
    if handler:
        return handler(args, config)

    # If subparser action missing (e.g. `neko task`), print help
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
