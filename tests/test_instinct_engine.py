"""
tests/test_instinct_engine.py
=============================
Unit and integration tests for Milestone 1 (Requirement R1):
- Continuous Learning v2.1 Instinct Engine
- Dual-Persistence (SQLite table 'instincts' + AtomicJsonStore instincts.json)
- Confidence evolution and strict clamping [0.30, 0.95]
- Project -> Global auto-promotion across 2+ distinct projects
- Inactivity decay & pruning of expired low-confidence instincts
- Idempotent startup seeding
- TokenBudgetManager budget fitting (BUDGET_INSTINCTS=300) and context breakdown
- HTTP API POST /api/instincts/action and GET /status integration
- Critical Data Hygiene: 100% isolated inside tempfile.TemporaryDirectory()
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from bridge.storage_manager import (
    AtomicJsonStore,
    StorageCorruptError,
    StorageManager,
    get_storage_manager,
)
from bridge.instinct_engine import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    CONFIDENCE_THRESHOLD_GLOBAL,
    CONFIDENCE_PRUNE_THRESHOLD,
    InstinctEngine,
    clamp_confidence,
    get_instinct_engine,
    normalize_instinct_text,
)
from bridge.retrieval import (
    BUDGET_INSTINCTS,
    TokenBudgetManager,
    estimate_tokens,
)
import bridge.ai_monitor as am
from bridge.ai_monitor import (
    CalendarManager,
    CompanionRequestHandler,
    MemoryAndProjectsManager,
    OllamaLocalBridge,
    SystemStateMonitor,
    WindowAndConnectionsManager,
    WindowsAudioCapture,
)


class TestStorageCRUDAndDualPersistence(unittest.TestCase):
    """Verifies SQLite DDL, CRUD operations, AtomicJsonStore, and dual persistence."""

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

    def test_instincts_table_schema_and_crud(self):
        # Table exists and empty initially
        instincts = self.sm.get_all_instincts()
        self.assertEqual(len(instincts), 0)

        # Save an instinct
        inst = {
            "id": "inst_test_01",
            "trigger": "Quando si esporta FBX",
            "action": "Usa scale factor 1.0 e asse Z verso l'alto",
            "confidence": 0.80,
            "scope": "project",
            "project_id": "proj_intermarine",
            "domain": "pipeline",
            "use_count": 2,
            "created_at": "2026-10-27T10:00:00Z",
            "last_seen_at": "2026-10-27T10:00:00Z",
        }
        self.sm.save_instinct(inst)

        # Verify read
        all_inst = self.sm.get_all_instincts()
        self.assertEqual(len(all_inst), 1)
        loaded = all_inst[0]
        self.assertEqual(loaded["id"], "inst_test_01")
        self.assertEqual(loaded["trigger"], "Quando si esporta FBX")
        self.assertAlmostEqual(loaded["confidence"], 0.80)
        self.assertEqual(loaded["scope"], "project")
        self.assertEqual(loaded["project_id"], "proj_intermarine")

        # Update instinct
        inst["confidence"] = 0.90
        inst["use_count"] = 3
        self.sm.save_instinct(inst)
        updated = self.sm.get_all_instincts()
        self.assertEqual(len(updated), 1)
        self.assertAlmostEqual(updated[0]["confidence"], 0.90)
        self.assertEqual(updated[0]["use_count"], 3)

        # Delete instinct
        del_ok = self.sm.delete_instinct("inst_test_01")
        self.assertTrue(del_ok)
        self.assertEqual(len(self.sm.get_all_instincts()), 0)

    def test_atomic_json_store(self):
        store_path = os.path.join(self.data_dir, "test_store.json")
        store = AtomicJsonStore(store_path, default=[], storage_manager=self.sm)

        # Initial load defaults
        self.assertEqual(store.load(), [])

        # Atomic save and reload
        payload = [{"key": "val1"}, {"key": "val2"}]
        self.assertTrue(store.save(payload))
        self.assertEqual(store.load(), payload)

        # Verify corrupt backup behavior
        with open(store_path, "wb") as f:
            f.write(b"INVALID_JSON_CORRUPTED{{{")

        with self.assertRaises(StorageCorruptError):
            store.load()

        # Confirm .corrupt.bak was generated
        corrupt_files = [f for f in os.listdir(self.data_dir) if "corrupt.bak" in f]
        self.assertGreaterEqual(len(corrupt_files), 1)

    def test_dual_persistence_sync(self):
        # Create InstinctEngine with isolated data directory
        engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

        # Adding an instinct persists to SQLite and instincts.json
        inst = engine.add_instinct(
            trigger="Quando si renderizza in Corona",
            action="Attiva sempre l'opzione Denoising",
            confidence=0.75,
            scope="project",
            project_id="proj_render",
            domain="tool",
        )
        self.assertEqual(inst["trigger"], "Quando si renderizza in Corona")

        # Check SQLite
        db_records = self.sm.get_all_instincts()
        self.assertTrue(any(i["id"] == inst["id"] for i in db_records))

        # Check JSON mirror
        json_path = os.path.join(self.data_dir, "instincts.json")
        self.assertTrue(os.path.isfile(json_path))
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        self.assertTrue(any(i["id"] == inst["id"] for i in json_data))

        # Create a second engine instance to test startup reconciliation
        engine2 = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)
        retrieved = engine2.get_instinct(inst["id"])
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["action"], "Attiva sempre l'opzione Denoising")


class TestConfidenceEvolutionAndClamping(unittest.TestCase):
    """Verifies confidence evolution, reinforcement boosts, and strict bounds [0.30, 0.95]."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
            self.sm.close_connection()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_confidence_clamping_helper(self):
        self.assertAlmostEqual(clamp_confidence(0.10), CONFIDENCE_MIN)
        self.assertAlmostEqual(clamp_confidence(0.29), CONFIDENCE_MIN)
        self.assertAlmostEqual(clamp_confidence(0.30), 0.30)
        self.assertAlmostEqual(clamp_confidence(0.70), 0.70)
        self.assertAlmostEqual(clamp_confidence(0.95), 0.95)
        self.assertAlmostEqual(clamp_confidence(0.99), CONFIDENCE_MAX)
        self.assertAlmostEqual(clamp_confidence(1.50), CONFIDENCE_MAX)

    def test_reinforcement_boosts_confidence_and_increments_use_count(self):
        inst = self.engine.add_instinct(
            trigger="Quando si compila Rust",
            action="Usa cargo check prima di cargo build --release",
            confidence=0.80,
            scope="global",
        )
        inst_id = inst["id"]
        initial_count = inst["use_count"]

        # Confirm 1
        updated = self.engine.confirm_instinct(inst_id, delta=0.05)
        self.assertIsNotNone(updated)
        self.assertAlmostEqual(updated["confidence"], 0.85)
        self.assertEqual(updated["use_count"], initial_count + 1)

        # Confirm 2
        updated2 = self.engine.confirm_instinct(inst_id, delta=0.05)
        self.assertAlmostEqual(updated2["confidence"], 0.90)
        self.assertEqual(updated2["use_count"], initial_count + 2)

    def test_confidence_clamped_at_upper_limit(self):
        inst = self.engine.add_instinct(
            trigger="Quando si scrive codice",
            action="Fai sempre test unitari",
            confidence=0.92,
            scope="global",
        )
        inst_id = inst["id"]

        # Boost beyond 0.95
        updated = self.engine.confirm_instinct(inst_id, delta=0.10)
        self.assertAlmostEqual(updated["confidence"], CONFIDENCE_MAX)

        # Boost again
        updated2 = self.engine.confirm_instinct(inst_id, delta=0.05)
        self.assertAlmostEqual(updated2["confidence"], CONFIDENCE_MAX)


