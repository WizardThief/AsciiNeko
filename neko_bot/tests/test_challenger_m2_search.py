"""
neko_bot/tests/test_challenger_m2_search.py - Adversarial Stress & Empirical Benchmark Suite for MemorySearchEngine.

Author: challenger_m2_2 (Empirical Challenger)
Milestone: Milestone 2 (Multi-Level Persistent Memory System)

Adversarial Verification Scope:
1. BM25 scoring with boundary conditions:
   - Empty corpus, single document, single word, repetitive words (10k tokens)
   - Empty documents, stopword-only documents, special symbols, punctuation
   - Unicode & Italian accented characters (città, caffè, perché, università, ecc.)
   - Italian rule-based stemming edge cases
2. Mathematical stability & ZeroDivisionError resistance:
   - avgdl == 0 (all documents have length 0)
   - Single document corpus (IDF calculation stability)
   - Empty query, whitespace query, stopword-only query, punctuation-only query
   - Zero max_raw_bm25 normalization stability
3. Time decay exponential bounds & clock drift:
   - Clock skew / future timestamps (negative delta_t must not explode > 1.0)
   - Exact now (delta_t == 0 -> recency == 1.0)
   - Exact half-life decay (semantic=30d, project=14d, episode=7d -> recency == 0.5)
   - Extreme past (10 years ago -> recency >= 0.0)
   - Malformed timestamp handling (None, invalid strings, malformed ISO)
4. Input robustness against None / missing values (Graceful handling required):
   - Level as None
   - entry['tags'] as None
   - entry['importance'] as None
   - entry['risk_level'] as None
   - entry['project_name'] as None
5. Empirical benchmark on 1,000 memory entries:
   - Verification of < 5ms query SLA across repeated benchmark trials
"""

from datetime import datetime, timedelta, timezone
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Tuple
import unittest

from neko_bot.memory.search import (
    MemorySearchEngine,
    compute_recency_score,
    extract_document_text,
    tokenize,
)


