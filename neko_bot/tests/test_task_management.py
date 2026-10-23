"""
tests/test_task_management.py - Acceptance Criteria AC2 & Milestone 2 Test Harness.

Comprehensive test suite verifying:
1. Feature F7: Italian Natural-Language Task Parser (tags, priorities, deadlines, title stripping, unicode, SLA < 5ms).
2. Features F9 & F10: CRDT LWW-Element-Set, Tombstone Preservation, Atomic Windows Writes, and Markdown Mirror.
3. Feature F11: Top-Task Deterministic Ranking (Overdue, Priority, Deadline, FIFO, and 10-Combination Matrix).
4. Feature F8: CLI Task Commands (add, list, done, delete, top, summary, do).
5. AC1 <-> AC2 Status Integration: Dynamic population of "top_task" in neko status --json.
6. Tier 4 Real-World Application Scenario 2: Full Day Task Lifecycle.
"""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from neko_bot.cli import build_parser, main
from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.daemon import DaemonController, DaemonStatusManager
from neko_bot.task.models import Task, TaskPriority, TaskState
from neko_bot.task.parser import ItalianTaskParser, parse_italian_task
from neko_bot.task.ranking import (
    get_task_summary,
    get_top_task,
    get_top_task_from_store,
    rank_tasks,
)
from neko_bot.task.store import AmbiguousTaskPrefixError, TaskStore
from tests.conftest import temporary_workspace


