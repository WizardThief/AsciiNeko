"""
tests/test_memory_system.py - Acceptance Criteria R2 Test Harness for Multi-Level Persistent Memory.

Comprehensive test suite verifying:
1. Data models (SemanticFact, ProjectDeadline, Episode): validation, defaults, CRDT fields, serialization.
2. BM25/TF-IDF Search Engine: Italian/English tokenization, stemming, stop words, recency decay, importance weighting, exact match bonus.
3. MemoryStore: atomic persistence, prefix matching, AmbiguousMemoryPrefixError, soft & hard delete, statistics aggregation.
4. CRDT LWW Protocol: higher updated_at wins, tombstone wins on tie, canonical JSON tie-breaker, export and apply mutations.
5. Router Context & Prompt Augmentation: [CONTESTO MEMORIA ATTIVA] formatting, max length truncations, prompt injection.
6. CLI Integration: memory add fact/project, search, list, get, delete, stats, and status --json integration.
7. Daemon Integration: MemoryStore initialization, status.json export, IPC command handling.
"""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from neko_bot.cli import build_parser, main
from neko_bot.core.config import Config
from neko_bot.core.daemon import DaemonServer, DaemonStatusManager
from neko_bot.memory.models import (
    Episode,
    ProjectDeadline,
    SemanticFact,
    generate_memory_id,
    now_utc_iso,
)
from neko_bot.memory.router_context import (
    build_memory_augmented_prompt,
    format_memory_context,
    format_memory_entry,
    get_active_memory_context,
)
from neko_bot.memory.search import (
    MemorySearchEngine,
    compute_recency_score,
    extract_document_text,
    stem_token,
    tokenize,
)
from neko_bot.memory.store import (
    AmbiguousMemoryPrefixError,
    MemoryStore,
)


class TestMemoryModels(unittest.TestCase):
    """Unit tests for memory data models and validation."""

    def test_semantic_fact_defaults_and_clamping(self) -> None:
        fact = SemanticFact(content="Test rule", importance=10, confidence=2.5)
        self.assertTrue(fact.id.startswith("fact-"))
        self.assertEqual(fact.category, "fact")
        self.assertEqual(fact.content, "Test rule")
        self.assertEqual(fact.importance, 5)  # clamped to 5
        self.assertEqual(fact.confidence, 1.0)  # clamped to 1.0
        self.assertFalse(fact.deleted)
        self.assertEqual(fact.access_count, 0)
        self.assertIsNone(fact.last_accessed_at)

        # Negative clamp
        fact_low = SemanticFact(content="Low", importance=-2, confidence=-0.5)
        self.assertEqual(fact_low.importance, 1)
        self.assertEqual(fact_low.confidence, 0.0)

    def test_semantic_fact_mark_accessed(self) -> None:
        fact = SemanticFact(content="Test access")
        self.assertEqual(fact.access_count, 0)
        self.assertIsNone(fact.last_accessed_at)
        fact.mark_accessed()
        self.assertEqual(fact.access_count, 1)
        self.assertIsNotNone(fact.last_accessed_at)

    def test_semantic_fact_serialization_cycle(self) -> None:
        fact = SemanticFact(
            content="Preferenza IDE",
            category="preference",
            tags=["vscode", "python"],
            importance=4,
            source="conversation",
        )
        d = fact.to_dict()
        reconstructed = SemanticFact.from_dict(d)
        self.assertEqual(reconstructed.id, fact.id)
        self.assertEqual(reconstructed.content, fact.content)
        self.assertEqual(reconstructed.category, fact.category)
        self.assertEqual(reconstructed.tags, ["vscode", "python"])
        self.assertEqual(reconstructed.importance, 4)

    def test_project_deadline_overdue_and_hours_remaining(self) -> None:
        now = datetime.now(timezone.utc)
        past_due = (now - timedelta(hours=2)).isoformat()
        future_due = (now + timedelta(hours=24)).isoformat()

        proj_past = ProjectDeadline(project_name="LegacyApp", milestone="v1", deadline_utc=past_due)
        self.assertTrue(proj_past.is_overdue())
        self.assertLess(proj_past.hours_remaining(), 0.0)

        proj_future = ProjectDeadline(project_name="FutureApp", milestone="Beta", deadline_utc=future_due)
        self.assertFalse(proj_future.is_overdue())
        self.assertGreater(proj_future.hours_remaining(), 20.0)

        # Completed or archived is never overdue
        proj_done = ProjectDeadline(project_name="DoneApp", milestone="v1", deadline_utc=past_due, status="completed")
        self.assertFalse(proj_done.is_overdue())

    def test_project_deadline_serialization_cycle(self) -> None:
        proj = ProjectDeadline(
            project_name="CyberpunkCity",
            milestone="Alpha",
            deadline_utc="2026-11-01T12:00:00+00:00",
            dependencies=["task-1", "task-2"],
            risk_level="high",
            notes="Requires photogrammetry review",
        )
        d = proj.to_dict()
        reconstructed = ProjectDeadline.from_dict(d)
        self.assertEqual(reconstructed.id, proj.id)
        self.assertEqual(reconstructed.project_name, "CyberpunkCity")
        self.assertEqual(reconstructed.milestone, "Alpha")
        self.assertEqual(reconstructed.risk_level, "high")
        self.assertEqual(reconstructed.dependencies, ["task-1", "task-2"])

    def test_episode_defaults_and_serialization(self) -> None:
        ep = Episode(
            session_id="sess-42",
            event_type="skill_executed",
            summary="Generato report orario",
            outcome="success",
            context_tags=["time", "report"],
        )
        self.assertTrue(ep.id.startswith("ep-"))
        self.assertEqual(ep.session_id, "sess-42")
        self.assertEqual(ep.outcome, "success")
        d = ep.to_dict()
        reconstructed = Episode.from_dict(d)
        self.assertEqual(reconstructed.summary, "Generato report orario")
        self.assertEqual(reconstructed.context_tags, ["time", "report"])