class TestAutoPromotionAcrossProjects(unittest.TestCase):
    """Verifies that an instinct observed across 2+ distinct projects is auto-promoted to global."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
            self.sm.close_connection()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_auto_promotion_on_multi_project_observation(self):
        # Observation in Project A
        inst_a = self.engine.add_instinct(
            trigger="Quando si esporta un modello 3D per Three.js",
            action="Bake sempre le normal maps prima dell'export",
            confidence=0.60,
            scope="project",
            project_id="proj_yacht_interior",
            domain="pipeline",
        )
        self.assertEqual(inst_a["scope"], "project")
        self.assertEqual(inst_a["project_id"], "proj_yacht_interior")

        # Same normalized trigger and action observed in Project B
        inst_b = self.engine.add_instinct(
            trigger="Quando si esporta un modello 3D per Three.js!",
            action="Bake sempre le normal maps prima dell'export.",
            confidence=0.65,
            scope="project",
            project_id="proj_museum_ar",
            domain="pipeline",
        )

        # Auto-promoted to global!
        self.assertEqual(inst_b["scope"], "global")
        self.assertIsNone(inst_b["project_id"])
        self.assertGreaterEqual(inst_b["confidence"], 0.65)

        # Check get_all_instincts
        all_inst = self.engine.get_all_instincts()
        matching = [
            i for i in all_inst
            if "Bake sempre le normal maps" in i["action"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["scope"], "global")
        self.assertIsNone(matching[0]["project_id"])

    def test_manual_promotion(self):
        inst = self.engine.add_instinct(
            trigger="Regola specifica di modellazione",
            action="Mantieni sempre topologia quad",
            confidence=0.70,
            scope="project",
            project_id="proj_cad",
        )
        self.assertEqual(inst["scope"], "project")

        promoted = self.engine.promote_instinct(inst["id"])
        self.assertEqual(promoted["scope"], "global")
        self.assertIsNone(promoted["project_id"])
        self.assertAlmostEqual(promoted["confidence"], 0.75)


class TestPruningOfExpiredInstincts(unittest.TestCase):
    """Verifies confidence decay and pruning of low-confidence unconfirmed instincts older than 14 days."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
            self.sm.close_connection()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_pruning_removes_old_low_confidence_and_preserves_seeds_and_active(self):
        now = time.time()
        fifteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

        # 1. Old low-confidence instinct (SHOULD be pruned: conf <= 0.35, age >= 14 days)
        old_low = {
            "id": "inst_old_low",
            "trigger": "Quando si apre Notepad",
            "action": "Attiva word wrap",
            "confidence": 0.32,
            "scope": "project",
            "project_id": "proj_notes",
            "domain": "tool",
            "use_count": 1,
            "created_at": fifteen_days_ago,
            "last_seen_at": fifteen_days_ago,
        }
        self.sm.save_instinct(old_low)

        # 2. Old high-confidence instinct (SHOULD NOT be pruned: conf 0.85)
        old_high = {
            "id": "inst_old_high",
            "trigger": "Regola antica e consolidata",
            "action": "Usa sempre il sistema metrico",
            "confidence": 0.85,
            "scope": "global",
            "project_id": None,
            "domain": "workflow",
            "use_count": 10,
            "created_at": fifteen_days_ago,
            "last_seen_at": fifteen_days_ago,
        }
        self.sm.save_instinct(old_high)

        # 3. Recent low-confidence instinct (SHOULD NOT be pruned: age only 2 days)
        recent_low = {
            "id": "inst_recent_low",
            "trigger": "Tentativo provvisorio",
            "action": "Prova formato webp",
            "confidence": 0.34,
            "scope": "project",
            "project_id": "proj_test",
            "domain": "pipeline",
            "use_count": 1,
            "created_at": two_days_ago,
            "last_seen_at": two_days_ago,
        }
        self.sm.save_instinct(recent_low)

        # Sync mirror
        self.engine._sync_and_seed()

        # Execute pruning
        pruned_count = self.engine.prune_expired_instincts(max_age_days=14.0, min_confidence=0.35)
        self.assertEqual(pruned_count, 1)

        # Verify old_low is gone
        self.assertIsNone(self.engine.get_instinct("inst_old_low"))

        # Verify old_high and recent_low still exist
        self.assertIsNotNone(self.engine.get_instinct("inst_old_high"))
        self.assertIsNotNone(self.engine.get_instinct("inst_recent_low"))

        # Verify seed rules are intact
        self.assertIsNotNone(self.engine.get_instinct("inst_seed_intermarine_logo"))
        self.assertIsNotNone(self.engine.get_instinct("inst_seed_glb_draco_pipeline"))


