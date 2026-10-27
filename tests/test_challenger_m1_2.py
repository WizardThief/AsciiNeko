"""
tests/test_challenger_m1_2.py
=============================
Empirical Stress & Adversarial Test Suite for Milestone 1 (Instinct Engine v2.1).
Written by challenger_m1_2.

Focus Areas:
1. Concurrency & Storage:
   - Multithreaded concurrent writes to AtomicJsonStore (same instance and independent instances).
   - High-throughput mixed concurrent readers and writers (ensuring zero partial/malformed JSON reads).
   - Cross-process concurrent writes using subprocesses contending on the same file with OS/file locks.
2. Corrupt File Recovery:
   - Malformed/truncated JSON detection, raising StorageCorruptError rather than wiping data.
   - Forensic copy creation: verified .corrupt.bak exists and matches corrupt payload byte-for-byte.
   - Binary non-UTF8 payload corruption detection and .corrupt.bak preservation.
   - Clean recovery verification: saving valid data clears error state, subsequent loads succeed, forensic backup retained.
3. Token Budget Manager Stress (50+ Instincts):
   - fit_instincts with 75+ instincts: strictly bounded by BUDGET_INSTINCTS (300).
   - High-confidence global instincts (confidence >= 0.55) prioritized over lower confidence.
   - Low-confidence global instincts (< 0.55) strictly filtered out.
   - Active project instincts filtered and prioritized appropriately.
   - Tie-breaking by use_count when confidence is identical.
   - Strict adherence to tight token caps (e.g., available_tokens=50, 100).
   - Context breakdown telemetry consistency.
4. Data Hygiene:
   - Strict verification that production bridge/data/ is completely untouched.
"""

import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone

# Ensure repo root and bridge/ are on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from bridge.storage_manager import (
    StorageManager,
    AtomicJsonStore,
    StorageCorruptError,
    StorageLockError,
    StorageError,
)
from bridge.retrieval import (
    TokenBudgetManager,
    BUDGET_INSTINCTS,
    estimate_tokens,
)
from bridge.instinct_engine import (
    InstinctEngine,
    CONFIDENCE_MIN,
    CONFIDENCE_MAX,
)


