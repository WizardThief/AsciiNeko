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
    ]
    mem = status.get("memory")
    if isinstance(mem, dict):
        tf = mem.get("total_facts", 0)
        ap = mem.get("active_projects", 0)
        re_count = mem.get("recent_episodes", 0)
        lines.append(f"  Memory:           {tf} facts, {ap} active projects, {re_count} episodes")
    skills = status.get("skills")
    if isinstance(skills, dict):
        tot = skills.get("total_skills", 0)
        act = len(skills.get("active_skills", []))
        last_s = skills.get("last_executed_skill") or "None"
        lines.append(f"  Skills:           {tot} installed ({act} active), last: {last_s}")
    cloud = status.get("cloud_sync")
    if isinstance(cloud, dict):
        cs = str(cloud.get("status", "unknown")).upper()
        conn = "CONNECTED" if cloud.get("connected") else "DISCONNECTED"
        pending = cloud.get("pending_mutations", cloud.get("pending", 0))
        lines.append(f"  Cloud Sync:       {cs} ({conn}), {pending} pending mutations")
    lines.append("=" * 60)
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

    # In-process skill matching and autonomous synthesis hook
    try:
        from neko_bot.skills.registry import SkillRegistry
        from neko_bot.skills.sandbox import SkillSandbox
        from neko_bot.skills.executor import SkillExecutor
        from neko_bot.skills.synthesizer import SkillSynthesizer
        from neko_bot.memory.store import MemoryStore

        sandbox = SkillSandbox()
        registry = SkillRegistry(config.workspace_dir, sandbox, config=config)
        mstore = MemoryStore(config.workspace_dir, config=config)

        # Match existing skill
        matched = registry.match_skill(prompt)
        if matched:
            executor = SkillExecutor(registry, sandbox, memory_store=mstore, config=config)
            res = executor.execute(
                matched["id"],
                inputs={"text": prompt, "prompt": prompt},
                session_id="sess_cli_do",
                log_episode=True,
                event_type="skill_reused",
            )
            res_data = res.get("result", {})
            print(f"[Skill: {matched.get('name', matched['id'])}] {res_data}")
            return 0

        # Autonomous discovery and synthesis for novel challenge
        synthesizer = SkillSynthesizer(sandbox, registry, config)
        ok, new_skill, diag = synthesizer.synthesize_and_register(prompt)
        if ok and new_skill:
            executor = SkillExecutor(registry, sandbox, memory_store=mstore, config=config)
            res = executor.execute(
                new_skill["id"],
                inputs={"text": prompt, "prompt": prompt},
                session_id="sess_cli_do",
                log_episode=True,
                event_type="skill_discovered_and_executed",
            )
            res_data = res.get("result", {})
            print(f"[Auto-Skill Created: {new_skill.get('name', new_skill['id'])}] {res_data}")
            return 0
    except Exception as e:
        logger.debug("Skill discovery hook in cmd_do failed: %s", e)

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


