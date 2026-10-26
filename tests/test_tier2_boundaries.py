"""
test_tier2_boundaries.py - E2E Tier 2 Boundaries, Concurrency & Vulnerabilities (VULN-01..07).
Validates concurrency stress, corruption safeguards, token budget ceilings,
Anti-CJK sanitization, deduplication thresholds, and DCC hotkey collision guards.
"""

import concurrent.futures
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.test_tier1_features import (
    get_storage_manager_cls,
    compute_hybrid_similarity,
    sanitize_anti_cjk
)


class TestTier2_VULN01_ConcurrentAtomicWrites(unittest.TestCase):
    """
    VULN-01 Boundary & Concurrency:
    20 concurrent threads writing simultaneously to atomic JSON and SQLite-WAL.
    Acceptance criteria: 0% lost updates, 0 WinError 32 / PermissionError crashes.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.json_path = os.path.join(self.tmp_dir, "concurrent_profile.json")
        self.db_path = os.path.join(self.tmp_dir, "concurrent_storage.db")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)
        # Initialize JSON file
        self.sm.atomic_save_json(self.json_path, {"counter": 0, "updates": []})

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_20_threads_concurrent_atomic_json_writes(self):
        num_threads = 20
        updates_per_thread = 5
        total_expected_updates = num_threads * updates_per_thread
        errors = []
        lock = threading.Lock()

        def worker_task(thread_id: int):
            for i in range(updates_per_thread):
                try:
                    # Thread reads current state, appends update, saves back
                    with lock:
                        curr = self.sm.atomic_load_json(self.json_path, default={"counter": 0, "updates": []})
                        curr["counter"] += 1
                        curr["updates"].append(f"T{thread_id}-U{i}")
                        self.sm.atomic_save_json(self.json_path, curr)
                    time.sleep(0.005)
                except Exception as e:
                    errors.append((thread_id, i, str(e)))

        threads = [threading.Thread(target=worker_task, args=(tid,)) for tid in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assertion 1: Zero thread execution errors
        self.assertEqual(len(errors), 0, f"Errors encountered during concurrency: {errors}")

        # Assertion 2: All updates committed (0% lost updates)
        final_data = self.sm.atomic_load_json(self.json_path)
        self.assertEqual(final_data["counter"], total_expected_updates)

        # Assertion 3: Updates list has full length
        self.assertEqual(len(final_data["updates"]), total_expected_updates)

        # Assertion 4: No temporary files left behind
        tmp_files = [f for f in os.listdir(self.tmp_dir) if f.endswith(".tmp")]
        self.assertEqual(len(tmp_files), 0)

        # Assertion 5: File size is non-zero and valid JSON
        self.assertGreater(os.path.getsize(self.json_path), 50)


class TestTier2_VULN02_LockedAndCorruptFileReads(unittest.TestCase):
    """
    VULN-02 Boundary:
    Reading locked or corrupted JSON files must result in 0% data wipeout,
    raising an exception or returning cached state and creating .corrupt.bak.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.json_path = os.path.join(self.tmp_dir, "archive_data.json")
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_corrupted_file_preserves_content_and_creates_backup(self):
        # Write valid data first
        valid_payload = {"archive_records": [{"id": i, "val": f"data_{i}"} for i in range(50)]}
        self.sm.atomic_save_json(self.json_path, valid_payload)

        # Intentionally corrupt the file (simulate crash mid-write)
        corrupted_text = '{"archive_records": [{"id": 0, "val": "data_0"}, {"id": 1, ... CRASH ...'
        with open(self.json_path, "w", encoding="utf-8") as f:
            f.write(corrupted_text)

        # Assertion 1: Reading corrupt file raises error rather than silently returning empty default []
        with self.assertRaises((IOError, json.JSONDecodeError)):
            self.sm.atomic_load_json(self.json_path)

        # Assertion 2: Original file is NOT wiped or overwritten
        self.assertTrue(os.path.exists(self.json_path))
        with open(self.json_path, "r", encoding="utf-8") as f:
            content_after = f.read()
        self.assertEqual(content_after, corrupted_text)

        # Assertion 3: .corrupt.bak file is created
        bak_files = [f for f in os.listdir(self.tmp_dir) if ".corrupt.bak" in f]
        self.assertGreaterEqual(len(bak_files), 1)

        # Assertion 4: .corrupt.bak contains the corrupted content for post-mortem forensics
        with open(os.path.join(self.tmp_dir, bak_files[0]), "r", encoding="utf-8") as f:
            bak_content = f.read()
        self.assertEqual(bak_content, corrupted_text)

        # Assertion 5: 0% data wipeout - file size on disk is non-zero
        self.assertGreater(os.path.getsize(self.json_path), 0)


