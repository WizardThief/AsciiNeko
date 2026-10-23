"""
neko_bot.sync.merge_driver - Custom Git Merge Driver & CRDT LWW-Element-Set Reconciliation.

Implements:
1. neko-task-merge CLI interface for Git (%O %A %B %P).
2. Pure 3-way CRDT LWW-Element-Set reconciliation algorithm.
3. Automatic Markdown mirror regeneration (TASKS.md).
4. Git configuration helper for .git/config and .gitattributes.
5. In-process rebase conflict auto-resolver fallback.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("neko_bot.sync.merge_driver")


def parse_timestamp_safe(ts: Optional[str]) -> float:
    """Parses ISO-8601 string to epoch timestamp float safely."""
    if not ts:
        return 0.0
    try:
        clean = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return 0.0


def compare_lww_task_dicts(cand: Dict[str, Any], curr: Dict[str, Any]) -> bool:
    """
    Returns True if cand should win over curr according to CRDT LWW-Element-Set rules:
    1. Higher updated_at timestamp wins.
    2. On timestamp tie, tombstone (deleted=True) wins.
    3. On tie, deterministic canonical JSON string comparison.
    """
    t_cand = parse_timestamp_safe(cand.get("updated_at"))
    t_curr = parse_timestamp_safe(curr.get("updated_at"))

    if t_cand > t_curr:
        return True
    if t_cand < t_curr:
        return False

    cand_del = bool(cand.get("deleted", False))
    curr_del = bool(curr.get("deleted", False))
    if cand_del != curr_del:
        return cand_del  # Tombstone wins tie-break

    json_cand = json.dumps(cand, sort_keys=True, ensure_ascii=False)
    json_curr = json.dumps(curr, sort_keys=True, ensure_ascii=False)
    return json_cand > json_curr


def reconcile_3way(
    ancestor: Dict[str, Dict[str, Any]],
    ours: Dict[str, Dict[str, Any]],
    theirs: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Executes 3-way CRDT LWW-Element-Set reconciliation across ancestor, ours, and theirs dictionaries.
    Returns a unified, conflict-free dictionary of task_id -> task_dict.
    """
    all_keys: Set[str] = set(ancestor.keys()) | set(ours.keys()) | set(theirs.keys())
    merged: Dict[str, Dict[str, Any]] = {}

    for k in sorted(all_keys):
        in_anc = k in ancestor
        in_ours = k in ours
        in_theirs = k in theirs

        if in_ours and in_theirs:
            o_dict = ours[k]
            t_dict = theirs[k]

            if o_dict == t_dict:
                merged[k] = dict(o_dict)
            elif in_anc and t_dict == ancestor[k]:
                # Only ours modified it
                merged[k] = dict(o_dict)
            elif in_anc and o_dict == ancestor[k]:
                # Only theirs modified it
                merged[k] = dict(t_dict)
            else:
                # Concurrent update! Apply CRDT LWW tie-breaker
                winner = t_dict if compare_lww_task_dicts(t_dict, o_dict) else o_dict
                merged[k] = dict(winner)

        elif in_ours and not in_theirs:
            o_dict = ours[k]
            if not in_anc:
                # Newly added by ours
                merged[k] = dict(o_dict)
            else:
                # Deleted by theirs. If ours modified it concurrently, preserve ours; otherwise delete.
                if o_dict != ancestor[k]:
                    merged[k] = dict(o_dict)

        elif in_theirs and not in_ours:
            t_dict = theirs[k]
            if not in_anc:
                # Newly added by theirs
                merged[k] = dict(t_dict)
            else:
                # Deleted by ours. If theirs modified it concurrently, preserve theirs; otherwise delete.
                if t_dict != ancestor[k]:
                    merged[k] = dict(t_dict)

        # If key only in ancestor, both sides deleted it -> omitted from merged.

    return merged


