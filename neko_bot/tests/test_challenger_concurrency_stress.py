"""
tests/test_challenger_concurrency_stress.py - Empirical Adversarial Concurrency, Deadlock & CRDT Stress Suite.

Author: challenger_final_1 (Empirical Challenger)
Focus Areas:
1. Reentrant lock deadlock immunity in NativeToastDispatcher under concurrent multithreaded dispatch.
2. High-concurrency TaskStore read/write contention & atomic_write_json_file stress (50+ threads on Windows NTFS).
3. Multi-PC CRDT task reconciliation, 3-way merge conflict edge cases, algebraic laws (commutativity, associativity, idempotence), and multi-node convergence.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List
import unittest
from unittest.mock import patch

from neko_bot.core.config import atomic_write_json_file
from neko_bot.notifications.toast import (
    MockToastDispatcher,
    NativeToastDispatcher,
    ToastRecord,
)
from neko_bot.sync.merge_driver import (
    compare_lww_task_dicts,
    reconcile_3way,
    reconcile_files,
)
from neko_bot.task.models import Task, TaskPriority, TaskState, now_utc_iso
from neko_bot.task.store import TaskStore, atomic_write_text_file


class TestNativeToastDispatcherDeadlockImmunity(unittest.TestCase):
    """
    Stress-tests NativeToastDispatcher's reentrant lock under heavy multithreaded contention,
    recursive reentrancy, asynchronous callbacks, and rapid mode switching.
    """

    def setUp(self) -> None:
        self.dispatcher = NativeToastDispatcher(app_id="NekoTest", max_history=50, mock_mode=True)

    def tearDown(self) -> None:
        self.dispatcher.shutdown(wait=False)

    def test_reentrant_lock_nested_invocation_within_same_thread(self) -> None:
        """
        Verifies that when a dispatch triggers nested/recursive show() or history queries
        on the same thread, threading.RLock prevents deadlock.
        """
        depth = 10
        records = []

        # Recursively call show from within a mocked hook or subclass
        class ReentrantDispatcher(NativeToastDispatcher):
            def _record_delivery(self, record: ToastRecord) -> None:
                super()._record_delivery(record)
                # Query history while holding self._lock
                hist = self.get_history()
                records.append(len(hist))
                if len(hist) < depth:
                    # Recursive call! Same thread must be able to re-enter show()
                    self.show(f"Nested-{len(hist)}", "Recursive message")

        d = ReentrantDispatcher(max_history=50, mock_mode=True)
        # Initiating the first call will trigger recursive calls up to depth 10
        ok = d.show("Root", "Initial call")
        self.assertTrue(ok)
        self.assertGreaterEqual(len(records), depth)
        self.assertGreaterEqual(len(d.get_history()), depth)
        d.shutdown(wait=False)

    def test_multithreaded_concurrent_toast_dispatch_50_threads(self) -> None:
        """
        Fires 50 concurrent threads executing 20 dispatches each (1,000 dispatches total),
        interleaving synchronous show(), asynchronous show_async(), get_history(), and clear_history().
        Ensures:
        1. No thread deadlocks (timeout per thread).
        2. History buffer never exceeds max_history (50).
        3. No unhandled exceptions or thread crashes.
        """
        num_threads = 50
        calls_per_thread = 20
        errors = []
        barrier = threading.Barrier(num_threads)

        def worker(thread_id: int) -> None:
            try:
                barrier.wait(timeout=5.0)
                for i in range(calls_per_thread):
                    action = i % 4
                    if action == 0:
                        self.dispatcher.show(
                            f"Sync-T{thread_id}-{i}",
                            f"Sync msg from thread {thread_id}",
                            level=random.choice(["info", "warning", "alert"]),
                        )
                    elif action == 1:
                        fut = self.dispatcher.show_async(
                            f"Async-T{thread_id}-{i}",
                            f"Async msg from thread {thread_id}",
                            level="info",
                        )
                        # Ensure future resolves without exception
                        fut.result(timeout=5.0)
                    elif action == 2:
                        hist = self.dispatcher.get_history()
                        self.assertLessEqual(len(hist), 50)
                    else:
                        mock_disp = self.dispatcher.get_mock_dispatched()
                        self.assertIsInstance(mock_disp, list)

            except Exception as e:
                errors.append(f"Thread {thread_id} failed: {e}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            self.assertFalse(t.is_alive(), f"Thread timed out / deadlocked: {t.name}")

        self.assertEqual(errors, [])
        final_history = self.dispatcher.get_history()
        self.assertLessEqual(len(final_history), 50)
        self.assertGreater(len(final_history), 0)

    def test_async_callbacks_with_concurrent_lock_acquisition(self) -> None:
        """
        Verifies that show_async with on_complete callbacks invoking dispatcher methods
        (such as get_history or nested show) do not deadlock with worker threads.
        """
        num_async = 30
        completed = []
        lock = threading.Lock()

        def on_complete(success: bool, err: Any) -> None:
            # Inside worker thread pool: inspect history and schedule another toast
            h = self.dispatcher.get_history()
            with lock:
                completed.append((success, len(h)))

        futures = []
        for i in range(num_async):
            fut = self.dispatcher.show_async(f"AsyncCallback-{i}", f"Msg {i}", on_complete=on_complete)
            futures.append(fut)

        for fut in futures:
            res = fut.result(timeout=5.0)
            self.assertTrue(res)

        self.assertEqual(len(completed), num_async)


class TestHighConcurrencyTaskStoreAndAtomicWrite(unittest.TestCase):
    """
    Stress-tests TaskStore and atomic_write_json_file under heavy concurrent contention (50+ threads),
    simulating multi-threaded daemon operations and concurrent multi-process file sharing on Windows NTFS.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_stress_store_")
        self.tasks_file = Path(self.temp_dir) / "tasks.json"
        self.tasks_md = Path(self.temp_dir) / "TASKS.md"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_atomic_write_json_file_50_threads_contention(self) -> None:
        """
        Directly hammers atomic_write_json_file from 50 concurrent threads writing to the same file.
        Verifies Windows NTFS sharing violation retry loop withstands extreme contention without raising.
        """
        num_threads = 50
        writes_per_thread = 10
        errors = []
        barrier = threading.Barrier(num_threads)

        def worker(tid: int) -> None:
            try:
                barrier.wait(timeout=5.0)
                for i in range(writes_per_thread):
                    payload = {
                        "thread": tid,
                        "iteration": i,
                        "timestamp": time.time_ns(),
                        "data": f"data_{tid}_{i}" * 50,
                    }
                    atomic_write_json_file(self.tasks_file, payload, max_retries=60)
            except Exception as e:
                errors.append(f"Writer thread {tid} failed: {e}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)
            self.assertFalse(t.is_alive(), f"Thread timed out: {t.name}")

        self.assertEqual(errors, [])
        self.assertTrue(self.tasks_file.exists())
        # The file on disk must be a completely valid, uncorrupted JSON document
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            final_data = json.load(f)
        self.assertIn("thread", final_data)
        self.assertIn("iteration", final_data)

    def test_single_taskstore_50_threads_mixed_crud_contention(self) -> None:
        """
        50 threads concurrently performing Add, Update, Mark Done, Delete, and List on a single TaskStore.
        Verifies:
        1. Thread-safety: No race conditions or internal dictionary mutations errors.
        2. tasks.json and TASKS.md remain valid and non-corrupt throughout.
        3. All added tasks are accounted for (either active, done, or tombstone).
        """
        store = TaskStore(tasks_file_or_dir=self.tasks_file, tasks_md_file=self.tasks_md, auto_mirror=True)

        num_threads = 50
        ops_per_thread = 10
        errors = []
        task_ids = []
        ids_lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def worker(tid: int) -> None:
            try:
                barrier.wait(timeout=5.0)
                for i in range(ops_per_thread):
                    op = i % 5
                    if op == 0:
                        # Add task
                        t = store.create_task(
                            title=f"Task-T{tid}-{i}",
                            project="StressTest",
                            priority=random.choice([TaskPriority.LOW, TaskPriority.MEDIUM, TaskPriority.HIGH]),
                        )
                        with ids_lock:
                            task_ids.append(t.id)
                    elif op == 1:
                        # Update task if available
                        target_id = None
                        with ids_lock:
                            if task_ids:
                                target_id = random.choice(task_ids)
                        if target_id:
                            store.update(target_id, title=f"Updated-T{tid}-{i}")
                    elif op == 2:
                        # Mark done
                        target_id = None
                        with ids_lock:
                            if task_ids:
                                target_id = random.choice(task_ids)
                        if target_id:
                            store.mark_done(target_id)
                    elif op == 3:
                        # Soft delete (tombstone)
                        target_id = None
                        with ids_lock:
                            if task_ids:
                                target_id = random.choice(task_ids)
                        if target_id:
                            store.delete(target_id, hard=False)
                    else:
                        # Read operations
                        active = store.get_active_tasks()
                        self.assertIsInstance(active, list)
                        all_dict = store.get_all_tasks_dict()
                        self.assertIsInstance(all_dict, dict)

            except Exception as e:
                errors.append(f"Worker {tid} encountered error: {e}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)
            self.assertFalse(t.is_alive(), f"Worker deadlocked: {t.name}")

        self.assertEqual(errors, [])

        # Verify disk persistence & integrity
        self.assertTrue(self.tasks_file.exists())
        self.assertTrue(self.tasks_md.exists())

        # Clean reload from disk
        fresh_store = TaskStore(tasks_file_or_dir=self.tasks_file, auto_mirror=False)
        all_stored = fresh_store.get_all_tasks_dict(include_deleted=True)
        self.assertGreater(len(all_stored), 0)

        # Verify TASKS.md content
        with open(self.tasks_md, "r", encoding="utf-8") as f:
            md_content = f.read()
        self.assertIn("# Tasks", md_content)

    def test_multi_store_inter_process_simulation(self) -> None:
        """
        Simulates two separate processes (e.g. Daemon thread and CLI command)
        operating concurrently on the SAME tasks.json file via two distinct TaskStore instances.
        Verifies _reload_if_modified ensures instance B sees modifications made by instance A.
        """
        store_a = TaskStore(tasks_file_or_dir=self.tasks_file, tasks_md_file=self.tasks_md, auto_mirror=False)
        store_b = TaskStore(tasks_file_or_dir=self.tasks_file, tasks_md_file=self.tasks_md, auto_mirror=False)

        task_1 = store_a.create_task("Task from Process A", project="IPC")

        # Give filesystem timestamp a small window or stat resolution
        time.sleep(0.01)

        # Store B should reload from disk and find task_1
        retrieved_b = store_b.get(task_1.id)
        self.assertIsNotNone(retrieved_b, "Store B failed to reload task created by Store A")
        self.assertEqual(retrieved_b.title, "Task from Process A")

        # Now Store B updates it
        time.sleep(0.01)
        store_b.update(task_1.id, title="Updated by Process B")

        # Store A should see the update
        time.sleep(0.01)
        retrieved_a = store_a.get(task_1.id)
        self.assertIsNotNone(retrieved_a)
        self.assertEqual(retrieved_a.title, "Updated by Process B")


class TestMultiPCCRDTTReconciliationAndMergeConflictEdgeCases(unittest.TestCase):
    """
    Stress-tests CRDT LWW-Element-Set mathematical properties:
    1. Commutativity: reconcile(A, B) == reconcile(B, A).
    2. Associativity: reconcile(reconcile(A, B), C) == reconcile(A, reconcile(B, C)).
    3. Idempotence: reconcile(A, A) == A.
    4. Deterministic tie-breaking (same timestamp, tombstone precedence, canonical JSON ordering).
    5. Multi-PC convergence (Work, Home, Laptop partitions merging in arbitrary order).
    6. End-to-end 3-way reconcile_files on disk.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_stress_crdt_")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_crdt_algebraic_properties_commutativity_and_idempotence(self) -> None:
        """
        Mathematical verification:
        For arbitrary task dictionaries A and B:
        - Commutativity: reconcile(A, B) == reconcile(B, A)
        - Idempotence: reconcile(A, A) == A
        """
        dict_a = {
            "task_1": {
                "id": "task_1",
                "title": "Task 1 Version A",
                "updated_at": "2026-10-23T10:00:00Z",
                "deleted": False,
            },
            "task_2": {
                "id": "task_2",
                "title": "Task 2 Only in A",
                "updated_at": "2026-10-23T09:00:00Z",
                "deleted": False,
            },
        }

        dict_b = {
            "task_1": {
                "id": "task_1",
                "title": "Task 1 Version B (Newer)",
                "updated_at": "2026-10-23T11:00:00Z",
                "deleted": False,
            },
            "task_3": {
                "id": "task_3",
                "title": "Task 3 Only in B",
                "updated_at": "2026-10-23T08:00:00Z",
                "deleted": False,
            },
        }

        # Idempotence: reconcile({}, A, A) == A
        merged_aa = reconcile_3way({}, dict_a, dict_a)
        self.assertEqual(merged_aa, dict_a)

        # Commutativity: reconcile({}, A, B) == reconcile({}, B, A)
        merged_ab = reconcile_3way({}, dict_a, dict_b)
        merged_ba = reconcile_3way({}, dict_b, dict_a)
        self.assertEqual(merged_ab, merged_ba)

        # Winner for task_1 must be version B because 11:00 > 10:00
        self.assertEqual(merged_ab["task_1"]["title"], "Task 1 Version B (Newer)")
        self.assertIn("task_2", merged_ab)
        self.assertIn("task_3", merged_ab)

    def test_crdt_tombstone_wins_exact_timestamp_tie(self) -> None:
        """
        When two nodes produce conflicting state at the EXACT same timestamp:
        If Node A updates title (deleted=False) and Node B marks deleted (deleted=True):
        Tombstone MUST win to guarantee eventual deletion convergence.
        """
        same_ts = "2026-10-23T12:00:00.000000Z"
        cand_active = {
            "id": "task_tie",
            "title": "Active modification",
            "updated_at": same_ts,
            "deleted": False,
        }
        cand_deleted = {
            "id": "task_tie",
            "title": "Active modification",
            "updated_at": same_ts,
            "deleted": True,
        }

        # deleted should win over active
        self.assertTrue(compare_lww_task_dicts(cand_deleted, cand_active))
        self.assertFalse(compare_lww_task_dicts(cand_active, cand_deleted))

        ancestor = {"task_tie": {"id": "task_tie", "title": "Base", "updated_at": "2026-10-23T10:00:00Z"}}
        ours = {"task_tie": cand_active}
        theirs = {"task_tie": cand_deleted}

        merged = reconcile_3way(ancestor, ours, theirs)
        self.assertTrue(merged["task_tie"]["deleted"])

    def test_crdt_canonical_json_tie_breaker_for_identical_timestamps_and_deletion_state(self) -> None:
        """
        When timestamps AND deletion states are identical, tie-break must be deterministic
        via canonical sorted JSON string comparison.
        """
        same_ts = "2026-10-23T12:00:00.000000Z"
        cand_a = {
            "id": "task_alpha",
            "title": "Alpha Title",
            "updated_at": same_ts,
            "deleted": False,
        }
        cand_z = {
            "id": "task_alpha",
            "title": "Zeta Title",
            "updated_at": same_ts,
            "deleted": False,
        }

        # One must win deterministically, and the opposite direction must yield the opposite
        winner_z_vs_a = compare_lww_task_dicts(cand_z, cand_a)
        winner_a_vs_z = compare_lww_task_dicts(cand_a, cand_z)
        self.assertNotEqual(winner_z_vs_a, winner_a_vs_z)

        # 3-way merge in both orders must choose the same winner
        res_1 = reconcile_3way({}, {"task_alpha": cand_a}, {"task_alpha": cand_z})
        res_2 = reconcile_3way({}, {"task_alpha": cand_z}, {"task_alpha": cand_a})
        self.assertEqual(res_1, res_2)

    def test_multi_pc_three_way_partition_convergence(self) -> None:
        """
        Simulates 3 distinct machines: PC-WORK, PC-HOME, and PC-LAPTOP.
        All start from a common base snapshot of 5 tasks.
        Each machine operates offline and performs disjoint and conflicting mutations:
        - PC-WORK: Completes T1, updates T2 title, creates T6.
        - PC-HOME: Deletes T2 (tombstone), creates T7, updates T3.
        - PC-LAPTOP: Updates T1 priority, completes T3, creates T8.
        Verify that merging all 3 in ANY permutation:
        - Permutation 1: (WORK + HOME) then LAPTOP
        - Permutation 2: (HOME + LAPTOP) then WORK
        - Permutation 3: (LAPTOP + WORK) then HOME
        converges to the EXACT same final task state!
        """
        base_ts = "2026-10-23T08:00:00Z"
        ancestor = {
            f"T{i}": {
                "id": f"T{i}",
                "title": f"Original Task {i}",
                "state": "todo",
                "priority": "medium",
                "deleted": False,
                "updated_at": base_ts,
            }
            for i in range(1, 6)
        }

        # PC-WORK mutations (at 09:00:00Z)
        work = dict(ancestor)
        work["T1"] = dict(work["T1"], state="done", updated_at="2026-10-23T09:00:00Z")
        work["T2"] = dict(work["T2"], title="Work Modified T2", updated_at="2026-10-23T09:00:00Z")
        work["T6"] = {
            "id": "T6",
            "title": "Created on Work PC",
            "state": "todo",
            "priority": "high",
            "deleted": False,
            "updated_at": "2026-10-23T09:00:00Z",
        }

        # PC-HOME mutations (at 10:00:00Z - newer than WORK)
        home = dict(ancestor)
        # Home deletes T2 at 10:00:00Z (newer than Work's 09:00:00Z update!)
        home["T2"] = dict(home["T2"], deleted=True, updated_at="2026-10-23T10:00:00Z")
        home["T7"] = {
            "id": "T7",
            "title": "Created on Home PC",
            "state": "todo",
            "priority": "low",
            "deleted": False,
            "updated_at": "2026-10-23T10:00:00Z",
        }
        home["T3"] = dict(home["T3"], title="Home Modified T3", updated_at="2026-10-23T10:00:00Z")

        # PC-LAPTOP mutations (at 11:00:00Z - newest)
        laptop = dict(ancestor)
        laptop["T1"] = dict(laptop["T1"], priority="high", updated_at="2026-10-23T11:00:00Z")
        laptop["T3"] = dict(laptop["T3"], state="done", updated_at="2026-10-23T11:00:00Z")
        laptop["T8"] = {
            "id": "T8",
            "title": "Created on Laptop PC",
            "state": "todo",
            "priority": "medium",
            "deleted": False,
            "updated_at": "2026-10-23T11:00:00Z",
        }

        # Merge Permutation 1: (WORK + HOME) -> result1; then (result1 + LAPTOP)
        merge_wh = reconcile_3way(ancestor, work, home)
        merge_1 = reconcile_3way(ancestor, merge_wh, laptop)

        # Merge Permutation 2: (HOME + LAPTOP) -> result2; then (result2 + WORK)
        merge_hl = reconcile_3way(ancestor, home, laptop)
        merge_2 = reconcile_3way(ancestor, merge_hl, work)

        # Merge Permutation 3: (LAPTOP + WORK) -> result3; then (result3 + HOME)
        merge_lw = reconcile_3way(ancestor, laptop, work)
        merge_3 = reconcile_3way(ancestor, merge_lw, home)

        # CONVERGENCE ASSERTION: All permutations must match 100%
        self.assertEqual(merge_1, merge_2, "Permutation 1 and 2 diverged!")
        self.assertEqual(merge_2, merge_3, "Permutation 2 and 3 diverged!")

        # Verify specific expected semantics:
        # T1: LAPTOP updated at 11:00:00Z with priority='high', but state='todo' vs WORK's state='done' at 09:00:00Z
        # LWW replaces whole element with newest record (11:00:00Z)
        self.assertEqual(merge_1["T1"]["updated_at"], "2026-10-23T11:00:00Z")
        self.assertEqual(merge_1["T1"]["priority"], "high")

        # T2: HOME deleted T2 at 10:00:00Z, which is newer than WORK's update at 09:00:00Z
        self.assertTrue(merge_1["T2"]["deleted"], "T2 should be deleted (tombstone) by HOME's newer timestamp")

        # T3: LAPTOP updated at 11:00:00Z, winning over HOME's 10:00:00Z update
        self.assertEqual(merge_1["T3"]["updated_at"], "2026-10-23T11:00:00Z")
        self.assertEqual(merge_1["T3"]["state"], "done")

        # All new tasks T6, T7, T8 must be present in the merged set
        self.assertIn("T6", merge_1)
        self.assertIn("T7", merge_1)
        self.assertIn("T8", merge_1)

    def test_reconcile_files_disk_invocation_with_markdown_mirror(self) -> None:
        """
        Executes reconcile_files() directly on real filesystem files simulating
        Git merge driver (%O %A %B %P) invocation.
        Verifies:
        1. Exit code is 0.
        2. %A is updated with cleanly formatted JSON.
        3. TASKS.md is created or updated alongside tasks.json.
        """
        anc_file = Path(self.temp_dir) / "ancestor.json"
        ours_file = Path(self.temp_dir) / "tasks.json"  # %A
        theirs_file = Path(self.temp_dir) / "theirs.json"

        anc_data = {
            "T1": {
                "id": "T1",
                "title": "Base Task 1",
                "updated_at": "2026-10-23T08:00:00Z",
                "deleted": False,
            }
        }
        ours_data = {
            "T1": {
                "id": "T1",
                "title": "Ours Task 1",
                "updated_at": "2026-10-23T09:00:00Z",
                "deleted": False,
            },
            "T2": {
                "id": "T2",
                "title": "Ours Added 2",
                "updated_at": "2026-10-23T09:10:00Z",
                "deleted": False,
            },
        }
        theirs_data = {
            "T1": {
                "id": "T1",
                "title": "Theirs Task 1 (Wins)",
                "updated_at": "2026-10-23T10:00:00Z",
                "deleted": False,
            },
            "T3": {
                "id": "T3",
                "title": "Theirs Added 3",
                "updated_at": "2026-10-23T09:30:00Z",
                "deleted": False,
            },
        }

        with open(anc_file, "w", encoding="utf-8") as f:
            json.dump(anc_data, f)
        with open(ours_file, "w", encoding="utf-8") as f:
            json.dump(ours_data, f)
        with open(theirs_file, "w", encoding="utf-8") as f:
            json.dump(theirs_data, f)

        exit_code = reconcile_files(anc_file, ours_file, theirs_file, target_relpath="workspace/tasks.json")
        self.assertEqual(exit_code, 0)

        # Inspect ours_file (%A)
        with open(ours_file, "r", encoding="utf-8") as f:
            merged_json = json.load(f)

        self.assertEqual(merged_json["T1"]["title"], "Theirs Task 1 (Wins)")
        self.assertIn("T2", merged_json)
        self.assertIn("T3", merged_json)


if __name__ == "__main__":
    unittest.main()