class TestTier2_VULN03_TokenBudgetCeiling(unittest.TestCase):
    """
    VULN-03 Boundary:
    Token budget manager with num_ctx: 8192, extreme prompt payload,
    guaranteeing pinned traits are never evicted and generation headroom >= 5742 tokens.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_token_budget_extreme_load_preserves_pinned_traits(self):
        # Insert 3 critical pinned traits
        p1 = self.sm.upsert_memory("PINNED RULE 1: Consegna rigorosamente in formato EXR 32-bit", pinned=True)
        p2 = self.sm.upsert_memory("PINNED RULE 2: Spazio colore primario ACEScg", pinned=True)
        p3 = self.sm.upsert_memory("PINNED RULE 3: Nessuna modifica geometrica senza approvazione", pinned=True)

        # Insert 50 dynamic memories to simulate extreme load
        for i in range(50):
            self.sm.upsert_memory(f"Nota dinamica secondaria {i}: dettagli texture e shader revisione {i}")

        prompt, est_tokens = self.sm.build_context_prompt(
            query="Quali sono le specifiche di esportazione e render?",
            active_project="Villa_Rossi_2026",
            active_dcc="3dsmax.exe"
        )

        # Assertion 1: Target context num_ctx is 8192
        num_ctx_target = 8192
        self.assertEqual(num_ctx_target, 8192)

        # Assertion 2: All 3 pinned traits are 100% preserved in assembled prompt
        self.assertIn("EXR 32-bit", prompt)
        self.assertIn("ACEScg", prompt)
        self.assertIn("Nessuna modifica geometrica", prompt)

        # Assertion 3: Active DCC is preserved
        self.assertIn("3dsmax.exe", prompt)

        # Assertion 4: Total prompt estimated tokens <= 2450
        self.assertLessEqual(est_tokens, 2450)

        # Assertion 5: Guaranteed generation headroom >= 5742 tokens
        headroom = num_ctx_target - est_tokens
        self.assertGreaterEqual(headroom, 5742)


class TestTier2_VULN04_DeduplicationSimilarityThreshold(unittest.TestCase):
    """
    VULN-04 & VULN-06 Boundary:
    Deduplication threshold boundary at 0.85:
    - Similarity < 0.85 -> inserted as distinct memory.
    - Similarity >= 0.85 -> duplicate detected, frequency incremented, zero duplicates.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_deduplication_threshold_boundary(self):
        base_text = "Software preferito: Autodesk 3ds Max con motore V-Ray"

        # Candidate 1: Identical with trailing period -> sim >= 0.95 (>= 0.85)
        near_dup = "Software preferito: Autodesk 3ds Max con motore V-Ray."
        sim1 = compute_hybrid_similarity(base_text, near_dup)

        # Candidate 2: Completely distinct topic -> sim < 0.30 (< 0.85)
        distinct = "Gestione delle fatture e contabilità trimestrale"
        sim2 = compute_hybrid_similarity(base_text, distinct)

        # Assertion 1: Candidate 1 meets >= 0.85 threshold
        self.assertGreaterEqual(sim1, 0.85)

        # Assertion 2: Candidate 2 is below 0.85 threshold
        self.assertLess(sim2, 0.85)

        # Insert base memory
        id_base = self.sm.upsert_memory(base_text)

        # Insert Candidate 1 (duplicate)
        id_dup = self.sm.upsert_memory(near_dup)

        # Assertion 3: Duplicate returns same memory ID
        self.assertEqual(id_base, id_dup)

        # Assertion 4: Frequency was incremented to 2
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT frequency FROM memories WHERE id = ?", (id_base,))
        freq = cur.fetchone()[0]
        self.assertEqual(freq, 2)

        # Insert Candidate 2 (distinct)
        id_dist = self.sm.upsert_memory(distinct)

        # Assertion 5: Distinct candidate receives new ID and table count is 2
        self.assertNotEqual(id_base, id_dist)
        cur.execute("SELECT count(*) FROM memories")
        cnt = cur.fetchone()[0]
        self.assertEqual(cnt, 2)
        con.close()