class TestBM25BoundaryAndStress(unittest.TestCase):
    """Adversarial boundary stress tests on BM25 corpus scoring and tokenization."""

    def setUp(self) -> None:
        self.engine = MemorySearchEngine()

    def test_empty_corpus(self) -> None:
        """Empty corpus should immediately return empty list without error."""
        res = self.engine.score_corpus("test query", [])
        self.assertEqual(res, [])

    def test_all_deleted_corpus(self) -> None:
        """Corpus where all entries have deleted=True should return empty list."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "test item 1", "deleted": True}),
            ("semantic", {"content": "test item 2", "deleted": True}),
        ]
        res = self.engine.score_corpus("test", entries)
        self.assertEqual(res, [])

    def test_single_document_corpus_matching(self) -> None:
        """Single document corpus matching query should score successfully without math errors."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "progetto ascineko architettura", "importance": 4}),
        ]
        res = self.engine.score_corpus("ascineko", entries)
        self.assertEqual(len(res), 1)
        self.assertGreater(res[0]["score"], 0.0)
        self.assertGreater(res[0]["sim_score"], 0.0)

    def test_single_document_corpus_non_matching(self) -> None:
        """Single document corpus not matching query should be filtered out."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "progetto ascineko architettura", "importance": 4}),
        ]
        res = self.engine.score_corpus("irrilevante", entries)
        self.assertEqual(len(res), 0)

    def test_repetitive_words_10k_tokens(self) -> None:
        """Document with 10,000 repetitions of the same word should not overflow or cause division/scaling issues."""
        repetitive_content = " ".join(["ripetizione"] * 10000)
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": repetitive_content, "importance": 5}),
            ("semantic", {"content": "breve menzione di ripetizione qui", "importance": 3}),
        ]
        res = self.engine.score_corpus("ripetizione", entries)
        self.assertEqual(len(res), 2)
        # Verify valid float scores
        for r in res:
            self.assertFalse(math.isnan(r["score"]))
            self.assertFalse(math.isinf(r["score"]))
            self.assertGreaterEqual(r["score"], 0.0)
            self.assertLessEqual(r["score"], 1.0)

    def test_special_symbols_and_punctuation_handling(self) -> None:
        """Corpus and queries with complex symbols, emojis, and punctuation."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "!@#$%^&*()_+=-{}[]:;\"'|<>,.?/ ~`", "importance": 3}),
            ("semantic", {"content": "🤖 AsciiNeko v2.0 - [DEBUG] :: (CRITICAL)!!", "importance": 4}),
            ("episodic", {"summary": "Crash in 0xDEADBEEF: null pointer dereference", "importance": 5}),
        ]
        res = self.engine.score_corpus("!@#$%^", entries)
        self.assertIsInstance(res, list)

        res2 = self.engine.score_corpus("asciineko debug", entries)
        self.assertGreaterEqual(len(res2), 1)
        self.assertEqual(res2[0]["entry"]["importance"], 4)

    def test_unicode_and_italian_accents(self) -> None:
        """Verify handling of accented Italian words and match fidelity."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "L'università ha sede nella città principale perché è antica", "importance": 3}),
            ("semantic", {"content": "Prendiamo un caffè più tardi già organizzato", "importance": 4}),
        ]
        res_univ = self.engine.score_corpus("università", entries)
        self.assertGreaterEqual(len(res_univ), 1)
        self.assertIn("università", res_univ[0]["entry"]["content"])

        res_caffe = self.engine.score_corpus("caffè", entries)
        self.assertGreaterEqual(len(res_caffe), 1)
        self.assertIn("caffè", res_caffe[0]["entry"]["content"])

    def test_italian_stemming_variations(self) -> None:
        """Italian stemming should match common inflections and suffixes."""
        t1 = tokenize("sviluppatore sviluppando sviluppato sviluppare")
        self.assertTrue(all(t.startswith("svilupp") for t in t1))

        t2 = tokenize("configurazione configurazioni")
        self.assertEqual(t2[0], t2[1])


class TestMathematicalStability(unittest.TestCase):
    """Stress tests targeting mathematical edge cases, log domain errors, and ZeroDivisionError."""

    def setUp(self) -> None:
        self.engine = MemorySearchEngine()

    def test_avgdl_zero_all_empty_documents(self) -> None:
        """When all documents in corpus have doc_len == 0, avgdl is 0 -> must not raise ZeroDivisionError."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "", "category": "", "tags": []}),
            ("semantic", {"content": "   ", "category": "", "tags": []}),
            ("project", {"project_name": "", "milestone": "", "notes": "", "risk_level": "medium", "dependencies": []}),
        ]
        res_empty = self.engine.score_corpus("", entries)
        self.assertEqual(len(res_empty), 3)
        for r in res_empty:
            self.assertFalse(math.isnan(r["score"]))

        res_query = self.engine.score_corpus("search_token", entries)
        self.assertEqual(res_query, [])

    def test_avgdl_zero_all_stopwords_documents(self) -> None:
        """When all documents contain only stopwords, doc_len == 0 after token filtering."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "il lo la i gli le un uno una", "tags": []}),
            ("semantic", {"content": "the a an and or to in on for", "tags": []}),
        ]
        res = self.engine.score_corpus("test", entries)
        self.assertEqual(res, [])

    def test_empty_and_whitespace_query(self) -> None:
        """Empty or whitespace queries must return all non-deleted documents ranked by importance and recency."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "bassa priorità", "importance": 1}),
            ("semantic", {"content": "alta priorità", "importance": 5}),
        ]
        for empty_q in ["", "   ", "\t\t\n", "    "]:
            res = self.engine.score_corpus(empty_q, entries)
            self.assertEqual(len(res), 2)
            self.assertGreater(res[0]["score"], res[1]["score"])
            self.assertEqual(res[0]["entry"]["importance"], 5)

    def test_stopword_only_query(self) -> None:
        """Query with only stopwords: query_tokens is empty."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "un cane nella casa", "importance": 3}),
            ("semantic", {"content": "gatto sul tavolo", "importance": 3}),
        ]
        res = self.engine.score_corpus("nella", entries)
        self.assertIsInstance(res, list)

    def test_normalization_when_max_raw_bm25_is_zero(self) -> None:
        """max_raw_bm25 == 0 division guard."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "alpha beta gamma", "importance": 3}),
        ]
        res = self.engine.score_corpus("zebra", entries)
        self.assertEqual(res, [])

    def test_idf_monotonicity_and_bounds(self) -> None:
        """IDF should remain strictly positive and well-behaved for rare vs ubiquitous terms."""
        entries: List[Tuple[str, Dict[str, Any]]] = [
            ("semantic", {"content": "comune raro", "importance": 3}),
            ("semantic", {"content": "comune differente", "importance": 3}),
            ("semantic", {"content": "comune ancora", "importance": 3}),
        ]
        res_raro = self.engine.score_corpus("raro", entries)
        res_comune = self.engine.score_corpus("comune", entries)
        self.assertEqual(len(res_raro), 1)
        self.assertEqual(len(res_comune), 3)


