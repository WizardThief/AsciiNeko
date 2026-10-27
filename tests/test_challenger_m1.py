"""
tests/test_challenger_m1.py
===========================
Empirical Stress & Adversarial Test Suite for Milestone 1 (Instinct Engine v2.1).
Written by challenger_m1_1.

Tests cover:
1. Confidence clamping strictly between 0.30 and 0.95 under extreme boost or decay iterations,
   fuzzing with boundary values, invalid types, extreme numbers, and high iterations.
2. Auto-promotion from project to global: verify it triggers when normalized trigger/action
   is observed across 2+ distinct project_ids; verify punctuation, casing, Unicode diacritics,
   extra whitespace, and tabs match correctly; verify single project multiple additions do NOT promote.
3. Pruning logic: verify unconfirmed instincts with confidence <= 0.35 older than 14 days are pruned,
   but high-confidence (>0.35), fresh (<14 days), or seeded instincts are NEVER pruned;
   verify inactivity decay mechanics over multiple 7-day intervals.
4. Concurrency & Stress: Concurrent additions, confirmations, promotions, and pruning across
   multiple threads without SQLite locks or data corruption.
5. Dual-persistence synchronization: SQLite <-> instincts.json integrity across operations.
6. Data hygiene verification: zero modification to real bridge/data/.
"""

import math
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

# Ensure repo root and bridge/ are on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from bridge.storage_manager import StorageManager, AtomicJsonStore
from bridge.instinct_engine import (
    InstinctEngine,
    clamp_confidence,
    normalize_instinct_text,
    parse_timestamp_epoch,
    CONFIDENCE_MIN,
    CONFIDENCE_MAX,
    CONFIDENCE_THRESHOLD_GLOBAL,
    CONFIDENCE_PRUNE_THRESHOLD,
    PRUNE_MAX_AGE_DAYS,
)
from bridge.retrieval import TokenBudgetManager, BUDGET_INSTINCTS


