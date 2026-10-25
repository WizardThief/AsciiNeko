"""
test_e2e_tier5_adversarial_part1.py - Milestone 6 Phase 2: Tier 5 White-Box Adversarial Coverage Hardening (Part 1).

Covers 4 critical subsystems with empirical adversarial stress harnesses, boundary inputs,
type anomalies, concurrency, and error handling:
1. Memory Store Edge Cases (zero facts, empty searches, corrupted tags, malformed dates, BM25 cache eviction).
2. Skill Execution Edge Cases (process termination timeouts, syntax errors, complex AST constructs, nested calls, resource limits).
3. Cloud Storage & Sync Edge Cases (concurrent WAL transactions, corrupted payloads, duplicate mutation IDs, clock skew rejection, Bearer token edge cases).
4. Bridge Telemetry Edge Cases (simultaneous SSE connections, rapid reconnection churn, malformed json, status file corruption).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
import http.server
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import unittest
import urllib.error
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
NEKO_BOT_DIR = PROJECT_ROOT / "neko_bot"
BRIDGE_DIR = PROJECT_ROOT / "bridge"

for p in [str(PROJECT_ROOT), str(NEKO_BOT_DIR), str(BRIDGE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from neko_bot.memory.models import Episode, ProjectDeadline, SemanticFact
from neko_bot.memory.search import MemorySearchEngine, compute_recency_score
from neko_bot.memory.store import MemoryStore
from neko_bot.skills.models import SkillManifest
from neko_bot.skills.sandbox import ASTValidator, SkillSandbox
from neko_bot.skills.executor import SkillExecutor
from neko_bot.skills.registry import SkillRegistry
from cloud.server import CloudServer
from cloud.storage.sqlite_adapter import SQLiteStorageAdapter
from bridge.ai_monitor import (
    CompanionRequestHandler,
    NekoBotTelemetryAggregator,
    stop_telemetry,
)


def tearDownModule():
    """Ensure no background bridge telemetry polling threads remain active."""
    try:
        stop_telemetry(timeout=1.0)
    except Exception:
        pass


def get_free_port() -> int:
    """Allocates an ephemeral TCP port for hermetic loopback servers."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestTier5AdversarialMemoryStore(unittest.TestCase):
    """Adversarial stress-testing of MemoryStore, models, and MemorySearchEngine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="neko_test_tier5_mem_")
        self.store = MemoryStore(self.temp_dir)
        self.engine = MemorySearchEngine()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_zero_facts_and_empty_corpus(self):
        """Zero facts, zero projects, zero episodes: empty search query resilience and division-by-zero safety."""
        # 1. Search in empty store
        res_normal = self.store.search("python")
        self.assertEqual(res_normal, [])

        res_empty = self.store.search("")
        self.assertEqual(res_empty, [])

        res_symbols = self.store.search("!@#$%^&*()_+")
        self.assertEqual(res_symbols, [])

        # 2. Export CRDT entities on empty store
        entities = self.store.export_crdt_entities()
        self.assertEqual(entities, [])

        # 3. Memory status on empty store
        status = self.store.get_stats()
        self.assertEqual(status["total_facts"], 0)
        self.assertEqual(status["active_projects"], 0)
        self.assertEqual(status["recent_episodes"], 0)
        self.assertEqual(status["upcoming_deadlines"], [])

        # 4. Direct engine score_corpus with empty corpus
        engine_res = self.engine.score_corpus("anything", [])
        self.assertEqual(engine_res, [])

        # 5. Direct engine score_corpus with all deleted entries
        deleted_corpus = [
            ("semantic", {"id": "f1", "content": "fact one", "deleted": True}),
            ("project", {"id": "p1", "project_name": "proj one", "deleted": True}),
        ]
        self.assertEqual(self.engine.score_corpus("fact", deleted_corpus), [])

    def test_02_empty_whitespace_and_stopword_searches(self):
        """Search queries with pure whitespace, stop words only, pure symbols, and emojis."""
        self.store.add_fact("Architecture specs for Cloud REST API", category="tech_spec", importance=5)
        self.store.add_fact("Client prefers dark mode UI", category="preference", importance=3)
        self.store.add_project("Project Chronos", "Milestone 1", deadline_utc="2026-12-31T00:00:00Z", risk_level="high")
        self.store.add_episode(summary="Completed initial sync setup", outcome="success")

        # Whitespace searches: should rank existing items by importance/recency without crashing
        res_ws = self.store.search("   ")
        self.assertIsInstance(res_ws, list)
        self.assertGreater(len(res_ws), 0)

        res_newlines = self.store.search("\t\r\n")
        self.assertIsInstance(res_newlines, list)
        self.assertGreater(len(res_newlines), 0)

        # Stop words only query
        res_stopwords = self.store.search("the a an and or to in on for")
        self.assertIsInstance(res_stopwords, list)

        # Pure punctuation/regex symbols query
        res_punct = self.store.search("[][]{}()!@#$%^&*+-=")
        self.assertIsInstance(res_punct, list)

        # Unicode emojis and foreign characters
        res_unicode = self.store.search("🐾 🐱 🚀 💻 漢字")
        self.assertIsInstance(res_unicode, list)

    def test_03_corrupted_tags_and_type_anomalies(self):
        """Corrupted tags (non-iterable int, dict, None, mixed types) and importance clamping."""
        # Test importance clamping in SemanticFact
        f_low = SemanticFact(content="Test low", importance=-10)
        self.assertEqual(f_low.importance, 1)

        f_high = SemanticFact(content="Test high", importance=99)
        self.assertEqual(f_high.importance, 5)

        # Test confidence clamping in SemanticFact
        f_conf_neg = SemanticFact(content="Test neg conf", confidence=-0.5)
        self.assertEqual(f_conf_neg.confidence, 0.0)

        f_conf_high = SemanticFact(content="Test high conf", confidence=2.5)
        self.assertEqual(f_conf_high.confidence, 1.0)

        # Test corrupted tags in search candidate selection
        # When query does NOT match inverted index, candidate selection iterates over entry tags:
        # line 328 in search.py: for tag in (item["entry"].get("tags") or [])
        # If tags is an integer (e.g. 12345), iterating crashes with TypeError: 'int' object is not iterable.
        corrupted_entries = [
            ("semantic", {"id": "c1", "content": "Database optimization", "tags": 12345, "importance": 3}),
            ("semantic", {"id": "c2", "content": "Network protocol buffers", "tags": None, "importance": 4}),
            ("semantic", {"id": "c3", "content": "Compiler pipeline design", "tags": [None, 999, True, "valid_tag"], "importance": 5}),
            ("semantic", {"id": "c4", "content": "Security sandbox isolation", "tags": "comma,separated,tags", "importance": 4}),
            ("semantic", {"id": "c5", "content": "Memory cache invalidation", "tags": {"key": "val"}, "importance": 3}),
        ]

        try:
            results = self.engine.score_corpus("nomatch_unindexed_query_term", corrupted_entries)
            self.assertIsInstance(results, list)
        except TypeError as e:
            self.fail(f"BUG exposed in search.py:328 - score_corpus crashed on non-iterable tags: {e}")

    def test_04_malformed_and_extreme_dates(self):
        """Malformed, non-existent, and extreme date strings in ProjectDeadline and recency calculations."""
        malformed_dates = [
            "invalid-iso-date",
            "2026-02-30T10:00:00Z",  # Non-existent calendar day
            "9999-99-99T99:99:99",
            "",
            None,
            "1800-01-01T00:00:00Z",  # Extreme past
            "3000-01-01T00:00:00Z",  # Extreme future
        ]

        for d_str in malformed_dates:
            proj = ProjectDeadline(
                project_name="Adversarial Proj",
                milestone="M1",
                deadline_utc=d_str,
                status="active"
            )
            # is_overdue() must safely return bool without crashing
            is_od = proj.is_overdue()
            self.assertIsInstance(is_od, bool)

            # hours_remaining() must safely return float or None without crashing
            hr = proj.hours_remaining()
            self.assertTrue(hr is None or isinstance(hr, (int, float)))

            # compute_recency_score must safely return float in [0.0, 1.0]
            rec = compute_recency_score(d_str, "project")
            self.assertIsInstance(rec, float)
            self.assertGreaterEqual(rec, 0.0)
            self.assertLessEqual(rec, 1.0)

        # Extreme past deadline should be overdue
        proj_past = ProjectDeadline(project_name="Past", milestone="M1", deadline_utc="1990-01-01T00:00:00Z")
        self.assertTrue(proj_past.is_overdue())

        # Extreme future deadline should NOT be overdue
        proj_future = ProjectDeadline(project_name="Future", milestone="M1", deadline_utc="2099-01-01T00:00:00Z")
        self.assertFalse(proj_future.is_overdue())

    def test_05_bm25_cache_eviction_and_corpus_signature(self):
        """BM25 cache eviction threshold (> 20000 entries) and corpus signature invalidation."""
        # 1. Verify clear_cache resets internal structures
        self.engine.clear_cache()
        self.assertEqual(len(self.engine._doc_cache), 0)
        self.assertIsNone(self.engine._last_corpus_sig)
        self.assertIsNone(self.engine._cached_corpus_data)

        # 2. Verify cache threshold eviction behavior
        # Artificially inject 20,001 items into _doc_cache
        for i in range(20001):
            self.engine._doc_cache[f"dummy_key_{i}"] = {"dummy": True}
        self.assertGreater(len(self.engine._doc_cache), 20000)

        # Fetching next doc data should trigger eviction (clearing down to 1 item)
        sample_entry = {"id": "fact-evict-01", "content": "Eviction trigger test", "importance": 3}
        doc_data = self.engine._get_doc_data("semantic", sample_entry)
        self.assertIsNotNone(doc_data)
        self.assertEqual(len(self.engine._doc_cache), 1)

        # 3. Document mutation cache invalidation test
        # Score an initial entry
        entry = {"id": "fact-mod-01", "content": "Initial alpha content", "updated_at": "2026-01-01T00:00:00Z", "importance": 3}
        corpus = [("semantic", entry)]
        res1 = self.engine.score_corpus("alpha", corpus)
        self.assertEqual(len(res1), 1)

        # Mutate document content with updated timestamp
        mutated_entry = {"id": "fact-mod-01", "content": "Updated beta content", "updated_at": "2026-01-02T00:00:00Z", "importance": 3}
        mutated_corpus = [("semantic", mutated_entry)]
        res2 = self.engine.score_corpus("beta", mutated_corpus)
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["entry"]["content"], "Updated beta content")


class TestTier5AdversarialSkillExecution(unittest.TestCase):
    """Adversarial stress-testing of SkillSandbox, ASTValidator, and SkillExecutor."""

    def setUp(self):
        self.sandbox = SkillSandbox()
        self.validator = ASTValidator()

    def test_06_process_termination_timeouts(self):
        """Infinite while-loop in tool skill cleanly terminated by subprocess timeout."""
        infinite_loop_code = """