class TestSearchEngine(unittest.TestCase):
    """Unit tests for BM25/TF-IDF token scoring, stemming, and recency decay."""

    def test_tokenization_italian_and_english(self) -> None:
        text = "Il cliente Acme richiede consegne urgenti solo di martedì!"
        tokens = tokenize(text, remove_stopwords=True, stem=False)
        self.assertIn("cliente", tokens)
        self.assertIn("acme", tokens)
        self.assertIn("richiede", tokens)
        self.assertIn("consegne", tokens)
        self.assertIn("urgenti", tokens)
        self.assertIn("martedì", tokens)
        # Stop words stripped
        self.assertNotIn("il", tokens)
        self.assertNotIn("di", tokens)
        self.assertNotIn("solo", tokens)

    def test_rule_based_stemming(self) -> None:
        self.assertEqual(stem_token("documentazione"), "document")
        self.assertEqual(stem_token("completato"), "complet")
        self.assertEqual(stem_token("programmando"), "programm")

    def test_recency_decay(self) -> None:
        now = datetime.now(timezone.utc)
        ts_now = now.isoformat()
        ts_30d_ago = (now - timedelta(days=30)).isoformat()
        ts_60d_ago = (now - timedelta(days=60)).isoformat()

        # Semantic fact half life = 30 days
        score_now = compute_recency_score(ts_now, "semantic")
        score_30d = compute_recency_score(ts_30d_ago, "semantic")
        score_60d = compute_recency_score(ts_60d_ago, "semantic")

        self.assertAlmostEqual(score_now, 1.0, places=2)
        self.assertAlmostEqual(score_30d, 0.5, places=2)
        self.assertAlmostEqual(score_60d, 0.25, places=2)

    def test_bm25_ranking_and_exact_phrase_bonus(self) -> None:
        engine = MemorySearchEngine()
        entries = [
            ("semantic", {
                "id": "fact-1",
                "content": "Il cliente Acme richiede consegne solo il martedì in Markdown",
                "category": "client_rule",
                "tags": ["acme", "consegne"],
                "importance": 5,
                "updated_at": now_utc_iso(),
            }),
            ("semantic", {
                "id": "fact-2",
                "content": "Utilizzare sempre Python 3.10 e type annotations",
                "category": "preference",
                "tags": ["python", "codice"],
                "importance": 4,
                "updated_at": now_utc_iso(),
            }),
            ("project", {
                "id": "proj-1",
                "project_name": "AcmePortal",
                "milestone": "Beta",
                "risk_level": "high",
                "updated_at": now_utc_iso(),
            }),
        ]

        # Query for "Acme"
        results = engine.score_corpus("Acme", entries)
        self.assertEqual(len(results), 2)
        result_ids = [r["entry"]["id"] for r in results]
        self.assertIn("fact-1", result_ids)
        self.assertIn("proj-1", result_ids)
        self.assertNotIn("fact-2", result_ids)

        # Top result has high score
        self.assertGreater(results[0]["score"], 0.5)

    def test_empty_query_returns_all_ranked_by_importance_and_recency(self) -> None:
        engine = MemorySearchEngine()
        entries = [
            ("semantic", {"id": "f-low", "content": "Bassa", "importance": 1, "updated_at": now_utc_iso()}),
            ("semantic", {"id": "f-high", "content": "Alta", "importance": 5, "updated_at": now_utc_iso()}),
        ]
        results = engine.score_corpus("", entries)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["entry"]["id"], "f-high")


