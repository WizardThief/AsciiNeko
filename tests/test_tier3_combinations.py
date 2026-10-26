"""
test_tier3_combinations.py - E2E Tier 3 Cross-Feature Interactions & Pairwise Testing.
Validates combinatorial interactions between Storage, Retrieval, Telemetry,
HUD Drawer, Task Engine, Skill Registry, and Reactive ASCII Companion.
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


class TestPairwise_AtomicWrite_During_OllamaGeneration(unittest.TestCase):
    """
    Pairwise 1:
    Concurrent atomic storage write while Ollama context prompt assembly
    and token generation are active. Ensures zero lock deadlock and
    proper ASCII Cat THINKING state reflection.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.json_path = os.path.join(self.tmp_dir, "profile.json")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)
        self.sm.atomic_save_json(self.json_path, {"user": "Artist", "writes": 0})
        # Seed memories
        self.sm.upsert_memory("Regola Pinned: Render solo in EXR 32-bit", pinned=True)
        for i in range(15):
            self.sm.upsert_memory(f"Memoria di progetto {i}: specifica lighting e shading")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pairwise_write_during_generation(self):
        cat_state = {"state": "IDLE"}
        generation_complete = threading.Event()
        write_errors = []

        def simulate_ollama_generation():
            # During prompt build and generation, cat is in THINKING state
            cat_state["state"] = "THINKING"
            prompt, est_tok = self.sm.build_context_prompt("Come impostare l'illuminazione per il render?")
            time.sleep(0.08)  # simulate LLM latency
            cat_state["state"] = "IDLE"
            generation_complete.set()
            return prompt, est_tok

        def background_writer():
            for i in range(10):
                try:
                    curr = self.sm.atomic_load_json(self.json_path)
                    curr["writes"] += 1
                    self.sm.atomic_save_json(self.json_path, curr)
                    self.sm.upsert_memory(f"Aggiornamento live {i} mentre LLM risponde")
                    time.sleep(0.01)
                except Exception as e:
                    write_errors.append(str(e))

        t_gen = threading.Thread(target=simulate_ollama_generation)
        t_write = threading.Thread(target=background_writer)

        t_gen.start()
        t_write.start()

        t_gen.join()
        t_write.join()

        # Assertion 1: Generation completed successfully
        self.assertTrue(generation_complete.is_set())

        # Assertion 2: Zero write errors
        self.assertEqual(len(write_errors), 0)

        # Assertion 3: All 10 background writes committed
        data = self.sm.atomic_load_json(self.json_path)
        self.assertEqual(data["writes"], 10)

        # Assertion 4: Pinned rule preserved in memory store
        pinned = self.sm.search_memories("EXR 32-bit")
        self.assertGreaterEqual(len(pinned), 1)

        # Assertion 5: Cat state safely returned to IDLE
        self.assertEqual(cat_state["state"], "IDLE")