def cmd_memory_add_fact(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory add fact "<content>" [--category <cat>] [--tags <t1,t2>] [--importance <1-5>]`."""
    from neko_bot.memory.store import MemoryStore

    content = args.content.strip()
    category = getattr(args, "category", "fact") or "fact"
    raw_tags = getattr(args, "tags", None)
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
    importance = getattr(args, "importance", 3) or 3

    # Attempt to send via daemon IPC if running
    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({
            "action": "memory_add_fact",
            "content": content,
            "category": category,
            "tags": tags,
            "importance": importance,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            fact = data.get("fact", {})
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        fact = store.add_fact(content=content, category=category, tags=tags, importance=importance)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(fact, indent=2, ensure_ascii=False) + "\n")
        return 0

    cat_upper = fact.get("category", "fact").upper()
    fid = fact.get("id", "")
    print(f"Fact added: [{cat_upper}] {fact.get('content')} (ID: {fid})")
    return 0


def cmd_memory_add_project(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory add project "<name>" [--milestone <m>] [--due <YYYY-MM-DD>] [--risk <low|med|high|crit>]`."""
    from neko_bot.memory.store import MemoryStore

    project_name = args.name.strip()
    milestone = getattr(args, "milestone", "General") or "General"
    deadline_utc = getattr(args, "due", None)
    risk_level = getattr(args, "risk", "medium") or "medium"
    raw_deps = getattr(args, "deps", None)
    dependencies = [d.strip() for d in raw_deps.split(",") if d.strip()] if raw_deps else []
    notes = getattr(args, "notes", "") or ""

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({
            "action": "memory_add_project",
            "project_name": project_name,
            "milestone": milestone,
            "deadline_utc": deadline_utc,
            "risk_level": risk_level,
            "dependencies": dependencies,
            "notes": notes,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            proj = data.get("project", {})
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        proj = store.add_project(
            project_name=project_name,
            milestone=milestone,
            deadline_utc=deadline_utc,
            risk_level=risk_level,
            dependencies=dependencies,
            notes=notes,
        )

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(proj, indent=2, ensure_ascii=False) + "\n")
        return 0

    pname = proj.get("project_name")
    ms = proj.get("milestone")
    due = proj.get("deadline_utc") or "None"
    risk = proj.get("risk_level", "medium").upper()
    pid = proj.get("id", "")
    print(f"Project deadline added: {pname} [{ms}] | Due: {due} | Risk: {risk} (ID: {pid})")
    return 0


def cmd_memory_search(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory search "<query>" [--level <lvl>] [--limit <N>] [--json]`."""
    from neko_bot.memory.store import MemoryStore

    query = args.query.strip()
    level = getattr(args, "level", "all") or "all"
    limit = getattr(args, "limit", 5) or 5

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({
            "action": "memory_search",
            "query": query,
            "level": level,
            "limit": limit,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        results = store.search(query=query, level=level, limit=limit)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
        return 0

    if not results:
        print(f"No memories found matching '{query}'.")
        return 0

    print(f"\nSearch results for '{query}' ({len(results)} matches):")
    print("-" * 65)
    for idx, item in enumerate(results, 1):
        score = item.get("score", 0.0)
        lvl = str(item.get("level", "")).upper()
        entry = item.get("entry", {})
        eid = entry.get("id", "")
        if lvl == "SEMANTIC":
            cat = entry.get("category", "fact")
            content = entry.get("content", "")
            print(f"  {idx}. [{lvl}][{cat}] (Score: {score:.2f}) {content} (ID: {eid})")
        elif lvl == "PROJECT":
            pname = entry.get("project_name", "")
            ms = entry.get("milestone", "")
            due = entry.get("deadline_utc", "None")
            print(f"  {idx}. [{lvl}] (Score: {score:.2f}) {pname} [{ms}] | Due: {due} (ID: {eid})")
        elif lvl == "EPISODIC":
            summary = entry.get("summary", "")
            etype = entry.get("event_type", "event")
            print(f"  {idx}. [{lvl}][{etype}] (Score: {score:.2f}) {summary} (ID: {eid})")
        else:
            print(f"  {idx}. [{lvl}] (Score: {score:.2f}) {entry} (ID: {eid})")
    print("-" * 65 + "\n")
    return 0


def cmd_memory_list(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory list [--level <lvl>] [--project <p>] [--json]`."""
    from neko_bot.memory.store import MemoryStore

    level = getattr(args, "level", "semantic") or "semantic"
    project = getattr(args, "project", None)
    include_deleted = getattr(args, "include_deleted", False)

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({
            "action": "memory_list",
            "level": level,
            "project": project,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            entries = data.get("entries", [])
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        entries = store.list_entries(level=level, project=project, include_deleted=include_deleted)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
        return 0

    if not entries:
        print(f"No {level} memory entries found.")
        return 0

    print(f"\nMemory entries [{level}] ({len(entries)} items):")
    print("-" * 65)
    for entry in entries:
        eid = entry.get("id", "")
        if "content" in entry:
            cat = entry.get("category", "fact").upper()
            content = entry.get("content", "")
            tags = entry.get("tags", [])
            tag_str = f" [#{', #'.join(tags)}]" if tags else ""
            print(f"  [{eid}] [{cat}] {content}{tag_str}")
        elif "project_name" in entry:
            pname = entry.get("project_name")
            ms = entry.get("milestone")
            due = entry.get("deadline_utc") or "None"
            risk = entry.get("risk_level", "medium").upper()
            status = entry.get("status", "active")
            print(f"  [{eid}] {pname} [{ms}] | Due: {due} | Risk: {risk} | Status: {status}")
        elif "session_id" in entry:
            summary = entry.get("summary")
            etype = entry.get("event_type", "event")
            print(f"  [{eid}] ({etype}) {summary}")
        else:
            print(f"  [{eid}] {entry}")
    print("-" * 65 + "\n")
    return 0


def cmd_memory_get(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory get <id> [--json]`."""
    from neko_bot.memory.store import MemoryStore

    entry_id = args.id.strip()
    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "memory_get", "entry_id": entry_id}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            entry = data.get("entry")
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        entry = store.get_entry(entry_id)

    if not entry:
        sys.stderr.write(f"Error: Memory entry '{entry_id}' not found.\n")
        return 1

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        return 0

    print("\n" + "=" * 50)
    print(f"MEMORY ENTRY: {entry.get('id')}")
    print("=" * 50)
    for k, v in entry.items():
        print(f"  {k:20s}: {v}")
    print("=" * 50 + "\n")
    return 0


def cmd_memory_delete(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory delete <id> [--hard] [--json]`."""
    from neko_bot.memory.store import MemoryStore

    entry_id = args.id.strip()
    hard = getattr(args, "hard", False)

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "memory_delete", "entry_id": entry_id, "hard": hard}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            deleted = bool(data.get("deleted", False))
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        deleted = store.delete_entry(entry_id, hard=hard)

    if not deleted:
        sys.stderr.write(f"Error: Memory entry '{entry_id}' not found.\n")
        return 1

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({"success": True, "id": entry_id, "hard": hard}, indent=2) + "\n")
        return 0

    del_type = "purged permanently" if hard else "tombstoned"
    print(f"Memory entry '{entry_id}' successfully deleted ({del_type}).")
    return 0


def cmd_memory_stats(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko memory stats [--json]`."""
    from neko_bot.memory.store import MemoryStore

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "memory_stats"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            stats = data.get("stats", {})
    except Exception:
        store = MemoryStore(config.workspace_dir, config=config)
        stats = store.get_stats()

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(stats, indent=2, ensure_ascii=False) + "\n")
        return 0

    lines = [
        "=" * 60,
        "                    NEKO MEMORY STATS                       ",
        "=" * 60,
        f"  Total Facts:        {stats.get('total_facts', 0)}",
        f"  Active Projects:    {stats.get('active_projects', 0)}",
        f"  Recent Episodes:    {stats.get('recent_episodes', 0)}",
    ]
    deadlines = stats.get("upcoming_deadlines", [])
    if deadlines:
        lines.append("  Upcoming Deadlines:")
        for dl in deadlines:
            pname = dl.get("project", "")
            ms = dl.get("milestone", "")
            due = dl.get("due", "")
            risk = dl.get("risk", "medium")
            overdue = " [OVERDUE]" if dl.get("overdue") else ""
            lines.append(f"    - {pname} [{ms}] Due: {due} (Risk: {risk}){overdue}")
    else:
        lines.append("  Upcoming Deadlines: None")
    lines.append("=" * 60)
    print("\n".join(lines))
    return 0


def cmd_skill_list(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko skill list [--json] [--all]`."""
    from neko_bot.skills.registry import SkillRegistry
    from neko_bot.skills.sandbox import SkillSandbox

    include_deleted = getattr(args, "include_deleted", False)
    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "skill_list", "include_deleted": include_deleted}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            skills = data.get("skills", [])
    except Exception:
        sbox = SkillSandbox()
        reg = SkillRegistry(config.workspace_dir, sbox, config=config)
        skills = reg.list_skills(include_deleted=include_deleted)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(skills, indent=2, ensure_ascii=False) + "\n")
        return 0

    if not skills:
        print("No skills registered.")
        return 0

    print("=" * 80)
    print(f"{'ID':<25} {'NAME':<20} {'TYPE':<10} {'STATUS':<10} {'RUNS':<6}")
    print("-" * 80)
    for s in skills:
        sid = s.get("id", "")
        name = s.get("name", "")[:19]
        stype = s.get("type", "tool")
        status = s.get("status", "verified")
        runs = str(s.get("execution_count", 0))
        del_marker = " (DEL)" if s.get("deleted") else ""
        print(f"{sid:<25} {name:<20} {stype:<10} {status + del_marker:<10} {runs:<6}")
    print("=" * 80)
    return 0


def cmd_skill_show(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko skill show <skill_id> [--json]`."""
    from neko_bot.skills.registry import SkillRegistry
    from neko_bot.skills.sandbox import SkillSandbox

    sid = args.skill_id.strip()
    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "skill_get", "skill_id": sid}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            skill = data.get("skill")
    except Exception:
        sbox = SkillSandbox()
        reg = SkillRegistry(config.workspace_dir, sbox, config=config)
        skill = reg.get_skill(sid)

    if not skill:
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps({"success": False, "error": f"Skill '{sid}' not found"}, indent=2) + "\n")
        else:
            sys.stderr.write(f"Error: Skill '{sid}' not found.\n")
        return 1

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(skill, indent=2, ensure_ascii=False) + "\n")
        return 0

    print("=" * 70)
    print(f"SKILL: {skill.get('name', sid)} (ID: {sid})")
    print("=" * 70)
    print(f"  Type:            {skill.get('type')}")
    print(f"  Version:         {skill.get('version', '1.0.0')}")
    print(f"  Status:          {skill.get('status', 'verified')}")
    print(f"  Description:     {skill.get('description', '')}")
    print(f"  Triggers:        {', '.join(skill.get('trigger_patterns', []))}")
    print(f"  Execution Count: {skill.get('execution_count', 0)}")
    print(f"  Last Run:        {skill.get('last_executed_at', 'Never')}")
    if skill.get("type") == "workflow":
        print("\n  Steps:")
        for idx, st in enumerate(skill.get("steps", [])):
            print(f"    {idx + 1}. {st.get('action')}: {st}")
    else:
        print("\n  Python Code:")
        code = skill.get("code") or skill.get("code_str") or ""
        for line in code.splitlines():
            print(f"    {line}")
    print("=" * 70)
    return 0