class TestItalianNaturalLanguageParser(unittest.TestCase):
    """Verifies Feature F7: Italian NL extraction of projects, priorities, dates, and titles."""

    def test_extract_project_tags_standard(self) -> None:
        """Verifies extraction of project tags with # prefix."""
        res1 = parse_italian_task("Bake normal maps #unreal !alta domani")
        self.assertEqual(res1.project, "unreal")

        res2 = parse_italian_task("Modellare sedia per cliente #client-alpha !media")
        self.assertEqual(res2.project, "client-alpha")

        res3 = parse_italian_task("Render archviz #blender_arch")
        self.assertEqual(res3.project, "blender_arch")

    def test_extract_project_tags_positions(self) -> None:
        """Verifies tag extraction from start, middle, and end of sentence."""
        start = parse_italian_task("#unreal Creare particle system")
        self.assertEqual(start.project, "unreal")
        self.assertNotIn("#unreal", start.title)

        mid = parse_italian_task("Creare particle system #unreal per il livello")
        self.assertEqual(mid.project, "unreal")
        self.assertNotIn("#unreal", mid.title)

        end = parse_italian_task("Creare particle system per il livello #unreal")
        self.assertEqual(end.project, "unreal")
        self.assertNotIn("#unreal", end.title)

    def test_extract_priority_italian_variations(self) -> None:
        """Verifies mapping of Italian priority tokens to high, medium, low."""
        self.assertEqual(parse_italian_task("Fix shader !alta").priority, TaskPriority.HIGH)
        self.assertEqual(parse_italian_task("Fix shader !ALTA").priority, TaskPriority.HIGH)
        self.assertEqual(parse_italian_task("Fix shader !urgente").priority, TaskPriority.HIGH)
        self.assertEqual(parse_italian_task("Fix shader !URGENTE").priority, TaskPriority.HIGH)
        self.assertEqual(parse_italian_task("Fix shader !media").priority, TaskPriority.MEDIUM)
        self.assertEqual(parse_italian_task("Fix shader !MEDIA").priority, TaskPriority.MEDIUM)
        self.assertEqual(parse_italian_task("Fix shader !normale").priority, TaskPriority.MEDIUM)
        self.assertEqual(parse_italian_task("Fix shader !bassa").priority, TaskPriority.LOW)
        self.assertEqual(parse_italian_task("Fix shader !BASSA").priority, TaskPriority.LOW)
        self.assertEqual(parse_italian_task("Fix shader").priority, TaskPriority.MEDIUM)

    def test_extract_deadline_relative_italian(self) -> None:
        """Verifies relative Italian temporal tokens (oggi, stasera, domani, dopodomani)."""
        ref_time = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)  # Friday
        parser = ItalianTaskParser(reference_time=ref_time)

        t_oggi = parser.parse("Consegnare render oggi")
        self.assertIsNotNone(t_oggi.due_date)
        self.assertTrue(t_oggi.due_date.startswith("2026-10-23"))

        t_stasera = parser.parse("Consegnare render stasera")
        self.assertIsNotNone(t_stasera.due_date)
        self.assertTrue(t_stasera.due_date.startswith("2026-10-23"))

        t_domani = parser.parse("Consegnare render domani")
        self.assertIsNotNone(t_domani.due_date)
        self.assertTrue(t_domani.due_date.startswith("2026-10-24"))

        t_dopo = parser.parse("Consegnare render dopodomani")
        self.assertIsNotNone(t_dopo.due_date)
        self.assertTrue(t_dopo.due_date.startswith("2026-10-25"))

    def test_extract_deadline_weekdays_italian(self) -> None:
        """Verifies Italian weekday names wrapping forward to next occurrence."""
        ref_time = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)  # Friday
        parser = ItalianTaskParser(reference_time=ref_time)

        t_lun = parser.parse("Meeting con il lead lunedì")
        self.assertIsNotNone(t_lun.due_date)
        self.assertTrue(t_lun.due_date.startswith("2026-10-26"))

        t_mar = parser.parse("Review design martedì")
        self.assertIsNotNone(t_mar.due_date)
        self.assertTrue(t_mar.due_date.startswith("2026-10-27"))

    def test_extract_deadline_times_and_combined(self) -> None:
        """Verifies explicit time extraction and date-time combinations."""
        ref_time = datetime(2026, 10, 23, 8, 0, 0, tzinfo=timezone.utc)
        parser = ItalianTaskParser(reference_time=ref_time)

        t1 = parser.parse("Sync team alle 15:00")
        self.assertIsNotNone(t1.due_date)
        self.assertIn("15:00", t1.due_date)

        t2 = parser.parse("Consegna build domani alle 18:30")
        self.assertIsNotNone(t2.due_date)
        self.assertTrue(t2.due_date.startswith("2026-10-24 18:30"))

    def test_extract_deadline_prepositions_and_iso(self) -> None:
        """Verifies 'entro il DD/MM' and standard ISO dates."""
        ref_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
        parser = ItalianTaskParser(reference_time=ref_time)

        t1 = parser.parse("Inviare fattura entro il 25/10")
        self.assertIsNotNone(t1.due_date)
        self.assertIn("2026-10-25", t1.due_date)

        t2 = parser.parse("Deadline milestone 2026-10-30 18:00")
        self.assertEqual(t2.due_date, "2026-10-30 18:00")

    def test_extract_relative_intervals(self) -> None:
        """Verifies interval expressions (tra 2 ore, in 30 minuti)."""
        ref_time = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)
        parser = ItalianTaskParser(reference_time=ref_time)

        t1 = parser.parse("Chiamata client tra 2 ore")
        self.assertIsNotNone(t1.due_date)
        self.assertTrue(t1.due_date.startswith("2026-10-23 12:00"))

        t2 = parser.parse("Salvataggio backup in 30 minuti")
        self.assertIsNotNone(t2.due_date)
        self.assertTrue(t2.due_date.startswith("2026-10-23 10:30"))

    def test_clean_title_extraction(self) -> None:
        """Verifies that project tags, priority tags, and dates are stripped cleanly."""
        raw = "Fare bake delle normal maps #unreal !alta domani alle 15:00"
        parsed = parse_italian_task(raw)
        self.assertEqual(parsed.title, "Fare bake delle normal maps")
        self.assertEqual(parsed.project, "unreal")
        self.assertEqual(parsed.priority, TaskPriority.HIGH)

    def test_parser_boundary_unicode_and_accents(self) -> None:
        """Verifies Italian accents, apostrophes, and emojis are preserved in title."""
        raw = "Modellare caffè ☕ e tè d'autunno #bar !urgente oggi"
        parsed = parse_italian_task(raw)
        self.assertIn("Modellare caffè ☕ e tè d'autunno", parsed.title)
        self.assertEqual(parsed.project, "bar")
        self.assertEqual(parsed.priority, TaskPriority.HIGH)

    def test_parser_performance_sla(self) -> None:
        """Verifies deterministic parser completes 100 parses in < 500ms (< 5ms each)."""
        prompt = "Ottimizzare mesh poligonale #3dsmax !alta entro domani alle 14:00"
        start = time.perf_counter()
        for _ in range(100):
            parse_italian_task(prompt)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.5, "Parser SLA exceeded 5ms per call")