def run(inputs, context):
    count = 0
    while True:
        count += 1
"""
        t0 = time.perf_counter()
        res = self.sandbox.run_tool_in_sandbox(infinite_loop_code, inputs={}, timeout_sec=0.5)
        duration = time.perf_counter() - t0

        self.assertFalse(res["success"])
        self.assertEqual(res["result"], {})
        self.assertIn("Execution timed out after 0.5s", res.get("error", ""))
        self.assertLess(duration, 3.0)

    def test_07_syntax_errors_and_malformed_ast(self):
        """Syntax errors, incomplete blocks, null bytes, and non-string code handling."""
        malformed_snippets = [
            "def run(inputs, context) return 42",  # Missing colon
            "def run(inputs, context):\nif True: pass",  # Bad indentation
            "def run(inputs, context):\n    x = (1 + 2\n",  # Unclosed paren
            "",  # Empty string
            "   \t\n",  # Whitespace only
            "def run(inputs, context):\n    return '\x00'",  # Null byte in string literal
        ]

        for code in malformed_snippets:
            valid, msg = self.validator.validate(code)
            self.assertFalse(valid)
            self.assertIsInstance(msg, str)

        # Test non-string input to verify_skill (type anomaly)
        try:
            valid, msg = self.sandbox.verify_skill({"type": "tool", "code": 12345})
            self.assertFalse(valid)
        except AttributeError as e:
            self.fail(f"BUG exposed in sandbox.py:370 - verify_skill crashed on non-string code: {e}")

    def test_08_complex_ast_constructs_and_depth_limits(self):
        """Boundary testing of AST nesting depth limit (MAX_AST_DEPTH = 25) and advanced language constructs."""
        # 1. 20 nested unary operations (AST depth = 24 <= 25, should pass)
        expr_20 = "1"
        for _ in range(20):
            expr_20 = f"(-{expr_20})"
        code_20 = f"def run(inputs, context):\n    x = {expr_20}\n    return {{'res': x}}\n"
        valid_20, msg_20 = self.validator.validate(code_20)
        self.assertTrue(valid_20, f"Depth <= 25 should pass: {msg_20}")

        # 2. 25 nested unary operations (AST depth = 29 > 25, should exceed limit)
        expr_25 = "1"
        for _ in range(25):
            expr_25 = f"(-{expr_25})"
        code_25 = f"def run(inputs, context):\n    x = {expr_25}\n    return {{'res': x}}\n"
        valid_25, msg_25 = self.validator.validate(code_25)
        self.assertFalse(valid_25)
        self.assertIn("AST complexity limit exceeded (> 25 levels)", msg_25)

        # 3. Complex valid AST constructs: comprehensions, lambdas, ternaries
        advanced_code = """