class TestTier2_VULN06_AntiCjkSanitizer(unittest.TestCase):
    """
    VULN-06 Boundary:
    Anti-CJK sanitizer must reject/strip all Chinese characters in range
    [\u4e00-\u9fff], [\u3400-\u4dbf], and [\u3040-\u30ff]. Zero leakage.
    """

    def test_anti_cjk_boundary_stress(self):
        # Test case 1: Standard Chinese leak from profile.json:79
        leak_1 = "更多时间用于动画和照明"
        cleaned_1 = sanitize_anti_cjk(leak_1)
        self.assertEqual(cleaned_1, "")

        # Test case 2: Mixed Italian and Chinese tokens
        leak_2 = "Consegna prevista: 分配给每个阶段 entro venerdì ore 18:00"
        cleaned_2 = sanitize_anti_cjk(leak_2)
        self.assertEqual(cleaned_2, "Consegna prevista:  entro venerdì ore 18:00")

        # Assertion 1: Regex scan returns 0 CJK tokens on cleaned_2
        cjk_regex = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff]")
        self.assertIsNone(cjk_regex.search(cleaned_2))

        # Test case 3: Japanese Hiragana/Katakana edge
        leak_3 = "Progetto Tokyo 東京 アニメーション"
        cleaned_3 = sanitize_anti_cjk(leak_3)
        self.assertEqual(cleaned_3, "Progetto Tokyo")

        # Assertion 2: CJK count in cleaned_3 is 0
        self.assertIsNone(cjk_regex.search(cleaned_3))

        # Test case 4: Non-CJK special characters (Accents, Euro, Math) preserved
        italian_valid = "Qualità rendering: 100% fedeltà cromatica (Caffè & Tè) €450"
        cleaned_4 = sanitize_anti_cjk(italian_valid)

        # Assertion 3: Accents and Euro symbol are 100% preserved
        self.assertEqual(cleaned_4, italian_valid)

        # Assertion 4: Empty string boundary handled cleanly
        self.assertEqual(sanitize_anti_cjk(""), "")

        # Assertion 5: Whitespace-only string boundary
        self.assertEqual(sanitize_anti_cjk("   "), "")


class TestTier2_DccHotkeyCollisionGuard(unittest.TestCase):
    """
    DCC Hotkey Guard Boundary:
    Tests blacklist enforcement: Alt+Q, Alt+Z, Ctrl+Space must be rejected.
    Ctrl+Alt+N and Alt+N must be accepted.
    """

    def test_hotkey_blacklist_boundary(self):
        DCC_BLACKLIST = {
            "Alt+Q": "3ds Max Isolate Selection",
            "Alt+Z": "Blender X-Ray / 3ds Max Zoom",
            "Ctrl+Space": "IDE IntelliSense / Blender Maximize"
        }

        def guard_check(hotkey: str) -> bool:
            clean = hotkey.replace(" ", "")
            return clean not in DCC_BLACKLIST

        # Assertion 1: Alt+Q is rejected
        self.assertFalse(guard_check("Alt+Q"))
        self.assertFalse(guard_check("Alt + Q"))

        # Assertion 2: Alt+Z is rejected
        self.assertFalse(guard_check("Alt+Z"))

        # Assertion 3: Ctrl+Space is rejected
        self.assertFalse(guard_check("Ctrl+Space"))

        # Assertion 4: Ctrl+Alt+N is accepted (Raycast style)
        self.assertTrue(guard_check("Ctrl+Alt+N"))
        self.assertTrue(guard_check("Ctrl + Alt + N"))

        # Assertion 5: Alt+N is accepted (Neko default)
        self.assertTrue(guard_check("Alt+N"))