class TestTimeDecayAndClockSkew(unittest.TestCase):
    """Stress tests on recency decay, clock skew (future timestamps), and timestamp edge cases."""

    def test_clock_skew_future_timestamps(self) -> None:
        """Future timestamps (clock skew/drift) must be clamped to 1.0 and NOT blow up or return > 1.0."""
        now = datetime.now(timezone.utc)
        future_1h = (now + timedelta(hours=1)).isoformat()
        future_30d = (now + timedelta(days=30)).isoformat()
        future_10y = (now + timedelta(days=3650)).isoformat()

        score_now = compute_recency_score(now.isoformat(), "semantic")
        score_1h = compute_recency_score(future_1h, "semantic")
        score_30d = compute_recency_score(future_30d, "semantic")
        score_10y = compute_recency_score(future_10y, "semantic")

        self.assertAlmostEqual(score_now, 1.0, places=4)
        self.assertAlmostEqual(score_1h, 1.0, places=4)
        self.assertAlmostEqual(score_30d, 1.0, places=4)
        self.assertAlmostEqual(score_10y, 1.0, places=4)

    def test_exact_half_life_decay(self) -> None:
        """Half-life periods must decay score to exactly 0.5."""
        now = datetime.now(timezone.utc)

        # Semantic: 30 days
        ts_semantic = (now - timedelta(days=30)).isoformat()
        score_sem = compute_recency_score(ts_semantic, "semantic")
        self.assertAlmostEqual(score_sem, 0.5, places=2)

        # Project: 14 days
        ts_proj = (now - timedelta(days=14)).isoformat()
        score_proj = compute_recency_score(ts_proj, "project")
        self.assertAlmostEqual(score_proj, 0.5, places=2)

        # Episode: 7 days
        ts_ep = (now - timedelta(days=7)).isoformat()
        score_ep = compute_recency_score(ts_ep, "episode")
        self.assertAlmostEqual(score_ep, 0.5, places=2)

        # Default fallback: 14 days
        ts_def = (now - timedelta(days=14)).isoformat()
        score_def = compute_recency_score(ts_def, "custom_level_xyz")
        self.assertAlmostEqual(score_def, 0.5, places=2)

    def test_distant_past_timestamps(self) -> None:
        """Timestamps in the distant past (e.g. 10 years, 1970) decay asymptotically towards 0.0 without underflow errors."""
        now = datetime.now(timezone.utc)
        ts_10y = (now - timedelta(days=3650)).isoformat()
        score_10y = compute_recency_score(ts_10y, "semantic")
        self.assertGreaterEqual(score_10y, 0.0)
        self.assertLess(score_10y, 0.001)

        ts_epoch = "1970-01-01T00:00:00Z"
        score_epoch = compute_recency_score(ts_epoch, "semantic")
        self.assertGreaterEqual(score_epoch, 0.0)
        self.assertLess(score_epoch, 0.0001)

    def test_malformed_and_none_timestamps(self) -> None:
        """None, empty, or garbage timestamps should default safely to current time (score 1.0)."""
        self.assertAlmostEqual(compute_recency_score(None, "semantic"), 1.0, places=4)
        self.assertAlmostEqual(compute_recency_score("", "semantic"), 1.0, places=4)
        self.assertAlmostEqual(compute_recency_score("not-a-valid-date", "semantic"), 1.0, places=4)
        self.assertAlmostEqual(compute_recency_score("2026-99-99T99:99:99", "semantic"), 1.0, places=4)


class TestInputRobustnessAndNoneSafety(unittest.TestCase):
    """Adversarial challenge on handling None or missing fields in corpus items."""

    def setUp(self) -> None:
        self.engine = MemorySearchEngine()

    def test_none_tags_graceful_handling(self) -> None:
        """An entry with tags=None must be safely handled without TypeError: 'NoneType' object is not iterable."""
        entry = {"content": "documento importante per testing", "tags": None, "importance": 3}
        res = self.engine.score_corpus("documento", [("semantic", entry)])
        self.assertEqual(len(res), 1)

    def test_none_importance_graceful_handling(self) -> None:
        """An entry with importance=None must default safely without TypeError: int() argument must be... not 'NoneType'."""
        entry = {"content": "documento importante per testing", "tags": ["test"], "importance": None}
        res = self.engine.score_corpus("documento", [("semantic", entry)])
        self.assertEqual(len(res), 1)

    def test_none_level_graceful_handling(self) -> None:
        """A None level tuple (None, entry) must default safely to default half-life without AttributeError."""
        entry = {"content": "documento importante per testing", "importance": 3}
        res = self.engine.score_corpus("documento", [(None, entry)])  # type: ignore
        self.assertEqual(len(res), 1)

    def test_project_with_none_fields_graceful_handling(self) -> None:
        """A project entry with project_name=None or risk_level=None must not crash string joining or lower()."""
        entry = {"project_name": None, "milestone": "M1", "notes": "note di test", "risk_level": None}
        res = self.engine.score_corpus("test", [("project", entry)])
        self.assertEqual(len(res), 1)


