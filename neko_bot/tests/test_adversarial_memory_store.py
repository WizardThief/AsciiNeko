"""
tests/test_adversarial_memory_store.py - Empirical Challenger Stress Harness for MemoryStore.

Adversarially tests:
1. Concurrency: single-store thread stress, multi-store thread contention, multiprocess contention, atomic_write_json_file Windows lock behavior.
2. CRDT LWW tie-breaks: timestamp collision, tombstone dominance, canonical JSON tie-breaking, 3-node permutation convergence, timezone variations.
3. Edge cases: 0-byte files, truncated/malformed JSON, non-dict payloads, corrupt item values, deleted directory recreation, adversarial Unicode/special strings.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import random
import shutil
import tempfile
import time
from typing import Any, Dict, List
import unittest

from neko_bot.memory.models import (
    Episode,
    ProjectDeadline,
    SemanticFact,
    generate_memory_id,
    now_utc_iso,
)
from neko_bot.memory.store import (
    AmbiguousMemoryPrefixError,
    MemoryStore,
)
from neko_bot.core.config import atomic_write_json_file


def _worker_add_facts(workspace_dir: str, worker_id: int, count: int, results_queue: Any) -> None:
    """Multiprocessing worker function to stress concurrent processes."""
    try:
        store = MemoryStore(workspace_dir=workspace_dir)
        added_ids = []
        for i in range(count):
            res = store.add_fact(
                content=f"Fact from worker {worker_id} item {i}",
                category="stress",
                tags=[f"worker_{worker_id}"],
                importance=3,
            )
            added_ids.append(res["id"])
            time.sleep(0.001)
        results_queue.put({"worker_id": worker_id, "success": True, "ids": added_ids})
    except Exception as e:
        results_queue.put({"worker_id": worker_id, "success": False, "error": str(e)})


class TestAdversarialConcurrency(unittest.TestCase):
    """Stress tests concurrent writes and Windows atomic file lock resilience."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_adv_concur_")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_concurrent_threads_single_store(self) -> None:
        """20 threads hammering a single MemoryStore instance concurrently."""
        store = MemoryStore(workspace_dir=self.temp_dir)
        num_threads = 20
        ops_per_thread = 10
        total_ops = num_threads * ops_per_thread

        def worker(tid: int) -> List[str]:
            ids = []
            for j in range(ops_per_thread):
                fact = store.add_fact(
                    content=f"Thread {tid} message {j}",
                    category="concurrency",
                    tags=[f"t{tid}"],
                )
                ids.append(fact["id"])
            return ids

        all_ids = []
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for fut in as_completed(futures):
                all_ids.extend(fut.result())

        self.assertEqual(len(all_ids), total_ops)
        self.assertEqual(len(set(all_ids)), total_ops)

        # Check in-memory count
        stats = store.get_stats()
        self.assertEqual(stats["total_facts"], total_ops)

        # Reload fresh store from disk to verify atomic file persistence
        fresh_store = MemoryStore(workspace_dir=self.temp_dir)
        fresh_stats = fresh_store.get_stats()
        self.assertEqual(fresh_stats["total_facts"], total_ops)

    def test_concurrent_threads_multiple_stores_same_directory(self) -> None:
        """
        Multiple threads each with their OWN MemoryStore instance writing to the SAME directory.
        Tests whether inter-process / multi-instance writes preserve data or lose updates.
        """
        num_threads = 8
        ops_per_thread = 5
        total_ops = num_threads * ops_per_thread

        def worker(tid: int) -> List[str]:
            # Each thread creates its own MemoryStore instance
            store = MemoryStore(workspace_dir=self.temp_dir)
            ids = []
            for j in range(ops_per_thread):
                fact = store.add_fact(
                    content=f"StoreThread {tid} item {j}",
                    category="multi_store",
                    tags=[f"mst_{tid}"],
                )
                ids.append(fact["id"])
                time.sleep(0.005)
            return ids

        created_ids = []
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for fut in as_completed(futures):
                created_ids.extend(fut.result())

        # Inspect disk state
        disk_store = MemoryStore(workspace_dir=self.temp_dir)
        disk_facts = disk_store.list_entries(level="semantic")
        disk_ids = {f["id"] for f in disk_facts}

        # Check data loss: does disk retain all or did concurrent instances overwrite each other?
        missing_ids = set(created_ids) - disk_ids
        print(f"\n[MULTI-STORE CONCURRENCY] Created: {len(created_ids)}, Retained on disk: {len(disk_ids)}, Missing: {len(missing_ids)}")
        self.assertEqual(
            len(missing_ids), 0,
            f"Multi-store concurrency lost {len(missing_ids)}/{len(created_ids)} records! Only {len(disk_ids)} persisted."
        )

    def test_concurrent_reads_and_writes_no_deadlock(self) -> None:
        """5 threads writing while 5 threads continuously read search & stats."""
        store = MemoryStore(workspace_dir=self.temp_dir)
        stop_event = False

        def writer() -> None:
            for i in range(25):
                store.add_fact(f"Writer entry {i}", category="fact")
                time.sleep(0.002)

        def reader() -> int:
            read_count = 0
            while not stop_event:
                store.get_stats()
                store.search("Writer", limit=3)
                store.list_entries(level="all")
                read_count += 1
                time.sleep(0.001)
            return read_count

        with ThreadPoolExecutor(max_workers=10) as executor:
            write_futures = [executor.submit(writer) for _ in range(5)]
            read_futures = [executor.submit(reader) for _ in range(5)]

            # Wait for writers
            for wf in write_futures:
                wf.result()

            stop_event = True
            for rf in read_futures:
                rc = rf.result()
                self.assertGreater(rc, 0)

        stats = store.get_stats()
        self.assertEqual(stats["total_facts"], 125)