def run(inputs, context):
    items = [x * 2 for x in inputs.get('numbers', []) if x > 0]
    sq = {k: v ** 2 for k, v in zip(range(len(items)), items)}
    f = lambda a, b: a + b if a > b else b - a
    return {'computed': [f(i, j) for i, j in enumerate(items)], 'sq': sq}
"""
        valid_adv, msg_adv = self.validator.validate(advanced_code)
        self.assertTrue(valid_adv, f"Advanced AST constructs should be valid: {msg_adv}")

    def test_09_nested_calls_and_recursion_limits(self):
        """Tool skill with infinite recursion caught by sandbox runner and mapped to failure."""
        recursive_code = """
def run(inputs, context):
    def rec(n):
        return rec(n + 1)
    return {"val": rec(0)}
"""
        res = self.sandbox.run_tool_in_sandbox(recursive_code, inputs={}, timeout_sec=2.0)
        self.assertFalse(res["success"])
        self.assertIn("maximum recursion depth exceeded", res.get("error", "").lower())

    def test_10_resource_limits_and_security_guardrails(self):
        """Static AST rejection of forbidden builtins, dunder attributes, and unauthorized modules."""
        forbidden_attacks = [
            "def run(i, c):\n    return eval('1 + 1')",
            "def run(i, c):\n    return exec('x = 1')",
            "def run(i, c):\n    return open('/etc/passwd').read()",
            "def run(i, c):\n    return globals()",
            "def run(i, c):\n    return locals()",
            "def run(i, c):\n    return ().__class__.__bases__[0].__subclasses__()",
            "import os\ndef run(i, c):\n    return os.listdir('.')",
            "import subprocess\ndef run(i, c):\n    return subprocess.run(['dir'])",
            "import socket\ndef run(i, c):\n    return socket.socket()",
            "import ctypes\ndef run(i, c):\n    return ctypes.windll.kernel32",
            "import math\ndef run(i, c):\n    return getattr(math, '__doc__')",
        ]

        for code in forbidden_attacks:
            valid, msg = self.validator.validate(code)
            self.assertFalse(valid, f"Attack code should have been rejected by ASTValidator: {code}")


class TestTier5AdversarialCloudSync(unittest.TestCase):
    """Adversarial stress-testing of SQLiteStorageAdapter and CloudServer."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="neko_test_tier5_cloud_")
        self.db_path = Path(self.temp_dir) / "cloud_test.db"
        self.storage = SQLiteStorageAdapter(self.db_path)
        self.port = get_free_port()
        self.server = CloudServer(storage=self.storage, port=self.port, host="127.0.0.1", require_auth=False)
        self.server.start()
        time.sleep(0.05)

    def tearDown(self):
        if self.server:
            self.server.stop()
        if self.storage:
            self.storage.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_11_concurrent_wal_transactions(self):
        """Concurrent WAL transactions across 10 threads executing 100 mutations/queries with zero locks."""
        thread_count = 10
        ops_per_thread = 10
        errors = []

        def worker(tid: int):
            for i in range(ops_per_thread):
                try:
                    mut = {
                        "entity_type": "task",
                        "entity_id": f"task-thr-{tid}-{i}",
                        "device_id": f"dev-{tid}",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "payload": {"title": f"Concurrent task {tid}-{i}", "status": "todo"},
                    }
                    ok, seq = self.storage.apply_mutation(mut)
                    if not ok or seq <= 0:
                        errors.append(f"Thread {tid} op {i}: apply_mutation failed")

                    if i % 3 == 0:
                        muts, _ = self.storage.pull_mutations(0)
                        self.assertIsInstance(muts, list)
                    if i % 5 == 0:
                        status = self.storage.get_status()
                        self.assertGreaterEqual(status["server_sequence"], seq)
                except Exception as e:
                    errors.append(f"Thread {tid} op {i} raised exception: {e}")

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = [executor.submit(worker, tid) for tid in range(thread_count)]
            for f in futures:
                f.result()

        self.assertEqual(errors, [], f"Encountered concurrency errors: {errors}")
        status = self.storage.get_status()
        self.assertEqual(status["active_entities"], thread_count * ops_per_thread)
        self.assertEqual(status["server_sequence"], thread_count * ops_per_thread)

    def test_12_corrupted_payloads_and_malformed_bodies(self):
        """CloudServer REST endpoints rejecting corrupted bodies and non-dict mutations with 400 Bad Request."""
        base_url = f"http://127.0.0.1:{self.port}"

        # 1. Non-JSON raw binary payload
        req1 = urllib.request.Request(
            f"{base_url}/api/v1/sync/push",
            data=b"\x80\xff\xfe\x00\x01\x02",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx1:
            urllib.request.urlopen(req1)
        self.assertEqual(ctx1.exception.code, 400)

        # 2. JSON body is an array instead of dict
        req2 = urllib.request.Request(
            f"{base_url}/api/v1/sync/push",
            data=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx2:
            urllib.request.urlopen(req2)
        self.assertEqual(ctx2.exception.code, 400)

        # 3. mutations is a string instead of a list
        req3 = urllib.request.Request(
            f"{base_url}/api/v1/sync/push",
            data=json.dumps({"mutations": "not_a_list"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx3:
            urllib.request.urlopen(req3)
        self.assertEqual(ctx3.exception.code, 400)

        # 4. mutations contains non-dict elements
        req4 = urllib.request.Request(
            f"{base_url}/api/v1/sync/push",
            data=json.dumps({"mutations": [123, "string_item"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx4:
            urllib.request.urlopen(req4)
        self.assertEqual(ctx4.exception.code, 400)

    def test_13_duplicate_mutation_ids_idempotency(self):
        """Duplicate mutation IDs handled idempotently without corrupting storage sequence or entity count."""
        mut_payload = {
            "entity_type": "task",
            "entity_id": "task-dup-01",
            "mutation_id": "mut-idempotent-001",
            "device_id": "dev-ws1",
            "updated_at": "2026-06-01T12:00:00.000000Z",
            "payload": {"title": "Idempotent task", "state": "todo"},
        }

        # 1. First application
        ok1, seq1 = self.storage.apply_mutation(mut_payload)
        self.assertTrue(ok1)
        self.assertEqual(seq1, 1)

        # 2. Duplicate application with exact same timestamp and content
        ok2, seq2 = self.storage.apply_mutation(mut_payload)
        self.assertFalse(ok2, "Duplicate mutation with identical timestamp should not win LWW")
        self.assertEqual(seq2, 1)

        # Entity count remains 1
        status = self.storage.get_status()
        self.assertEqual(status["active_entities"], 1)

        # Pull mutations returns the entity once
        muts, max_seq = self.storage.pull_mutations(0)
        self.assertEqual(len(muts), 1)
        self.assertEqual(max_seq, 1)

    def test_14_future_clock_skew_rejection(self):
        """Rejection of mutations with timestamps skewed > 60 seconds into the future."""
        now_ts = time.time()

        # 1. 300 seconds (5 min) into the future: must be rejected
        ts_future_300 = datetime.fromtimestamp(now_ts + 300, timezone.utc).isoformat()
        ok_300, seq_300 = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "task-future-300",
            "updated_at": ts_future_300,
            "payload": {"title": "Future task"},
        })
        self.assertFalse(ok_300)
        self.assertEqual(seq_300, 0)

        # 2. 75 seconds into the future: must be rejected
        ts_future_75 = datetime.fromtimestamp(now_ts + 75, timezone.utc).isoformat()
        ok_75, seq_75 = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "task-future-75",
            "updated_at": ts_future_75,
            "payload": {"title": "Future task 75"},
        })
        self.assertFalse(ok_75)
        self.assertEqual(seq_75, 0)

        # 3. 30 seconds into the future: accepted within 60s tolerance
        ts_future_30 = datetime.fromtimestamp(now_ts + 30, timezone.utc).isoformat()
        ok_30, seq_30 = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "task-future-30",
            "updated_at": ts_future_30,
            "payload": {"title": "Tolerated future task"},
        })
        self.assertTrue(ok_30)
        self.assertGreater(seq_30, 0)

    def test_15_bearer_token_edge_cases(self):
        """CloudServer authentication enforcement: Bearer headers, missing tokens, invalid tokens, body tokens."""
        # Create an authenticated CloudServer instance
        auth_port = get_free_port()
        auth_server = CloudServer(storage=self.storage, port=auth_port, host="127.0.0.1", require_auth=True)
        auth_server.start()
        time.sleep(0.05)

        base_url = f"http://127.0.0.1:{auth_port}"

        try:
            # Register device
            reg_body = json.dumps({"device_id": "dev-auth-test", "device_name": "ws1"}).encode("utf-8")
            reg_req = urllib.request.Request(
                f"{base_url}/api/v1/auth/register",
                data=reg_body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(reg_req) as resp:
                reg_data = json.loads(resp.read().decode("utf-8"))
                token = reg_data["token"]

            # Case A: Request without Authorization header or body token -> 401
            req_no_auth = urllib.request.Request(
                f"{base_url}/api/v1/sync/push",
                data=json.dumps({"mutations": []}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx_a:
                urllib.request.urlopen(req_no_auth)
            self.assertEqual(ctx_a.exception.code, 401)

            # Case B: Empty Bearer token -> 401
            req_empty_bearer = urllib.request.Request(
                f"{base_url}/api/v1/sync/push",
                data=json.dumps({"mutations": []}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer   "},
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx_b:
                urllib.request.urlopen(req_empty_bearer)
            self.assertEqual(ctx_b.exception.code, 401)

            # Case C: Wrong token -> 401
            req_bad_token = urllib.request.Request(
                f"{base_url}/api/v1/sync/push",
                data=json.dumps({"mutations": []}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wrong_secret_token",
                    "X-Device-Id": "dev-auth-test"
                },
                method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx_c:
                urllib.request.urlopen(req_bad_token)
            self.assertEqual(ctx_c.exception.code, 401)

            # Case D: Valid Bearer token with matching X-Device-Id -> 200
            req_valid = urllib.request.Request(
                f"{base_url}/api/v1/sync/push",
                data=json.dumps({"mutations": []}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "X-Device-Id": "dev-auth-test"
                },
                method="POST"
            )
            with urllib.request.urlopen(req_valid) as resp_d:
                self.assertEqual(resp_d.status, 200)

            # Case E: Body-based authentication fallback -> 200
            req_body_auth = urllib.request.Request(
                f"{base_url}/api/v1/sync/push",
                data=json.dumps({
                    "device_id": "dev-auth-test",
                    "token": token,
                    "mutations": []
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req_body_auth) as resp_e:
                self.assertEqual(resp_e.status, 200)

        finally:
            auth_server.stop()


class TestTier5AdversarialBridgeTelemetry(unittest.TestCase):
    """Adversarial stress-testing of Bridge HTTP/SSE CompanionRequestHandler and TelemetryAggregator."""

    def setUp(self):
        self.port = get_free_port()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), CompanionRequestHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        time.sleep(0.05)

    def tearDown(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        stop_telemetry(timeout=1.0)

    def test_16_simultaneous_sse_connections(self):
        """Simultaneous SSE connections receiving event frames concurrently without starvation."""
        client_count = 4
        frames_per_client = 2
        received_events = [[] for _ in range(client_count)]
        errors = []

        def sse_client(cid: int):
            try:
                url = f"http://127.0.0.1:{self.port}/stream?client_id=client_{cid}"
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    self.assertEqual(resp.status, 200)
                    content_type = resp.headers.get("Content-Type", "")
                    self.assertIn("text/event-stream", content_type)

                    frames_read = 0
                    while frames_read < frames_per_client:
                        line = resp.readline().decode("utf-8")
                        if line.startswith("data: "):
                            raw_json = line[6:].strip()
                            data = json.loads(raw_json)
                            received_events[cid].append(data)
                            frames_read += 1
            except Exception as e:
                errors.append(f"Client {cid} error: {e}")

        with ThreadPoolExecutor(max_workers=client_count) as executor:
            futures = [executor.submit(sse_client, i) for i in range(client_count)]
            for f in futures:
                f.result()

        self.assertEqual(errors, [], f"Encountered SSE errors: {errors}")
        for cid in range(client_count):
            self.assertEqual(len(received_events[cid]), frames_per_client)
            for event in received_events[cid]:
                self.assertIn("cpu", event)
                self.assertIn("ram", event)
                self.assertIn("timestamp", event)

    def test_17_rapid_reconnection_churn(self):
        """Rapid connection and immediate abortion churn does not crash the server or exhaust sockets."""
        churn_iterations = 15
        for i in range(churn_iterations):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(("127.0.0.1", self.port))
                req = f"GET /stream?client_id=churn_{i} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n"
                s.sendall(req.encode("utf-8"))
                # Read just enough to establish connection, then abruptly close socket
                s.recv(64)
                s.close()
            except Exception:
                pass

        # Verify server remains healthy and responds to subsequent requests
        status_url = f"http://127.0.0.1:{self.port}/status"
        with urllib.request.urlopen(status_url, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("cpu", data)
            self.assertIn("ram", data)

    def test_18_malformed_json_and_payloads(self):
        """Graceful recovery from malformed JSON in POST requests to /cat_sync and /ai_chat."""
        base_url = f"http://127.0.0.1:{self.port}"

        # 1. Malformed JSON to /cat_sync
        req_cat = urllib.request.Request(
            f"{base_url}/cat_sync",
            data=b"{bad json...",
            headers={"Content-Type": "application/json", "Content-Length": "12"},
            method="POST"
        )
        with urllib.request.urlopen(req_cat, timeout=3.0) as resp_cat:
            self.assertEqual(resp_cat.status, 200)
            res_json = json.loads(resp_cat.read().decode("utf-8"))
            self.assertTrue(res_json.get("ok"))

        # 2. Malformed JSON to /ai_chat
        req_ai = urllib.request.Request(
            f"{base_url}/ai_chat",
            data=b"not json at all",
            headers={"Content-Type": "application/json", "Content-Length": "15"},
            method="POST"
        )
        with urllib.request.urlopen(req_ai, timeout=3.0) as resp_ai:
            self.assertEqual(resp_ai.status, 200)
            res_ai = json.loads(resp_ai.read().decode("utf-8"))
            self.assertIsInstance(res_ai, dict)

        # 3. GET /status with garbage query string
        with urllib.request.urlopen(f"{base_url}/status?client_id=&foo=bar&%00=1", timeout=3.0) as resp_status:
            self.assertEqual(resp_status.status, 200)

    def test_19_corrupted_status_file_handling(self):
        """NekoBotTelemetryAggregator fallback on corrupted, empty, and binary status.json files."""
        temp_dir = tempfile.mkdtemp(prefix="neko_test_status_file_")
        status_file = Path(temp_dir) / "status.json"

        aggregator = NekoBotTelemetryAggregator(auto_start=False)
        # Mock _find_status_file to point directly to our test status_file
        aggregator._find_status_file = lambda: str(status_file)

        try:
            # Case A: Corrupted JSON file
            status_file.write_text('{"daemon_health": "running", "cloud_sync": ', encoding="utf-8")
            aggregator._poll_once(force=True)
            telemetry_a = aggregator.get_telemetry()
            self.assertEqual(telemetry_a["cloud_sync"]["status"], "error")

            # Case B: 0-byte file
            status_file.write_text('', encoding="utf-8")
            aggregator._poll_once(force=True)
            telemetry_b = aggregator.get_telemetry()
            self.assertEqual(telemetry_b["daemon_health"], "offline")

            # Case C: Binary garbage
            status_file.write_bytes(b"\x00\x01\x02\xff\xfe\xfd\x80")
            aggregator._poll_once(force=True)
            telemetry_c = aggregator.get_telemetry()
            self.assertEqual(telemetry_c["cloud_sync"]["status"], "error")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