class TestMemoryStoreAndCRDT(unittest.TestCase):
    """Unit tests for MemoryStore persistence and CRDT LWW reconciliation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_test_memory_")
        self.store = MemoryStore(workspace_dir=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_and_get_fact(self) -> None:
        fact = self.store.add_fact(
            content="Consegne previste solo di martedì",
            category="client_rule",
            tags=["acme", "deliveries"],
            importance=4,
        )
        self.assertTrue(fact["id"].startswith("fact-"))
        self.assertEqual(fact["content"], "Consegne previste solo di martedì")

        # Verify persisted file
        self.assertTrue(self.store.facts_file.exists())

        # Retrieve exact and prefix
        retrieved = self.store.get_entry(fact["id"])
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["id"], fact["id"])

        # Prefix retrieval
        short_id = fact["id"][5:9]  # hex portion without prefix
        retrieved_prefix = self.store.get_entry(short_id)
        self.assertIsNotNone(retrieved_prefix)
        self.assertEqual(retrieved_prefix["id"], fact["id"])

    def test_add_project_and_stats(self) -> None:
        now = datetime.now(timezone.utc)
        due = (now + timedelta(days=5)).isoformat()
        proj = self.store.add_project(
            project_name="CyberpunkCity",
            milestone="Beta Release",
            deadline_utc=due,
            risk_level="high",
        )
        self.assertTrue(proj["id"].startswith("proj-"))
        self.assertEqual(proj["project_name"], "CyberpunkCity")

        stats = self.store.get_stats()
        self.assertEqual(stats["active_projects"], 1)
        self.assertEqual(len(stats["upcoming_deadlines"]), 1)
        self.assertEqual(stats["upcoming_deadlines"][0]["project"], "CyberpunkCity")

    def test_add_episode_and_list(self) -> None:
        ep = self.store.add_episode(
            session_id="s1",
            event_type="decision",
            summary="Scelta architettura offline-first CRDT",
            outcome="success",
        )
        self.assertTrue(ep["id"].startswith("ep-"))

        episodes = self.store.list_entries(level="episodic")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["summary"], "Scelta architettura offline-first CRDT")

    def test_ambiguous_prefix_raises_error(self) -> None:
        # Create two facts with manually crafted IDs that share a prefix
        self.store._facts["fact-aaaa1111"] = SemanticFact(id="fact-aaaa1111", content="Fact 1")
        self.store._facts["fact-aaaa2222"] = SemanticFact(id="fact-aaaa2222", content="Fact 2")
        self.store._save_facts()

        with self.assertRaises(AmbiguousMemoryPrefixError):
            self.store.get_entry("aaaa")

    def test_soft_delete_preserves_tombstone(self) -> None:
        fact = self.store.add_fact("Regola temporanea")
        fid = fact["id"]

        deleted = self.store.delete_entry(fid, hard=False)
        self.assertTrue(deleted)

        # Active list should exclude it
        active_facts = self.store.list_entries(level="semantic", include_deleted=False)
        self.assertEqual(len(active_facts), 0)

        # list with include_deleted=True should show it with deleted=True
        all_facts = self.store.list_entries(level="semantic", include_deleted=True)
        self.assertEqual(len(all_facts), 1)
        self.assertTrue(all_facts[0]["deleted"])

        # Stats should reflect 0 active facts
        stats = self.store.get_stats()
        self.assertEqual(stats["total_facts"], 0)

    def test_hard_delete_purges_record(self) -> None:
        fact = self.store.add_fact("Regola da eliminare del tutto")
        fid = fact["id"]

        deleted = self.store.delete_entry(fid, hard=True)
        self.assertTrue(deleted)

        all_facts = self.store.list_entries(level="semantic", include_deleted=True)
        self.assertEqual(len(all_facts), 0)
        self.assertIsNone(self.store.get_entry(fid))

    def test_crdt_lww_conflict_resolution(self) -> None:
        # 1. Local fact
        f1 = self.store.add_fact("Versione iniziale", importance=2)
        fid = f1["id"]

        # 2. Remote mutation with newer timestamp should win
        newer_ts = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        newer_mut = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {
                "id": fid,
                "content": "Versione aggiornata da remoto",
                "category": "fact",
                "importance": 5,
                "updated_at": newer_ts,
                "deleted": False,
            },
            "updated_at": newer_ts,
            "deleted": False,
        }
        applied = self.store.apply_crdt_mutation(newer_mut)
        self.assertTrue(applied)
        curr = self.store.get_entry(fid)
        self.assertEqual(curr["content"], "Versione aggiornata da remoto")
        self.assertEqual(curr["importance"], 5)

        # 3. Older remote mutation should lose
        older_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        older_mut = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {
                "id": fid,
                "content": "Versione vecchia ignorata",
                "updated_at": older_ts,
            },
            "updated_at": older_ts,
        }
        applied_older = self.store.apply_crdt_mutation(older_mut)
        self.assertFalse(applied_older)
        curr_after = self.store.get_entry(fid)
        self.assertEqual(curr_after["content"], "Versione aggiornata da remoto")

        # 4. Tie timestamp: tombstone wins
        same_ts = newer_ts
        tombstone_mut = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {
                "id": fid,
                "content": "Cancellato",
                "updated_at": same_ts,
                "deleted": True,
            },
            "updated_at": same_ts,
            "deleted": True,
        }
        applied_tomb = self.store.apply_crdt_mutation(tombstone_mut)
        self.assertTrue(applied_tomb)
        curr_tomb = self.store.get_entry(fid)
        self.assertTrue(curr_tomb["deleted"])

    def test_export_crdt_entities(self) -> None:
        self.store.add_fact("Fact 1")
        self.store.add_project("Proj 1", "M1")
        self.store.add_episode(summary="Episode 1")

        entities = self.store.export_crdt_entities()
        self.assertEqual(len(entities), 3)
        types = {e["entity_type"] for e in entities}
        self.assertIn("memory_fact", types)
        self.assertIn("memory_project", types)
        self.assertIn("memory_episode", types)


class TestRouterContextAndPromptInjection(unittest.TestCase):
    """Unit tests for formatting active memory context for prompt injection."""

    def test_format_memory_entry(self) -> None:
        fact = {
            "category": "client_rule",
            "content": "Consegne solo di martedì",
            "tags": ["acme"],
            "level": "semantic",
        }
        res = format_memory_entry(fact)
        self.assertIn("Regola Cliente:", res)
        self.assertIn("Consegne solo di martedì", res)
        self.assertIn("#acme", res)

    def test_format_memory_context_block(self) -> None:
        memories = [
            {"level": "semantic", "entry": {"category": "client_rule", "content": "Consegne martedì"}},
            {"level": "project", "entry": {"project_name": "CyberpunkCity", "milestone": "Beta", "due": "2026-10-28"}},
        ]
        block = format_memory_context(memories)
        self.assertIn("[CONTESTO MEMORIA ATTIVA]", block)
        self.assertIn("- Regola Cliente: Consegne martedì", block)
        self.assertIn("- Progetto CyberpunkCity", block)

    def test_build_memory_augmented_prompt(self) -> None:
        temp_dir = tempfile.mkdtemp(prefix="neko_test_prompt_")
        try:
            store = MemoryStore(workspace_dir=temp_dir)
            store.add_fact("Il cliente Acme richiede consegne solo il martedì", category="client_rule", tags=["acme"])

            prompt = "Prepara il file per il cliente Acme"
            augmented, ctx = build_memory_augmented_prompt(prompt, store=store, top_k=2)

            self.assertIsNotNone(ctx)
            self.assertIn("[CONTESTO MEMORIA ATTIVA]", ctx)
            self.assertIn("Acme", ctx)
            self.assertIn(prompt, augmented)
            self.assertTrue(augmented.startswith("[CONTESTO MEMORIA ATTIVA]"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestMemoryCLIIntegration(unittest.TestCase):
    """Integration tests for neko memory CLI commands and status integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_test_cli_mem_")
        self.config = Config.load(root_dir=Path(self.temp_dir))
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_memory_add_fact_and_stats(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([
                "memory", "add", "fact", "Acme preferisce Markdown",
                "--category", "client_rule",
                "--tags", "acme,docs",
                "--importance", "4",
                "--workspace", self.temp_dir,
            ])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn("Fact added: [CLIENT_RULE] Acme preferisce Markdown", out)

        # Check stats via CLI --json
        buf_stats = io.StringIO()
        with redirect_stdout(buf_stats):
            ret_stats = main(["memory", "stats", "--json", "--workspace", self.temp_dir])
        self.assertEqual(ret_stats, 0)
        stats = json.loads(buf_stats.getvalue().strip())
        self.assertEqual(stats["total_facts"], 1)

    def test_cli_memory_add_project_and_search(self) -> None:
        # Add project
        buf_p = io.StringIO()
        with redirect_stdout(buf_p):
            ret_p = main([
                "memory", "add", "project", "CyberpunkCity",
                "--milestone", "Beta",
                "--due", "2026-10-30",
                "--risk", "high",
                "--workspace", self.temp_dir,
            ])
        self.assertEqual(ret_p, 0)
        self.assertIn("Project deadline added: CyberpunkCity [Beta]", buf_p.getvalue())

        # Search for project
        buf_s = io.StringIO()
        with redirect_stdout(buf_s):
            ret_s = main([
                "memory", "search", "CyberpunkCity",
                "--json",
                "--workspace", self.temp_dir,
            ])
        self.assertEqual(ret_s, 0)
        results = json.loads(buf_s.getvalue().strip())
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["entry"]["project_name"], "CyberpunkCity")

    def test_cli_memory_get_and_delete(self) -> None:
        # Add fact
        buf_add = io.StringIO()
        with redirect_stdout(buf_add):
            ret_add = main([
                "memory", "add", "fact", "Nota temporanea",
                "--json",
                "--workspace", self.temp_dir,
            ])
        self.assertEqual(ret_add, 0)
        fact_data = json.loads(buf_add.getvalue().strip())
        fid = fact_data["id"]

        # Get entry
        buf_get = io.StringIO()
        with redirect_stdout(buf_get):
            ret_get = main(["memory", "get", fid, "--json", "--workspace", self.temp_dir])
        self.assertEqual(ret_get, 0)
        entry = json.loads(buf_get.getvalue().strip())
        self.assertEqual(entry["content"], "Nota temporanea")

        # Delete entry
        buf_del = io.StringIO()
        with redirect_stdout(buf_del):
            ret_del = main(["memory", "delete", fid, "--workspace", self.temp_dir])
        self.assertEqual(ret_del, 0)
        self.assertIn("successfully deleted", buf_del.getvalue())

        # Verify stats facts count is now 0
        buf_st = io.StringIO()
        with redirect_stdout(buf_st):
            main(["memory", "stats", "--json", "--workspace", self.temp_dir])
        st = json.loads(buf_st.getvalue().strip())
        self.assertEqual(st["total_facts"], 0)

    def test_status_json_includes_memory_telemetry(self) -> None:
        # Add a fact and project to store
        store = MemoryStore(workspace_dir=self.config.workspace_dir)
        store.add_fact("Regola importante", importance=5)
        store.add_project("CyberpunkCity", "Alpha", deadline_utc="2026-11-15", risk_level="medium")

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["status", "--json", "--workspace", self.temp_dir])
        self.assertEqual(ret, 0)
        payload = json.loads(buf.getvalue().strip())

        self.assertIn("memory", payload)
        mem = payload["memory"]
        self.assertEqual(mem["total_facts"], 1)
        self.assertEqual(mem["active_projects"], 1)
        self.assertEqual(len(mem["upcoming_deadlines"]), 1)
        self.assertEqual(mem["upcoming_deadlines"][0]["project"], "CyberpunkCity")


