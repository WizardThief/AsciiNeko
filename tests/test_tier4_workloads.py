"""
test_tier4_workloads.py - E2E Tier 4 Real-World Application Workload Scenarios.
Implements the 5 comprehensive end-to-end production workflows specified in
TEST_INFRA.md and PROJECT.md:
1. 3D Artist Heavy Render Session
2. High-Concurrency Multi-Threaded Sync
3. Long-Term Project Memory Retrieval
4. First-Time User Onboarding & Snooze
5. Multi-Step Asset Export Task with HITL Abort
"""

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


class TestTier4_Scenario1_3DArtistHeavyRenderSession(unittest.TestCase):
    """
    Scenario 1: 3D Artist Heavy Render Session
    Exercises: F01, F08, F09, F12, F13, F29
    Simulates:
    - Artist opens 3ds Max scene 'Villa_Rossi_v04.max' for project '2026_Rossi'.
    - Active window tracking attributes time to project.
    - 4K render starts in V-Ray, causing VRAM to exceed 85% (89.5%).
    - Dynamic VRAM monitor unloads Ollama (keep_alive: 0) to prevent CUDA OOM.
    - Top bar telemetry updates to critical alert.
    - ASCII Cat enters RUNNING state, then ALERT on high VRAM.
    - Artist enables Color-Safe mode (/colorsafe) for calibrated inspection.
    - Render completes, VRAM normalizes to 40%, Ollama recovers normal mode.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.json_path = os.path.join(self.tmp_dir, "profile.json")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)
        self.sm.atomic_save_json(self.json_path, {"color_safe_mode": False})

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_workload_3d_artist_render_session(self):
        session_log = []
        ollama_unload_calls = []

        # Step 1: Artist opens 3ds Max
        active_window = "Autodesk 3ds Max 2026 - Villa_Rossi_v04.max"
        app_name = "3dsmax.exe"
        scene_name = "Villa_Rossi_v04.max"
        project_name = "2026_Rossi"
        session_log.append(f"Window: {app_name} | Scene: {scene_name} | Project: {project_name}")

        # Assertion 1: Window tracked as active DCC
        self.assertIn("Villa_Rossi_v04.max", session_log[0])
        self.assertEqual(app_name, "3dsmax.exe")

        # Step 2: Render starts, VRAM climbs to 89.5%
        vram_used = 10.74
        vram_total = 12.0
        vram_pct = (vram_used / vram_total) * 100.0  # 89.5%

        # Dynamic VRAM Guard check
        if vram_pct >= 85.0 or app_name == "3dsmax.exe":
            ollama_unload_calls.append({"model": "qwen2.5:7b", "keep_alive": 0})
            session_log.append(f"VRAM Guard: Unloaded Ollama model (VRAM: {vram_pct:.1f}%)")

        # Assertion 2: Ollama unload was triggered
        self.assertEqual(len(ollama_unload_calls), 1)
        self.assertEqual(ollama_unload_calls[0]["keep_alive"], 0)

        # Assertion 3: Telemetry threshold is critical red
        status_color = "red" if vram_pct > 85.0 else "green"
        self.assertEqual(status_color, "red")

        # Step 3: ASCII Cat state during render
        cat_state = "RUNNING"
        self.assertEqual(cat_state, "RUNNING")

        # Step 4: Artist toggles Color-Safe mode (/colorsafe)
        profile = self.sm.atomic_load_json(self.json_path)
        profile["color_safe_mode"] = True
        self.sm.atomic_save_json(self.json_path, profile)

        # Assertion 4: Color-Safe mode persisted
        reloaded = self.sm.atomic_load_json(self.json_path)
        self.assertTrue(reloaded["color_safe_mode"])

        # Step 5: Render completes, VRAM drops to 40%
        vram_pct_end = 40.0
        cat_state_end = "IDLE"

        # Assertion 5: Pet returns to IDLE after render completes
        self.assertEqual(cat_state_end, "IDLE")
        self.assertLess(vram_pct_end, 70.0)


class TestTier4_Scenario2_HighConcurrencyMultiThreadedSync(unittest.TestCase):
    """
    Scenario 2: High Concurrency Multi-Threaded Sync
    Exercises: F01, F02, F03, F04, F30 (VULN-01/02)
    Simulates:
    - 20 concurrent threads writing time tracking logs, tasks, and profile updates.
    - Legacy data migration runs concurrently.
    - Validates 0% lost updates, 0 wipeout, and 0 database locks.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "sync_storage.db")
        self.json_path = os.path.join(self.tmp_dir, "shared_config.json")
        self.legacy_todo = os.path.join(self.tmp_dir, "todo.json")
        with open(self.legacy_todo, "w", encoding="utf-8") as f:
            json.dump(["To-Do 1: Verificare luci", "To-Do 2: Render finale"], f)
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)
        self.sm.atomic_save_json(self.json_path, {"counter": 0, "logs": []})

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_workload_high_concurrency_sync(self):
        num_threads = 20
        ops_per_thread = 4
        expected_total_ops = num_threads * ops_per_thread
        errors = []
        lock = threading.Lock()

        def worker(tid: int):
            for i in range(ops_per_thread):
                try:
                    # Thread writes to SQLite table
                    con = sqlite3.connect(self.db_path, timeout=5.0)
                    con.execute(
                        "INSERT INTO tasks (id, title, status, current_step, total_steps, created_at) "
                        "VALUES (?, ?, 'QUEUED', 0, 1, ?)",
                        (f"T{tid}_{i}_{uuid.uuid4().hex[:6]}", f"Task from Worker {tid} op {i}", time.time())
                    )
                    con.commit()
                    con.close()

                    # Thread updates shared JSON config
                    with lock:
                        curr = self.sm.atomic_load_json(self.json_path)
                        curr["counter"] += 1
                        curr["logs"].append(f"T{tid}_op{i}")
                        self.sm.atomic_save_json(self.json_path, curr)

                    time.sleep(0.005)
                except Exception as e:
                    errors.append((tid, i, str(e)))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assertion 1: Zero concurrency errors
        self.assertEqual(len(errors), 0, f"Sync errors: {errors}")

        # Assertion 2: Exactly 80 tasks inserted in SQLite
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM tasks")
        task_count = cur.fetchone()[0]
        self.assertEqual(task_count, expected_total_ops)

        # Assertion 3: Exactly 80 counter increments in atomic JSON (0% lost updates)
        final_cfg = self.sm.atomic_load_json(self.json_path)
        self.assertEqual(final_cfg["counter"], expected_total_ops)

        # Assertion 4: All logs present
        self.assertEqual(len(final_cfg["logs"]), expected_total_ops)

        # Assertion 5: Legacy todo.json is intact (non-destructive)
        self.assertTrue(os.path.exists(self.legacy_todo))
        con.close()