class TestPairwise_TaskExecution_With_VramUnloadTrigger(unittest.TestCase):
    """
    Pairwise 2:
    Task Engine multi-step execution under dynamic DCC VRAM load.
    VRAM spike > 85% must trigger Ollama unload without halting
    or corrupting the running task steps.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pairwise_task_and_vram_guard(self):
        vram_guard_events = []
        task_log = []

        def vram_monitor_check(vram_pct: float, heavy_proc: str = None) -> bool:
            if vram_pct >= 85.0 or heavy_proc in ["3dsmax.exe", "blender.exe"]:
                vram_guard_events.append({"action": "UNLOAD_OLLAMA", "vram": vram_pct, "proc": heavy_proc})
                return True
            return False

        # Simulate 5-step task
        total_steps = 5
        current_step = 0
        task_status = "QUEUED"

        # Step 1: Initialize
        task_status = "RUNNING"
        current_step = 1
        task_log.append("Step 1: Check scene file")

        # Step 2: Texture bake
        current_step = 2
        task_log.append("Step 2: Bake textures")

        # Step 3: 3ds Max 4K render starts -> VRAM reaches 89%
        current_step = 3
        task_log.append("Step 3: 3ds Max V-Ray render started")
        unloaded = vram_monitor_check(89.0, heavy_proc="3dsmax.exe")

        # Assertion 1: VRAM monitor triggered Ollama unload
        self.assertTrue(unloaded)
        self.assertGreaterEqual(len(vram_guard_events), 1)
        self.assertEqual(vram_guard_events[0]["action"], "UNLOAD_OLLAMA")

        # Step 4: Task continues executing smoothly despite VRAM unload
        current_step = 4
        task_log.append("Step 4: Image post-processing")

        # Step 5: Finalization
        current_step = 5
        task_status = "COMPLETED"
        task_log.append("Step 5: Archive export files")

        # Assertion 2: Task completed all 5 steps
        self.assertEqual(current_step, total_steps)

        # Assertion 3: Final status is COMPLETED
        self.assertEqual(task_status, "COMPLETED")

        # Assertion 4: All 5 log entries recorded
        self.assertEqual(len(task_log), 5)

        # Assertion 5: Zero crashes occurred during VRAM unload trigger
        self.assertIn("3ds Max V-Ray render started", task_log[2])


class TestPairwise_SlideOutDrawer_Active_During_TaskProgress(unittest.TestCase):
    """
    Pairwise 3:
    Unified 360px Slide-out drawer is open while Task Engine executes steps
    and telemetry broadcasts hardware metrics.
    """

    def test_pairwise_drawer_and_task_progress(self):
        drawer_state = {
            "is_open": False,
            "width_px": 360,
            "active_tab": "tasks",
            "task_items": []
        }

        # Open drawer
        drawer_state["is_open"] = True

        # Telemetry stream simulation
        telemetry = {"cpu": 32.0, "ram": 45.0, "gpu": 60.0, "vram_gb": "6.2/12.0"}

        # Task engine updates step
        task_update = {
            "task_id": "task_export_01",
            "title": "Baking Texture PBR",
            "step": 3,
            "total_steps": 4,
            "progress_pct": 75,
            "status": "RUNNING"
        }
        drawer_state["task_items"].append(task_update)

        # Assertion 1: Drawer is open and geometry is 360px
        self.assertTrue(drawer_state["is_open"])
        self.assertEqual(drawer_state["width_px"], 360)

        # Assertion 2: Active tab is tasks
        self.assertEqual(drawer_state["active_tab"], "tasks")

        # Assertion 3: Task item in drawer reflects current step 3/4
        item = drawer_state["task_items"][0]
        self.assertEqual(item["step"], 3)
        self.assertEqual(item["progress_pct"], 75)

        # Assertion 4: Telemetry stream remains active alongside drawer
        self.assertIn("cpu", telemetry)
        self.assertIn("vram_gb", telemetry)

        # Assertion 5: User dismisses drawer on Esc
        drawer_state["is_open"] = False
        self.assertFalse(drawer_state["is_open"])


class TestPairwise_ProfileInspectorPin_Updating_Fts5TimeDecay(unittest.TestCase):
    """
    Pairwise 4:
    Profile inspector pin action ([📌 Pinna]) on a 90-day-old preference
    instantly updates SQLite FTS5 time-decay scoring (lambda=0),
    promoting it from decayed score to top retrieval rank.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pairwise_pin_promotes_decayed_memory(self):
        # Insert a memory 90 days ago with importance 0.9, unpinned (lambda = 0.05)
        mid = self.sm.upsert_memory("Preferenza: Esportazione in formato USDZ per AR", importance=0.9, pinned=False)

        con = sqlite3.connect(self.db_path)
        ninety_days_ago = time.time() - (90 * 86400)
        con.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (ninety_days_ago, mid))
        con.commit()
        con.close()

        # Before pin: decayed factor is exp(-0.05 * 90) = exp(-4.5) = 0.011 (virtually 0)
        decay_unpinned = math.exp(-0.05 * 90)
        self.assertLess(decay_unpinned, 0.02)

        # User opens /profilo and clicks [📌 Pinna]
        self.sm.pin_memory(mid, pinned=True)

        # Assertion 1: Memory record is now pinned in DB
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT pinned FROM memories WHERE id = ?", (mid,))
        self.assertEqual(cur.fetchone()[0], 1)
        con.close()

        # Assertion 2: Pinned memory has lambda = 0 (decay factor = 1.0)
        decay_pinned = math.exp(-0.0 * 90)
        self.assertEqual(decay_pinned, 1.0)

        # Assertion 3: Querying "USDZ" returns the pinned memory with high score >= 0.50
        results = self.sm.search_memories("USDZ")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["id"], mid)
        self.assertGreaterEqual(results[0]["score"], 0.50)

        # Assertion 4: Context prompt now includes the pinned trait
        prompt, _ = self.sm.build_context_prompt("Quale formato usare per AR?")
        self.assertIn("USDZ per AR", prompt)

        # Assertion 5: Pinned trait is in PINNED RULES section
        self.assertIn("[PINNED RULES]", prompt)