class TestIdempotentSeeding(unittest.TestCase):
    """Verifies that startup seeding seeds initial instincts idempotently."""

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

    def test_seeding_creates_two_initial_seeds(self):
        engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

        # Seed 1: Intermarine navy logo
        intermarine = engine.get_instinct("inst_seed_intermarine_logo")
        self.assertIsNotNone(intermarine)
        self.assertEqual(intermarine["project_id"], "proj_nave_intermarine")
        self.assertEqual(intermarine["scope"], "project")
        self.assertAlmostEqual(intermarine["confidence"], 0.90)
        self.assertIn("Blu Marino", intermarine["action"])

        # Seed 2: 3D realtime pipeline
        pipeline = engine.get_instinct("inst_seed_glb_draco_pipeline")
        self.assertIsNotNone(pipeline)
        self.assertEqual(pipeline["scope"], "global")
        self.assertAlmostEqual(pipeline["confidence"], 0.85)
        self.assertIn("Draco", pipeline["action"])

        initial_count = len(engine.get_all_instincts())
        self.assertGreaterEqual(initial_count, 2)

        # Re-initialize another engine instance on the same data_dir
        engine2 = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)
        self.assertEqual(len(engine2.get_all_instincts()), initial_count)


class TestTokenBudgetManagerInstincts(unittest.TestCase):
    """Verifies fit_instincts (BUDGET_INSTINCTS=300) and compute_context_breakdown."""

    def setUp(self):
        self.budget_mgr = TokenBudgetManager()

    def test_constants_and_defaults(self):
        self.assertEqual(BUDGET_INSTINCTS, 300)
        self.assertEqual(self.budget_mgr.budget_instincts, 300)
        self.assertEqual(self.budget_mgr.max_prompt_budget, 2450)

    def test_fit_instincts_filtering(self):
        instincts = [
            # Global with conf >= 0.55 -> INCLUDED
            {
                "id": "i1",
                "trigger": "Quando esporti 3D",
                "action": "Usa Draco GLB",
                "confidence": 0.85,
                "scope": "global",
                "project_id": None,
                "domain": "pipeline",
            },
            # Global with conf < 0.55 -> EXCLUDED
            {
                "id": "i2",
                "trigger": "Quando navighi",
                "action": "Usa Firefox",
                "confidence": 0.50,
                "scope": "global",
                "project_id": None,
                "domain": "tool",
            },
            # Project matching active_project_id -> INCLUDED
            {
                "id": "i3",
                "trigger": "Per Intermarine",
                "action": "Logo sempre Blu Marino",
                "confidence": 0.90,
                "scope": "project",
                "project_id": "proj_nave_intermarine",
                "domain": "client-rule",
            },
            # Project for a different project -> EXCLUDED
            {
                "id": "i4",
                "trigger": "Per Cliente Ferrari",
                "action": "Logo sempre Rosso Corsa",
                "confidence": 0.95,
                "scope": "project",
                "project_id": "proj_ferrari",
                "domain": "client-rule",
            },
        ]

        fitted = self.budget_mgr.fit_instincts(
            instincts=instincts,
            active_project_id="proj_nave_intermarine",
        )

        fitted_ids = [x["id"] for x in fitted]
        self.assertIn("i1", fitted_ids)
        self.assertIn("i3", fitted_ids)
        self.assertNotIn("i2", fitted_ids)
        self.assertNotIn("i4", fitted_ids)

    def test_fit_instincts_budget_cap(self):
        # Create many eligible instincts that exceed 300 tokens
        long_action = "A" * 120
        many_instincts = [
            {
                "id": f"inst_bulk_{i}",
                "trigger": f"Trigger {i}",
                "action": f"Action {i} {long_action}",
                "confidence": 0.80,
                "scope": "global",
                "project_id": None,
                "domain": "workflow",
                "use_count": i,
            }
            for i in range(25)
        ]

        fitted, tokens = self.budget_mgr.fit_instincts(
            instincts=many_instincts,
            available_tokens=300,
            as_tuple=True,
        )
        self.assertLessEqual(tokens, 300)
        self.assertGreater(len(fitted), 0)
        self.assertLess(len(fitted), len(many_instincts))

    def test_compute_context_breakdown(self):
        system_text = "Sei Neko AI, il gatto desktop."
        profile = {"name": "Diego", "traits": ["3ds Max", "Blender", "Rust"]}
        skills = [{"name": "Draco GLB", "level": "avanzato"}]
        instincts = [
            {"trigger": "Render Intermarine", "action": "Logo Blu Marino", "domain": "client-rule"}
        ]
        memories = [{"text": "Diego lavora su simulazioni navali."}]
        projects = [{"name": "Nave Intermarine", "client": "Intermarine"}]
        todos = [{"title": "Export GLB ottimizzato", "status": "doing"}]
        chat_history = [{"role": "user", "text": "Miao!"}, {"role": "assistant", "text": "Miao a te!"}]

        breakdown = self.budget_mgr.compute_context_breakdown(
            system_prompt=system_text,
            profile=profile,
            skills=skills,
            instincts=instincts,
            memories=memories,
            projects=projects,
            todos=todos,
            chat_history=chat_history,
        )

        self.assertEqual(breakdown["total_budget"], 8192)
        self.assertGreater(breakdown["used_tokens"], 0)
        self.assertGreater(breakdown["usage_pct"], 0.0)

        # Per category check
        cats = breakdown["breakdown"]
        self.assertIn("system", cats)
        self.assertIn("profile_and_skills", cats)
        self.assertIn("instincts", cats)
        self.assertIn("memories", cats)
        self.assertIn("projects_and_todos", cats)
        self.assertIn("chat_history", cats)

        for cat_name, count in cats.items():
            self.assertGreater(count, 0, f"Category {cat_name} should have > 0 tokens")

        self.assertEqual(sum(cats.values()), breakdown["used_tokens"])