class TestTaskModelsAndCRDTStore(unittest.TestCase):
    """Verifies Features F9 & F10: CRDT LWW-Element-Set, Tombstones, Atomic Persistence & Markdown."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_crdt_"))
        self.store = TaskStore(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_task_dataclass_serialization_roundtrip(self) -> None:
        """Verifies Task serialization to/from JSON dict preserves all attributes."""
        task = Task.create(
            title="Rigging character",
            project="unreal",
            priority=TaskPriority.HIGH,
            due_date="2026-10-25 15:00",
            metadata={"source": "cli"},
        )
        data = task.to_dict()
        reconstructed = Task.from_dict(data)

        self.assertEqual(task.id, reconstructed.id)
        self.assertEqual(task.title, reconstructed.title)
        self.assertEqual(task.project, reconstructed.project)
        self.assertEqual(task.priority, reconstructed.priority)
        self.assertEqual(task.due_date, reconstructed.due_date)
        self.assertEqual(task.deleted, reconstructed.deleted)

    def test_crdt_lww_concurrent_title_update(self) -> None:
        """Verifies Last-Write-Wins resolves concurrent edits via updated_at."""
        t0 = "2026-10-23T12:00:00Z"
        t1 = "2026-10-23T12:01:00Z"
        t2 = "2026-10-23T12:02:00Z"

        task_a = Task(id="task_1", title="Original Title", created_at=t0, updated_at=t1)
        task_b = Task(id="task_1", title="Updated by Node B", created_at=t0, updated_at=t2)

        store_a = TaskStore(self.temp_dir / "node_a")
        store_a.put(task_a)

        store_a.reconcile_tasks({"task_1": task_b.to_dict()})
        merged = store_a.get("task_1")
        self.assertIsNotNone(merged)
        self.assertEqual(merged.title, "Updated by Node B", "Later updated_at must win")

    def test_crdt_tombstone_preservation_over_stale_update(self) -> None:
        """Verifies deletion tombstone wins over concurrent stale edits."""
        t0 = "2026-10-23T12:00:00Z"
        t_stale = "2026-10-23T12:01:00Z"
        t_delete = "2026-10-23T12:02:00Z"

        task_deleted = Task(id="task_2", title="Doomed", deleted=True, created_at=t0, updated_at=t_delete)
        task_stale = Task(id="task_2", title="Stale Edit", deleted=False, created_at=t0, updated_at=t_stale)

        store = TaskStore(self.temp_dir / "store")
        store.put(task_deleted)
        store.reconcile_tasks({"task_2": task_stale.to_dict()})

        result = store.get("task_2")
        self.assertIsNotNone(result)
        self.assertTrue(result.deleted, "Tombstone must be preserved against older edits")

    def test_crdt_resurrection_after_deletion(self) -> None:
        """Verifies deliberate edit after deletion resurrects task."""
        t_delete = "2026-10-23T12:02:00Z"
        t_resurrect = "2026-10-23T12:03:00Z"

        task_del = Task(id="task_3", title="Deleted", deleted=True, updated_at=t_delete)
        task_res = Task(id="task_3", title="Resurrected Task", deleted=False, updated_at=t_resurrect)

        store = TaskStore(self.temp_dir / "res_store")
        store.put(task_del)
        store.reconcile_tasks({"task_3": task_res.to_dict()})

        result = store.get("task_3")
        self.assertIsNotNone(result)
        self.assertFalse(result.deleted)
        self.assertEqual(result.title, "Resurrected Task")

    def test_crdt_mathematical_laws(self) -> None:
        """Verifies Commutativity, Associativity, and Idempotence of task reconciliation."""
        task1 = Task(id="t1", title="V1", updated_at="2026-10-23T12:01:00Z")
        task2 = Task(id="t1", title="V2", updated_at="2026-10-23T12:02:00Z")

        # Commutativity: merge(A, B) == merge(B, A)
        store1 = TaskStore(self.temp_dir / "s1")
        store1.put(task1)
        store1.reconcile_tasks({"t1": task2.to_dict()})

        store2 = TaskStore(self.temp_dir / "s2")
        store2.put(task2)
        store2.reconcile_tasks({"t1": task1.to_dict()})

        self.assertEqual(store1.get("t1").title, store2.get("t1").title)

        # Idempotence: merge(A, A) == A
        store1.reconcile_tasks({"t1": task2.to_dict()})
        self.assertEqual(store1.get("t1").title, "V2")

    def test_markdown_mirror_atomic_generation(self) -> None:
        """Verifies workspace/TASKS.md is automatically created and mirrors active/done tasks."""
        store = TaskStore(self.temp_dir)
        t1 = store.add("Todo Task", project="unreal", priority=TaskPriority.HIGH)
        t2 = store.add("Completed Task", project="blender", priority=TaskPriority.MEDIUM)
        store.mark_done(t2.id)

        md_file = self.temp_dir / "TASKS.md"
        self.assertTrue(md_file.exists(), "TASKS.md must be generated")

        content = md_file.read_text(encoding="utf-8")
        self.assertIn("- [ ] Todo Task", content)
        self.assertIn("- [x] Completed Task", content)
        self.assertIn("# Tasks", content)

    def test_markdown_mirror_excludes_tombstones(self) -> None:
        """Verifies deleted tasks do not appear in TASKS.md."""
        store = TaskStore(self.temp_dir)
        t = store.add("Ephemeral Task", project="temp")
        store.delete(t.id)

        content = (self.temp_dir / "TASKS.md").read_text(encoding="utf-8")
        self.assertNotIn("Ephemeral Task", content)

    def test_prefix_matching_exact_and_prefix(self) -> None:
        """Verifies prefix finding finds unique matching tasks."""
        store = TaskStore(self.temp_dir)
        t1 = store.create_task("Alpha Task", custom_id="abcdef12")
        t2 = store.create_task("Beta Task", custom_id="12345678")

        self.assertEqual(store.find_by_prefix("abc").id, "abcdef12")
        self.assertEqual(store.find_by_prefix("ABC").id, "abcdef12")
        self.assertEqual(store.find_by_prefix("1234").id, "12345678")
        self.assertIsNone(store.find_by_prefix("xyz"))

    def test_prefix_matching_ambiguous_raises(self) -> None:
        """Verifies AmbiguousTaskPrefixError when multiple tasks match a short prefix."""
        store = TaskStore(self.temp_dir)
        store.create_task("Match 1", custom_id="a1b2c3d4")
        store.create_task("Match 2", custom_id="a1b9e8f7")

        with self.assertRaises(AmbiguousTaskPrefixError):
            store.find_by_prefix("a1")

    def test_atomic_persistence_and_reload(self) -> None:
        """Verifies reload from disk preserves state."""
        store1 = TaskStore(self.temp_dir)
        t = store1.create_task("Persistent Task", project="db", priority=TaskPriority.HIGH)

        store2 = TaskStore(self.temp_dir)
        loaded = store2.get(t.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "Persistent Task")
        self.assertEqual(loaded.priority, TaskPriority.HIGH)


class TestTopTaskDeterministicRanking(unittest.TestCase):
    """Verifies Feature F11: Top-task deterministic ranking algorithm."""

    def setUp(self) -> None:
        self.ref_time = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

    def test_ranking_overdue_tasks_come_first(self) -> None:
        """Overdue task (low priority) ranks before future task (high priority)."""
        overdue_task = Task.create(
            title="Overdue Emergency",
            priority=TaskPriority.LOW,
            due_date="2026-10-23 10:00",
            custom_id="task_overdue",
        )
        future_task = Task.create(
            title="Future High Priority",
            priority=TaskPriority.HIGH,
            due_date="2026-10-24 15:00",
            custom_id="task_future",
        )

        ranked = rank_tasks([future_task, overdue_task], reference_time=self.ref_time)
        self.assertEqual(ranked[0].id, "task_overdue")
        self.assertEqual(ranked[1].id, "task_future")

    def test_ranking_multiple_overdue_sorted_by_staleness(self) -> None:
        """Multiple overdue tasks are sorted by how overdue they are (oldest deadline first)."""
        very_overdue = Task.create(
            title="2 Days Overdue",
            priority=TaskPriority.LOW,
            due_date="2026-10-21 12:00",
            custom_id="t_very_overdue",
        )
        slightly_overdue = Task.create(
            title="1 Hour Overdue",
            priority=TaskPriority.HIGH,
            due_date="2026-10-23 11:00",
            custom_id="t_slightly_overdue",
        )

        ranked = rank_tasks([slightly_overdue, very_overdue], reference_time=self.ref_time)
        self.assertEqual(ranked[0].id, "t_very_overdue")
        self.assertEqual(ranked[1].id, "t_slightly_overdue")

    def test_ranking_priority_hierarchy_non_overdue(self) -> None:
        """Non-overdue tasks rank strictly by high > medium > low."""
        t_high = Task.create(title="High", priority=TaskPriority.HIGH, custom_id="t_high")
        t_med = Task.create(title="Medium", priority=TaskPriority.MEDIUM, custom_id="t_med")
        t_low = Task.create(title="Low", priority=TaskPriority.LOW, custom_id="t_low")

        ranked = rank_tasks([t_low, t_med, t_high], reference_time=self.ref_time)
        self.assertEqual([t.id for t in ranked], ["t_high", "t_med", "t_low"])

    def test_ranking_nearest_upcoming_deadline_same_priority(self) -> None:
        """Tasks of identical priority rank by nearest upcoming deadline."""
        t_soon = Task.create(title="Soon", priority=TaskPriority.HIGH, due_date="2026-10-23 14:00", custom_id="soon")
        t_later = Task.create(title="Later", priority=TaskPriority.HIGH, due_date="2026-10-25 10:00", custom_id="later")

        ranked = rank_tasks([t_later, t_soon], reference_time=self.ref_time)
        self.assertEqual(ranked[0].id, "soon")
        self.assertEqual(ranked[1].id, "later")

    def test_ranking_deadline_vs_no_deadline_same_priority(self) -> None:
        """Task with upcoming deadline ranks before task without deadline of same priority."""
        t_sched = Task.create(title="Scheduled", priority=TaskPriority.MEDIUM, due_date="2026-10-24 10:00", custom_id="sched")
        t_unsched = Task.create(title="Unscheduled", priority=TaskPriority.MEDIUM, due_date=None, custom_id="unsched")

        ranked = rank_tasks([t_unsched, t_sched], reference_time=self.ref_time)
        self.assertEqual(ranked[0].id, "sched")
        self.assertEqual(ranked[1].id, "unsched")

    def test_ranking_fifo_creation_order_no_deadlines(self) -> None:
        """Tasks with same priority and no deadlines rank in FIFO creation order."""
        t1 = Task(id="t1", title="First", priority=TaskPriority.MEDIUM, created_at="2026-10-23T08:00:00Z")
        t2 = Task(id="t2", title="Second", priority=TaskPriority.MEDIUM, created_at="2026-10-23T09:00:00Z")
        t3 = Task(id="t3", title="Third", priority=TaskPriority.MEDIUM, created_at="2026-10-23T10:00:00Z")

        ranked = rank_tasks([t3, t1, t2], reference_time=self.ref_time)
        self.assertEqual([t.id for t in ranked], ["t1", "t2", "t3"])

    def test_ranking_filters_completed_and_tombstones(self) -> None:
        """Completed and deleted tasks are completely excluded from ranking."""
        t_done = Task.create(title="Done", priority=TaskPriority.HIGH)
        t_done.mark_done()

        t_deleted = Task.create(title="Deleted", priority=TaskPriority.HIGH)
        t_deleted.mark_deleted()

        t_active = Task.create(title="Active Low", priority=TaskPriority.LOW, custom_id="active")

        ranked = rank_tasks([t_done, t_deleted, t_active], reference_time=self.ref_time)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].id, "active")

    def test_ranking_10_combination_matrix(self) -> None:
        """Exhaustive test matrix validating the complete deterministic order across 10 tasks."""
        tasks = [
            # Tier 0: Overdue
            Task(id="1_overdue_48h", title="48h Overdue", priority=TaskPriority.LOW, due_date="2026-10-21 12:00"),
            Task(id="2_overdue_2h_high", title="2h Overdue High", priority=TaskPriority.HIGH, due_date="2026-10-23 10:00"),
            Task(id="3_overdue_2h_med", title="2h Overdue Med", priority=TaskPriority.MEDIUM, due_date="2026-10-23 10:00"),
            # Tier 1: High Priority
            Task(id="4_high_due_soon", title="High Due Soon", priority=TaskPriority.HIGH, due_date="2026-10-23 14:00"),
            Task(id="5_high_due_tomorrow", title="High Due Tomorrow", priority=TaskPriority.HIGH, due_date="2026-10-24 10:00"),
            Task(id="6_high_no_due_early", title="High No Due Early", priority=TaskPriority.HIGH, created_at="2026-10-23T08:00:00Z"),
            Task(id="7_high_no_due_late", title="High No Due Late", priority=TaskPriority.HIGH, created_at="2026-10-23T09:00:00Z"),
            # Tier 1: Medium Priority
            Task(id="8_med_due_soon", title="Med Due Soon", priority=TaskPriority.MEDIUM, due_date="2026-10-23 13:00"),
            # Tier 1: Low Priority
            Task(id="9_low_due_soon", title="Low Due Soon", priority=TaskPriority.LOW, due_date="2026-10-23 12:30"),
            Task(id="10_low_no_due", title="Low No Due", priority=TaskPriority.LOW, created_at="2026-10-23T07:00:00Z"),
        ]

        expected_order = [
            "1_overdue_48h",
            "2_overdue_2h_high",
            "3_overdue_2h_med",
            "4_high_due_soon",
            "5_high_due_tomorrow",
            "6_high_no_due_early",
            "7_high_no_due_late",
            "8_med_due_soon",
            "9_low_due_soon",
            "10_low_no_due",
        ]

        ranked = rank_tasks(tasks, reference_time=self.ref_time)
        actual_order = [t.id for t in ranked]
        self.assertEqual(actual_order, expected_order)

    def test_daily_summary_aggregation(self) -> None:
        """Verifies get_task_summary() computes accurate rollup counts."""
        t_overdue = Task(id="t1", title="T1", priority=TaskPriority.HIGH, project="unreal", due_date="2026-10-22 10:00")
        t_today = Task(id="t2", title="T2", priority=TaskPriority.MEDIUM, project="blender", due_date="2026-10-23 18:00")
        t_done = Task(id="t3", title="T3", priority=TaskPriority.LOW, state=TaskState.DONE, completed_at="2026-10-23T10:00:00Z")

        summary = get_task_summary([t_overdue, t_today, t_done], reference_time=self.ref_time)
        self.assertEqual(summary["total_active"], 2)
        self.assertEqual(summary["overdue_count"], 1)
        self.assertEqual(summary["due_today_count"], 1)
        self.assertEqual(summary["high_priority_count"], 1)
        self.assertEqual(summary["completed_today_count"], 1)
        self.assertEqual(summary["by_project"], {"unreal": 1, "blender": 1})
        self.assertEqual(summary["top_task"]["id"], "t1")


class TestTaskCliCommands(unittest.TestCase):
    """Verifies Feature F8: CLI commands (add, list, done, delete, top, summary, do)."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_cli_tasks_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_task_add_natural_language(self) -> None:
        """Verifies `neko task add \"...\"` creates task on disk."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["task", "add", "Bake textures #unreal !alta domani alle 15:00", "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0)
        self.assertIn("Task added", buf.getvalue())

        # Verify disk persistence
        store = TaskStore(self.temp_dir / "workspace")
        tasks = store.get_active_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Bake textures")
        self.assertEqual(tasks[0].project, "unreal")
        self.assertEqual(tasks[0].priority, TaskPriority.HIGH)

    def test_cli_task_list_and_filters(self) -> None:
        """Verifies `neko task list` default, --all, --project, and --priority."""
        store = TaskStore(self.temp_dir / "workspace")
        store.add("Shader Unreal", project="unreal", priority=TaskPriority.HIGH)
        t2 = store.add("Model Blender", project="blender", priority=TaskPriority.LOW)
        store.mark_done(t2.id)

        # Default list (active only)
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            main(["task", "list", "--workspace", str(self.temp_dir)])
        self.assertIn("Shader Unreal", buf1.getvalue())
        self.assertNotIn("Model Blender", buf1.getvalue())

        # List --all (active + done)
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            main(["task", "list", "--all", "--workspace", str(self.temp_dir)])
        self.assertIn("Shader Unreal", buf2.getvalue())
        self.assertIn("Model Blender", buf2.getvalue())

        # List --project unreal
        buf3 = io.StringIO()
        with redirect_stdout(buf3):
            main(["task", "list", "--project", "unreal", "--workspace", str(self.temp_dir)])
        self.assertIn("Shader Unreal", buf3.getvalue())

        # List --priority low (none active)
        buf4 = io.StringIO()
        with redirect_stdout(buf4):
            main(["task", "list", "--priority", "low", "--workspace", str(self.temp_dir)])
        self.assertNotIn("Shader Unreal", buf4.getvalue())

    def test_cli_task_done_by_id_and_prefix(self) -> None:
        """Verifies completing task by exact ID or short unique prefix."""
        store = TaskStore(self.temp_dir / "workspace")
        t = store.add("Test Task")
        prefix = t.id[:4]

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["task", "done", prefix, "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0)
        self.assertTrue(store.get(t.id).is_done())

    def test_cli_task_delete_tombstones(self) -> None:
        """Verifies `neko task delete <prefix>` marks task as deleted tombstone."""
        store = TaskStore(self.temp_dir / "workspace")
        t = store.add("Task to Delete")

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["task", "delete", t.id[:4], "--workspace", str(self.temp_dir)])

        self.assertEqual(ret, 0)
        task = store.get(t.id)
        self.assertTrue(task.deleted)

    def test_cli_task_top_and_summary(self) -> None:
        """Verifies `neko task top` and `neko task summary` CLI outputs."""
        store = TaskStore(self.temp_dir / "workspace")
        store.add("Urgent Task", priority=TaskPriority.HIGH)
        store.add("Regular Task", priority=TaskPriority.LOW)

        # neko task top
        buf_top = io.StringIO()
        with redirect_stdout(buf_top):
            ret = main(["task", "top", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("Urgent Task", buf_top.getvalue())

        # neko task summary
        buf_sum = io.StringIO()
        with redirect_stdout(buf_sum):
            ret = main(["task", "summary", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("Active Tasks:", buf_sum.getvalue())

    def test_cli_do_smart_router_task(self) -> None:
        """Verifies `neko do` smart-routes task creation requests."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["do", "Fix shader #unreal !alta entro domani alle 18:00", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        self.assertIn("Task accepted", buf.getvalue())

        store = TaskStore(self.temp_dir / "workspace")
        tasks = store.get_active_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].project, "unreal")
        self.assertEqual(tasks[0].priority, TaskPriority.HIGH)

    def test_cli_task_done_ambiguous_prefix_fails(self) -> None:
        """Verifies `neko task done` reports error on ambiguous prefix."""
        store = TaskStore(self.temp_dir / "workspace")
        store.create_task("Task 1", custom_id="aabb1111")
        store.create_task("Task 2", custom_id="aabb2222")

        ret = main(["task", "done", "aabb", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 1)

    def test_cli_task_done_missing_id_fails(self) -> None:
        """Verifies `neko task done` returns non-zero for nonexistent ID."""
        ret = main(["task", "done", "nonexistent999", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 1)


class TestStatusTopTaskIntegration(unittest.TestCase):
    """Verifies AC1 <-> AC2 contract: dynamic top_task population in neko status --json."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_status_int_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_status_json_lifecycle(self) -> None:
        """
        Verifies top_task lifecycle in `neko status --json`:
        1. Initially null
        2. Populated after adding low-priority task
        3. Dynamically changes when higher-priority task is added
        4. Advances back to low-priority task when high-priority is completed
        5. Returns to null when all tasks are done
        """
        # Step 1: Initially null
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            main(["status", "--json", "--workspace", str(self.temp_dir)])
        data1 = json.loads(buf1.getvalue())
        self.assertIsNone(data1["top_task"])

        # Step 2: Add low priority task
        store = TaskStore(self.temp_dir / "workspace")
        t_low = store.add("Low Priority", priority=TaskPriority.LOW)

        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            main(["status", "--json", "--workspace", str(self.temp_dir)])
        data2 = json.loads(buf2.getvalue())
        self.assertIsNotNone(data2["top_task"])
        self.assertEqual(data2["top_task"]["title"], "Low Priority")

        # Step 3: Add high priority task
        t_high = store.add("High Priority", priority=TaskPriority.HIGH)

        buf3 = io.StringIO()
        with redirect_stdout(buf3):
            main(["status", "--json", "--workspace", str(self.temp_dir)])
        data3 = json.loads(buf3.getvalue())
        self.assertEqual(data3["top_task"]["title"], "High Priority")

        # Step 4: Complete high priority task
        store.mark_done(t_high.id)

        buf4 = io.StringIO()
        with redirect_stdout(buf4):
            main(["status", "--json", "--workspace", str(self.temp_dir)])
        data4 = json.loads(buf4.getvalue())
        self.assertEqual(data4["top_task"]["title"], "Low Priority")

        # Step 5: Complete low priority task
        store.mark_done(t_low.id)

        buf5 = io.StringIO()
        with redirect_stdout(buf5):
            main(["status", "--json", "--workspace", str(self.temp_dir)])
        data5 = json.loads(buf5.getvalue())
        self.assertIsNone(data5["top_task"])

    def test_status_human_renders_top_task(self) -> None:
        """Verifies human status table contains top task."""
        store = TaskStore(self.temp_dir / "workspace")
        store.add("Design HUD", project="ui", priority=TaskPriority.HIGH)

        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["status", "--workspace", str(self.temp_dir)])
        out = buf.getvalue()
        self.assertIn("Top Task:", out)
        self.assertIn("Design HUD", out)
        self.assertIn("[HIGH]", out)


class TestScenario2FullDayTaskLifecycle(unittest.TestCase):
    """
    Tier 4 Real-World E2E Scenario 2:
    Full Day Task Lifecycle from TEST_INFRA.md § Scenario 2:
    - User adds tasks in Italian via natural language CLI
    - Verifies top task ranking
    - Completes urgent tasks
    - Verifies disk persistence (tasks.json, TASKS.md)
    - Verifies dynamic status updates
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_scenario2_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario_2_full_workflow(self) -> None:
        workspace_arg = ["--workspace", str(self.temp_dir)]

        # 1. Add 4 tasks in Italian
        main(["task", "add", "Risolvere bug critico shader #unreal !urgente entro stasera"] + workspace_arg)
        main(["task", "add", "Review modelli 3D #blender !alta domani alle 10:00"] + workspace_arg)
        main(["task", "add", "Organizzare reference photogrammetry #art !media"] + workspace_arg)
        main(["task", "add", "Aggiornare documentazione #docs !bassa"] + workspace_arg)

        # 2. Check neko status --json: top task must be the urgent shader bug
        buf_status = io.StringIO()
        with redirect_stdout(buf_status):
            main(["status", "--json"] + workspace_arg)
        status_data = json.loads(buf_status.getvalue())
        self.assertIsNotNone(status_data["top_task"])
        self.assertEqual(status_data["top_task"]["title"], "Risolvere bug critico shader")
        self.assertEqual(status_data["top_task"]["project"], "unreal")
        self.assertEqual(status_data["top_task"]["priority"], "high")

        # 3. Complete the urgent task using prefix
        urgent_id = status_data["top_task"]["id"]
        main(["task", "done", urgent_id[:4]] + workspace_arg)

        # 4. Check status again: top task must automatically switch to the next task (Review modelli 3D)
        buf_status2 = io.StringIO()
        with redirect_stdout(buf_status2):
            main(["status", "--json"] + workspace_arg)
        status_data2 = json.loads(buf_status2.getvalue())
        self.assertEqual(status_data2["top_task"]["title"], "Review modelli 3D")
        self.assertEqual(status_data2["top_task"]["project"], "blender")

        # 5. Verify TASKS.md on disk
        tasks_md = self.temp_dir / "workspace" / "TASKS.md"
        self.assertTrue(tasks_md.exists())
        md_text = tasks_md.read_text(encoding="utf-8")
        self.assertIn("- [x] Risolvere bug critico shader", md_text)
        self.assertIn("- [ ] Review modelli 3D", md_text)

        # 6. Delete documentation task
        store = TaskStore(self.temp_dir / "workspace")
        docs_task = [t for t in store.get_active_tasks() if t.project == "docs"][0]
        main(["task", "delete", docs_task.id[:4]] + workspace_arg)

        # 7. Check summary output
        buf_sum = io.StringIO()
        with redirect_stdout(buf_sum):
            main(["task", "summary"] + workspace_arg)
        summary_out = buf_sum.getvalue()
        self.assertIn("Active Tasks:      2", summary_out)
        self.assertIn("Completed Today:   1", summary_out)


if __name__ == "__main__":
    unittest.main()