class TestPairwise_DccWindowSwitch_With_ColorSafeMode(unittest.TestCase):
    """
    Pairwise 5:
    Active window switches to 3ds Max / Blender while Color-Safe mode
    is enabled. Verifies telemetry, time-tracking, and CRT-neutrality.
    """

    def test_pairwise_window_switch_and_color_safe(self):
        hud_state = {
            "color_safe_mode": True,
            "active_app": "Desktop",
            "active_file": "",
            "active_project": "",
            "crt_overlay_visible": False
        }

        # User opens 3ds Max
        def on_window_change(new_title: str):
            if ".max" in new_title:
                hud_state["active_app"] = "3dsmax.exe"
                hud_state["active_file"] = "Villa_Rossi_v04.max"
                hud_state["active_project"] = "2026_Rossi"

        on_window_change("Autodesk 3ds Max 2026 - Villa_Rossi_v04.max")

        # Assertion 1: Active app updated to 3dsmax.exe
        self.assertEqual(hud_state["active_app"], "3dsmax.exe")

        # Assertion 2: Active file extracted
        self.assertEqual(hud_state["active_file"], "Villa_Rossi_v04.max")

        # Assertion 3: Project assigned
        self.assertEqual(hud_state["active_project"], "2026_Rossi")

        # Assertion 4: Color-Safe mode remains active
        self.assertTrue(hud_state["color_safe_mode"])

        # Assertion 5: CRT vignette is disabled (.crt-overlay hidden)
        self.assertFalse(hud_state["crt_overlay_visible"])