def load_tasks_dict_safe(file_path: Optional[Union[Path, str]]) -> Dict[str, Dict[str, Any]]:
    """Safely reads a JSON file into a {task_id: task_dict} mapping, handling corruption and wrapping."""
    if file_path is None:
        return {}
    p = Path(file_path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)

        items: Any = {}
        if isinstance(data, dict):
            if "tasks" in data:
                inner = data["tasks"]
                if isinstance(inner, dict):
                    items = inner
                elif isinstance(inner, list):
                    items = {item["id"]: item for item in inner if isinstance(item, dict) and "id" in item}
            else:
                items = data
        elif isinstance(data, list):
            items = {item["id"]: item for item in data if isinstance(item, dict) and "id" in item}

        result: Dict[str, Dict[str, Any]] = {}
        for k, v in items.items():
            if isinstance(v, dict):
                tid = str(v.get("id") or k)
                result[tid] = v
        return result
    except Exception as e:
        logger.warning("Error reading JSON from %s (%s). Falling back to empty dict.", file_path, e)
        return {}


def regenerate_tasks_markdown(tasks_dict: Dict[str, Dict[str, Any]], output_path: Union[Path, str]) -> None:
    """Regenerates TASKS.md using TaskStore export format and writes atomically."""
    from neko_bot.task.models import Task
    from neko_bot.task.store import TaskStore, atomic_write_text_file

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    store = TaskStore(tasks_file_or_dir=out_p.parent / "tasks.json", auto_mirror=False)
    store._tasks = {tid: Task.from_dict(d) for tid, d in tasks_dict.items()}
    md_content = store.export_markdown()
    atomic_write_text_file(out_p, md_content)