class TestAtomicJsonStoreConcurrencyStress(unittest.TestCase):
    """Adversarial stress-testing of AtomicJsonStore concurrency across threads and processes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)

    def tearDown(self):
        try:
            self.sm.close_connection()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_multithreaded_concurrent_writes_same_store(self):
        """Stress: 10 threads concurrently writing to the same AtomicJsonStore instance."""
        target_file = os.path.join(self.data_dir, "concurrent_shared_store.json")
        store = AtomicJsonStore(target_file, default=[], storage_manager=self.sm)

        errors = []
        writes_per_thread = 20
        num_threads = 10

        def worker(thread_idx):
            for i in range(writes_per_thread):
                try:
                    payload = {
                        "thread_idx": thread_idx,
                        "iteration": i,
                        "timestamp": time.time(),
                        "payload": [f"item_{thread_idx}_{i}_{x}" for x in range(20)],
                    }
                    success = store.save(payload)
                    if not success:
                        errors.append(f"Thread {thread_idx} write {i} returned False")
                    time.sleep(random.uniform(0.001, 0.003))
                except Exception as exc:
                    errors.append(f"Thread {thread_idx} write {i} exception: {exc}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Encountered write errors: {errors[:5]}")

        # Verify final file is valid JSON and not corrupt
        self.assertTrue(os.path.exists(target_file))
        data = store.load()
        self.assertIsInstance(data, dict)
        self.assertIn("thread_idx", data)
        self.assertIn("iteration", data)
        self.assertIn("payload", data)
        self.assertEqual(len(data["payload"]), 20)

    def test_multithreaded_concurrent_writes_independent_stores(self):
        """Stress: 8 threads, each with its own StorageManager and AtomicJsonStore on the same path."""
        target_file = os.path.join(self.data_dir, "concurrent_independent_stores.json")
        errors = []
        writes_per_thread = 20
        num_threads = 8

        def worker(thread_idx):
            sm_local = StorageManager(data_dir=self.data_dir)
            store_local = AtomicJsonStore(target_file, default={}, storage_manager=sm_local)
            try:
                for i in range(writes_per_thread):
                    payload = {
                        "writer_id": thread_idx,
                        "step": i,
                        "data": f"content_block_{thread_idx}_{i}" * 10,
                    }
                    store_local.save(payload)
                    time.sleep(random.uniform(0.001, 0.004))
            except Exception as exc:
                errors.append(f"Independent store thread {thread_idx} exception: {exc}")
            finally:
                sm_local.close_connection()

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Independent store errors: {errors[:5]}")

        # Final inspection
        final_store = AtomicJsonStore(target_file, default={}, storage_manager=self.sm)
        final_data = final_store.load()
        self.assertIsInstance(final_data, dict)
        self.assertIn("writer_id", final_data)
        self.assertIn("step", final_data)

    def test_mixed_concurrent_readers_and_writers(self):
        """Stress: High-concurrency mixed readers and writers. Zero malformed JSON reads allowed."""
        target_file = os.path.join(self.data_dir, "mixed_rw_stress.json")

        # Seed initial content
        initial_store = AtomicJsonStore(target_file, storage_manager=self.sm)
        initial_store.save({"status": "seeded", "records": list(range(100)), "valid": True})

        read_errors = []
        write_errors = []
        reads_count = [0]
        writes_count = [0]
        stop_event = threading.Event()

        def writer(w_id):
            sm = StorageManager(data_dir=self.data_dir)
            store = AtomicJsonStore(target_file, storage_manager=sm)
            step = 0
            while not stop_event.is_set():
                step += 1
                payload = {
                    "writer": w_id,
                    "step": step,
                    "records": [f"rec_{w_id}_{step}_{j}" for j in range(random.randint(10, 50))],
                    "valid": True,
                }
                saved = False
                for attempt in range(3):
                    try:
                        store.save(payload)
                        saved = True
                        writes_count[0] += 1
                        break
                    except StorageLockError:
                        time.sleep(random.uniform(0.01, 0.03))
                    except Exception as exc:
                        write_errors.append((w_id, exc))
                        break
                time.sleep(random.uniform(0.002, 0.005))
            sm.close_connection()

        def reader(r_id):
            sm = StorageManager(data_dir=self.data_dir)
            store = AtomicJsonStore(target_file, storage_manager=sm)
            while not stop_event.is_set():
                try:
                    data = store.load()
                    reads_count[0] += 1
                    if not isinstance(data, dict) or not data.get("valid"):
                        read_errors.append((r_id, "Incomplete or malformed read payload"))
                except Exception as exc:
                    read_errors.append((r_id, exc))
                time.sleep(random.uniform(0.002, 0.005))
            sm.close_connection()

        num_w = 4
        num_r = 4
        w_threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_w)]
        r_threads = [threading.Thread(target=reader, args=(i,)) for i in range(num_r)]

        for t in w_threads + r_threads:
            t.start()

        # Run under contention for 1.5 seconds
        time.sleep(1.5)
        stop_event.set()

        for t in w_threads + r_threads:
            t.join()

        self.assertGreater(writes_count[0], 20, "Expected at least 20 writes to complete")
        self.assertGreater(reads_count[0], 100, "Expected at least 100 reads to complete")
        self.assertEqual(len(write_errors), 0, f"Fatal write errors occurred: {write_errors[:5]}")
        self.assertEqual(len(read_errors), 0, f"Read errors / malformed JSON reads occurred: {read_errors[:5]}")

    def test_cross_process_concurrent_writes(self):
        """Stress: Multiple independent OS processes contending on the same file via AtomicJsonStore."""
        target_file = os.path.join(self.data_dir, "cross_process_stress.json")

        initial_store = AtomicJsonStore(target_file, storage_manager=self.sm)
        initial_store.save({"init": True, "count": 0})

        child_script = """
import sys, os, time, random
from bridge.storage_manager import StorageManager, AtomicJsonStore

p_id = int(sys.argv[1])
file_path = sys.argv[2]
data_dir = sys.argv[3]
iterations = int(sys.argv[4])

sm = StorageManager(data_dir=data_dir)
store = AtomicJsonStore(file_path, storage_manager=sm)

for i in range(iterations):
    payload = {
        "proc_id": p_id,
        "iter": i,
        "time": time.time(),
        "payload": [f"blob_{p_id}_{i}_{k}" for k in range(15)],
    }
    store.save(payload)
    time.sleep(random.uniform(0.002, 0.006))

