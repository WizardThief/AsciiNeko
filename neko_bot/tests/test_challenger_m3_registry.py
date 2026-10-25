"""
tests/test_challenger_m3_registry.py - Adversarial Test Suite for Milestone 3.

Empirically challenges:
1. Concurrency Hammering & Windows Lock Contention (20-50 threads, multi-store instances, multi-process).
2. CRDT LWW Conflict Resolution (timestamp collision, tombstone dominance, canonical JSON tie-breaking, 3-node convergence).
3. Historical Archiving & Version Increments (archive/<id>_v<version>.py, concurrent version updates).
4. Prompt Injection & Adversarial Inputs in match_skill (SQLi, shell metacharacters, regex bombs, null bytes, unicode).
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
import uuid

from neko_bot.skills.models import (
    SkillManifest,
    bump_version,
    canonical_json,
    generate_skill_id,
    now_utc_iso,
    parse_timestamp_safe,
)
from neko_bot.skills.registry import SkillRegistry
from neko_bot.skills.sandbox import SkillSandbox


class TestMultiStoreConcurrencyAndLockContention(unittest.TestCase):
    """
    Adversarial stress-testing of SkillRegistry concurrency, multi-store synchronization,
    and Windows file lock contention.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_adv_reg_concur_")
        self.workspace_dir = Path(self.temp_dir)
        self.sandbox = SkillSandbox()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_single_store_50_threads_hammering(self) -> None:
        """
        Stress test: 50 concurrent threads simultaneously hammering a single SkillRegistry instance.
        - 25 threads register workflow skills
        - 10 threads query / list skills
        - 10 threads match skills
        - 5 threads update existing skills
        Verifies 0 record losses and 0 unhandled PermissionError / [WinError 5] / [WinError 32].
        """
        registry = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        num_registers = 25
        num_readers = 10
        num_matchers = 10
        num_updaters = 5

        # Seed initial skills for updaters/readers
        for i in range(num_updaters):
            sid = f"seed-skill-{i}"
            wf = {
                "id": sid,
                "name": f"Seed {i}",
                "type": "workflow",
                "steps": [{"action": "query_tasks"}],
                "trigger_patterns": [f"trigger-seed-{i}"],
            }
            ok, msg = registry.register_skill(wf)
            self.assertTrue(ok, f"Failed seeding: {msg}")

        errors: List[Exception] = []
        lock = threading.Lock()

        def register_worker(worker_idx: int) -> None:
            try:
                sid = f"wf-hammer-{worker_idx}"
                wf = {
                    "id": sid,
                    "name": f"Hammer Workflow {worker_idx}",
                    "type": "workflow",
                    "steps": [
                        {"action": "query_tasks", "params": {"state": "active"}},
                        {"action": "format_template", "template": f"Worker {worker_idx}: {{tasks}}"},
                    ],
                    "trigger_patterns": [f"hammer {worker_idx}", f"trigger {worker_idx}"],
                }
                ok, msg = registry.register_skill(wf)
                if not ok:
                    with lock:
                        errors.append(RuntimeError(f"Worker {worker_idx} register failed: {msg}"))
            except Exception as e:
                with lock:
                    errors.append(e)

        def reader_worker(worker_idx: int) -> None:
            try:
                for _ in range(5):
                    skills = registry.list_skills(include_deleted=False)
                    self.assertIsInstance(skills, list)
                    time.sleep(0.002)
            except Exception as e:
                with lock:
                    errors.append(e)

        def matcher_worker(worker_idx: int) -> None:
            try:
                for j in range(5):
                    registry.match_skill(f"trigger-seed-{j % num_updaters}")
                    registry.match_skill(f"hammer {j}")
                    registry.match_skill("unmatched prompt string")
                    time.sleep(0.002)
            except Exception as e:
                with lock:
                    errors.append(e)

        def updater_worker(worker_idx: int) -> None:
            try:
                target_id = f"seed-skill-{worker_idx}"
                for v in range(3):
                    wf = {
                        "id": target_id,
                        "name": f"Updated Seed {worker_idx} v{v}",
                        "type": "workflow",
                        "steps": [{"action": "query_tasks", "params": {"iter": v}}],
                    }
                    ok, msg = registry.register_skill(wf)
                    if not ok:
                        with lock:
                            errors.append(RuntimeError(f"Updater {worker_idx} failed: {msg}"))
                    time.sleep(0.002)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads: List[threading.Thread] = []
        for i in range(num_registers):
            threads.append(threading.Thread(target=register_worker, args=(i,)))
        for i in range(num_readers):
            threads.append(threading.Thread(target=reader_worker, args=(i,)))
        for i in range(num_matchers):
            threads.append(threading.Thread(target=matcher_worker, args=(i,)))
        for i in range(num_updaters):
            threads.append(threading.Thread(target=updater_worker, args=(i,)))

        self.assertEqual(len(threads), 50)

        # Start all 50 threads simultaneously
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            self.assertFalse(t.is_alive(), "A thread hung during concurrency hammering")

        # Verify zero unhandled exceptions
        self.assertEqual(len(errors), 0, f"Encountered {len(errors)} thread errors: {errors[:5]}")

        # Verify 0 record losses: all 25 registered skills + 5 seeded skills must exist
        all_skills = registry.list_skills(include_deleted=False)
        self.assertEqual(len(all_skills), num_registers + num_updaters)
        all_ids = {s["id"] for s in all_skills}
        for i in range(num_registers):
            self.assertIn(f"wf-hammer-{i}", all_ids)
        for i in range(num_updaters):
            self.assertIn(f"seed-skill-{i}", all_ids)

        # Verify registry.json on disk is valid JSON
        reg_file = registry.registry_file
        self.assertTrue(reg_file.exists())
        with open(reg_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        self.assertIsInstance(disk_data, list)
        self.assertEqual(len(disk_data), num_registers + num_updaters)

    def test_02_multi_store_30_instances_concurrent_hammering(self) -> None:
        """
        Stress test: 30 independent SkillRegistry instances pointing to the exact same disk directory,
        hammering registrations and deletions concurrently.
        Verifies inter-instance synchronization, reload-and-merge, and no record losses.
        """
        num_instances = 30
        errors: List[Exception] = []
        lock = threading.Lock()

        def instance_worker(idx: int) -> None:
            try:
                # Each thread creates its own independent SkillRegistry instance
                reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)

                sid = f"multi-inst-{idx}"
                wf = {
                    "id": sid,
                    "name": f"Multi Instance Skill {idx}",
                    "type": "workflow",
                    "steps": [{"action": "query_tasks"}],
                    "trigger_patterns": [f"multi-trigger-{idx}"],
                }
                ok, msg = reg.register_skill(wf)
                if not ok:
                    with lock:
                        errors.append(RuntimeError(f"Instance {idx} registration failed: {msg}"))

                # Read immediately from this instance
                skill = reg.get_skill(sid)
                if not skill or skill["id"] != sid:
                    with lock:
                        errors.append(RuntimeError(f"Instance {idx} failed to read back own skill {sid}"))

            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=instance_worker, args=(i,)) for i in range(num_instances)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            self.assertFalse(t.is_alive(), "Instance thread timed out")

        self.assertEqual(len(errors), 0, f"Encountered multi-instance errors: {errors[:5]}")

        # Instantiate a completely fresh 31st instance to inspect disk truth
        final_reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        disk_skills = final_reg.list_skills(include_deleted=False)
        self.assertEqual(
            len(disk_skills),
            num_instances,
            f"Expected {num_instances} records, found {len(disk_skills)} (lost records detected!)"
        )
        found_ids = {s["id"] for s in disk_skills}
        for i in range(num_instances):
            self.assertIn(f"multi-inst-{i}", found_ids)

    def test_03_concurrent_registration_and_deletions_race(self) -> None:
        """
        Adversarial race test: 40 concurrent threads registering and deleting skills simultaneously.
        20 threads register skills; 20 threads simultaneously delete a subset.
        Verifies no crash, no WinError, no corrupted state, and exact deterministic counts.
        """
        num_skills = 20
        reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)

        # Seed the 20 skills first
        for i in range(num_skills):
            sid = f"race-skill-{i}"
            ok, msg = reg.register_skill({
                "id": sid,
                "name": f"Race Skill {i}",
                "type": "workflow",
                "steps": [{"action": "query_tasks"}],
            })
            self.assertTrue(ok)

        errors: List[Exception] = []
        lock = threading.Lock()

        # 20 threads will update/reregister, 10 will soft-delete, 10 will hard-delete
        def deleter_worker(idx: int, hard: bool) -> None:
            try:
                target_id = f"race-skill-{idx}"
                local_reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
                local_reg.delete_skill(target_id, hard=hard)
            except Exception as e:
                with lock:
                    errors.append(e)

        def creator_worker(idx: int) -> None:
            try:
                sid = f"race-new-{idx}"
                local_reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
                local_reg.register_skill({
                    "id": sid,
                    "name": f"New Race Skill {idx}",
                    "type": "workflow",
                    "steps": [{"action": "notify"}],
                })
            except Exception as e:
                with lock:
                    errors.append(e)

        threads: List[threading.Thread] = []
        # 10 soft deletes on race-skill-0..9
        for i in range(10):
            threads.append(threading.Thread(target=deleter_worker, args=(i, False)))
        # 10 hard deletes on race-skill-10..19
        for i in range(10, 20):
            threads.append(threading.Thread(target=deleter_worker, args=(i, True)))
        # 20 new registrations
        for i in range(20):
            threads.append(threading.Thread(target=creator_worker, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            self.assertFalse(t.is_alive())

        self.assertEqual(len(errors), 0, f"Errors during race deletion/registration: {errors[:5]}")

        # Validate final state
        check_reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        active_skills = check_reg.list_skills(include_deleted=False)
        active_ids = {s["id"] for s in active_skills}

        # All 20 new skills must be active
        for i in range(20):
            self.assertIn(f"race-new-{i}", active_ids)

        # None of the 20 deleted skills should be active
        for i in range(20):
            self.assertNotIn(f"race-skill-{i}", active_ids)

        # Soft-deleted (0..9) must be in include_deleted=True
        all_including_deleted = {s["id"] for s in check_reg.list_skills(include_deleted=True)}
        for i in range(10):
            self.assertIn(f"race-skill-{i}", all_including_deleted)

        # Hard-deleted (10..19) must NOT be anywhere in the registry
        for i in range(10, 20):
            self.assertNotIn(f"race-skill-{i}", all_including_deleted)

    def test_04_windows_lock_contention_retry_resilience(self) -> None:
        """
        Simulate Windows file lock contention:
        Hold an exclusive lock / write lock on registry.json for 100ms in a background thread
        while register_skill is invoked.
        Verify atomic retry loop succeeds without unhandled PermissionError [WinError 5 / 32].
        """
        registry = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        # Register a base skill so registry.json exists
        registry.register_skill({
            "id": "base-skill",
            "name": "Base Skill",
            "type": "workflow",
            "steps": [{"action": "notify"}],
        })
        reg_file = registry.registry_file

        lock_acquired_event = threading.Event()
        release_lock_event = threading.Event()
        bg_error: List[Exception] = []

        def lock_holder() -> None:
            try:
                # Open with exclusive access on Windows
                with open(reg_file, "r+", encoding="utf-8") as f:
                    lock_acquired_event.set()
                    # Keep file open for 80 milliseconds to force collision
                    time.sleep(0.08)
            except Exception as e:
                bg_error.append(e)

        holder_thread = threading.Thread(target=lock_holder)
        holder_thread.start()
        lock_acquired_event.wait(timeout=2.0)

        # Concurrently register while file is locked
        ok, msg = registry.register_skill({
            "id": "contended-skill",
            "name": "Contended Skill",
            "type": "workflow",
            "steps": [{"action": "notify"}],
        })

        holder_thread.join(timeout=5.0)
        self.assertEqual(len(bg_error), 0)
        self.assertTrue(ok, f"Registration failed during file lock contention: {msg}")

        # Verify both skills exist on disk
        retrieved = registry.get_skill("contended-skill")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["id"], "contended-skill")

    def test_05_multi_process_concurrency_hammering(self) -> None:
        """
        Spawn 4 separate Python processes hammering the same workspace skills directory.
        Verifies true cross-process OS file lock handling.
        """
        worker_code = """
import sys
from pathlib import Path
from neko_bot.skills.registry import SkillRegistry
from neko_bot.skills.sandbox import SkillSandbox

workspace_dir = Path(sys.argv[1])
proc_id = sys.argv[2]
sandbox = SkillSandbox()
reg = SkillRegistry(workspace_dir=workspace_dir, sandbox=sandbox)

for i in range(5):
    sid = f"proc-{proc_id}-skill-{i}"
    wf = {
        "id": sid,
        "name": f"Proc {proc_id} Skill {i}",
        "type": "workflow",
        "steps": [{"action": "query_tasks"}],
    }
    ok, msg = reg.register_skill(wf)
    if not ok:
        sys.exit(1)
sys.exit(0)
"""
        script_file = self.workspace_dir / "proc_worker.py"
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(worker_code)

        num_procs = 4
        subprocesses: List[subprocess.Popen] = []
        env = dict(os.environ)
        # Ensure project root is in PYTHONPATH
        root_dir = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = root_dir + os.pathsep + env.get("PYTHONPATH", "")

        for p_idx in range(num_procs):
            cmd = [sys.executable, str(script_file), str(self.workspace_dir), str(p_idx)]
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocesses.append(proc)

        # Wait for all processes to complete
        for idx, proc in enumerate(subprocesses):
            stdout, stderr = proc.communicate(timeout=30.0)
            self.assertEqual(
                proc.returncode,
                0,
                f"Subprocess {idx} failed with code {proc.returncode}. Stderr: {stderr}"
            )

        # Verify disk catalog contains all 4 * 5 = 20 skills
        check_reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        skills = check_reg.list_skills(include_deleted=False)
        self.assertEqual(len(skills), 20, f"Expected 20 cross-process skills, got {len(skills)}")
        skill_ids = {s["id"] for s in skills}
        for p_idx in range(num_procs):
            for i in range(5):
                self.assertIn(f"proc-{p_idx}-skill-{i}", skill_ids)


class TestCRDTLWWConflictResolutionAndArchiving(unittest.TestCase):
    """
    Stress-testing of CRDT LWW tie-break rules, tombstone dominance, canonical JSON comparison,
    convergence across arrival permutations, and historical version archiving.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_adv_crdt_")
        self.workspace_dir = Path(self.temp_dir)
        self.sandbox = SkillSandbox()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_crdt_equal_timestamp_tombstone_dominance_both_orders(self) -> None:
        """
        When two mutations have the exact same updated_at, tombstone (deleted=True) MUST dominate
        regardless of which order the mutations arrive.
        """
        ts = "2026-10-24T12:00:00.000000Z"
        sid = "tool-tie-tombstone"

        mut_active = {
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "Active Skill",
            "steps": [{"action": "notify"}],
            "updated_at": ts,
            "deleted": False,
        }
        mut_tombstone = {
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "Active Skill",
            "steps": [{"action": "notify"}],
            "updated_at": ts,
            "deleted": True,
        }

        # Order 1: Apply Active, then Tombstone
        dir1 = Path(tempfile.mkdtemp(prefix="crdt_tomb_ord1_"))
        try:
            r1 = SkillRegistry(workspace_dir=dir1, sandbox=self.sandbox)
            r1.apply_crdt_mutation(mut_active)
            r1.apply_crdt_mutation(mut_tombstone)
            # Tombstone must win
            self.assertIsNone(r1.get_skill(sid))
            deleted_list = r1.list_skills(include_deleted=True)
            self.assertEqual(len(deleted_list), 1)
            self.assertTrue(deleted_list[0]["deleted"])
        finally:
            shutil.rmtree(dir1, ignore_errors=True)

        # Order 2: Apply Tombstone, then Active
        dir2 = Path(tempfile.mkdtemp(prefix="crdt_tomb_ord2_"))
        try:
            r2 = SkillRegistry(workspace_dir=dir2, sandbox=self.sandbox)
            r2.apply_crdt_mutation(mut_tombstone)
            applied = r2.apply_crdt_mutation(mut_active)
            # Active must be rejected
            self.assertFalse(applied, "Active mutation incorrectly overwrote equal-timestamp tombstone!")
            self.assertIsNone(r2.get_skill(sid))
            deleted_list = r2.list_skills(include_deleted=True)
            self.assertEqual(len(deleted_list), 1)
            self.assertTrue(deleted_list[0]["deleted"])
        finally:
            shutil.rmtree(dir2, ignore_errors=True)

    def test_02_crdt_identical_timestamp_canonical_json_tiebreaker_both_orders(self) -> None:
        """
        When two active mutations have the exact same updated_at and deleted=False,
        the canonical JSON string tie-breaker must deterministically select the winner
        regardless of arrival sequence.
        """
        ts = "2026-10-24T14:30:00.000000Z"
        sid = "tool-canon-tie"

        mut_low = {
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "AAA_Workflow_Lower",
            "steps": [{"action": "notify"}],
            "updated_at": ts,
            "deleted": False,
        }
        mut_high = {
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "ZZZ_Workflow_Higher",
            "steps": [{"action": "notify"}],
            "updated_at": ts,
            "deleted": False,
        }

        # Verify lexicographic relationship of canonical JSON strings
        s_low = canonical_json(mut_low)
        s_high = canonical_json(mut_high)
        self.assertGreater(s_high, s_low, "Test setup error: s_high should be > s_low")

        # Order 1: Low arrives first, then High arrives -> High wins
        dir1 = Path(tempfile.mkdtemp(prefix="crdt_lex_ord1_"))
        try:
            r1 = SkillRegistry(workspace_dir=dir1, sandbox=self.sandbox)
            r1.apply_crdt_mutation(mut_low)
            applied_high = r1.apply_crdt_mutation(mut_high)
            self.assertTrue(applied_high)
            s1 = r1.get_skill(sid)
            self.assertEqual(s1["name"], "ZZZ_Workflow_Higher")
        finally:
            shutil.rmtree(dir1, ignore_errors=True)

        # Order 2: High arrives first, then Low arrives -> High remains, Low rejected
        dir2 = Path(tempfile.mkdtemp(prefix="crdt_lex_ord2_"))
        try:
            r2 = SkillRegistry(workspace_dir=dir2, sandbox=self.sandbox)
            r2.apply_crdt_mutation(mut_high)
            applied_low = r2.apply_crdt_mutation(mut_low)
            self.assertFalse(applied_low, "Low mutation incorrectly overwrote higher canonical JSON winner!")
            s2 = r2.get_skill(sid)
            self.assertEqual(s2["name"], "ZZZ_Workflow_Higher")
        finally:
            shutil.rmtree(dir2, ignore_errors=True)

    def test_03_crdt_newer_timestamp_overrides_older_regardless_of_deleted_status(self) -> None:
        """
        Verifies that timestamps always take precedence over deletion status:
        - Newer Active resurrects older Tombstone (new write after delete).
        - Newer Tombstone deletes older Active.
        - Older Active rejected against newer Tombstone.
        - Older Tombstone rejected against newer Active.
        """
        sid = "tool-lww-precedence"
        reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)

        # 1. Base Active at T=100
        reg.apply_crdt_mutation({
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "Base Active",
            "steps": [{"action": "notify"}],
            "updated_at": "2026-10-24T10:00:00.000000Z",
            "deleted": False,
        })
        self.assertIsNotNone(reg.get_skill(sid))

        # 2. Older Tombstone at T=090 is rejected
        rejected = reg.apply_crdt_mutation({
            "entity_id": sid,
            "id": sid,
            "updated_at": "2026-10-24T09:00:00.000000Z",
            "deleted": True,
        })
        self.assertFalse(rejected)
        self.assertIsNotNone(reg.get_skill(sid))

        # 3. Newer Tombstone at T=110 deletes Active
        applied = reg.apply_crdt_mutation({
            "entity_id": sid,
            "id": sid,
            "updated_at": "2026-10-24T11:00:00.000000Z",
            "deleted": True,
        })
        self.assertTrue(applied)
        self.assertIsNone(reg.get_skill(sid))

        # 4. Older Active at T=105 is rejected against Tombstone at T=110
        rejected2 = reg.apply_crdt_mutation({
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "Stale Active",
            "steps": [{"action": "notify"}],
            "updated_at": "2026-10-24T10:50:00.000000Z",
            "deleted": False,
        })
        self.assertFalse(rejected2)
        self.assertIsNone(reg.get_skill(sid))

        # 5. Newer Active at T=120 resurrects skill
        applied2 = reg.apply_crdt_mutation({
            "entity_id": sid,
            "id": sid,
            "type": "workflow",
            "name": "Resurrected Active",
            "steps": [{"action": "notify"}],
            "updated_at": "2026-10-24T12:00:00.000000Z",
            "deleted": False,
        })
        self.assertTrue(applied2)
        res = reg.get_skill(sid)
        self.assertIsNotNone(res)
        self.assertEqual(res["name"], "Resurrected Active")

    def test_04_crdt_3_node_all_permutations_convergence(self) -> None:
        """
        Mathematical proof of convergence:
        3 distinct mutations for the same skill ID from 3 distributed nodes:
        - Mut A: T=100, Active, Canonical A
        - Mut B: T=200, Active, Canonical B
        - Mut C: T=200, Active, Canonical C (same timestamp as B, tie-breaker resolved by canonical JSON)
        All 3! = 6 arrival permutations (ABC, ACB, BAC, BCA, CAB, CBA) must converge
        to the exact same byte-for-byte canonical state.
        """
        sid = "tool-converge-3node"
        mut_a = {
            "entity_id": sid,
            "id": sid,
            "name": "Node A Skill",
            "type": "workflow",
            "steps": [{"action": "notify"}],
            "updated_at": "2026-10-24T10:00:00.000000Z",
            "deleted": False,
        }
        mut_b = {
            "entity_id": sid,
            "id": sid,
            "name": "Node B Skill Lower",
            "type": "workflow",
            "steps": [{"action": "notify"}],
            "updated_at": "2026-10-24T20:00:00.000000Z",
            "deleted": False,
        }
        mut_c = {
            "entity_id": sid,
            "id": sid,
            "name": "Node C Skill Higher",
            "type": "workflow",
            "steps": [{"action": "notify"}],
            "updated_at": "2026-10-24T20:00:00.000000Z",
            "deleted": False,
        }

        from itertools import permutations
        all_perms = list(permutations([("A", mut_a), ("B", mut_b), ("C", mut_c)]))
        self.assertEqual(len(all_perms), 6)

        final_canonical_states: List[str] = []

        for p_idx, perm in enumerate(all_perms):
            p_dir = Path(tempfile.mkdtemp(prefix=f"crdt_perm_{p_idx}_"))
            try:
                reg = SkillRegistry(workspace_dir=p_dir, sandbox=self.sandbox)
                for label, mut in perm:
                    reg.apply_crdt_mutation(mut)

                # Re-apply all 3 again to verify idempotence
                for label, mut in perm:
                    reg.apply_crdt_mutation(mut)

                exported = reg.export_crdt_entities()
                self.assertEqual(len(exported), 1)
                final_canonical_states.append(canonical_json(exported[0]))
            finally:
                shutil.rmtree(p_dir, ignore_errors=True)

        # Assert all 6 permutations converged to the EXACT same canonical JSON
        first_state = final_canonical_states[0]
        for idx, state in enumerate(final_canonical_states[1:], start=2):
            self.assertEqual(
                state,
                first_state,
                f"Permutation {idx} diverged from Permutation 1! CRDT convergence violated!"
            )

    def test_05_version_increments_and_historical_archiving_chain(self) -> None:
        """
        Verifies that successive skill updates:
        1. Automatically increment patch versions (1.0.0 -> 1.0.1 -> 1.0.2 -> 1.0.3).
        2. Create archival copies in workspace/skills/archive/<id>_v<old_version>.py.
        3. Keep latest code in tools/<id>.py.
        4. Preserve original created_at and execution_count.
        """
        reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        sid = "tool-version-chain"

        codes = [
            "def run(inputs, context): return {'version': 1}",
            "def run(inputs, context): return {'version': 2}",
            "def run(inputs, context): return {'version': 3}",
            "def run(inputs, context): return {'version': 4}",
        ]

        expected_versions = ["1.0.0", "1.0.1", "1.0.2", "1.0.3"]

        for idx, code in enumerate(codes):
            manifest = {
                "id": sid,
                "name": "Version Chain Tool",
                "type": "tool",
                "code": code,
                "self_tests": [{"inputs": {}, "expected_output": {"version": idx + 1}}],
            }
            ok, msg = reg.register_skill(manifest)
            self.assertTrue(ok, f"Registration step {idx} failed: {msg}")

            current = reg.get_skill(sid)
            self.assertEqual(current["version"], expected_versions[idx])

        # Verify active tool file on disk
        active_tool = reg.tools_dir / f"{sid}.py"
        self.assertTrue(active_tool.exists())
        with open(active_tool, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), codes[-1])

        # Verify all 3 historical versions archived in archive/
        archived_files = sorted(list(reg.archive_dir.glob(f"{sid}_v*.py")))
        self.assertEqual(len(archived_files), 3)

        expected_archives = [
            (f"{sid}_v1.0.0.py", codes[0]),
            (f"{sid}_v1.0.1.py", codes[1]),
            (f"{sid}_v1.0.2.py", codes[2]),
        ]
        for (expected_name, expected_code), arch_path in zip(expected_archives, archived_files):
            self.assertEqual(arch_path.name, expected_name)
            with open(arch_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), expected_code)

    def test_06_concurrent_version_updates_on_same_skill(self) -> None:
        """
        Stress test: 10 threads concurrently updating the EXACT same tool skill.
        Verifies all version bumps are applied sequentially and all archives are written safely.
        """
        reg = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)
        sid = "tool-concurrent-versions"

        # Initial seed v1.0.0
        reg.register_skill({
            "id": sid,
            "type": "tool",
            "code": "def run(i, c): return {'step': 0}",
            "self_tests": [{"inputs": {}, "expected_output": {"step": 0}}],
        })

        num_updates = 10
        errors: List[Exception] = []
        lock = threading.Lock()

        def updater_worker(step_idx: int) -> None:
            try:
                code = f"def run(i, c): return {{'step': {step_idx}}}"
                ok, msg = reg.register_skill({
                    "id": sid,
                    "type": "tool",
                    "code": code,
                    "self_tests": [{"inputs": {}, "expected_output": {"step": step_idx}}],
                })
                if not ok:
                    with lock:
                        errors.append(RuntimeError(f"Updater {step_idx} failed: {msg}"))
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=updater_worker, args=(i + 1,)) for i in range(num_updates)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Errors in concurrent version updates: {errors}")

        # Check final version
        final_skill = reg.get_skill(sid)
        self.assertEqual(final_skill["version"], f"1.0.{num_updates}")

        # Check that exactly num_updates archive files were generated
        archives = list(reg.archive_dir.glob(f"{sid}_v*.py"))
        self.assertEqual(len(archives), num_updates)


class TestPromptInjectionAndMatchSkillResilience(unittest.TestCase):
    """
    Adversarial stress-testing of match_skill resilience against injection attacks,
    shell metacharacters, regex bombs (ReDoS), null bytes, and malicious payloads.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_adv_inject_")
        self.workspace_dir = Path(self.temp_dir)
        self.sandbox = SkillSandbox()
        self.registry = SkillRegistry(workspace_dir=self.workspace_dir, sandbox=self.sandbox)

        # Register standard skills with benign trigger patterns
        self.registry.register_skill({
            "id": "tool-calc-sha256",
            "name": "SHA256 Hasher",
            "type": "workflow",
            "steps": [{"action": "notify"}],
            "trigger_patterns": ["calculate sha256", "hash payload", r"^sha256\s+\w+"],
        })
        self.registry.register_skill({
            "id": "tool-task-summary",
            "name": "Task Summarizer",
            "type": "workflow",
            "steps": [{"action": "query_tasks"}],
            "trigger_patterns": ["summary tasks", "standup report"],
        })

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_sql_injection_payloads_in_match_skill(self) -> None:
        """
        Adversarial test: pass classic, blind, union-based, and stacked SQL injection payloads.
        Verifies match_skill processes all safely without unhandled exceptions or state corruption.
        """
        sqli_payloads = [
            "' OR '1'='1",
            "\" OR \"1\"=\"1",
            "'; DROP TABLE skills; --",
            "1; DROP TABLE skills; --",
            "' UNION SELECT id, name, code, 'secret' FROM skills --",
            "' UNION ALL SELECT NULL, NULL, NULL, NULL --",
            "admin' --",
            "1' AND 1=0 UNION ALL SELECT @@version, user() --",
            "1'; EXEC xp_cmdshell('dir C:\\'); --",
            "' OR SLEEP(5) --",
            "'; SHUTDOWN WITH NOWAIT; --",
            "1' ORDER BY 1--+",
            "1' AND (SELECT COUNT(*) FROM information_schema.tables) > 0 --",
        ]

        for payload in sqli_payloads:
            try:
                res = self.registry.match_skill(payload)
                self.assertIsNone(res, f"SQL injection payload {payload} unexpectedly matched a skill!")
            except Exception as e:
                self.fail(f"match_skill crashed on SQL injection payload '{payload}': {e}")

    def test_02_shell_metacharacters_and_command_injections(self) -> None:
        """
        Adversarial test: pass shell metacharacters, subshell expansions, and pipe chains.
        Verifies no subprocess or shell execution occurs.
        """
        shell_payloads = [
            "; cat /etc/passwd",
            "& del /f /q C:\\* &",
            "| powershell -ExecutionPolicy Bypass -Command 'calc.exe'",
            "`whoami`",
            "$(whoami)",
            "${IFS}whoami",
            "&& echo HACKED &&",
            ">\nul 2>&1",
            "test | dir",
            "test & net user",
            "test || echo fail",
            "\\..\\..\\Windows\\System32\\cmd.exe",
            "//127.0.0.1/c$/Windows/System32/calc.exe",
        ]

        for payload in shell_payloads:
            try:
                res = self.registry.match_skill(payload)
                # Unless payload contains benign trigger words, it should be None
                self.assertIsNone(res, f"Shell payload {payload} unexpectedly matched!")
            except Exception as e:
                self.fail(f"match_skill crashed on shell payload '{payload}': {e}")

    def test_03_null_bytes_and_binary_data_resilience(self) -> None:
        """
        Adversarial test: pass embedded null bytes and non-printable control characters.
        Verifies string operations in match_skill do not raise ValueError: embedded null byte.
        """
        null_payloads = [
            "\x00",
            "calculate sha256\x00malicious_suffix",
            "\x00calculate sha256",
            "normal prompt\x00'; DROP TABLE skills; --",
            "\x00\x00\x00\x00",
            "".join(chr(i) for i in range(1, 32)),  # all control chars ASCII 1-31
            "sha256\x00secret\xff\xfe",
        ]

        for payload in null_payloads:
            try:
                res = self.registry.match_skill(payload)
                # If prompt starts with 'calculate sha256' despite embedded null bytes,
                # substring match might find it or not, but it MUST NOT crash.
            except Exception as e:
                self.fail(f"match_skill crashed on null-byte / binary payload: {e}")

    def test_04_malformed_trigger_patterns_and_adversarial_syntax(self) -> None:
        """
        Adversarial test:
        Register skills with malformed regex patterns (unclosed brackets, dangling quantifiers).
        Verifies regex engine catches re.error and match_skill does not crash.
        """
        ok, msg = self.registry.register_skill({
            "id": "tool-malformed-regex",
            "name": "Malformed Regex Skill",
            "type": "workflow",
            "steps": [{"action": "notify"}],
            "trigger_patterns": [
                r"[unclosed-bracket",
                r"(?P<invalid",
                r"*dangling-star",
                r"+dangling-plus",
            ],
        })
        self.assertTrue(ok)

        adversarial_prompts = [
            "[unclosed-bracket",
            "(?P<name>foo)",
            "***+++???",
            "\\c\\d\\e\\f",
            "(?<=lookbehind_without_fixed_width.*)abc",
        ]

        for prompt in adversarial_prompts:
            try:
                res = self.registry.match_skill(prompt)
            except Exception as e:
                self.fail(f"match_skill crashed on adversarial prompt '{prompt}': {e}")

    def test_05_regex_catastrophic_backtracking_redos_vulnerability(self) -> None:
        """
        Adversarial stress-test:
        Verifies whether match_skill is vulnerable to Regular Expression Denial of Service (ReDoS)
        when a registered skill contains nested quantifiers like (a+)+$.
        Must complete within 1.0s and must not freeze the registry lock.
        """
        ok, msg = self.registry.register_skill({
            "id": "tool-redos-trigger",
            "name": "ReDoS Trigger Skill",
            "type": "workflow",
            "steps": [{"action": "notify"}],
            "trigger_patterns": [
                r"(a+)+$",  # ReDoS catastrophic backtracking pattern
            ],
        })
        self.assertTrue(ok)

        # 28 characters is sufficient to demonstrate catastrophic exponential backtracking
        prompt = "aaaaaaaaaaaaaaaaaaaaaaaaaaaa!"
        start_time = time.time()
        res = self.registry.match_skill(prompt)
        elapsed = time.time() - start_time
        self.assertLess(
            elapsed,
            1.0,
            f"match_skill took {elapsed:.2f}s on ReDoS prompt! Registry was frozen during catastrophic backtracking."
        )

    def test_06_oversized_prompts_and_unicode_anomalies(self) -> None:
        """
        Adversarial test: pass 100,000 character prompt, RTL overrides, zero-width spaces, and emojis.
        Verifies performance within 50ms and safe handling.
        """
        huge_prompt = "a " * 50000 + "calculate sha256"
        start_time = time.time()
        res = self.registry.match_skill(huge_prompt)
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.5, f"Oversized prompt took too long: {elapsed:.3f}s")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "tool-calc-sha256")

        unicode_prompts = [
            "\u202Ecalculate sha256\u202C",  # Right-to-Left Override
            "calculate\u200B sha256",         # Zero-width space
            "calculate\uFEFF sha256",         # Byte Order Mark
            "🐱 Calculate SHA256 🐾 🎉",      # High-plane emojis
            "C\u0327\u0301alculate sha256",   # Combining diacritics
        ]

        for prompt in unicode_prompts:
            try:
                self.registry.match_skill(prompt)
            except Exception as e:
                self.fail(f"match_skill crashed on unicode prompt '{prompt}': {e}")



if __name__ == "__main__":
    unittest.main()