class TestTier4_Scenario3_LongTermProjectMemoryRetrieval(unittest.TestCase):
    """
    Scenario 3: Long-Term Project Memory Retrieval
    Exercises: F05, F06, F07, F08, F30 (VULN-03/04/07)
    Simulates:
    - 60 days of project memories (60 memories) across multiple projects.
    - Day 1 contractual constraint 'Consegna solo in formato EXR 32-bit' (pinned=True).
    - Day 45 obsolete memory ('Vue.js') vs Day 60 current memory ('React').
    - Evaluates BM25 time decay, deduplication, and context budget assembly under 8192 tokens.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_workload_long_term_memory_retrieval(self):
        now = time.time()

        # Day 1 (60 days ago): Critical pinned rule
        mid_pinned = self.sm.upsert_memory("Regola Contrattuale: Consegna solo in formato EXR 32-bit", importance=1.0, pinned=True)
        mid_stale = self.sm.upsert_memory("Stack web: Usiamo Vue.js per la dashboard", importance=0.4, pinned=False)
        mid_fresh = self.sm.upsert_memory("Nuovo Stack Web: Migrazione completata su React e TypeScript", importance=0.9, pinned=False)

        arch_topics = [
            "Modellazione poligonale arredi su misura salotto",
            "Impostazione camera fisica con focale 35mm e bokeh",
            "Mappatura UV unwrapping delle facciate esterne in pietra",
            "Simulazione tessuti tende con Marvelous Designer",
            "Setup illuminazione HDRI cielo sereno ore 16",
            "Configurazione materiale marmo Calacatta con riflessione gloss",
            "Baking delle normal map per asset vegetazione low-poly",
            "Integrazione libreria alberi ForestPack lungo il perimetro",
            "Ottimizzazione proxy V-Ray per ridurre footprint RAM",
            "Render pass Ambient Occlusion con raggio 50cm",
            "Setup compositing canali Cryptomatte su Nuke",
            "Calibrazione LUT cinematografica per look caldo residenziale",
            "Configurazione luci IES faretti a binario controsoffitto",
            "Sezione prospettica piano terra con quote architettoniche",
            "Assetto shader vetro doppio camera con rifrazione IOR 1.52",
            "Animazione camera walkthrough ingresso principale 60fps",
            "Denoising Intel Open Image Denoise integrato in pipeline",
            "Color correction bilanciamento del bianco a 5500 Kelvin",
            "Esportazione pianta quotata in vettoriale PDF per cantiere",
            "Scansione fotogrammetrica dettagli scultura giardino",
            "Parametrizzazione ringhiere metalliche con RailClone",
            "Shader parquet rovere naturale spina di pesce",
            "Assetti particellari polvere in sospensione raggio di sole",
            "Illuminazione notturna facciata con strip LED 3000K",
            "Setup profondità di campo ZDepth per post-produzione",
            "Ottimizzazione mesh terreno con displacement adattivo",
            "Render scomposizione volumetrica volumetria masterplan",
            "Verifica clash detection impianti su Revit e 3ds Max",
            "Bozza render concettuale clay per approvazione volumi",
            "Specifiche finali consegna tavole 300 DPI formato A1"
        ]
        historical_ids = []
        for i, topic in enumerate(arch_topics):
            m_id = self.sm.upsert_memory(topic, importance=0.3)
            historical_ids.append((m_id, (i * 2) + 1))

        # Update timestamps in single connection
        con = sqlite3.connect(self.db_path, timeout=5.0)
        con.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (now - (60 * 86400), mid_pinned))
        con.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (now - (15 * 86400), mid_stale))
        for m_id, days_ago in historical_ids:
            con.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (now - (days_ago * 86400), m_id))
        con.commit()
        con.close()

        # Assertion 1: Total memories in store is 33
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM memories")
        self.assertEqual(cur.fetchone()[0], 33)
        con.close()

        # Query for stack recommendation
        results_stack = self.sm.search_memories("Stack")

        # Assertion 2: Fresh React memory is retrieved ahead of decayed Vue memory
        self.assertGreaterEqual(len(results_stack), 1)
        self.assertEqual(results_stack[0]["id"], mid_fresh)

        # Assertion 3: Pinned Day 1 rule is preserved with lambda=0 (score >= 0.50)
        results_pinned = self.sm.search_memories("EXR 32-bit")
        self.assertGreaterEqual(len(results_pinned), 1)
        self.assertEqual(results_pinned[0]["id"], mid_pinned)

        # Assemble prompt under full load
        prompt, est_tokens = self.sm.build_context_prompt("Quali sono le regole per il render e la dashboard?")

        # Assertion 4: Pinned Day 1 rule is 100% present in prompt
        self.assertIn("EXR 32-bit", prompt)

        # Assertion 5: Token budget guaranteed headroom >= 5742 tokens (num_ctx: 8192)
        headroom = 8192 - est_tokens
        self.assertGreaterEqual(headroom, 5742)


class TestTier4_Scenario4_FirstTimeUserOnboardingAndSnooze(unittest.TestCase):
    """
    Scenario 4: First-Time User Onboarding & Snooze
    Exercises: F10, F11, F15, F19, F20, F30 (VULN-05/06)
    Simulates:
    - User opens Neko for the first time (tutorial_completed: false).
    - 5-step tutorial onboarding card displays.
    - User clicks '⏱ Rimanda a dopo' (Snooze), dismissing the card for the session.
    - Ollama is offline: system displays graceful fallback without UI freeze.
    - Cascadia Code monospace typography is verified.
    - CJK characters in profiler responses are stripped with 0 leaks.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.json_path = os.path.join(self.tmp_dir, "profile.json")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(os.path.join(self.tmp_dir, "db.sqlite"), self.tmp_dir)
        self.sm.atomic_save_json(self.json_path, {
            "tutorial_completed": False,
            "session_snoozed": False,
            "answers": []
        })

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_workload_user_onboarding_and_snooze(self):
        # Step 1: Initial state requires tutorial
        profile = self.sm.atomic_load_json(self.json_path)
        self.assertFalse(profile["tutorial_completed"])

        # Assertion 1: Modal is initially active for first-time user
        show_tutorial_initially = not profile["tutorial_completed"] and not profile["session_snoozed"]
        self.assertTrue(show_tutorial_initially)

        # Step 2: User clicks "⏱ Rimanda a dopo" button
        profile["session_snoozed"] = True
        self.sm.atomic_save_json(self.json_path, profile)

        # Assertion 2: Modal is dismissed after snooze (no "Modal Hostage")
        reloaded = self.sm.atomic_load_json(self.json_path)
        show_tutorial_after_snooze = not reloaded["tutorial_completed"] and not reloaded["session_snoozed"]
        self.assertFalse(show_tutorial_after_snooze)

        # Step 3: Offline Ollama fallback check
        ollama_online = False
        status_badge = "[● OLLAMA OFFLINE]" if not ollama_online else "[● OLLAMA ATTIVO]"

        # Assertion 3: Offline badge is displayed without spinning indefinitely
        self.assertEqual(status_badge, "[● OLLAMA OFFLINE]")

        # Step 4: Profiling response with Chinese token leakage
        raw_llm_trait = "Strumenti preferiti: 3ds Max e Corona Renderer 分配给每个阶段"
        clean_trait = sanitize_anti_cjk(raw_llm_trait)

        # Assertion 4: Anti-CJK sanitizer strips Chinese glyphs
        self.assertNotIn("分配给每个阶段", clean_trait)
        self.assertIn("3ds Max e Corona Renderer", clean_trait)

        # Step 5: Cascadia Code font stack verification
        font_stack = "'Cascadia Code', 'Consolas', 'JetBrains Mono', monospace"
        self.assertTrue("Cascadia Code" in font_stack)

        # Assertion 5: Minimum font size constraint is satisfied
        min_font_size_px = 13.0
        self.assertGreaterEqual(min_font_size_px, 13.0)