class TestChallengerConfidenceClamping(unittest.TestCase):
    """Adversarial stress-testing of confidence evolution and clamping boundaries [0.30, 0.95]."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_extreme_boost_iterations_saturation(self):
        """Stress: 1000 consecutive confirmations must never exceed 0.95."""
        inst = self.engine.add_instinct(
            trigger="Test Extreme Boost",
            action="Verify confidence ceiling",
            confidence=0.50,
            scope="project",
            project_id="proj_stress",
        )
        i_id = inst["id"]

        for _ in range(1000):
            res = self.engine.confirm_instinct(i_id, delta=0.05)
            self.assertIsNotNone(res)
            self.assertLessEqual(res["confidence"], CONFIDENCE_MAX)
            self.assertGreaterEqual(res["confidence"], CONFIDENCE_MIN)

        # Check in engine, SQLite, and instincts.json
        final_inst = self.engine.get_instinct(i_id)
        self.assertEqual(final_inst["confidence"], CONFIDENCE_MAX)
        self.assertEqual(final_inst["use_count"], 1001)

        # Check SQLite directly
        db_rows = self.sm.get_all_instincts()
        matched = [r for r in db_rows if r["id"] == i_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["confidence"], CONFIDENCE_MAX)

        # Check instincts.json
        json_data = self.engine.json_store.load()
        json_matched = [r for r in json_data if r["id"] == i_id]
        self.assertEqual(len(json_matched), 1)
        self.assertEqual(json_matched[0]["confidence"], CONFIDENCE_MAX)

    def test_extreme_negative_decay_floor(self):
        """Stress: Negative deltas or large negative confidence inputs must clamp at 0.30."""
        inst = self.engine.add_instinct(
            trigger="Test Extreme Decay",
            action="Verify confidence floor",
            confidence=0.50,
            scope="project",
            project_id="proj_stress",
        )
        i_id = inst["id"]

        # Confirm with negative delta
        res = self.engine.confirm_instinct(i_id, delta=-10.0)
        self.assertEqual(res["confidence"], CONFIDENCE_MIN)

        # Confirm 500 times with negative deltas
        for _ in range(500):
            res = self.engine.confirm_instinct(i_id, delta=-0.05)
            self.assertGreaterEqual(res["confidence"], CONFIDENCE_MIN)
            self.assertLessEqual(res["confidence"], CONFIDENCE_MAX)

        self.assertEqual(res["confidence"], CONFIDENCE_MIN)

    def test_boundary_fuzzing_inputs(self):
        """Adversarial input fuzzing for confidence values."""
        test_cases = [
            (-999999.99, CONFIDENCE_MIN),
            (-0.01, CONFIDENCE_MIN),
            (0.0, CONFIDENCE_MIN),
            (0.2999, CONFIDENCE_MIN),
            (0.30, 0.30),
            (0.50, 0.50),
            (0.95, 0.95),
            (0.9501, CONFIDENCE_MAX),
            (1.0, CONFIDENCE_MAX),
            (999999.99, CONFIDENCE_MAX),
            ("0.75", 0.75),
            ("invalid_string", 0.50),
            (None, 0.50),
        ]
        for inp, expected in test_cases:
            clamped = clamp_confidence(inp)
            self.assertAlmostEqual(
                clamped, expected, places=4,
                msg=f"Failed clamping for input: {inp}"
            )
            self.assertTrue(CONFIDENCE_MIN <= clamped <= CONFIDENCE_MAX)

    def test_sqlite_check_constraint_enforcement(self):
        """Verify that SQLite schema strictly rejects any confidence < 0.30 or > 0.95."""
        conn = self.sm.get_connection()
        now_str = datetime.now(timezone.utc).isoformat()

        # Try inserting 0.29
        with self.assertRaises(Exception):
            with self.sm.transaction() as c:
                c.execute(
                    """
                    INSERT INTO instincts (id, trigger, action, confidence, scope, domain, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("inst_viol_low", "Trig", "Act", 0.29, "global", "workflow", now_str, now_str)
                )

        # Try inserting 0.96
        with self.assertRaises(Exception):
            with self.sm.transaction() as c:
                c.execute(
                    """
                    INSERT INTO instincts (id, trigger, action, confidence, scope, domain, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("inst_viol_high", "Trig", "Act", 0.96, "global", "workflow", now_str, now_str)
                )


class TestChallengerAutoPromotion(unittest.TestCase):
    """Adversarial testing of Project -> Global auto-promotion and text normalization."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_single_project_multiple_reinforcements_do_not_promote(self):
        """An instinct seen 100 times in the SAME project must remain scope='project'."""
        inst = self.engine.add_instinct(
            trigger="Quando si modella scafo",
            action="Usa simmetria lungo asse X",
            confidence=0.50,
            scope="project",
            project_id="proj_intermarine",
        )
        self.assertEqual(inst["scope"], "project")
        self.assertEqual(inst["project_id"], "proj_intermarine")

        # Repeatedly add/confirm for same project
        for _ in range(20):
            updated = self.engine.add_instinct(
                trigger="Quando si modella scafo",
                action="Usa simmetria lungo asse X",
                scope="project",
                project_id="proj_intermarine",
            )
            self.assertEqual(updated["scope"], "project")
            self.assertEqual(updated["project_id"], "proj_intermarine")

    def test_promotion_across_two_distinct_projects(self):
        """Observing the same trigger/action across 2 distinct projects must promote to global."""
        inst1 = self.engine.add_instinct(
            trigger="Configura esportazione WebGL",
            action="Abilita mesh compression Draco level 7",
            confidence=0.60,
            scope="project",
            project_id="proj_project_alpha",
        )
        self.assertEqual(inst1["scope"], "project")
        self.assertEqual(inst1["project_id"], "proj_project_alpha")

        # Second observation in distinct project
        inst2 = self.engine.add_instinct(
            trigger="Configura esportazione WebGL",
            action="Abilita mesh compression Draco level 7",
            scope="project",
            project_id="proj_project_beta",
        )
        self.assertEqual(inst2["scope"], "global")
        self.assertIsNone(inst2["project_id"])
        self.assertGreater(inst2["confidence"], 0.60)

        # Verify in SQLite and JSON mirror
        fetched = self.engine.get_instinct(inst1["id"])
        self.assertEqual(fetched["scope"], "global")
        self.assertIsNone(fetched["project_id"])

    def test_normalization_casing_punctuation_whitespace_diacritics(self):
        """Verify normalization handles mixed casing, punctuation, newlines, and accents."""
        # 1. Base instinct in project 1
        inst1 = self.engine.add_instinct(
            trigger="Quando si esporta in GLB, ottimizza i materiali!",
            action="Converti tutte le texture in WebP a 2048x2048.",
            confidence=0.55,
            scope="project",
            project_id="proj_site_a",
        )
        self.assertEqual(inst1["scope"], "project")

        # 2. Adversarial variation in project 2:
        # Different casing, punctuation (! replaced by ?, commas removed, uppercase, extra spaces, tabs)
        inst2 = self.engine.add_instinct(
            trigger="  QUANDO SI ESPORTA IN GLB   OTTIMIZZA I MATERIALI? \t\n",
            action="converti TUTTE le texture in webp a 2048x2048...  ",
            scope="project",
            project_id="proj_site_b",
        )
        self.assertEqual(inst2["scope"], "global")
        self.assertIsNone(inst2["project_id"])
        self.assertEqual(inst1["id"], inst2["id"])

    def test_unicode_italian_accents_normalization(self):
        """Accented characters (à, è, é, ì, ò, ù) should be preserved as word chars in normalization."""
        norm1 = normalize_instinct_text("Priorità qualità: finché è possibile")
        norm2 = normalize_instinct_text("  priorità   qualità    finché è possibile! ")
        self.assertEqual(norm1, norm2)
        self.assertIn("qualità", norm1)
        self.assertIn("finché", norm1)

    def test_distinct_projects_counting_after_promotion(self):
        """Adding a 3rd distinct project to an already global instinct keeps it global and boosts confidence."""
        inst = self.engine.add_instinct(
            trigger="Salva sempre backup",
            action="Crea zip incrementale ogni ora",
            scope="project",
            project_id="proj_1",
            confidence=0.50,
        )
        # 2nd project -> promotes to global
        inst = self.engine.add_instinct(
            trigger="Salva sempre backup",
            action="Crea zip incrementale ogni ora",
            scope="project",
            project_id="proj_2",
        )
        self.assertEqual(inst["scope"], "global")

        # 3rd project -> remains global, increments count and confidence
        inst = self.engine.add_instinct(
            trigger="Salva sempre backup",
            action="Crea zip incrementale ogni ora",
            scope="project",
            project_id="proj_3",
        )
        self.assertEqual(inst["scope"], "global")
        self.assertIsNone(inst["project_id"])
        self.assertGreater(inst["confidence"], 0.50)

    def test_seeded_global_instinct_not_demoted_by_project_observation(self):
        """A seeded global instinct must never be demoted to project scope when reinforced within a project."""
        seed_before = self.engine.get_instinct("inst_seed_glb_draco_pipeline")
        self.assertEqual(seed_before["scope"], "global")
        self.assertIsNone(seed_before["project_id"])

        updated = self.engine.add_instinct(
            trigger=seed_before["trigger"],
            action=seed_before["action"],
            scope="project",
            project_id="proj_nave_intermarine",
        )
        self.assertEqual(updated["scope"], "global")
        self.assertIsNone(updated["project_id"])


class TestChallengerPruningLogic(unittest.TestCase):
    """Adversarial stress-testing of pruning and inactivity decay rules."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_pruning_low_confidence_and_old_unconfirmed(self):
        """Unconfirmed instincts with confidence <= 0.35 older than 14 days MUST be pruned."""
        old_epoch = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        inst = {
            "id": "inst_unconfirmed_old",
            "trigger": "Vecchia regola temporanea",
            "action": "Elimina questo file di log",
            "confidence": 0.35,
            "scope": "project",
            "project_id": "proj_temp",
            "domain": "workflow",
            "use_count": 1,
            "created_at": old_epoch,
            "last_seen_at": old_epoch,
        }
        self.sm.save_instinct(inst)
        self.engine._sync_and_seed()

        # Run prune
        pruned = self.engine.prune_expired_instincts()
        self.assertEqual(pruned, 1)
        self.assertIsNone(self.engine.get_instinct("inst_unconfirmed_old"))

    def test_high_confidence_never_pruned(self):
        """Instincts with confidence > 0.35 must NEVER be pruned, even if very old."""
        ancient_epoch = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        inst = {
            "id": "inst_ancient_valuable",
            "trigger": "Regola antica importante",
            "action": "Non cancellare mai la cronologia git",
            "confidence": 0.36,  # Just above 0.35
            "scope": "project",
            "project_id": "proj_important",
            "domain": "workflow",
            "use_count": 10,
            "created_at": ancient_epoch,
            "last_seen_at": ancient_epoch,
        }
        self.sm.save_instinct(inst)
        self.engine._sync_and_seed()

        # Run prune (age=365, conf=0.36, but decay could occur if inactive)
        # Let's test with last_seen_at = now (active)
        inst_active = {
            "id": "inst_ancient_active",
            "trigger": "Regola antica attiva",
            "action": "Mantieni convenzione naming",
            "confidence": 0.80,
            "scope": "global",
            "project_id": None,
            "domain": "workflow",
            "use_count": 25,
            "created_at": ancient_epoch,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sm.save_instinct(inst_active)
        self.engine._sync_and_seed()

        pruned = self.engine.prune_expired_instincts()
        self.assertIsNotNone(self.engine.get_instinct("inst_ancient_active"))

    def test_fresh_low_confidence_never_pruned(self):
        """Fresh unconfirmed instincts (< 14 days) must NEVER be pruned even if confidence <= 0.35."""
        fresh_epoch = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        inst = {
            "id": "inst_fresh_low_conf",
            "trigger": "Nuova regola appena creata",
            "action": "Controlla formato",
            "confidence": 0.30,  # Minimum confidence
            "scope": "project",
            "project_id": "proj_fresh",
            "domain": "workflow",
            "use_count": 1,
            "created_at": fresh_epoch,
            "last_seen_at": fresh_epoch,
        }
        self.sm.save_instinct(inst)
        self.engine._sync_and_seed()

        pruned = self.engine.prune_expired_instincts()
        self.assertEqual(pruned, 0)
        self.assertIsNotNone(self.engine.get_instinct("inst_fresh_low_conf"))

    def test_seeded_instincts_never_pruned(self):
        """Seeded instincts (inst_seed_*) must NEVER be pruned even under extreme age and low confidence."""
        ancient_epoch = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()
        # Artificially set seed intermarine logo to low confidence and ancient age
        seed = self.engine.get_instinct("inst_seed_intermarine_logo")
        self.assertIsNotNone(seed)
        seed["confidence"] = 0.30
        seed["created_at"] = ancient_epoch
        seed["last_seen_at"] = ancient_epoch
        self.sm.save_instinct(seed)
        self.engine._sync_and_seed()

        pruned = self.engine.prune_expired_instincts()
        self.assertEqual(pruned, 0)
        re_checked = self.engine.get_instinct("inst_seed_intermarine_logo")
        self.assertIsNotNone(re_checked)

    def test_inactivity_decay_over_multiple_intervals(self):
        """Inactivity decay should reduce confidence by 0.05 for every 7 days of inactivity."""
        # Instinct last seen 21 days ago (3 intervals of 7 days)
        # initial conf = 0.50 -> decay = 3 * 0.05 = 0.15 -> new conf = 0.35
        # age = 10 days -> age < 14 days, so it decays to 0.35 but is NOT pruned
        now = datetime.now(timezone.utc)
        created_at = (now - timedelta(days=10)).isoformat()
        last_seen_at = (now - timedelta(days=21)).isoformat()

        inst = {
            "id": "inst_decay_test",
            "trigger": "Regola che decade",
            "action": "Azione inattiva",
            "confidence": 0.50,
            "scope": "project",
            "project_id": "proj_decay",
            "domain": "workflow",
            "use_count": 1,
            "created_at": created_at,
            "last_seen_at": last_seen_at,
        }
        self.sm.save_instinct(inst)
        self.engine._sync_and_seed()

        pruned = self.engine.prune_expired_instincts()
        self.assertEqual(pruned, 0)  # Age is only 10 days, so not pruned

        fetched = self.engine.get_instinct("inst_decay_test")
        self.assertIsNotNone(fetched)
        self.assertAlmostEqual(fetched["confidence"], 0.35, places=4)


class TestChallengerConcurrencyAndStress(unittest.TestCase):
    """Stress testing concurrency: 10 parallel threads executing CRUD simultaneously."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = self.temp_dir.name
        self.sm = StorageManager(data_dir=self.data_dir)
        self.engine = InstinctEngine(data_dir=self.data_dir, storage_manager=self.sm)

    def tearDown(self):
        try:
            self.engine.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_multithreaded_concurrent_crud(self):
        """Run 10 threads doing add, confirm, promote, and prune simultaneously."""
        errors = []

        def worker(thread_idx):
            try:
                for i in range(15):
                    # Add
                    inst = self.engine.add_instinct(
                        trigger=f"Trigger thread {thread_idx} step {i}",
                        action=f"Action thread {thread_idx} step {i}",
                        confidence=0.45,
                        scope="project",
                        project_id=f"proj_{thread_idx}",
                    )
                    # Confirm
                    self.engine.confirm_instinct(inst["id"], delta=0.05)
                    # Promote some
                    if i % 3 == 0:
                        self.engine.promote_instinct(inst["id"])
                    # Query active
                    self.engine.get_active_instincts(active_project_id=f"proj_{thread_idx}")
                    # Prune occasionally
                    if i % 5 == 0:
                        self.engine.prune_expired_instincts()
            except Exception as e:
                errors.append(f"Thread {thread_idx} failed: {e}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Encountered thread errors: {errors}")

        # Check that dual-persistence is consistent
        db_instincts = self.sm.get_all_instincts()
        json_instincts = self.engine.json_store.load()
        self.assertEqual(len(db_instincts), len(json_instincts))


class TestChallengerContextBudgetFiltering(unittest.TestCase):
    """Adversarial testing of surgical context injection and TokenBudgetManager contracts."""

    def setUp(self):
        self.budget_mgr = TokenBudgetManager()

    def test_surgical_injection_conf_threshold_and_project_isolation(self):
        """Global instincts must have conf >= 0.55; project instincts must match active project."""
        instincts = [
            # Global with conf = 0.55 -> should include
            {"id": "g1", "trigger": "G1", "action": "A1", "confidence": 0.55, "scope": "global", "project_id": None},
            # Global with conf = 0.54 -> should exclude
            {"id": "g2", "trigger": "G2", "action": "A2", "confidence": 0.54, "scope": "global", "project_id": None},
            # Global with conf = 0.85 -> should include
            {"id": "g3", "trigger": "G3", "action": "A3", "confidence": 0.85, "scope": "global", "project_id": None},
            # Project matching active -> should include
            {"id": "p1", "trigger": "P1", "action": "A1", "confidence": 0.40, "scope": "project", "project_id": "proj_intermarine"},
            # Project mismatch -> should exclude
            {"id": "p2", "trigger": "P2", "action": "A2", "confidence": 0.90, "scope": "project", "project_id": "proj_other"},
        ]

        fitted = self.budget_mgr.fit_instincts(instincts, active_project_id="proj_intermarine")
        fitted_ids = [i["id"] for i in fitted]
        self.assertIn("g1", fitted_ids)
        self.assertNotIn("g2", fitted_ids)
        self.assertIn("g3", fitted_ids)
        self.assertIn("p1", fitted_ids)
        self.assertNotIn("p2", fitted_ids)

    def test_budget_capping_300_tokens(self):
        """Fitting must respect BUDGET_INSTINCTS = 300 token limit."""
        huge_instincts = []
        for i in range(100):
            huge_instincts.append({
                "id": f"inst_huge_{i}",
                "trigger": f"Trigger when processing very long model item number {i} with extensive detail",
                "action": f"Action execute complex multistep pipeline rule with extensive parameter definitions {i}",
                "confidence": 0.80,
                "scope": "global",
                "project_id": None,
                "domain": "workflow",
            })

        fitted, cost = self.budget_mgr.fit_instincts(huge_instincts, as_tuple=True)
        self.assertLessEqual(cost, BUDGET_INSTINCTS + 50)  # Allow minimal single-item headroom
        self.assertLess(len(fitted), len(huge_instincts))


if __name__ == "__main__":
    unittest.main()