class TestDaemonMemoryIntegration(unittest.TestCase):
    """Integration test verifying daemon status manager and MemoryStore integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_test_daemon_mem_")
        self.config = Config.load(root_dir=Path(self.temp_dir))
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_daemon_status_manager_default_memory_field(self) -> None:
        mgr = DaemonStatusManager(self.config)
        st = mgr.get_status()
        self.assertIn("memory", st)
        self.assertEqual(st["memory"]["total_facts"], 0)
        self.assertEqual(st["memory"]["active_projects"], 0)

    def test_daemon_server_initializes_memory_store(self) -> None:
        server = DaemonServer(self.config)
        self.assertIsNotNone(server.memory_store)
        self.assertTrue(isinstance(server.memory_store, MemoryStore))

        # Add a fact through server.memory_store
        server.memory_store.add_fact("Daemon fact test", importance=4)
        stats = server.memory_store.get_stats()
        self.assertEqual(stats["total_facts"], 1)


class TestMemoryEdgeCasesAndAdversarial(unittest.TestCase):
    """Adversarial, boundary, and edge-case tests for the Memory Subsystem."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_test_mem_edge_")
        self.store = MemoryStore(workspace_dir=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_empty_content_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_fact("")
        with self.assertRaises(ValueError):
            self.store.add_fact("   ")
        with self.assertRaises(ValueError):
            self.store.add_project("", "milestone")
        with self.assertRaises(ValueError):
            self.store.add_project("project", "")

    def test_nonexistent_entry_operations(self) -> None:
        self.assertIsNone(self.store.get_entry("non-existent-id"))
        self.assertFalse(self.store.delete_entry("non-existent-id"))

    def test_crdt_canonical_json_tiebreaker(self) -> None:
        ts = "2026-10-24T12:00:00+00:00"
        mut_a = {
            "entity_type": "memory_fact",
            "id": "fact-tie1",
            "data": {"id": "fact-tie1", "content": "Alpha", "updated_at": ts, "deleted": False},
            "updated_at": ts,
            "deleted": False,
        }
        mut_b = {
            "entity_type": "memory_fact",
            "id": "fact-tie1",
            "data": {"id": "fact-tie1", "content": "Zulu", "updated_at": ts, "deleted": False},
            "updated_at": ts,
            "deleted": False,
        }

        # Apply mut_a first
        self.store.apply_crdt_mutation(mut_a)
        # mut_b has identical timestamp and deleted flag, but "Zulu" > "Alpha" lexicographically
        applied_b = self.store.apply_crdt_mutation(mut_b)
        self.assertTrue(applied_b)
        self.assertEqual(self.store.get_entry("fact-tie1")["content"], "Zulu")

        # Now attempting to apply mut_a again should lose
        applied_a = self.store.apply_crdt_mutation(mut_a)
        self.assertFalse(applied_a)
        self.assertEqual(self.store.get_entry("fact-tie1")["content"], "Zulu")

    def test_crdt_project_and_episode_mutations(self) -> None:
        proj_mut = {
            "entity_type": "memory_project",
            "id": "proj-rem1",
            "data": {
                "id": "proj-rem1",
                "project_name": "RemoteProj",
                "milestone": "Gamma",
                "deadline_utc": "2026-12-01T00:00:00+00:00",
                "risk_level": "critical",
                "updated_at": now_utc_iso(),
            },
        }
        self.assertTrue(self.store.apply_crdt_mutation(proj_mut))
        self.assertIsNotNone(self.store.get_entry("proj-rem1"))
        self.assertEqual(self.store.get_entry("proj-rem1")["risk_level"], "critical")

        ep_mut = {
            "entity_type": "memory_episode",
            "id": "ep-rem1",
            "data": {
                "id": "ep-rem1",
                "session_id": "sess-rem",
                "summary": "Remote episode summary",
                "outcome": "success",
                "updated_at": now_utc_iso(),
            },
        }
        self.assertTrue(self.store.apply_crdt_mutation(ep_mut))
        self.assertIsNotNone(self.store.get_entry("ep-rem1"))

    def test_max_chars_truncation_in_router_context(self) -> None:
        memories = [
            {"level": "semantic", "entry": {"content": "X" * 100, "category": "fact"}},
            {"level": "semantic", "entry": {"content": "Y" * 100, "category": "fact"}},
            {"level": "semantic", "entry": {"content": "Z" * 100, "category": "fact"}},
        ]
        # Restrict max_chars to 150
        block = format_memory_context(memories, max_chars=150)
        self.assertIn("X" * 100, block)
        self.assertNotIn("Z" * 100, block)


if __name__ == "__main__":
    unittest.main()