class TestInstinctsAPIEndpoints(unittest.TestCase):
    """Verifies POST /api/instincts/action, GET /status snapshot, and /prune slash command."""

    @classmethod
    def setUpClass(cls):
        # Save original references from bridge.ai_monitor
        cls.orig_get_storage_manager = am.get_storage_manager
        cls.orig_get_instinct_engine = am.get_instinct_engine
        cls.orig_ollama_bridge = am.ollama_bridge
        cls.orig_monitor = am.monitor
        cls.orig_mem_proj_manager = am.mem_proj_manager

        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.data_dir = cls.temp_dir.name
        cls.sm = StorageManager(data_dir=cls.data_dir)
        cls.ie = InstinctEngine(data_dir=cls.data_dir, storage_manager=cls.sm)

        # Patch global singletons for this test suite
        am.get_storage_manager = lambda *args, **kwargs: cls.sm
        am.get_instinct_engine = lambda *args, **kwargs: cls.ie

        # Setup test server on an ephemeral port
        cls.cal_mgr = CalendarManager()
        cls.mem_mgr = MemoryAndProjectsManager()
        cls.bridge = OllamaLocalBridge(cls.cal_mgr, cls.mem_mgr, data_dir=cls.data_dir, instinct_engine=cls.ie)
        cls.win_mgr = WindowAndConnectionsManager(cls.bridge, cls.cal_mgr, cls.mem_mgr)
        cls.audio = WindowsAudioCapture()
        cls.monitor = SystemStateMonitor(
            cls.audio, cls.bridge, cls.cal_mgr, cls.mem_mgr, cls.win_mgr, instinct_engine=cls.ie
        )

        # Inject into am module level
        am.ollama_bridge = cls.bridge
        am.monitor = cls.monitor
        am.mem_proj_manager = cls.mem_mgr

        # Start ThreadingHTTPServer on 127.0.0.1:0 (ephemeral port)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass
        try:
            cls.ie.close()
            cls.sm.close_connection()
        except Exception:
            pass
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass
        # Restore original references back to bridge.ai_monitor
        am.get_storage_manager = cls.orig_get_storage_manager
        am.get_instinct_engine = cls.orig_get_instinct_engine
        am.ollama_bridge = cls.orig_ollama_bridge
        am.monitor = cls.orig_monitor
        am.mem_proj_manager = cls.orig_mem_proj_manager

    def _post(self, path: str, payload: dict) -> dict:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_raw(self, path: str, payload: dict):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.code, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = json.loads(e.read().decode("utf-8"))
            return e.code, err_body

    def _get(self, path: str) -> dict:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_api_action_add_confirm_promote_prune_delete(self):
        # 1. Action: ADD
        add_res = self._post("/api/instincts/action", {
            "action": "add",
            "trigger": "Quando esporti da Blender",
            "action_text": "Applica tutte le trasformazioni con Ctrl+A",
            "confidence": 0.70,
            "scope": "project",
            "project_id": "proj_blender_assets",
            "domain": "tool",
        })
        self.assertTrue(add_res.get("ok"))
        inst = add_res["instinct"]
        inst_id = inst["id"]
        self.assertEqual(inst["trigger"], "Quando esporti da Blender")
        self.assertAlmostEqual(inst["confidence"], 0.70)

        # 2. Action: CONFIRM
        conf_res = self._post("/api/instincts/action", {
            "action": "confirm",
            "id": inst_id,
        })
        self.assertTrue(conf_res.get("ok"))
        self.assertAlmostEqual(conf_res["instinct"]["confidence"], 0.75)
        self.assertEqual(conf_res["instinct"]["use_count"], inst["use_count"] + 1)

        # 3. Action: PROMOTE
        prom_res = self._post("/api/instincts/action", {
            "action": "promote",
            "id": inst_id,
        })
        self.assertTrue(prom_res.get("ok"))
        self.assertEqual(prom_res["instinct"]["scope"], "global")
        self.assertIsNone(prom_res["instinct"]["project_id"])
        self.assertAlmostEqual(prom_res["instinct"]["confidence"], 0.80)

        # 4. Action: PRUNE
        prune_res = self._post("/api/instincts/action", {
            "action": "prune",
            "max_age_days": 14.0,
            "min_confidence": 0.35,
        })
        self.assertTrue(prune_res.get("ok"))
        self.assertIn("pruned_count", prune_res)

        # 5. Action: DELETE
        del_res = self._post("/api/instincts/action", {
            "action": "delete",
            "id": inst_id,
        })
        self.assertTrue(del_res.get("ok"))
        self.assertIsNone(self.ie.get_instinct(inst_id))

    def test_get_status_contains_instincts_and_context_budget(self):
        status = self._get("/status")
        self.assertIn("instincts", status)
        self.assertIn("instinct_count", status)
        self.assertIn("context_budget", status)

        self.assertIsInstance(status["instincts"], list)
        self.assertGreaterEqual(status["instinct_count"], 2)

        cb = status["context_budget"]
        self.assertIn("total_budget", cb)
        self.assertIn("used_tokens", cb)
        self.assertIn("usage_pct", cb)
        self.assertIn("breakdown", cb)
        self.assertIn("instincts", cb["breakdown"])

    def test_prune_slash_command(self):
        res = self.bridge.handle_chat_or_command("/prune")
        reply_text = res.get("reply", "") if isinstance(res, dict) else str(res)
        self.assertIn("Pruning completato", reply_text)
        self.assertIn("istinti", reply_text.lower())

    def test_api_action_error_handling_returns_http_400(self):
        # 1. Empty trigger
        code, body = self._post_raw("/api/instincts/action", {
            "action": "add",
            "trigger": "",
            "action_text": "Valida",
        })
        self.assertEqual(code, 400)
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("status"), "error")
        self.assertIn("message", body)

        # 2. Invalid scope
        code, body = self._post_raw("/api/instincts/action", {
            "action": "add",
            "trigger": "Trig",
            "action_text": "Act",
            "scope": "general",
        })
        self.assertEqual(code, 400)
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("status"), "error")

        # 3. Invalid confidence
        code, body = self._post_raw("/api/instincts/action", {
            "action": "add",
            "trigger": "Trig",
            "action_text": "Act",
            "confidence": "molto_alta",
        })
        self.assertEqual(code, 400)
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("status"), "error")

        # 4. Missing ID for confirm
        code, body = self._post_raw("/api/instincts/action", {
            "action": "confirm",
        })
        self.assertEqual(code, 400)
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("status"), "error")