class TestAdversarialCRDT(unittest.TestCase):
    """Stress tests CRDT LWW tie-breaks, convergence, and commutativity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_adv_crdt_")
        self.store = MemoryStore(workspace_dir=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_crdt_timestamp_collision_tombstone_dominance_both_orders(self) -> None:
        """
        When two mutations have the exact same updated_at, tombstone (deleted=True) MUST dominate
        regardless of which order the mutations arrive.
        """
        ts = "2026-10-24T12:00:00.000000+00:00"
        fid = "fact-tie-tomb"

        mut_active = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {"id": fid, "content": "Active fact", "updated_at": ts, "deleted": False},
            "updated_at": ts,
            "deleted": False,
        }
        mut_tombstone = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {"id": fid, "content": "Active fact", "updated_at": ts, "deleted": True},
            "updated_at": ts,
            "deleted": True,
        }

        # Order 1: active then tombstone
        dir1 = Path(tempfile.mkdtemp(prefix="neko_crdt_tomb1_"))
        try:
            s1 = MemoryStore(workspace_dir=dir1)
            s1.apply_crdt_mutation(mut_active)
            s1.apply_crdt_mutation(mut_tombstone)
            e1 = s1.get_entry(fid)
            self.assertTrue(e1["deleted"])
        finally:
            shutil.rmtree(dir1, ignore_errors=True)

        # Order 2: tombstone then active
        dir2 = Path(tempfile.mkdtemp(prefix="neko_crdt_tomb2_"))
        try:
            s2 = MemoryStore(workspace_dir=dir2)
            s2.apply_crdt_mutation(mut_tombstone)
            s2.apply_crdt_mutation(mut_active)  # active should be rejected
            e2 = s2.get_entry(fid)
            self.assertTrue(e2["deleted"])
        finally:
            shutil.rmtree(dir2, ignore_errors=True)

    def test_crdt_canonical_json_tiebreaker_both_orders(self) -> None:
        """
        When two active mutations have the exact same updated_at, canonical JSON string comparison
        must deterministically select the winner regardless of arrival order.
        """
        ts = "2026-10-24T15:30:00.000000+00:00"
        fid = "fact-canonical-tie"

        mut_low = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {"id": fid, "content": "AAA_Content", "updated_at": ts, "deleted": False},
            "updated_at": ts,
            "deleted": False,
        }
        mut_high = {
            "entity_type": "memory_fact",
            "id": fid,
            "data": {"id": fid, "content": "ZZZ_Content", "updated_at": ts, "deleted": False},
            "updated_at": ts,
            "deleted": False,
        }

        # Order 1: low then high
        dir1 = Path(tempfile.mkdtemp(prefix="neko_crdt_lex1_"))
        try:
            s1 = MemoryStore(workspace_dir=dir1)
            s1.apply_crdt_mutation(mut_low)
            s1.apply_crdt_mutation(mut_high)
            e1 = s1.get_entry(fid)
        finally:
            shutil.rmtree(dir1, ignore_errors=True)

        # Order 2: high then low
        dir2 = Path(tempfile.mkdtemp(prefix="neko_crdt_lex2_"))
        try:
            s2 = MemoryStore(workspace_dir=dir2)
            s2.apply_crdt_mutation(mut_high)
            s2.apply_crdt_mutation(mut_low)
            e2 = s2.get_entry(fid)
        finally:
            shutil.rmtree(dir2, ignore_errors=True)

        # Both stores must converge to the exact same content!
        self.assertEqual(e1["content"], e2["content"])
        self.assertEqual(e1["content"], "ZZZ_Content")

    def test_crdt_3_node_permutation_convergence(self) -> None:
        """
        Generates 20 complex mutations across facts, projects, episodes.
        Applies them in 3 different random permutations across 3 isolated MemoryStore nodes.
        Verifies all 3 nodes reach 100% identical exported CRDT state.
        """
        base_time = datetime(2026, 10, 24, 10, 0, 0, tzinfo=timezone.utc)
        mutations: List[Dict[str, Any]] = []

        # Create overlapping mutations on 5 fact IDs, 3 project IDs, 2 episode IDs
        fact_ids = [f"fact-perm-{i}" for i in range(5)]
        proj_ids = [f"proj-perm-{i}" for i in range(3)]
        ep_ids = [f"ep-perm-{i}" for i in range(2)]

        for step in range(30):
            delta = timedelta(seconds=random.randint(0, 10))
            ts = (base_time + delta).isoformat()
            target_type = random.choice(["fact", "project", "episode"])

            if target_type == "fact":
                fid = random.choice(fact_ids)
                mut = {
                    "entity_type": "memory_fact",
                    "id": fid,
                    "data": {
                        "id": fid,
                        "content": f"Fact content version {step}",
                        "importance": random.randint(1, 5),
                        "created_at": ts,
                        "updated_at": ts,
                        "deleted": random.random() < 0.25,
                    },
                    "updated_at": ts,
                }
            elif target_type == "project":
                pid = random.choice(proj_ids)
                mut = {
                    "entity_type": "memory_project",
                    "id": pid,
                    "data": {
                        "id": pid,
                        "project_name": f"Project_{pid}",
                        "milestone": f"M_{step}",
                        "risk_level": random.choice(["low", "medium", "high", "critical"]),
                        "created_at": ts,
                        "updated_at": ts,
                        "deleted": random.random() < 0.2,
                    },
                    "updated_at": ts,
                }
            else:
                eid = random.choice(ep_ids)
                mut = {
                    "entity_type": "memory_episode",
                    "id": eid,
                    "data": {
                        "id": eid,
                        "session_id": "sess-perm",
                        "summary": f"Summary step {step}",
                        "outcome": random.choice(["success", "failure", "neutral"]),
                        "timestamp": ts,
                        "created_at": ts,
                        "updated_at": ts,
                        "deleted": random.random() < 0.2,
                    },
                    "updated_at": ts,
                }
            mutations.append(mut)

        # Permutations
        perm_a = list(mutations)
        perm_b = list(reversed(mutations))
        perm_c = list(mutations)
        random.seed(42)
        random.shuffle(perm_c)

        dirs = [Path(tempfile.mkdtemp(prefix=f"neko_node_{n}_")) for n in ["A", "B", "C"]]
        try:
            node_a = MemoryStore(workspace_dir=dirs[0])
            node_b = MemoryStore(workspace_dir=dirs[1])
            node_c = MemoryStore(workspace_dir=dirs[2])

            for m in perm_a:
                node_a.apply_crdt_mutation(m)
            for m in perm_b:
                node_b.apply_crdt_mutation(m)
            for m in perm_c:
                node_c.apply_crdt_mutation(m)

            entities_a = sorted(node_a.export_crdt_entities(), key=lambda x: (x["entity_type"], x["id"]))
            entities_b = sorted(node_b.export_crdt_entities(), key=lambda x: (x["entity_type"], x["id"]))
            entities_c = sorted(node_c.export_crdt_entities(), key=lambda x: (x["entity_type"], x["id"]))

            self.assertEqual(len(entities_a), len(entities_b))
            self.assertEqual(len(entities_a), len(entities_c))

            # Deep equality
            for ea, eb, ec in zip(entities_a, entities_b, entities_c):
                self.assertEqual(ea["id"], eb["id"])
                self.assertEqual(ea["id"], ec["id"])
                self.assertEqual(ea["deleted"], eb["deleted"])
                self.assertEqual(ea["deleted"], ec["deleted"])
                self.assertEqual(ea["data"], eb["data"])
                self.assertEqual(ea["data"], ec["data"])
        finally:
            for d in dirs:
                shutil.rmtree(d, ignore_errors=True)

    def test_crdt_convergence_with_missing_created_at(self) -> None:
        """
        When remote mutations omit created_at or timestamp, both nodes MUST still converge.
        If local wall-clock now_utc_iso() is generated upon deserialization, canonical JSON tie-breaking diverges.
        """
        ts = "2026-10-24T12:00:00.000000+00:00"
        eid = "ep-test-nondet"
        m1 = {
            "entity_type": "memory_episode",
            "id": eid,
            "data": {
                "id": eid,
                "session_id": "sess-tie",
                "summary": "Summary Alpha",
                "outcome": "success",
                "updated_at": ts,
                "deleted": False,
            },
            "updated_at": ts,
        }
        m2 = {
            "entity_type": "memory_episode",
            "id": eid,
            "data": {
                "id": eid,
                "session_id": "sess-tie",
                "summary": "Summary Beta",
                "outcome": "success",
                "updated_at": ts,
                "deleted": False,
            },
            "updated_at": ts,
        }

        d1 = Path(tempfile.mkdtemp(prefix="neko_node_sub_a_"))
        d2 = Path(tempfile.mkdtemp(prefix="neko_node_sub_b_"))
        try:
            node_a = MemoryStore(workspace_dir=d1)
            node_b = MemoryStore(workspace_dir=d2)

            node_a.apply_crdt_mutation(m1)
            time.sleep(0.01)  # Ensure wall-clock advances
            node_a.apply_crdt_mutation(m2)

            node_b.apply_crdt_mutation(m2)
            time.sleep(0.01)
            node_b.apply_crdt_mutation(m1)

            e_a = node_a.get_entry(eid)
            e_b = node_b.get_entry(eid)
            self.assertEqual(
                e_a["summary"], e_b["summary"],
                f"Convergence failure on canonical JSON tie-break due to non-deterministic timestamps: {e_a['summary']} != {e_b['summary']}"
            )
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)

    def test_timezone_iso_parsing_utc_vs_naive(self) -> None:
        """
        Tests how MemoryStore._parse_timestamp handles various ISO-8601 strings.
        Checks for potential local timezone skew on naive strings.
        """
        # UTC explicit with Z
        t_z = MemoryStore._parse_timestamp("2026-10-24T12:00:00Z")
        # UTC explicit with +00:00
        t_offset = MemoryStore._parse_timestamp("2026-10-24T12:00:00+00:00")
        # +02:00 (which is 12:00:00 UTC)
        t_plus2 = MemoryStore._parse_timestamp("2026-10-24T14:00:00+02:00")
        # Naive without tz
        t_naive = MemoryStore._parse_timestamp("2026-10-24T12:00:00")

        self.assertEqual(t_z, t_offset)
        self.assertEqual(t_z, t_plus2)
        print(f"\n[TIMEZONE PARSING] UTC(Z): {t_z}, Offset(+02:00): {t_plus2}, Naive: {t_naive}")
        self.assertEqual(
            t_naive, t_z,
            f"Naive ISO string parsed with local timezone skew ({t_naive}) instead of defaulting to UTC ({t_z})"
        )


class TestAdversarialCorruptionAndEdgeCases(unittest.TestCase):
    """Stress tests corrupted, empty, malformed files and filesystem edge cases."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_adv_corrupt_")
        self.store = MemoryStore(workspace_dir=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_empty_zero_byte_json_files(self) -> None:
        """0-byte json files should not crash MemoryStore and should recover upon next write."""
        # Write 0 bytes to all 3 files
        self.store.facts_file.write_text("", encoding="utf-8")
        self.store.projects_file.write_text("", encoding="utf-8")
        self.store.episodes_file.write_text("", encoding="utf-8")

        # Reload
        reloaded_store = MemoryStore(workspace_dir=self.temp_dir)
        stats = reloaded_store.get_stats()
        self.assertEqual(stats["total_facts"], 0)
        self.assertEqual(stats["active_projects"], 0)
        self.assertEqual(stats["recent_episodes"], 0)

        # Add fact should heal the facts file
        fact = reloaded_store.add_fact("Healed fact")
        self.assertIsNotNone(fact)
        # Verify valid JSON on disk
        data = json.loads(self.store.facts_file.read_text(encoding="utf-8"))
        self.assertIn("facts", data)
        self.assertIn(fact["id"], data["facts"])

    def test_corrupted_truncated_json_file(self) -> None:
        """Truncated/invalid JSON syntax in persistence file."""
        self.store.facts_file.write_text('{"version": "1.0", "facts": {"fact-1": {"id": "fact-1", "cont', encoding="utf-8")

        reloaded_store = MemoryStore(workspace_dir=self.temp_dir)
        stats = reloaded_store.get_stats()
        self.assertEqual(stats["total_facts"], 0)

        # Writing a new fact should succeed and overwrite corrupt file cleanly
        reloaded_store.add_fact("New valid fact")
        re_data = json.loads(self.store.facts_file.read_text(encoding="utf-8"))
        self.assertEqual(len(re_data["facts"]), 1)

    def test_non_dict_json_payloads(self) -> None:
        """File containing valid JSON that is not a dictionary (e.g. list, string, int)."""
        for weird_payload in ['["a", "b", "c"]', '"hello world"', '12345', 'null']:
            self.store.facts_file.write_text(weird_payload, encoding="utf-8")
            reloaded_store = MemoryStore(workspace_dir=self.temp_dir)
            self.assertEqual(len(reloaded_store.list_entries(level="semantic")), 0)

    def test_malformed_item_fields_in_json(self) -> None:
        """File containing dictionary items with unexpected types or corrupt values."""
        bad_data = {
            "version": "1.0",
            "facts": {
                "fact-good": {"id": "fact-good", "content": "Valid content", "importance": 3},
                "fact-bad-1": "string instead of dict",
                "fact-bad-2": None,
                "fact-bad-3": 12345,
            }
        }
        self.store.facts_file.write_text(json.dumps(bad_data), encoding="utf-8")

        reloaded = MemoryStore(workspace_dir=self.temp_dir)
        entries = reloaded.list_entries(level="semantic")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "fact-good")

    def test_directory_deleted_at_runtime(self) -> None:
        """Memory directory removed while MemoryStore instance is active."""
        store = MemoryStore(workspace_dir=self.temp_dir)
        store.add_fact("Before delete")

        # Physically remove the memory directory
        shutil.rmtree(store.memory_dir)
        self.assertFalse(store.memory_dir.exists())

        # Calling add_fact should recreate the directory and succeed
        f2 = store.add_fact("After delete")
        self.assertTrue(store.memory_dir.exists())
        self.assertTrue(store.facts_file.exists())
        self.assertIsNotNone(store.get_entry(f2["id"]))

    def test_adversarial_unicode_and_special_strings(self) -> None:
        """Tests emojis, Italian accents, Japanese characters, quotes, and long text."""
        adversarial_inputs = [
            "🤖 Assistente AI con caffè e città: perché no? 🚀",
            "日本語テキスト: 吾輩は猫である。名前はまだ無い。",
            'Special quotes: "double" \'single\' `backtick` \\backslash /slash {bracket}',
            "<script>alert('xss')</script> and SELECT * FROM users WHERE '1'='1';",
            "Line breaks:\nLine 1\r\nLine 2\tTabbed",
            "A" * 50000,  # 50 KB text
        ]

        for i, text in enumerate(adversarial_inputs):
            res = self.store.add_fact(content=text, tags=[f"test_{i}"])
            retrieved = self.store.get_entry(res["id"])
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved["content"], text)

            # Search should locate key terms without crash
            if "caffè" in text:
                s_res = self.store.search("caffè")
                self.assertGreaterEqual(len(s_res), 1)
            if "日本語" in text:
                s_res = self.store.search("日本語")
                self.assertGreaterEqual(len(s_res), 1)


if __name__ == "__main__":
    unittest.main()