sm.close_connection()
sys.exit(0)
"""
        num_procs = 4
        iters_per_proc = 15
        processes = []

        for p in range(num_procs):
            proc = subprocess.Popen(
                [sys.executable, "-c", child_script, str(p), target_file, self.data_dir, str(iters_per_proc)],
                cwd=REPO_ROOT,
            )
            processes.append(proc)

        exit_codes = [p.wait(timeout=15) for p in processes]
        self.assertTrue(all(code == 0 for code in exit_codes), f"Process failure exit codes: {exit_codes}")

        # Check final integrity
        final_data = initial_store.load()
        self.assertIsInstance(final_data, dict)
        self.assertIn("proc_id", final_data)
        self.assertIn("iter", final_data)
        self.assertIn("payload", final_data)
        self.assertEqual(len(final_data["payload"]), 15)


class TestCorruptFileForensicsAndCleanRecovery(unittest.TestCase):
    """Verifies that corrupted JSON creates .corrupt.bak without wipeout, and recovers cleanly."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)

    def tearDown(self):
        try:
            self.sm.close_connection()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_corrupt_file_creates_backup_and_preserves_content(self):
        """Corrupt JSON raises StorageCorruptError, preserves original file, and creates .corrupt.bak."""
        target_file = os.path.join(self.data_dir, "instincts_corrupt.json")
        store = AtomicJsonStore(target_file, storage_manager=self.sm)

        # Write valid data first
        valid_payload = [{"id": "inst_1", "trigger": "test", "action": "test_action"}]
        store.save(valid_payload)
        self.assertEqual(store.load(), valid_payload)

        # Intentionally corrupt the file with truncated JSON
        corrupt_text = '{"instincts": [{"id": "inst_corrupt", "trigger": "mid-write crash...'
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(corrupt_text)

        # Reading must raise StorageCorruptError (not silently returning default)
        with self.assertRaises(StorageCorruptError) as ctx:
            store.load()
        self.assertIn("CORRUPT JSON DETECTED", str(ctx.exception))

        # Original file must NOT be wiped or deleted
        self.assertTrue(os.path.exists(target_file))
        with open(target_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), corrupt_text)

        # A forensic copy matching *.corrupt.bak must exist
        bak_files = [f for f in os.listdir(self.data_dir) if f.endswith(".corrupt.bak")]
        self.assertGreaterEqual(len(bak_files), 1, f"Expected .corrupt.bak file, found: {bak_files}")

        # The forensic backup must contain the exact corrupted content
        bak_full_path = os.path.join(self.data_dir, bak_files[0])
        with open(bak_full_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), corrupt_text)

    def test_corrupt_file_clean_recovery_on_save(self):
        """After corruption is caught and archived, saving valid data cleanly recovers the store."""
        target_file = os.path.join(self.data_dir, "recoverable_store.json")
        store = AtomicJsonStore(target_file, storage_manager=self.sm)

        # Seed corrupted file
        with open(target_file, "w", encoding="utf-8") as f:
            f.write("MALFORMED JSON {{{")

        # Load fails and archives forensic copy
        with self.assertRaises(StorageCorruptError):
            store.load()

        bak_files = [f for f in os.listdir(self.data_dir) if f.endswith(".corrupt.bak")]
        self.assertEqual(len(bak_files), 1)

        # Clean recovery: caller saves valid data
        recovered_payload = [
            {"id": "inst_rec_01", "trigger": "On compile", "action": "cargo test"},
            {"id": "inst_rec_02", "trigger": "On render", "action": "denoise enable"},
        ]
        save_ok = store.save(recovered_payload)
        self.assertTrue(save_ok)

        # Subsequent loads succeed cleanly without exception
        loaded = store.load()
        self.assertEqual(loaded, recovered_payload)

        # Forensic copy is still preserved for audits
        self.assertTrue(os.path.exists(os.path.join(self.data_dir, bak_files[0])))

    def test_non_utf8_binary_garbage_forensics_and_recovery(self):
        """Random binary garbage triggers Unicode/Decode error handling and creates .corrupt.bak."""
        target_file = os.path.join(self.data_dir, "binary_garbage.json")
        store = AtomicJsonStore(target_file, storage_manager=self.sm)

        # Write invalid binary bytes
        garbage_bytes = b"\x80\xff\xfe\x00\x01\x02\x03\x04\x05\x06\xaa\xbb\xcc"
        with open(target_file, "wb") as f:
            f.write(garbage_bytes)

        # Must raise StorageCorruptError
        with self.assertRaises(StorageError):
            store.load()

        # Check backup created
        bak_files = [f for f in os.listdir(self.data_dir) if f.endswith(".corrupt.bak")]
        self.assertGreaterEqual(len(bak_files), 1)

        # Verify binary bytes preserved in backup
        with open(os.path.join(self.data_dir, bak_files[0]), "rb") as f:
            self.assertEqual(f.read(), garbage_bytes)

        # Clean recovery with new payload
        clean_data = {"recovered": True, "ts": time.time()}
        self.assertTrue(store.save(clean_data))
        self.assertEqual(store.load(), clean_data)