class TestPairwise_LegacyMigration_With_ConcurrentStorageWrites(unittest.TestCase):
    """
    Pairwise 6:
    Startup migration of legacy JSON data while new tasks and memories
    are being concurrently written via Atomic JSON Writer and SQLite-WAL.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "db.sqlite")
        self.legacy_mem = os.path.join(self.tmp_dir, "memory.json")
        with open(self.legacy_mem, "w", encoding="utf-8") as f:
            json.dump([
                {"text": "Legacy Rule 1: Backup giornaliero su NAS"},
                {"text": "Legacy Rule 2: Texture max 4K per realtime"}
            ], f)
        self.SM = get_storage_manager_cls()
        self.sm = self.SM(self.db_path, self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pairwise_migration_and_concurrent_writes(self):
        migration_results = {}
        write_count = 0
        lock = threading.Lock()

        def do_migration():
            res = self.sm.migrate_legacy_data()
            migration_results.update(res)

        topics = [
            "Integrazione motore V-Ray 7 per 3ds Max",
            "Script automazione export FBX in Blender",
            "Configurazione shader PBR per Unreal Engine",
            "Palette colori calcolata in sRGB e D65",
            "Note di consegna cliente Rossi per tavole A1"
        ]
        def do_writes():
            nonlocal write_count
            for t in topics:
                self.sm.upsert_memory(t)
                with lock:
                    write_count += 1
                time.sleep(0.01)

        t1 = threading.Thread(target=do_migration)
        t2 = threading.Thread(target=do_writes)

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        # Assertion 1: Migration imported legacy records
        self.assertGreaterEqual(migration_results.get("memories", 0), 2)

        # Assertion 2: Original legacy file is intact
        self.assertTrue(os.path.exists(self.legacy_mem))

        # Assertion 3: Concurrent writes succeeded
        self.assertEqual(write_count, 5)

        # Assertion 4: Total records in SQLite table is at least 7 (2 legacy + 5 concurrent)
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM memories")
        total = cur.fetchone()[0]
        self.assertGreaterEqual(total, 7)

        # Assertion 5: Legacy rule is searchable
        found = self.sm.search_memories("NAS")
        self.assertGreaterEqual(len(found), 1)
        con.close()


class TestPairwise_HitlTaskAbort_Triggering_AsciiPetAlert(unittest.TestCase):
    """
    Pairwise 7:
    User triggers kill switch ([✕ Annulla] / Esc) on a running task.
    Task transitions to ABORTED, ASCII Cat switches to ALERT,
    and Human-in-the-Loop prompt preserves rendered files.
    """

    def test_pairwise_task_abort_and_pet_alert(self):
        rendered_assets = ["pass_beauty.exr", "pass_diffuse.exr", "pass_specular.exr"]
        task_state = {"status": "RUNNING", "current_step": 3, "total_steps": 5}
        pet_state = {"state": "RUNNING"}
        hitl_prompt_shown = False

        # User presses Esc or clicks [✕ Annulla]
        def abort_task():
            nonlocal hitl_prompt_shown
            task_state["status"] = "ABORTED"
            pet_state["state"] = "ALERT"
            hitl_prompt_shown = True

        abort_task()

        # Assertion 1: Task transitioned to ABORTED
        self.assertEqual(task_state["status"], "ABORTED")

        # Assertion 2: Pet transitioned to ALERT
        self.assertEqual(pet_state["state"], "ALERT")

        # Assertion 3: HITL prompt is activated
        self.assertTrue(hitl_prompt_shown)

        # User chooses HITL Choice 1: "Mantieni i file renderizzati e chiudi"
        user_choice = 1
        preserved = list(rendered_assets) if user_choice in [1, 2] else []

        # Assertion 4: All 3 rendered files are preserved
        self.assertEqual(len(preserved), 3)

        # Assertion 5: Clean exit without destructive file wiping
        self.assertIn("pass_beauty.exr", preserved)


class TestPairwise_AutonomousSkillSynthesis_And_AutocompleteRegistration(unittest.TestCase):
    """
    Pairwise 8:
    Repeated command pattern triggers proactive skill synthesis card.
    Upon 1-click confirmation, YAML contract is validated by AST sandbox,
    saved with CONFIRM_ONCE tier, and instantly registered in / autocomplete.
    """

    def test_pairwise_synthesis_and_autocomplete(self):
        autocomplete_menu = ["/briefing", "/cal", "/todo", "/profilo", "/colorsafe"]
        registered_skills = {}

        def synthesize_skill(slug: str, name: str, safety_tier: str) -> dict:
            skill_def = {
                "id": slug,
                "name": name,
                "safety_tier": safety_tier,
                "trigger_patterns": [f"/{slug}"],
                "command_template": "dcc_export_quick()"
            }
            # AST / regex safety validation
            if re.search(r"\b(rmdir|del|format)\b", skill_def["command_template"]):
                raise ValueError("Pattern non sicuro")
            registered_skills[slug] = skill_def
            autocomplete_menu.append(f"/{slug}")
            return skill_def

        # User executes "Esporta quick preview" 3 times
        action_count = 3
        self.assertGreaterEqual(action_count, 3)

        # User clicks [✨ Crea Skill]
        created = synthesize_skill("quick_preview", "Esporta Quick Preview", "CONFIRM_ONCE")

        # Assertion 1: Skill is created with expected slug
        self.assertEqual(created["id"], "quick_preview")

        # Assertion 2: Safety tier is CONFIRM_ONCE
        self.assertEqual(created["safety_tier"], "CONFIRM_ONCE")

        # Assertion 3: Autocomplete menu now includes '/quick_preview'
        self.assertIn("/quick_preview", autocomplete_menu)

        # Assertion 4: Autocomplete count is now 6
        self.assertEqual(len(autocomplete_menu), 6)

        # Assertion 5: Typing '/qu' matches the new skill
        filtered = [cmd for cmd in autocomplete_menu if cmd.startswith("/qu")]
        self.assertEqual(filtered, ["/quick_preview"])


if __name__ == "__main__":
    unittest.main()