class TestBenchmarkQueryPerformance1000Docs(unittest.TestCase):
    """Empirical performance benchmark on 1,000 document corpus (< 5ms latency SLA)."""

    def setUp(self) -> None:
        self.engine = MemorySearchEngine()
        random.seed(42)
        self.corpus: List[Tuple[str, Dict[str, Any]]] = []

        now = datetime.now(timezone.utc)
        vocab = [
            "progetto", "architettura", "database", "sqlite", "sync", "cloud",
            "offline", "memoria", "semantica", "episodica", "attivita", "deadline",
            "sviluppo", "test", "benchmark", "python", "windows", "daemon",
            "router", "prompt", "briefing", "notifica", "toast", "concorrenza",
            "lww", "crdt", "merge", "git", "hud", "telemetria", "reattivo",
            "blender", "photoshop", "unreal", "visualstudio", "priorita", "urgente"
        ]

        levels = ["semantic", "project", "episodic"]
        for i in range(1000):
            lvl = levels[i % len(levels)]
            age_days = random.uniform(0.0, 60.0)
            ts = (now - timedelta(days=age_days)).isoformat()
            doc_words = random.sample(vocab, k=random.randint(5, 15))
            content = " ".join(doc_words)
            tags = random.sample(vocab, k=random.randint(1, 3))
            importance = random.randint(1, 5)

            if lvl == "semantic":
                entry = {
                    "id": f"fact_{i}",
                    "content": content,
                    "category": "work",
                    "tags": tags,
                    "importance": importance,
                    "created_at": ts,
                    "updated_at": ts,
                }
            elif lvl == "project":
                entry = {
                    "id": f"proj_{i}",
                    "project_name": tags[0],
                    "milestone": f"M{i % 5}",
                    "notes": content,
                    "risk_level": "high" if importance >= 4 else "medium",
                    "dependencies": tags[1:],
                    "created_at": ts,
                    "updated_at": ts,
                }
            else:
                entry = {
                    "id": f"ep_{i}",
                    "event_type": "interaction",
                    "summary": content,
                    "full_text": f"Dettagli completi per {content}",
                    "outcome": "success" if i % 2 == 0 else "neutral",
                    "context_tags": tags,
                    "timestamp": ts,
                }
            self.corpus.append((lvl, entry))

    def test_benchmark_sub_5ms_1000_documents(self) -> None:
        """Benchmark 100 queries against 1,000 entries and verify latency SLA."""
        query_pool = [
            "database sqlite cloud",
            "progetto architettura",
            "memoria semantica episodica",
            "deadline urgente",
            "benchmark test python",
            "sync crdt git",
            "toast notifica daemon",
            "blender unreal creative",
            "termine_inesistente_xyz_123",
            "sviluppo reattivo",
        ]

        # Warm-up run
        for q in query_pool[:3]:
            self.engine.score_corpus(q, self.corpus)

        latencies_ms: List[float] = []
        num_trials = 100

        for i in range(num_trials):
            query = query_pool[i % len(query_pool)]
            t0 = time.perf_counter()
            results = self.engine.score_corpus(query, self.corpus)
            t1 = time.perf_counter()
            elapsed_ms = (t1 - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

        latencies_ms.sort()
        mean_lat = sum(latencies_ms) / len(latencies_ms)
        p50_lat = latencies_ms[int(0.50 * len(latencies_ms))]
        p95_lat = latencies_ms[int(0.95 * len(latencies_ms))]
        p99_lat = latencies_ms[int(0.99 * len(latencies_ms))]
        max_lat = max(latencies_ms)

        print(
            f"\n[BENCHMARK] 1,000 Document Search Performance ({num_trials} trials):\n"
            f"  Mean latency:   {mean_lat:.3f} ms\n"
            f"  p50 (median):   {p50_lat:.3f} ms\n"
            f"  p95 latency:    {p95_lat:.3f} ms\n"
            f"  p99 latency:    {p99_lat:.3f} ms\n"
            f"  Max latency:    {max_lat:.3f} ms"
        )

        self.assertLess(
            mean_lat,
            5.0,
            f"SLA Violation: Mean query latency {mean_lat:.3f}ms exceeded 5.0ms threshold by {mean_lat - 5.0:.3f}ms (x{mean_lat / 5.0:.2f})",
        )


if __name__ == "__main__":
    unittest.main()