class TestTokenBudgetManagerStress50PlusInstincts(unittest.TestCase):
    """Stress tests TokenBudgetManager.fit_instincts when 50+ instincts exist."""

    def setUp(self):
        self.tbm = TokenBudgetManager()

    def test_fit_instincts_with_75_instincts_never_exceeds_budget_instincts(self):
        """Stress: 75 diverse instincts must never exceed BUDGET_INSTINCTS (300 tokens)."""
        instincts = []
        for i in range(75):
            conf = round(0.30 + (i % 65) * 0.01, 2)
            scope = "global" if i % 2 == 0 else "project"
            proj_id = "proj_target" if i % 4 == 1 else f"proj_other_{i % 5}"
            instincts.append({
                "id": f"inst_{i:03d}",
                "trigger": f"Trigger rule for component {i} requiring specific pipeline actions and validation",
                "action": f"Action requirement for step {i} ensuring standard compliance and output formatting",
                "confidence": conf,
                "scope": scope,
                "project_id": proj_id if scope == "project" else None,
                "domain": "pipeline" if i % 3 == 0 else "client-rule",
                "use_count": i * 3,
            })

        # Fit instincts with active_project_id = 'proj_target'
        selected, total_tokens = self.tbm.fit_instincts(
            instincts,
            active_project_id="proj_target",
            available_tokens=None,
            as_tuple=True,
        )

        self.assertGreater(len(selected), 0, "At least one instinct should fit")
        self.assertLessEqual(
            total_tokens,
            BUDGET_INSTINCTS,
            f"Allocated tokens {total_tokens} exceeded BUDGET_INSTINCTS ({BUDGET_INSTINCTS})",
        )

        # Verify all selected items satisfy eligibility criteria
        for item in selected:
            if item["scope"] == "global":
                self.assertGreaterEqual(
                    item["confidence"],
                    0.55,
                    f"Global instinct with confidence < 0.55 included: {item}",
                )
            elif item["scope"] == "project":
                self.assertEqual(
                    item["project_id"],
                    "proj_target",
                    f"Project instinct for inactive project included: {item}",
                )

    def test_fit_instincts_prioritizes_high_confidence_global(self):
        """Verify high-confidence global instincts (0.90-0.95) are prioritized over lower confidence (0.55-0.65)."""
        instincts = []
        # 30 low-confidence eligible global instincts (0.55 to 0.65)
        for i in range(30):
            instincts.append({
                "id": f"inst_low_{i:02d}",
                "trigger": f"Low priority trigger {i}",
                "action": f"Low priority action {i}",
                "confidence": 0.55 + (i % 10) * 0.01,
                "scope": "global",
                "use_count": 5,
            })

        # 25 high-confidence eligible global instincts (0.90 to 0.95)
        for i in range(25):
            instincts.append({
                "id": f"inst_high_{i:02d}",
                "trigger": f"High priority trigger {i}",
                "action": f"High priority action {i}",
                "confidence": 0.90 + (i % 6) * 0.01,
                "scope": "global",
                "use_count": 50,
            })

        selected, tokens = self.tbm.fit_instincts(instincts, as_tuple=True)
        self.assertLessEqual(tokens, BUDGET_INSTINCTS)

        # All selected instincts must be from the high-confidence pool
        selected_ids = [s["id"] for s in selected]
        self.assertTrue(
            all(i_id.startswith("inst_high_") for i_id in selected_ids),
            f"Expected only high-confidence instincts to fit within budget, got: {selected_ids}",
        )

        # Verify descending order by confidence
        confs = [s["confidence"] for s in selected]
        for idx in range(len(confs) - 1):
            self.assertGreaterEqual(
                confs[idx],
                confs[idx + 1],
                f"Selected instincts not sorted in descending confidence: {confs}",
            )

    def test_fit_instincts_strictly_filters_unqualified_instincts(self):
        """Global instincts with confidence < 0.55 and mismatched project instincts must be strictly excluded."""
        instincts = [
            {"id": "g_low_1", "trigger": "t1", "action": "a1", "confidence": 0.30, "scope": "global"},
            {"id": "g_low_2", "trigger": "t2", "action": "a2", "confidence": 0.54, "scope": "global"},
            {"id": "p_mismatch", "trigger": "t3", "action": "a3", "confidence": 0.95, "scope": "project", "project_id": "other_proj"},
            {"id": "p_no_active", "trigger": "t4", "action": "a4", "confidence": 0.95, "scope": "project", "project_id": None},
            {"id": "g_valid", "trigger": "t5", "action": "a5", "confidence": 0.55, "scope": "global"},
        ]

        selected = self.tbm.fit_instincts(instincts, active_project_id="my_proj")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["id"], "g_valid")

    def test_fit_instincts_tie_breaking_by_use_count(self):
        """When multiple instincts share identical confidence, the one with higher use_count is prioritized."""
        instincts = [
            {"id": "inst_low_use", "trigger": "Trigger Alpha", "action": "Action Alpha", "confidence": 0.85, "scope": "global", "use_count": 2},
            {"id": "inst_high_use", "trigger": "Trigger Beta", "action": "Action Beta", "confidence": 0.85, "scope": "global", "use_count": 150},
            {"id": "inst_mid_use", "trigger": "Trigger Gamma", "action": "Action Gamma", "confidence": 0.85, "scope": "global", "use_count": 45},
        ]

        # Allocate tight tokens so only 1 instinct fits
        selected, tokens = self.tbm.fit_instincts(instincts, available_tokens=15, as_tuple=True)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["id"], "inst_high_use")

    def test_fit_instincts_tight_token_caps(self):
        """available_tokens < 300 must strictly bound the total tokens returned."""
        instincts = [
            {
                "id": f"i_{i}",
                "trigger": f"Short trigger {i}",
                "action": f"Short action {i}",
                "confidence": 0.80,
                "scope": "global",
                "use_count": 1,
            }
            for i in range(50)
        ]

        # Test cap of 50 tokens
        sel_50, tok_50 = self.tbm.fit_instincts(instincts, available_tokens=50, as_tuple=True)
        self.assertLessEqual(tok_50, 50)
        self.assertGreater(len(sel_50), 0)

        # Test cap of 120 tokens
        sel_120, tok_120 = self.tbm.fit_instincts(instincts, available_tokens=120, as_tuple=True)
        self.assertLessEqual(tok_120, 120)
        self.assertGreater(len(sel_120), len(sel_50))

        # Test available_tokens > 300 caps at BUDGET_INSTINCTS (300)
        sel_1000, tok_1000 = self.tbm.fit_instincts(instincts, available_tokens=1000, as_tuple=True)
        self.assertLessEqual(tok_1000, BUDGET_INSTINCTS)

    def test_compute_context_breakdown_telemetry(self):
        """Verify context budget breakdown accurately calculates instincts token telemetry."""
        instincts = [
            {"trigger": "Trigger 1", "action": "Action 1", "domain": "pipeline"},
            {"trigger": "Trigger 2", "action": "Action 2", "domain": "client-rule"},
        ]
        breakdown = self.tbm.compute_context_breakdown(
            system_prompt="You are Neko",
            profile={"name": "User"},
            skills=[],
            instincts=instincts,
            memories=[],
            projects=[],
            todos=[],
            chat_history=[],
        )

        self.assertIn("breakdown", breakdown)
        self.assertIn("instincts", breakdown["breakdown"])
        self.assertGreater(breakdown["breakdown"]["instincts"], 0)
        self.assertEqual(breakdown["total_budget"], 8192)
        self.assertIn("usage_pct", breakdown)
        self.assertIn("percentages", breakdown)


class TestProductionDataHygiene(unittest.TestCase):
    """Verifies that production bridge/data/ directory is completely pristine and untouched."""

    def test_bridge_data_directory_clean(self):
        """Ensure git status reports bridge/data is 100% clean with zero untracked/modified files."""
        result = subprocess.run(
            ["git", "status", "--porcelain", "bridge/data"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        output = result.stdout.strip()
        self.assertEqual(
            output,
            "",
            f"Production bridge/data/ has dirty state or uncommitted files:\n{output}",
        )


if __name__ == "__main__":
    unittest.main()