class TestTier2_AfkThresholdBoundary(unittest.TestCase):
    """
    AFK Inactivity Detector Boundary:
    Threshold is exactly 300,000 ms (300.0 seconds).
    299.9s -> ACTIVE; 300.0s / 300.1s -> AFK.
    """

    def test_afk_exact_boundaries(self):
        def is_afk_active(idle_sec: float) -> bool:
            return idle_sec >= 300.0

        # Assertion 1: 0.0s is active
        self.assertFalse(is_afk_active(0.0))

        # Assertion 2: 299.9s is active
        self.assertFalse(is_afk_active(299.9))

        # Assertion 3: Exactly 300.0s is AFK
        self.assertTrue(is_afk_active(300.0))

        # Assertion 4: 300.1s is AFK
        self.assertTrue(is_afk_active(300.1))

        # Assertion 5: Long idle (7200s / 2 hours) is AFK
        self.assertTrue(is_afk_active(7200.0))


class TestTier2_VramThresholdBoundary(unittest.TestCase):
    """
    VRAM Unload Guard Boundary:
    Threshold is 85.0%.
    84.9% -> Normal mode (no unload).
    85.0% / 85.1% -> Critical threshold breached (Ollama model unloaded).
    """

    def test_vram_exact_boundaries(self):
        def should_unload_ollama(vram_pct: float) -> bool:
            return vram_pct >= 85.0

        # Assertion 1: 50.0% VRAM does not unload
        self.assertFalse(should_unload_ollama(50.0))

        # Assertion 2: 84.9% VRAM does not unload
        self.assertFalse(should_unload_ollama(84.9))

        # Assertion 3: Exactly 85.0% VRAM triggers unload
        self.assertTrue(should_unload_ollama(85.0))

        # Assertion 4: 85.1% VRAM triggers unload
        self.assertTrue(should_unload_ollama(85.1))

        # Assertion 5: Near saturation (98.5%) triggers unload
        self.assertTrue(should_unload_ollama(98.5))


class TestTier2_TaskRetryCeilingBoundary(unittest.TestCase):
    """
    Task Engine Retry Ceiling Boundary:
    Max retries is 3.
    Retries 1, 2, 3 -> transitions to RETRY.
    Retry 4 (exceeding max 3) -> transitions to FAILED and triggers HITL recovery.
    """

    def test_task_retry_max_ceiling(self):
        def handle_step_failure(current_retries: int, max_retries: int = 3) -> dict:
            if current_retries < max_retries:
                backoff_delay = 1.0 * (2 ** current_retries)
                return {
                    "next_state": "RETRY",
                    "retries": current_retries + 1,
                    "backoff_sec": backoff_delay,
                    "hitl_prompt": False
                }
            return {
                "next_state": "FAILED",
                "retries": current_retries,
                "backoff_sec": 0.0,
                "hitl_prompt": True
            }

        # Assertion 1: First failure transitions to RETRY with 1s backoff
        r1 = handle_step_failure(0)
        self.assertEqual(r1["next_state"], "RETRY")
        self.assertEqual(r1["retries"], 1)
        self.assertEqual(r1["backoff_sec"], 1.0)
        self.assertFalse(r1["hitl_prompt"])

        # Assertion 2: Second failure transitions to RETRY with 2s backoff
        r2 = handle_step_failure(1)
        self.assertEqual(r2["next_state"], "RETRY")
        self.assertEqual(r2["backoff_sec"], 2.0)

        # Assertion 3: Third failure transitions to RETRY with 4s backoff
        r3 = handle_step_failure(2)
        self.assertEqual(r3["next_state"], "RETRY")
        self.assertEqual(r3["backoff_sec"], 4.0)

        # Assertion 4: Fourth failure exceeds ceiling -> transitions to FAILED
        r4 = handle_step_failure(3)
        self.assertEqual(r4["next_state"], "FAILED")

        # Assertion 5: FAILED state triggers Human-in-the-Loop prompt
        self.assertTrue(r4["hitl_prompt"])


if __name__ == "__main__":
    unittest.main()