def cmd_skill_run(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko skill run <skill_id> [--input JSON] [--json]`."""
    from neko_bot.skills.registry import SkillRegistry
    from neko_bot.skills.sandbox import SkillSandbox
    from neko_bot.skills.executor import SkillExecutor
    from neko_bot.memory.store import MemoryStore

    sid = args.skill_id.strip()
    try:
        inputs = json.loads(args.input) if getattr(args, "input", None) else {}
    except Exception as e:
        sys.stderr.write(f"Error parsing --input JSON: {e}\n")
        return 1

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "skill_run", "skill_id": sid, "inputs": inputs}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except Exception:
        sbox = SkillSandbox()
        reg = SkillRegistry(config.workspace_dir, sbox, config=config)
        mstore = MemoryStore(config.workspace_dir, config=config)
        executor = SkillExecutor(reg, sbox, memory_store=mstore, config=config)
        res = executor.execute(sid, inputs=inputs, session_id="cli_skill_run")

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res.get("success") else 1

    if res.get("success"):
        print(f"Skill '{sid}' executed successfully:")
        print(json.dumps(res.get("result", {}), indent=2, ensure_ascii=False))
        return 0
    else:
        sys.stderr.write(f"Skill execution failed: {res.get('error')}\n")
        return 1


def cmd_skill_test(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko skill test <skill_id> [--json]`."""
    from neko_bot.skills.registry import SkillRegistry
    from neko_bot.skills.sandbox import SkillSandbox

    sid = args.skill_id.strip()
    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "skill_test", "skill_id": sid}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ok = data.get("success", False)
            msg = data.get("message", "")
    except Exception:
        sbox = SkillSandbox()
        reg = SkillRegistry(config.workspace_dir, sbox, config=config)
        skill = reg.get_skill(sid)
        if not skill:
            ok, msg = False, f"Skill '{sid}' not found"
        else:
            ok, msg = sbox.verify_skill(skill)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({"success": ok, "message": msg, "skill_id": sid}, indent=2) + "\n")
        return 0 if ok else 1

    if ok:
        print(f"[PASS] Skill '{sid}' verification and self-tests passed.")
        return 0
    else:
        sys.stderr.write(f"[FAIL] Skill '{sid}' verification failed: {msg}\n")
        return 1


def cmd_skill_delete(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko skill delete <skill_id> [--hard] [--json]`."""
    from neko_bot.skills.registry import SkillRegistry
    from neko_bot.skills.sandbox import SkillSandbox

    sid = args.skill_id.strip()
    hard = getattr(args, "hard", False)

    try:
        url = f"http://{config.host}:{config.port}/command"
        payload = json.dumps({"action": "skill_delete", "skill_id": sid, "hard": hard}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            deleted = bool(data.get("deleted", False))
    except Exception:
        sbox = SkillSandbox()
        reg = SkillRegistry(config.workspace_dir, sbox, config=config)
        deleted = reg.delete_skill(sid, hard=hard)

    if not deleted:
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps({"success": False, "error": f"Skill '{sid}' not found"}, indent=2) + "\n")
        else:
            sys.stderr.write(f"Error: Skill '{sid}' not found.\n")
        return 1

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({"success": True, "skill_id": sid, "hard": hard}, indent=2) + "\n")
        return 0

    mode = "permanently purged" if hard else "tombstoned"
    print(f"Skill '{sid}' successfully {mode}.")
    return 0