def reconcile_files(
    ancestor_path: Union[Path, str],
    ours_path: Union[Path, str],
    theirs_path: Union[Path, str],
    target_relpath: Optional[str] = None,
) -> int:
    """
    Main driver logic for Git %O %A %B %P invocation:
    - Reconciles ancestor (%O), ours (%A), and theirs (%B).
    - Overwrites %A with clean JSON.
    - Updates TASKS.md mirror.
    - Exits with status 0.
    """
    ancestor_p = Path(ancestor_path)
    ours_p = Path(ours_path)
    theirs_p = Path(theirs_path)
    target_rel = str(target_relpath) if target_relpath else ""

    # Check if this invocation was triggered for TASKS.md directly
    is_markdown = (
        "tasks.md" in target_rel.lower()
        or target_rel.lower().endswith(".md")
        or "tasks.md" in ours_p.name.lower()
        or ours_p.name.lower().endswith(".md")
    )
    if not is_markdown and ours_p.exists():
        try:
            with open(ours_p, "r", encoding="utf-8", errors="ignore") as f:
                header = f.read(64)
                if header.strip().startswith("#"):
                    is_markdown = True
        except Exception:
            pass

    if is_markdown:
        # Locate tasks.json in repo or adjacent
        root = find_repo_root()
        tasks_dict: Dict[str, Any] = {}
        if root and (root / ".git" / "neko_merged_tasks.json").exists():
            tasks_dict = load_tasks_dict_safe(root / ".git" / "neko_merged_tasks.json")

        if not tasks_dict:
            candidate_paths = [
                root / "workspace" / "tasks.json" if root else None,
                root / "tasks.json" if root else None,
                ours_p.parent / "tasks.json",
                Path.cwd() / "workspace" / "tasks.json",
                Path.cwd() / "tasks.json",
            ]
            for cand in candidate_paths:
                if cand and cand.exists():
                    d = load_tasks_dict_safe(cand)
                    if d:
                        tasks_dict = d
                        break

        regenerate_tasks_markdown(tasks_dict, ours_p)
        return 0

    # Load 3-way inputs
    anc_dict = load_tasks_dict_safe(ancestor_p)
    ours_dict = load_tasks_dict_safe(ours_p)
    theirs_dict = load_tasks_dict_safe(theirs_p)

    # Reconcile CRDT state
    merged = reconcile_3way(anc_dict, ours_dict, theirs_dict)

    # Write merged JSON atomically into ours_path (%A)
    ours_p.parent.mkdir(parents=True, exist_ok=True)
    with open(ours_p, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")

    # Cache merged tasks in .git so subsequent markdown mirror merge in this step has access to the reconciled dictionary
    root = find_repo_root()
    if root and (root / ".git").is_dir():
        cache_file = root / ".git" / "neko_merged_tasks.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False)
        except Exception:
            pass

    return 0


# =========================================================================
# Git Registration Helper
# =========================================================================

def find_repo_root(start_dir: Optional[Union[Path, str]] = None) -> Optional[Path]:
    """Finds top-level Git repository directory containing .git."""
    start = Path(start_dir or Path.cwd()).resolve()
    curr = start
    while curr != curr.parent:
        if (curr / ".git").exists():
            return curr
        curr = curr.parent

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return None


def register_merge_driver(
    repo_dir: Optional[Union[Path, str]] = None,
    driver_name: str = "neko-task-merge",
    python_path: Optional[str] = None,
) -> bool:
    """
    Registers the neko-task-merge driver in .git/config and .gitattributes safely across platforms.
    """
    root = Path(repo_dir) if repo_dir else find_repo_root()
    if root is None or not (root / ".git").exists():
        logger.warning("Cannot register merge driver: %s is not a Git repository.", root)
        return False

    py_exe = Path(python_path or sys.executable).resolve().as_posix()
    driver_cmd = f'"{py_exe}" -m neko_bot.sync.merge_driver "%O" "%A" "%B" "%P"'

    # 1. Configure .git/config via git CLI
    try:
        subprocess.run(
            ["git", "config", f"merge.{driver_name}.name", "Neko Bot CRDT Task Merge Driver"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", f"merge.{driver_name}.driver", driver_cmd],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        logger.error("Failed to run git config: %s", e)
        return False

    # 2. Configure .gitattributes in repository root
    gitattributes_path = root / ".gitattributes"
    desired_lines = [
        f"workspace/tasks.json merge={driver_name}",
        f"workspace/TASKS.md merge={driver_name}",
        f"tasks.json merge={driver_name}",
        f"TASKS.md merge={driver_name}",
    ]

    existing_content = ""
    if gitattributes_path.exists():
        with open(gitattributes_path, "r", encoding="utf-8") as f:
            existing_content = f.read()

    lines_to_add = [line for line in desired_lines if line not in existing_content]
    if lines_to_add:
        prefix = "\n" if existing_content and not existing_content.endswith("\n") else ""
        new_block = prefix + "\n".join(lines_to_add) + "\n"
        with open(gitattributes_path, "a", encoding="utf-8") as f:
            f.write(new_block)

    return True


def is_merge_driver_registered(
    repo_dir: Optional[Union[Path, str]] = None,
    driver_name: str = "neko-task-merge",
) -> bool:
    """Checks whether merge.<driver_name>.driver is set and properly quoted in repository git config."""
    root = Path(repo_dir) if repo_dir else find_repo_root()
    if root is None or not (root / ".git").exists():
        return False
    try:
        proc = subprocess.run(
            ["git", "config", "--get", f"merge.{driver_name}.driver"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return False
        val = proc.stdout.strip()
        # Verify placeholder quoting so legacy unquoted drivers are re-registered
        if '"%O"' not in val or '"%A"' not in val:
            return False
        return True
    except Exception:
        return False


# =========================================================================
# In-Process Rebase Conflict Auto-Resolver Fallback
# =========================================================================

def detect_conflict_markers(content: str) -> bool:
    """Returns True if string contains Git conflict markers."""
    return "<<<<<<<" in content and ">>>>>>>" in content


def extract_conflict_versions_from_text(content: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parses a text containing Git conflict markers and reconstructs ours and theirs JSON structures.
    """
    lines = content.splitlines()
    ours_lines: List[str] = []
    theirs_lines: List[str] = []
    mode = "both"  # "both", "ours", "ancestor", "theirs"

    for line in lines:
        if line.startswith("<<<<<<<"):
            mode = "ours"
        elif line.startswith("|||||||"):
            mode = "ancestor"
        elif line.startswith("======="):
            mode = "theirs"
        elif line.startswith(">>>>>>>"):
            mode = "both"
        else:
            if mode == "both":
                ours_lines.append(line)
                theirs_lines.append(line)
            elif mode == "ours":
                ours_lines.append(line)
            elif mode == "theirs":
                theirs_lines.append(line)

    def safe_parse(raw: str) -> Dict[str, Any]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            tasks: Dict[str, Any] = {}
            for m in re.finditer(r'\{[^{}]*"id"\s*:\s*"([^"]+)"[^{}]*\}', raw):
                try:
                    obj = json.loads(m.group(0))
                    tasks[obj["id"]] = obj
                except Exception:
                    pass
            return tasks

    ours_json = safe_parse("\n".join(ours_lines))
    theirs_json = safe_parse("\n".join(theirs_lines))
    return ours_json, theirs_json


def auto_resolve_rebase_conflicts(
    repo_dir: Optional[Union[Path, str]] = None,
    max_rebase_steps: int = 10,
) -> bool:
    """
    Scans for conflicted productivity files during an active rebase/merge, reconciles them,
    stages them, and invokes `git rebase --continue` automatically.
    """
    root = find_repo_root(repo_dir)
    if root is None:
        return False

    for _ in range(max_rebase_steps):
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if status_proc.returncode != 0:
            return False

        conflicted_files: List[str] = []
        for line in status_proc.stdout.splitlines():
            if len(line) >= 4 and (line[:2] in ("UU", "AA", "UD", "DU", "AU", "UA")):
                conflicted_files.append(line[3:].strip())

        is_rebasing = (root / ".git" / "rebase-merge").exists() or (root / ".git" / "rebase-apply").exists()
        if not conflicted_files:
            if not is_rebasing:
                return True  # Rebase fully complete and no conflicts left

            # In the middle of an active rebase with resolved/modified files
            for rel in ("workspace/tasks.json", "workspace/TASKS.md", "tasks.json", "TASKS.md"):
                if (root / rel).exists():
                    subprocess.run(["git", "add", rel], cwd=str(root), check=False)

            env = dict(os.environ)
            project_root = Path(__file__).resolve().parent.parent.parent
            curr_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{project_root};{curr_pp}" if curr_pp else str(project_root)
            py_editor = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
            env["GIT_EDITOR"] = py_editor
            env["GIT_SEQUENCE_EDITOR"] = py_editor
            res = subprocess.run(
                ["git", "-c", f"core.editor={py_editor}", "rebase", "--continue"],
                cwd=str(root),
                capture_output=True,
                text=True,
                env=env,
            )
            is_still_rebasing = (root / ".git" / "rebase-merge").exists() or (root / ".git" / "rebase-apply").exists()
            if not is_still_rebasing:
                return True
            if res.returncode != 0:
                logger.error("git rebase --continue failed: %s", res.stderr)
                return False
            continue

        # Check if all conflicts are productivity files
        resolvable = [
            f for f in conflicted_files
            if f.lower().endswith("tasks.json") or f.lower().endswith("tasks.md")
        ]
        if len(resolvable) != len(conflicted_files):
            logger.warning("Unresolvable conflicts in non-task files: %s", set(conflicted_files) - set(resolvable))
            return False

        # Resolve tasks.json first
        tasks_file_rel = next((f for f in resolvable if f.lower().endswith("tasks.json")), None)
        if tasks_file_rel:
            tasks_full_path = root / tasks_file_rel

            # Try reading Git stages :2: (ours) and :3: (theirs)
            proc_ours = subprocess.run(["git", "show", f":2:{tasks_file_rel}"], cwd=str(root), capture_output=True, text=True)
            proc_theirs = subprocess.run(["git", "show", f":3:{tasks_file_rel}"], cwd=str(root), capture_output=True, text=True)

            ours_dict: Dict[str, Any] = {}
            theirs_dict: Dict[str, Any] = {}

            if proc_ours.returncode == 0 and proc_theirs.returncode == 0:
                try:
                    ours_dict = json.loads(proc_ours.stdout)
                    theirs_dict = json.loads(proc_theirs.stdout)
                except Exception:
                    ours_dict, theirs_dict = {}, {}

            if not ours_dict and not theirs_dict:
                if tasks_full_path.exists():
                    with open(tasks_full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    ours_dict, theirs_dict = extract_conflict_versions_from_text(content)

            # Reconcile CRDT state
            merged = reconcile_3way({}, ours_dict, theirs_dict)

            with open(tasks_full_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.write("\n")

            # Update TASKS.md
            md_path = tasks_full_path.parent / "TASKS.md"
            regenerate_tasks_markdown(merged, md_path)

            subprocess.run(["git", "add", tasks_file_rel], cwd=str(root), check=True)
            if md_path.exists():
                try:
                    subprocess.run(["git", "add", str(md_path.relative_to(root))], cwd=str(root), check=True)
                except Exception:
                    pass

        # Check if TASKS.md was conflicted alone
        tasks_md_rel = next((f for f in resolvable if f.lower().endswith("tasks.md")), None)
        if tasks_md_rel and not tasks_file_rel:
            md_full_path = root / tasks_md_rel
            json_partner = md_full_path.parent / "tasks.json"
            tasks_dict = load_tasks_dict_safe(json_partner)
            regenerate_tasks_markdown(tasks_dict, md_full_path)
            subprocess.run(["git", "add", tasks_md_rel], cwd=str(root), check=True)

        # Check if a rebase is in progress
        is_rebasing = (root / ".git" / "rebase-merge").exists() or (root / ".git" / "rebase-apply").exists()
        if is_rebasing:
            env = dict(os.environ)
            project_root = Path(__file__).resolve().parent.parent.parent
            curr_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{project_root};{curr_pp}" if curr_pp else str(project_root)
            py_editor = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
            env["GIT_EDITOR"] = py_editor
            env["GIT_SEQUENCE_EDITOR"] = py_editor
            res = subprocess.run(
                ["git", "-c", f"core.editor={py_editor}", "rebase", "--continue"],
                cwd=str(root),
                capture_output=True,
                text=True,
                env=env,
            )
            if res.returncode != 0:
                logger.error("git rebase --continue failed: %s", res.stderr)
                return False
        else:
            return True

    return True


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for merge driver execution and registration."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m neko_bot.sync.merge_driver <ancestor %O> <ours %A> <theirs %B> [target %P]")
        print("       python -m neko_bot.sync.merge_driver register [--repo-dir <path>]")
        return 1

    if args[0] == "register":
        repo_dir = None
        if len(args) >= 3 and args[1] == "--repo-dir":
            repo_dir = Path(args[2])
        success = register_merge_driver(repo_dir)
        if success:
            print("[Neko Bot] Custom Git merge driver registered successfully.")
            return 0
        else:
            print("[Neko Bot] Failed to register merge driver.")
            return 1

    if len(args) >= 3:
        ancestor = Path(args[0])
        ours = Path(args[1])
        theirs = Path(args[2])
        target = args[3] if len(args) > 3 else None
        return reconcile_files(ancestor, ours, theirs, target)

    print("Invalid arguments.")
    return 1


def merge_driver_main(*args: Any, **kwargs: Any) -> int:
    """Convenience functional wrapper around reconcile_files or CLI main."""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return main(list(args[0]))
    if len(args) >= 3:
        ancestor, ours, theirs = args[0], args[1], args[2]
        target = args[3] if len(args) > 3 else kwargs.get("target")
        return reconcile_files(Path(ancestor), Path(ours), Path(theirs), str(target) if target else None)
    return main(list(args) if args else None)


if __name__ == "__main__":
    sys.exit(main())