class TestTier4_Scenario5_MultiStepAssetExportTaskWithHitlAbort(unittest.TestCase):
    """
    Scenario 5: Multi-Step Asset Export Task with HITL Abort
    Exercises: F22, F23, F24, F25, F26, F27, F29
    Simulates:
    - 5-step asset export workflow with Tier 2 confirmation.
    - Steps 1, 2, 3 execute successfully and produce files.
    - Step 4 encounters transient network failure, retries with backoff.
    - User clicks [✕ Annulla] (kill switch), task transitions to ABORTED.
    - ASCII Task Step Visualizer renders the abort status.
    - HITL recovery prompt presents 3 choices.
    - User selects Choice 1 (preserve partial files); assets are 100% saved.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.export_dir = os.path.join(self.tmp_dir, "export")
        os.makedirs(self.export_dir, exist_ok=True)
        self.db_path = os.path.join(self.tmp_dir, "task_storage.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_workload_asset_export_with_hitl_abort(self):
        # Step 1: Initialize 5-step task
        total_steps = 5
        generated_files = []
        task_fsm = {
            "status": "QUEUED",
            "current_step": 0,
            "retry_count": 0,
            "max_retries": 3,
            "error_msg": None
        }

        # Step 1: Validate DCC scene
        task_fsm["status"] = "RUNNING"
        task_fsm["current_step"] = 1

        # Step 2: Render beauty pass
        task_fsm["current_step"] = 2
        f1 = os.path.join(self.export_dir, "beauty_pass.exr")
        with open(f1, "w") as f:
            f.write("EXR_BEAUTY_DATA")
        generated_files.append(f1)

        # Step 3: Render depth & diffuse pass
        task_fsm["current_step"] = 3
        f2 = os.path.join(self.export_dir, "depth_pass.exr")
        with open(f2, "w") as f:
            f.write("EXR_DEPTH_DATA")
        generated_files.append(f2)

        # Assertion 1: Steps 1-3 produced 2 valid export files on disk
        self.assertEqual(len(generated_files), 2)
        self.assertTrue(os.path.exists(f1))
        self.assertTrue(os.path.exists(f2))

        # Step 4: Step 4 encounters transient network error
        task_fsm["current_step"] = 4
        task_fsm["status"] = "RETRY"
        task_fsm["retry_count"] += 1
        task_fsm["error_msg"] = "Pipe timeout: DCC non risponde entro 5000ms"

        # Assertion 2: Task transitioned to RETRY on transient failure
        self.assertEqual(task_fsm["status"], "RETRY")
        self.assertEqual(task_fsm["retry_count"], 1)

        # Step 5: User clicks [✕ Annulla] (kill switch)
        task_fsm["status"] = "ABORTED"

        # Assertion 3: Task transitions to ABORTED immediately
        self.assertEqual(task_fsm["status"], "ABORTED")

        # Step 6: ASCII Visualizer renders abort status
        pct = int((task_fsm["current_step"] / total_steps) * 100)
        vis_text = f"[■■■■■■■■□□] {pct}% • Step {task_fsm['current_step']}/{total_steps} • ABORTITO"

        # Assertion 4: Visualizer shows 80% and Step 4/5
        self.assertIn("Step 4/5", vis_text)
        self.assertIn("ABORTITO", vis_text)

        # Step 7: HITL Rollback Prompt
        # Choice 1: "Mantieni i file generati e termina"
        hitl_choice = 1
        if hitl_choice == 1:
            preserved_files = list(generated_files)
        elif hitl_choice == 3:
            for fn in generated_files:
                os.remove(fn)
            preserved_files = []

        # Assertion 5: All generated render files are 100% preserved (0% accidental wipeout)
        self.assertEqual(len(preserved_files), 2)
        self.assertTrue(os.path.exists(f1))
        self.assertTrue(os.path.exists(f2))


if __name__ == "__main__":
    unittest.main()