# =========================================================================
# Cloud Subsystem Handlers (Milestone 4)
# =========================================================================

def cmd_cloud_status(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko cloud status [--json]`."""
    controller = DaemonController(config)
    payload = controller.get_status_payload()
    cloud_data = payload.get("cloud_sync")

    if not cloud_data or not cloud_data.get("cloud_server_url"):
        cloud_url = getattr(config, "cloud_url", "http://127.0.0.1:8767")
        q_file = getattr(config, "cloud_mutations_file", config.neko_dir / "cloud_mutations.json")
        pending = 0
        if q_file.exists():
            try:
                with open(q_file, "r", encoding="utf-8") as f:
                    q = json.load(f)
                    if isinstance(q, list):
                        pending = len(q)
            except Exception:
                pass

        # Ping health
        connected = False
        try:
            req = urllib.request.Request(f"{cloud_url}/api/v1/health")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                connected = (resp.status == 200)
        except Exception:
            connected = False

        cloud_data = {
            "status": "synced" if (connected and pending == 0) else ("pending" if connected else "offline"),
            "connected": connected,
            "last_sync_time": None,
            "last_sync": None,
            "pending_mutations": pending,
            "pending": pending,
            "cloud_server_url": cloud_url,
            "server": cloud_url,
            "device_id": getattr(config, "cloud_device_id", None) or "unregistered",
            "device_name": getattr(config, "cloud_device_name", None) or "unregistered",
        }

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(cloud_data, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0

    conn_str = "ONLINE (Connected)" if cloud_data.get("connected") else "OFFLINE (Unreachable)"
    cs_str = str(cloud_data.get("status", "unknown")).upper()
    url = cloud_data.get("cloud_server_url") or cloud_data.get("server", "N/A")
    dev_id = cloud_data.get("device_id") or getattr(config, "cloud_device_id", "N/A")
    dev_name = cloud_data.get("device_name") or getattr(config, "cloud_device_name", "N/A")
    pending = cloud_data.get("pending_mutations", cloud_data.get("pending", 0))
    last_sync = cloud_data.get("last_sync_time") or cloud_data.get("last_sync") or "Never"

    lines = [
        "=" * 60,
        "                    NEKO CLOUD STATUS                       ",
        "=" * 60,
        f"  Connection:       {conn_str}",
        f"  Server URL:       {url}",
        f"  Device ID:        {dev_id} ({dev_name})",
        f"  Sync Status:      {cs_str}",
        f"  Pending Queue:    {pending} mutations",
        f"  Last Sync:        {last_sync}",
        "=" * 60,
    ]
    print("\n".join(lines))
    return 0


def cmd_cloud_register(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko cloud register [--url URL] [--device-name NAME] [--device-id ID] [--secret SECRET]`."""
    import platform
    import uuid

    url = args.url if getattr(args, "url", None) else getattr(config, "cloud_url", "http://127.0.0.1:8767")
    dev_name = getattr(args, "device_name", None) or getattr(args, "name", None) or getattr(config, "cloud_device_name", None) or f"{platform.node()} Workstation"
    dev_id = getattr(args, "device_id", None) or getattr(config, "cloud_device_id", None) or f"ws-{platform.node().lower()}-{uuid.uuid4().hex[:6]}"
    secret = getattr(args, "secret", None) or ""

    reg_payload = {
        "device_id": dev_id,
        "device_name": dev_name,
        "client_version": "2.0.0",
        "registration_secret": secret,
        "token": getattr(args, "token", None) or f"tok_{uuid.uuid4().hex}",
    }

    try:
        req = urllib.request.Request(
            f"{url}/api/v1/auth/register",
            data=json.dumps(reg_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("auth_token") or data.get("token") or reg_payload["token"]

            config.cloud_url = url
            config.cloud_device_id = dev_id
            config.cloud_device_name = dev_name
            config.cloud_auth_token = token
            config.save()

            if getattr(args, "json", False):
                sys.stdout.write(json.dumps({
                    "success": True,
                    "device_id": dev_id,
                    "device_name": dev_name,
                    "cloud_server_url": url,
                    "status": "registered",
                }, indent=2) + "\n")
                sys.stdout.flush()
            else:
                print(f"Dispositivo '{dev_name}' registrato con successo!")
                print(f"ID Dispositivo : {dev_id}")
                print(f"Server URL     : {url}")
                print("Token generato salvato localmente in .neko/config.json")
            return 0

    except Exception as e:
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps({"success": False, "error": str(e)}, indent=2) + "\n")
            sys.stdout.flush()
        else:
            print(f"Errore durante la registrazione al Cloud Server ({url}): {e}")
        return 1


def cmd_cloud_sync(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko cloud sync [--json]`."""
    controller = DaemonController(config)
    running, _ = controller.is_running()

    # Step 1: Attempt IPC if daemon is active
    if running:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{config.port}/command",
                data=json.dumps({"action": "cloud_sync"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                res = data.get("result", {})
                if getattr(args, "json", False):
                    sys.stdout.write(json.dumps(res, indent=2) + "\n")
                    sys.stdout.flush()
                else:
                    if res.get("status") == "synced":
                        print("Connessione al Cloud stabilita.")
                        pulled = res.get("pulled_count", 0)
                        seq = res.get("server_sequence", 0)
                        print(f"Sincronizzate {pulled} modifiche remote. Server Sequence attuale: {seq}. Stato: SINCRONIZZATO.")
                    else:
                        print(f"Sincronizzazione completata con stato: {res.get('status', 'unknown')}")
                return 0 if res.get("status") == "synced" else 1
        except Exception:
            pass

    # Step 2: In-process fallback
    try:
        from neko_bot.cloud.sync_manager import CloudSyncManager
        from neko_bot.task.store import TaskStore
        from neko_bot.memory.store import MemoryStore
        from neko_bot.skills.sandbox import SkillSandbox
        from neko_bot.skills.registry import SkillRegistry

        t_store = TaskStore(config.tasks_file, config.tasks_md_file, config=config)
        m_store = MemoryStore(config.workspace_dir, config=config)
        s_sandbox = SkillSandbox()
        s_registry = SkillRegistry(config.workspace_dir, s_sandbox, config=config)

        sync_mgr = CloudSyncManager(
            config.workspace_dir,
            server_url=getattr(config, "cloud_url", "http://127.0.0.1:8767"),
            device_id=getattr(config, "cloud_device_id", None) or "local_device",
            token=getattr(config, "cloud_auth_token", None) or "",
            task_store=t_store,
            memory_store=m_store,
            skill_registry=s_registry,
            config=config,
        )
        res = sync_mgr.reconcile()
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps(res, indent=2) + "\n")
            sys.stdout.flush()
        else:
            if res.get("status") == "synced":
                print("Connessione al Cloud stabilita.")
                pulled = res.get("pulled_count", 0)
                seq = res.get("server_sequence", 0)
                print(f"Sincronizzate {pulled} modifiche remote. Server Sequence attuale: {seq}. Stato: SINCRONIZZATO.")
            else:
                err = res.get("error", "Server irraggiungibile")
                pending = res.get("pending_mutations", res.get("pending", 0))
                print(f"Errore connessione Cloud: {err}. Mutazioni salvate in coda locale ({pending} in sospeso).")
        return 0 if res.get("status") == "synced" else 1
    except Exception as e:
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps({"success": False, "status": "error", "error": str(e)}, indent=2) + "\n")
            sys.stdout.flush()
        else:
            print(f"Errore durante l'esecuzione del sync: {e}")
        return 1


def cmd_cloud_config(args: argparse.Namespace, config: Config) -> int:
    """Handles `neko cloud config [--url URL] [--device-name NAME] [--device-id ID] [--json]`."""
    updated = False
    if getattr(args, "url", None):
        config.cloud_url = args.url
        updated = True
    if getattr(args, "device_name", None) or getattr(args, "name", None):
        config.cloud_device_name = getattr(args, "device_name", None) or getattr(args, "name", None)
        updated = True
    if getattr(args, "device_id", None):
        config.cloud_device_id = args.device_id
        updated = True

    if updated:
        config.save()

    cfg_data = {
        "cloud_server_url": config.cloud_url,
        "device_id": config.cloud_device_id,
        "device_name": config.cloud_device_name,
        "has_auth_token": bool(config.cloud_auth_token),
        "cloud_sync_interval": config.cloud_sync_interval,
    }

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(cfg_data, indent=2) + "\n")
        sys.stdout.flush()
        return 0

    if updated:
        print("Configurazione Cloud aggiornata con successo:")
    else:
        print("Configurazione Cloud attuale:")
    print(f"  Server URL     : {config.cloud_url}")
    print(f"  Device ID      : {config.cloud_device_id or 'Non impostato'}")
    print(f"  Device Name    : {config.cloud_device_name or 'Non impostato'}")
    token_status = "Configurato" if config.cloud_auth_token else "Non configurato"
    print(f"  Auth Token     : {token_status}")
    print(f"  Sync Interval  : {config.cloud_sync_interval}s")
    print("Salvato in .neko/config.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Builds top-level CLI argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=str, default=argparse.SUPPRESS, help="Path to workspace root")
    common.add_argument("--port", type=int, default=argparse.SUPPRESS, help=f"Daemon IPC port (default: {DEFAULT_PORT})")

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

    # memory command
    p_mem = subparsers.add_parser("memory", parents=[common], help="Multi-level persistent memory management")
    mem_sub = p_mem.add_subparsers(dest="memory_action", metavar="<action>")

    # memory add
    p_madd = mem_sub.add_parser("add", parents=[common], help="Add memory entry")
    madd_sub = p_madd.add_subparsers(dest="add_type", metavar="<type>")

    # memory add fact <content>
    p_madd_fact = madd_sub.add_parser("fact", parents=[common], help="Add semantic fact")
    p_madd_fact.add_argument("content", type=str, help="Fact content")
    p_madd_fact.add_argument("--category", type=str, default="fact", help="Category (preference, client_rule, work_pattern, tech_spec, fact)")
    p_madd_fact.add_argument("--tags", type=str, default=None, help="Comma-separated tags")
    p_madd_fact.add_argument("--importance", type=int, default=3, choices=[1, 2, 3, 4, 5], help="Importance level (1-5)")
    p_madd_fact.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_madd_fact.set_defaults(handler=cmd_memory_add_fact)

    # memory add project <name>
    p_madd_proj = madd_sub.add_parser("project", parents=[common], help="Add project deadline")
    p_madd_proj.add_argument("name", type=str, help="Project name")
    p_madd_proj.add_argument("--milestone", type=str, default="General", help="Milestone name")
    p_madd_proj.add_argument("--due", type=str, default=None, help="Deadline date (YYYY-MM-DD or ISO-8601 UTC)")
    p_madd_proj.add_argument("--risk", type=str, default="medium", choices=["low", "medium", "high", "critical"], help="Risk level")
    p_madd_proj.add_argument("--deps", type=str, default=None, help="Comma-separated dependencies")
    p_madd_proj.add_argument("--notes", type=str, default="", help="Project notes")
    p_madd_proj.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_madd_proj.set_defaults(handler=cmd_memory_add_project)

    # memory search <query>
    p_msearch = mem_sub.add_parser("search", parents=[common], help="Search memory using BM25 scoring")
    p_msearch.add_argument("query", type=str, help="Search query string")
    p_msearch.add_argument("--level", type=str, default="all", choices=["semantic", "project", "episodic", "all"], help="Memory level to search")
    p_msearch.add_argument("--limit", type=int, default=5, help="Maximum number of results to return")
    p_msearch.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_msearch.set_defaults(handler=cmd_memory_search)

    # memory list
    p_mlist = mem_sub.add_parser("list", parents=[common], help="List memory entries")
    p_mlist.add_argument("--level", type=str, default="semantic", choices=["semantic", "project", "episodic", "all"], help="Memory level to list")
    p_mlist.add_argument("--project", type=str, default=None, help="Filter projects by project name")
    p_mlist.add_argument("--all", dest="include_deleted", action="store_true", help="Include deleted tombstones")
    p_mlist.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_mlist.set_defaults(handler=cmd_memory_list)

    # memory get <id>
    p_mget = mem_sub.add_parser("get", parents=[common], help="Get memory entry by ID or prefix")
    p_mget.add_argument("id", type=str, help="Memory entry ID or prefix")
    p_mget.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_mget.set_defaults(handler=cmd_memory_get)

    # memory delete <id>
    p_mdel = mem_sub.add_parser("delete", parents=[common], help="Delete memory entry")
    p_mdel.add_argument("id", type=str, help="Memory entry ID or prefix to delete")
    p_mdel.add_argument("--hard", action="store_true", help="Hard delete (completely purge record)")
    p_mdel.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_mdel.set_defaults(handler=cmd_memory_delete)

    # memory stats
    p_mstats = mem_sub.add_parser("stats", parents=[common], help="Display memory subsystem statistics")
    p_mstats.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_mstats.set_defaults(handler=cmd_memory_stats)

    # skill command
    p_skill = subparsers.add_parser("skill", parents=[common], help="Autonomous hybrid skill engine management")
    skill_sub = p_skill.add_subparsers(dest="skill_action", metavar="<action>")

    # skill list
    p_slist = skill_sub.add_parser("list", parents=[common], help="List installed skills")
    p_slist.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_slist.add_argument("--all", dest="include_deleted", action="store_true", help="Include deleted tombstones")
    p_slist.set_defaults(handler=cmd_skill_list)

    # skill show <id>
    p_sshow = skill_sub.add_parser("show", parents=[common], help="Display skill manifest, steps, or code")
    p_sshow.add_argument("skill_id", type=str, help="Skill identifier")
    p_sshow.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_sshow.set_defaults(handler=cmd_skill_show)

    # skill run <id> [--input JSON]
    p_srun = skill_sub.add_parser("run", parents=[common], help="Execute a skill directly")
    p_srun.add_argument("skill_id", type=str, help="Skill identifier to run")
    p_srun.add_argument("--input", type=str, default="{}", help="Input parameters as JSON string")
    p_srun.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_srun.set_defaults(handler=cmd_skill_run)

    # skill test <id>
    p_stest = skill_sub.add_parser("test", parents=[common], help="Execute self-tests on an installed skill")
    p_stest.add_argument("skill_id", type=str, help="Skill identifier to test")
    p_stest.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_stest.set_defaults(handler=cmd_skill_test)

    # skill delete <id> [--hard]
    p_sdel = skill_sub.add_parser("delete", parents=[common], help="Delete a skill (tombstone or hard purge)")
    p_sdel.add_argument("skill_id", type=str, help="Skill identifier to delete")
    p_sdel.add_argument("--hard", action="store_true", help="Hard delete (completely purge)")
    p_sdel.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_sdel.set_defaults(handler=cmd_skill_delete)

    # cloud command (Milestone 4)
    p_cloud = subparsers.add_parser("cloud", parents=[common], help="Manage cloud server pairing, status, and synchronization")
    cloud_sub = p_cloud.add_subparsers(dest="cloud_action", metavar="<action>")

    # cloud status
    p_cstatus = cloud_sub.add_parser("status", parents=[common], help="Display cloud server connection state, last sync, and pending mutations")
    p_cstatus.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_cstatus.set_defaults(handler=cmd_cloud_status)

    # cloud register
    p_creg = cloud_sub.add_parser("register", parents=[common], help="Register and pair device with cloud backend")
    p_creg.add_argument("--url", type=str, default=None, help="Cloud server URL (e.g. http://127.0.0.1:8767)")
    p_creg.add_argument("--device-name", "--name", dest="device_name", type=str, default=None, help="Descriptive name for this device")
    p_creg.add_argument("--device-id", type=str, default=None, help="Explicit device ID (auto-generated if omitted)")
    p_creg.add_argument("--secret", type=str, default=None, help="Optional registration cluster secret")
    p_creg.add_argument("--token", type=str, default=None, help="Optional explicit auth token")
    p_creg.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_creg.set_defaults(handler=cmd_cloud_register)

    # cloud sync
    p_csync = cloud_sub.add_parser("sync", parents=[common], help="Trigger immediate bidirectional cloud reconciliation")
    p_csync.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_csync.set_defaults(handler=cmd_cloud_sync)

    # cloud config
    p_ccfg = cloud_sub.add_parser("config", parents=[common], help="Inspect or configure cloud server connection settings")
    p_ccfg.add_argument("--url", type=str, default=None, help="Set cloud server URL")
    p_ccfg.add_argument("--device-name", "--name", dest="device_name", type=str, default=None, help="Set device name")
    p_ccfg.add_argument("--device-id", type=str, default=None, help="Set device ID")
    p_ccfg.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_ccfg.set_defaults(handler=cmd_cloud_config)

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

    ws_arg = getattr(args, "workspace", None)
    root_dir = Path(ws_arg).resolve() if ws_arg else None
    config = Config.load(root_dir=root_dir)
    port_arg = getattr(args, "port", None)
    if port_arg:
        config.port = port_arg

    handler = getattr(args, "handler", None)
    if handler:
        return handler(args, config)

    # If subparser action missing (e.g. `neko task`), print help
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