class TestGate1Remediations(unittest.TestCase):
    """Specific regression and edge-case unit tests for Milestone 1 Gate 1 remediations."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.sm.close_connection()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_scope_preservation_when_global_reinforced_in_project(self):
        # 1. Seeded global instinct stays global with project_id=None
        seed_glb = self.engine.get_instinct("inst_seed_glb_draco_pipeline")
        self.assertEqual(seed_glb["scope"], "global")
        self.assertIsNone(seed_glb["project_id"])

        updated = self.engine.add_instinct(
            trigger=seed_glb["trigger"],
            action=seed_glb["action"],
            scope="project",
            project_id="proj_nave_intermarine",
        )
        self.assertEqual(updated["scope"], "global")
        self.assertIsNone(updated["project_id"])

        # 2. Promoted instinct stays global when seen in a 3rd project
        inst1 = self.engine.add_instinct("Regola comune", "Azione comune", scope="project", project_id="proj_alpha")
        self.assertEqual(inst1["scope"], "project")
        inst2 = self.engine.add_instinct("Regola comune", "Azione comune", scope="project", project_id="proj_beta")
        self.assertEqual(inst2["scope"], "global")
        self.assertIsNone(inst2["project_id"])

        inst3 = self.engine.add_instinct("Regola comune", "Azione comune", scope="project", project_id="proj_gamma")
        self.assertEqual(inst3["scope"], "global")
        self.assertIsNone(inst3["project_id"])
        self.assertGreater(inst3["confidence"], inst2["confidence"])

    def test_seed_reconciliation_for_intermarine_logo(self):
        # Corrupt the record in DB to have project_id=None
        intermarine = self.engine.get_instinct("inst_seed_intermarine_logo")
        self.assertIsNotNone(intermarine)
        intermarine["project_id"] = None
        self.sm.save_instinct(intermarine)

        # Before reconciliation, active query for proj_nave_intermarine will not include it
        active_before = self.engine.get_active_instincts("proj_nave_intermarine")
        self.assertFalse(any(i["id"] == "inst_seed_intermarine_logo" for i in active_before))

        # Re-run _sync_and_seed
        self.engine._sync_and_seed()

        # After reconciliation, project_id is restored and it is returned by get_active_instincts
        reconciled = self.engine.get_instinct("inst_seed_intermarine_logo")
        self.assertEqual(reconciled["project_id"], "proj_nave_intermarine")
        self.assertEqual(reconciled["scope"], "project")

        active_after = self.engine.get_active_instincts("proj_nave_intermarine")
        self.assertTrue(any(i["id"] == "inst_seed_intermarine_logo" for i in active_after))

    def test_no_compounded_decay_on_consecutive_prune(self):
        now = datetime.now(timezone.utc)
        created_at = (now - timedelta(days=20)).isoformat()
        fourteen_days_ago = (now - timedelta(days=14)).isoformat()

        inst = {
            "id": "inst_decay_compounding_check",
            "trigger": "Trigger non attivo",
            "action": "Azione non attiva",
            "confidence": 0.50,
            "scope": "project",
            "project_id": "proj_compounding",
            "domain": "workflow",
            "use_count": 1,
            "created_at": created_at,
            "last_seen_at": fourteen_days_ago,
        }
        self.sm.save_instinct(inst)
        self.engine._sync_and_seed()

        # Call 1: 14 days inactive -> 2 intervals of 7 days -> decay 0.10 -> 0.40
        self.engine.prune_expired_instincts()
        after_first = self.engine.get_instinct("inst_decay_compounding_check")
        self.assertIsNotNone(after_first)
        self.assertAlmostEqual(after_first["confidence"], 0.40, places=4)

        # Call 2: Rapid back-to-back call -> confidence MUST remain 0.40, NOT decay to 0.30!
        self.engine.prune_expired_instincts()
        after_second = self.engine.get_instinct("inst_decay_compounding_check")
        self.assertIsNotNone(after_second)
        self.assertAlmostEqual(after_second["confidence"], 0.40, places=4)


if __name__ == "__main__":
    unittest.main()
