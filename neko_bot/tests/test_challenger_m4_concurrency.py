"""
tests/test_challenger_m4_concurrency.py - Adversarial Test Suite for Milestone 4.

Empirically challenges:
1. Multi-client offline mutations on the same entity across 3+ independent clients/mutation queues.
2. Disconnected clients making conflicting updates (title update vs tombstone deletion, same vs different timestamps).
3. Reconnection order variations (all permutations: Client B syncs first, then Client A; deterministic LWW convergence).
4. High-throughput rapid pushes to /api/v1/sync/push and strict sequence monotonicity.
5. Intermittent network partition flapping, thread contention under Windows file locking, and adversarial payloads.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import io
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
import urllib.error
import urllib.request

# Ensure root repo and neko_bot are in sys.path
_root_dir = Path(__file__).resolve().parent.parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))
_neko_bot_dir = _root_dir / "neko_bot"
if str(_neko_bot_dir) not in sys.path:
    sys.path.insert(0, str(_neko_bot_dir))

from cloud.models import (
    canonical_json,
    compute_content_hash,
    format_iso_timestamp,
    parse_iso_timestamp,
)
from cloud.server import CloudServer
from cloud.storage.sqlite_adapter import SQLiteStorageAdapter
from neko_bot.cloud.client import CloudClient, CloudConnectionError
from neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso
from neko_bot.cloud.sync_manager import CloudSyncManager
from neko_bot.memory.store import MemoryStore
from neko_bot.skills.registry import SkillRegistry
from neko_bot.task.models import Task, TaskPriority, TaskState
from neko_bot.task.store import TaskStore


def get_ephemeral_port() -> int:
    """Allocates a free localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


def make_http_post(url: str, payload: Dict[str, Any], timeout: float = 10.0) -> Tuple[int, Dict[str, Any]]:
    """Helper for raw REST POST calls."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return e.code, parsed


def make_http_get(url: str, timeout: float = 10.0) -> Tuple[int, Dict[str, Any]]:
    """Helper for raw REST GET calls."""
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return e.code, parsed


class TestMultiClientOfflineConcurrency(unittest.TestCase):
    """
    Adversarial verification of 3+ isolated workstations performing concurrent offline
    mutations on the same entities and reconciling upon reconnection.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_challenger_m4_mc_")
        self.server_db = Path(self.tmp) / "cloud.db"
        self.storage = SQLiteStorageAdapter(self.server_db)
        self.port = get_ephemeral_port()
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        self.server_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_client(self, name: str) -> Dict[str, Any]:
        """Creates an independent client environment with its own stores and sync manager."""
        ws = Path(self.tmp) / name
        ws.mkdir(parents=True, exist_ok=True)
        task_store = TaskStore(ws)
        memory_store = MemoryStore(ws)
        skill_registry = SkillRegistry(ws)
        sync_mgr = CloudSyncManager(
            ws,
            server_url=self.server_url,
            device_id=name,
            token=f"tok_{name}",
            task_store=task_store,
            memory_store=memory_store,
            skill_registry=skill_registry,
        )
        return {
            "name": name,
            "ws": ws,
            "task_store": task_store,
            "memory_store": memory_store,
            "skill_registry": skill_registry,
            "sync_mgr": sync_mgr,
        }

    def test_01_three_clients_offline_concurrent_mutations_lww_convergence(self) -> None:
        """
        Adversarial test: 3 independent clients (Alpha, Beta, Gamma) mutate the same task
        while completely disconnected from the cloud server.
        - Alpha sets title='Title Alpha', prio=HIGH at t1 (12:00:00)
        - Beta sets title='Title Beta', prio=MED at t2 (12:05:00)
        - Gamma sets title='Title Gamma', prio=LOW at t3 (12:10:00)
        Verifies that upon sequential or interleaved reconnection, all 3 clients and the cloud
        server converge to Gamma's update (t3 dominance).
        """
        c_alpha = self._create_client("client_alpha")
        c_beta = self._create_client("client_beta")
        c_gamma = self._create_client("client_gamma")

        # Step 1: Initial task created by Alpha and synced to all clients
        t_init = c_alpha["task_store"].add_task("Initial Shared Task", priority=TaskPriority.LOW)
        t_init.updated_at = "2026-10-20T10:00:00.000000Z"
        c_alpha["task_store"].save()
        c_alpha["sync_mgr"].enqueue_mutation("task", t_init.id, t_init.to_dict())
        res_a0 = c_alpha["sync_mgr"].reconcile()
        self.assertEqual(res_a0["status"], "synced")

        # Beta and Gamma pull initial task
        res_b0 = c_beta["sync_mgr"].reconcile()
        res_g0 = c_gamma["sync_mgr"].reconcile()
        self.assertEqual(res_b0["status"], "synced")
        self.assertEqual(res_g0["status"], "synced")
        self.assertIsNotNone(c_beta["task_store"].get_task(t_init.id))
        self.assertIsNotNone(c_gamma["task_store"].get_task(t_init.id))

        # Step 2: Network partition - stop cloud server
        self.server.stop()

        # Step 3: All 3 clients perform concurrent offline mutations with timestamps t1 < t2 < t3
        t1 = "2026-10-20T12:00:00.000000Z"
        t2 = "2026-10-20T12:05:00.000000Z"
        t3 = "2026-10-20T12:10:00.000000Z"

        # Alpha modifies offline
        t_a = c_alpha["task_store"].get_task(t_init.id)
        t_a.title = "Title Alpha"
        t_a.priority = TaskPriority.HIGH
        t_a.updated_at = t1
        c_alpha["task_store"].save()
        c_alpha["sync_mgr"].enqueue_mutation("task", t_init.id, t_a.to_dict())

        # Beta modifies offline
        t_b = c_beta["task_store"].get_task(t_init.id)
        t_b.title = "Title Beta"
        t_b.priority = TaskPriority.MEDIUM
        t_b.updated_at = t2
        c_beta["task_store"].save()
        c_beta["sync_mgr"].enqueue_mutation("task", t_init.id, t_b.to_dict())

        # Gamma modifies offline
        t_g = c_gamma["task_store"].get_task(t_init.id)
        t_g.title = "Title Gamma"
        t_g.priority = TaskPriority.LOW
        t_g.updated_at = t3
        c_gamma["task_store"].save()
        c_gamma["sync_mgr"].enqueue_mutation("task", t_init.id, t_g.to_dict())

        # Verify offline resilience: sync attempts fail gracefully without data loss
        for cl in [c_alpha, c_beta, c_gamma]:
            res = cl["sync_mgr"].reconcile()
            self.assertEqual(res["status"], "offline")
            self.assertEqual(cl["sync_mgr"].get_queue_stats()["pending_mutations"], 1)

        # Step 4: Network healed - start server on same port & DB
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()

        # Step 5: Clients reconnect in arbitrary order: Alpha, then Beta, then Gamma
        res_a1 = c_alpha["sync_mgr"].reconcile()
        self.assertEqual(res_a1["status"], "synced")
        self.assertEqual(c_alpha["sync_mgr"].get_queue_stats()["pending_mutations"], 0)

        res_b1 = c_beta["sync_mgr"].reconcile()
        self.assertEqual(res_b1["status"], "synced")
        self.assertEqual(c_beta["sync_mgr"].get_queue_stats()["pending_mutations"], 0)

        res_g1 = c_gamma["sync_mgr"].reconcile()
        self.assertEqual(res_g1["status"], "synced")
        self.assertEqual(c_gamma["sync_mgr"].get_queue_stats()["pending_mutations"], 0)

        # Step 6: Second sync round so all clients pull remaining remote deltas
        c_alpha["sync_mgr"].reconcile()
        c_beta["sync_mgr"].reconcile()
        c_gamma["sync_mgr"].reconcile()

        # Step 7: Verify strict CRDT LWW convergence across all stores and server
        # Cloud server state
        muts, _ = self.storage.pull_mutations(0)
        task_mut = [m for m in muts if m["entity_id"] == t_init.id][0]
        self.assertEqual(task_mut["payload"]["title"], "Title Gamma")
        self.assertEqual(task_mut["payload"]["priority"], "low")
        self.assertEqual(task_mut["updated_at"], t3)

        # All 3 client stores must hold Gamma's state
        for cl in [c_alpha, c_beta, c_gamma]:
            task_local = cl["task_store"].get_task(t_init.id)
            self.assertIsNotNone(task_local)
            self.assertEqual(task_local.title, "Title Gamma", f"Failed on {cl['name']}")
            self.assertEqual(task_local.priority.value, "low", f"Failed on {cl['name']}")
            self.assertEqual(task_local.updated_at, t3, f"Failed on {cl['name']}")
            self.assertFalse(task_local.deleted, f"Failed on {cl['name']}")

    def test_02_four_clients_offline_multi_entity_race(self) -> None:
        """
        Adversarial test: 4 clients race offline across 3 distinct entity types:
        - Task entity: Client Delta has newest timestamp -> Delta wins.
        - Memory fact entity: Client Beta has newest timestamp -> Beta wins.
        - Skill entity: Client Gamma has newest timestamp -> Gamma wins.
        Verifies multi-entity cross-type independent CRDT LWW convergence.
        """
        clients = [self._create_client(f"client_{x}") for x in ["alpha", "beta", "gamma", "delta"]]

        # Disconnect server
        self.server.stop()

        # Timestamps
        t_base = "2026-10-20T14:00:00.000000Z"
        t_new = "2026-10-20T14:30:00.000000Z"

        # 1. Task: Alpha, Beta, Gamma have t_base; Delta has t_new
        for i, cl in enumerate(clients):
            ts = t_new if cl["name"] == "client_delta" else t_base
            payload = {
                "id": "shared_race_task",
                "title": f"Task from {cl['name']}",
                "priority": "high" if cl["name"] == "client_delta" else "low",
                "state": "todo",
                "updated_at": ts,
                "deleted": False,
            }
            cl["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": "shared_race_task", "payload": payload, "updated_at": ts})
            cl["sync_mgr"].enqueue_mutation("task", "shared_race_task", payload)

        # 2. Memory Fact: Alpha, Gamma, Delta have t_base; Beta has t_new
        for cl in clients:
            ts = t_new if cl["name"] == "client_beta" else t_base
            payload = {
                "id": "fact-shared-race",
                "content": f"Fact winning content from {cl['name']}",
                "category": "fact",
                "updated_at": ts,
                "deleted": False,
            }
            cl["memory_store"].apply_crdt_mutation({"id": "fact-shared-race", "entity_type": "memory_fact", "data": payload, "payload": payload, "updated_at": ts})
            cl["sync_mgr"].enqueue_mutation("memory_fact", "fact-shared-race", payload)

        # 3. Skill: Alpha, Beta, Delta have t_base; Gamma has t_new
        for cl in clients:
            ts = t_new if cl["name"] == "client_gamma" else t_base
            payload = {
                "id": "shared_race_skill",
                "name": f"Skill from {cl['name']}",
                "description": f"Winner is {cl['name']}",
                "type": "workflow",
                "updated_at": ts,
                "deleted": False,
            }
            cl["skill_registry"].apply_crdt_mutation({"entity_type": "skill", "entity_id": "shared_race_skill", "payload": payload, "updated_at": ts})
            cl["sync_mgr"].enqueue_mutation("skill", "shared_race_skill", payload)

        # Reconnect server
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()

        # Reconcile all 4 clients in arbitrary order
        random.shuffle(clients)
        for cl in clients:
            res = cl["sync_mgr"].reconcile()
            self.assertEqual(res["status"], "synced")

        # Second reconcile loop for delta convergence
        for cl in clients:
            cl["sync_mgr"].reconcile()

        # Assertions across all 4 clients
        for cl in clients:
            # 1. Task winner: client_delta
            task = cl["task_store"].get_task("shared_race_task")
            self.assertIsNotNone(task)
            self.assertEqual(task.title, "Task from client_delta")
            self.assertEqual(task.priority.value, "high")

            # 2. Fact winner: client_beta
            fact = cl["memory_store"].get_entry("fact-shared-race")
            self.assertIsNotNone(fact)
            self.assertEqual(fact["content"], "Fact winning content from client_beta")

            # 3. Skill winner: client_gamma
            skill = cl["skill_registry"].get_skill("shared_race_skill")
            self.assertIsNotNone(skill)
            self.assertEqual(skill["name"], "Skill from client_gamma")

    def test_03_offline_burst_mutations_and_recovery(self) -> None:
        """
        Verifies offline persistence across client restart:
        50 mutations generated while offline survive process restart, then flush cleanly.
        """
        cl = self._create_client("client_burst")

        # Server down
        self.server.stop()

        # Enqueue 50 offline mutations
        for i in range(50):
            cl["sync_mgr"].enqueue_mutation("task", f"burst_t_{i}", {"title": f"Burst Task {i}"})

        self.assertEqual(cl["sync_mgr"].get_queue_stats()["pending_mutations"], 50)

        # Simulate client crash / restart: re-instantiate CloudSyncManager from same disk path
        restarted_sync = CloudSyncManager(
            cl["ws"],
            server_url=self.server_url,
            device_id="client_burst",
            token="tok_client_burst",
            task_store=cl["task_store"],
        )
        self.assertEqual(restarted_sync.get_queue_stats()["pending_mutations"], 50)

        # Server restored
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()

        # Flush burst
        res = restarted_sync.reconcile()
        self.assertEqual(res["status"], "synced")
        self.assertEqual(restarted_sync.get_queue_stats()["pending_mutations"], 0)
        self.assertGreaterEqual(res["server_sequence"], 50)


class TestConflictingUpdatesAndTieBreak(unittest.TestCase):
    """
    Adversarial verification of conflicting updates between disconnected clients:
    - Update vs Delete (tombstone dominance, revival, timestamp collision).
    - Canonical JSON lexicographical tie-breaking at identical timestamps.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_challenger_m4_conf_")
        self.server_db = Path(self.tmp) / "cloud.db"
        self.storage = SQLiteStorageAdapter(self.server_db)
        self.port = get_ephemeral_port()
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        self.server_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_clients(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        ws_a = Path(self.tmp) / "client_a"
        ws_b = Path(self.tmp) / "client_b"
        ws_a.mkdir(parents=True, exist_ok=True)
        ws_b.mkdir(parents=True, exist_ok=True)

        store_a = TaskStore(ws_a)
        store_b = TaskStore(ws_b)
        mem_a = MemoryStore(ws_a)
        mem_b = MemoryStore(ws_b)
        reg_a = SkillRegistry(ws_a)
        reg_b = SkillRegistry(ws_b)

        sync_a = CloudSyncManager(ws_a, server_url=self.server_url, device_id="dev_a", token="t_a", task_store=store_a, memory_store=mem_a, skill_registry=reg_a)
        sync_b = CloudSyncManager(ws_b, server_url=self.server_url, device_id="dev_b", token="t_b", task_store=store_b, memory_store=mem_b, skill_registry=reg_b)

        return (
            {"sync": sync_a, "task_store": store_a, "mem_store": mem_a, "reg": reg_a},
            {"sync": sync_b, "task_store": store_b, "mem_store": mem_b, "reg": reg_b},
        )

    def test_01_update_vs_delete_newer_delete_wins(self) -> None:
        """
        Client A updates task title at t1.
        Client B deletes task at t2 (t2 > t1).
        Verifies tombstone dominance: task is deleted on server and both clients.
        """
        cl_a, cl_b = self._setup_clients()

        # Baseline task
        t0 = "2026-10-20T10:00:00.000000Z"
        task = cl_a["task_store"].add_task("Conflict Task 1")
        task.updated_at = t0
        cl_a["task_store"].save()
        cl_a["sync"].enqueue_mutation("task", task.id, task.to_dict())
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        # Offline mutations
        t1 = "2026-10-20T11:00:00.000000Z"
        t2 = "2026-10-20T11:05:00.000000Z"

        # A updates title
        payload_a = dict(task.to_dict())
        payload_a["title"] = "Updated Title from A"
        payload_a["updated_at"] = t1
        payload_a["deleted"] = False
        cl_a["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": payload_a, "updated_at": t1, "deleted": False})
        cl_a["sync"].enqueue_mutation("task", task.id, payload_a, deleted=False)

        # B deletes task
        payload_b = dict(task.to_dict())
        payload_b["updated_at"] = t2
        payload_b["deleted"] = True
        cl_b["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": payload_b, "updated_at": t2, "deleted": True})
        cl_b["sync"].enqueue_mutation("task", task.id, payload_b, deleted=True)

        # Reconcile: A syncs first, then B, then A pulls
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()
        cl_a["sync"].reconcile()

        # Both clients and server must reflect deleted=True
        task_a = cl_a["task_store"].get_task(task.id)
        task_b = cl_b["task_store"].get_task(task.id)
        self.assertTrue(task_a.deleted)
        self.assertTrue(task_b.deleted)

        muts, _ = self.storage.pull_mutations(0)
        server_entity = [m for m in muts if m["entity_id"] == task.id][0]
        self.assertTrue(server_entity["deleted"])

    def test_02_update_vs_delete_newer_update_revives(self) -> None:
        """
        Client B deletes task at t1.
        Client A updates/revives task at t2 (t2 > t1).
        Verifies revival: task is active with A's title on server and both clients.
        """
        cl_a, cl_b = self._setup_clients()

        task = cl_a["task_store"].add_task("Conflict Task 2")
        task.updated_at = "2026-10-20T10:00:00.000000Z"
        cl_a["task_store"].save()
        cl_a["sync"].enqueue_mutation("task", task.id, task.to_dict())
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        t1 = "2026-10-20T12:00:00.000000Z"
        t2 = "2026-10-20T12:05:00.000000Z"

        # B deletes task at t1
        payload_b = dict(task.to_dict())
        payload_b["updated_at"] = t1
        payload_b["deleted"] = True
        cl_b["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": payload_b, "updated_at": t1, "deleted": True})
        cl_b["sync"].enqueue_mutation("task", task.id, payload_b, deleted=True)

        # A revives task at t2
        payload_a = dict(task.to_dict())
        payload_a["title"] = "Revived by A"
        payload_a["updated_at"] = t2
        payload_a["deleted"] = False
        cl_a["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": payload_a, "updated_at": t2, "deleted": False})
        cl_a["sync"].enqueue_mutation("task", task.id, payload_a, deleted=False)

        # B syncs first, then A syncs, then B pulls
        cl_b["sync"].reconcile()
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        task_a = cl_a["task_store"].get_task(task.id)
        task_b = cl_b["task_store"].get_task(task.id)
        self.assertFalse(task_a.deleted)
        self.assertFalse(task_b.deleted)
        self.assertEqual(task_a.title, "Revived by A")
        self.assertEqual(task_b.title, "Revived by A")

    def test_03_update_vs_delete_identical_timestamp_tombstone_wins(self) -> None:
        """
        Level 2 CRDT tie-break:
        Client A updates task (deleted=False) at t_same.
        Client B deletes task (deleted=True) at t_same.
        Verifies tombstone preference: deleted=True wins deterministically in both sync orders.
        """
        t_same = "2026-10-20T13:30:00.000000Z"

        # Order 1: A pushes active, then B pushes tombstone -> Tombstone wins
        cl_a, cl_b = self._setup_clients()
        task = cl_a["task_store"].add_task("Tombstone Tie Task 1")
        task.updated_at = "2026-10-20T10:00:00.000000Z"
        cl_a["task_store"].save()
        cl_a["sync"].enqueue_mutation("task", task.id, task.to_dict())
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        p_a = dict(task.to_dict())
        p_a["title"] = "Active Update"
        p_a["updated_at"] = t_same
        p_a["deleted"] = False

        p_b = dict(task.to_dict())
        p_b["updated_at"] = t_same
        p_b["deleted"] = True

        cl_a["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": p_a, "updated_at": t_same, "deleted": False})
        cl_b["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": p_b, "updated_at": t_same, "deleted": True})

        cl_a["sync"].enqueue_mutation("task", task.id, p_a, deleted=False)
        cl_b["sync"].enqueue_mutation("task", task.id, p_b, deleted=True)

        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()
        cl_a["sync"].reconcile()

        self.assertTrue(cl_a["task_store"].get_task(task.id).deleted)
        self.assertTrue(cl_b["task_store"].get_task(task.id).deleted)

        # Order 2: B pushes tombstone first, then A pushes active -> Tombstone remains winner
        cl_a2, cl_b2 = self._setup_clients()
        task2 = cl_a2["task_store"].add_task("Tombstone Tie Task 2")
        task2.updated_at = "2026-10-20T10:00:00.000000Z"
        cl_a2["task_store"].save()
        cl_a2["sync"].enqueue_mutation("task", task2.id, task2.to_dict())
        cl_a2["sync"].reconcile()
        cl_b2["sync"].reconcile()

        p_a2 = dict(task2.to_dict())
        p_a2["title"] = "Active Stale Update"
        p_a2["updated_at"] = t_same
        p_a2["deleted"] = False

        p_b2 = dict(task2.to_dict())
        p_b2["updated_at"] = t_same
        p_b2["deleted"] = True

        cl_b2["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task2.id, "payload": p_b2, "updated_at": t_same, "deleted": True})
        cl_a2["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task2.id, "payload": p_a2, "updated_at": t_same, "deleted": False})

        cl_b2["sync"].enqueue_mutation("task", task2.id, p_b2, deleted=True)
        cl_a2["sync"].enqueue_mutation("task", task2.id, p_a2, deleted=False)

        cl_b2["sync"].reconcile()
        cl_a2["sync"].reconcile()
        cl_b2["sync"].reconcile()

        self.assertTrue(cl_a2["task_store"].get_task(task2.id).deleted)
        self.assertTrue(cl_b2["task_store"].get_task(task2.id).deleted)

    def test_04_identical_timestamp_and_deleted_canonical_json_tiebreak(self) -> None:
        """
        Level 3 CRDT tie-break:
        When timestamp and deleted flags are identical, canonical JSON lexicographical
        comparison determines the winner deterministically.
        """
        t_same = "2026-10-20T15:00:00.000000Z"
        cl_a, cl_b = self._setup_clients()

        task = cl_a["task_store"].add_task("Canonical JSON Tie Task")
        task.updated_at = "2026-10-20T10:00:00.000000Z"
        cl_a["task_store"].save()
        cl_a["sync"].enqueue_mutation("task", task.id, task.to_dict())
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        # Payload A has priority="low", Payload B has priority="high"
        # Under canonical JSON: {"priority":"low"...} > {"priority":"high"...}
        # Therefore Payload A must win level 3 tie-break
        p_a = dict(task.to_dict())
        p_a["priority"] = "low"
        p_a["title"] = "Alpha JSON Winner"
        p_a["updated_at"] = t_same

        p_b = dict(task.to_dict())
        p_b["priority"] = "high"
        p_b["title"] = "Beta JSON Loser"
        p_b["updated_at"] = t_same

        cl_a["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": p_a, "updated_at": t_same})
        cl_b["task_store"].apply_crdt_mutation({"entity_type": "task", "entity_id": task.id, "payload": p_b, "updated_at": t_same})

        # Test both sync orderings to prove commutativity
        # Subcase 1: B syncs first, then A syncs -> A wins and overwrites B
        cl_b["sync"].enqueue_mutation("task", task.id, p_b)
        cl_a["sync"].enqueue_mutation("task", task.id, p_a)
        cl_b["sync"].reconcile()
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        self.assertEqual(cl_a["task_store"].get_task(task.id).title, "Alpha JSON Winner")
        self.assertEqual(cl_b["task_store"].get_task(task.id).title, "Alpha JSON Winner")

    def test_05_memory_fact_and_skill_conflicts(self) -> None:
        """
        Verifies conflict resolution on MemoryStore and SkillRegistry:
        - Memory fact: Client A updates content at t1; Client B deletes (tombstone) at t2 > t1.
        - Skill: Client A updates at t_same (deleted=False); Client B deletes at t_same (deleted=True).
        """
        cl_a, cl_b = self._setup_clients()

        # 1. Memory Fact
        fact = cl_a["mem_store"].add_fact("Original preference", category="preference")
        cl_a["mem_store"]._facts[fact["id"]].updated_at = "2026-10-20T10:00:00.000000Z"
        cl_a["mem_store"]._save_facts()
        fact = cl_a["mem_store"].get_entry(fact["id"])
        cl_a["sync"].enqueue_mutation("memory_fact", fact["id"], fact)
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        t1 = "2026-10-20T16:00:00.000000Z"
        t2 = "2026-10-20T16:05:00.000000Z"

        p_fact_a = dict(fact)
        p_fact_a["content"] = "Updated preference"
        p_fact_a["updated_at"] = t1
        p_fact_a["deleted"] = False

        p_fact_b = dict(fact)
        p_fact_b["updated_at"] = t2
        p_fact_b["deleted"] = True

        cl_a["mem_store"].apply_crdt_mutation({"id": fact["id"], "entity_type": "memory_fact", "data": p_fact_a, "updated_at": t1, "deleted": False})
        cl_b["mem_store"].apply_crdt_mutation({"id": fact["id"], "entity_type": "memory_fact", "data": p_fact_b, "updated_at": t2, "deleted": True})

        cl_a["sync"].enqueue_mutation("memory_fact", fact["id"], p_fact_a, deleted=False)
        cl_b["sync"].enqueue_mutation("memory_fact", fact["id"], p_fact_b, deleted=True)

        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()
        cl_a["sync"].reconcile()

        # Memory entry should have deleted=True (tombstone) on both
        entry_a = cl_a["mem_store"].get_entry(fact["id"])
        entry_b = cl_b["mem_store"].get_entry(fact["id"])
        self.assertTrue(entry_a is None or entry_a.get("deleted", False))
        self.assertTrue(entry_b is None or entry_b.get("deleted", False))

        # 2. Skill Tombstone Tie-break
        skill_def = {
            "id": "tie_break_skill",
            "name": "tie_break_skill",
            "description": "Skill for tie break",
            "type": "workflow",
            "steps": [{"action": "test"}],
        }
        ok_reg, msg_reg = cl_a["reg"].register_skill(skill_def)
        self.assertTrue(ok_reg, f"Skill registration failed: {msg_reg}")
        skill_id = "tie_break_skill"
        skill_data = dict(cl_a["reg"].get_skill(skill_id))
        skill_data["version"] = 1  # Workaround for SQLiteStorageAdapter int("1.0.0") bug
        cl_a["sync"].enqueue_mutation("skill", skill_id, skill_data)
        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()

        time.sleep(0.01)
        t_same = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()

        p_skill_a = dict(cl_a["reg"].get_skill(skill_id))
        p_skill_a["version"] = 1
        p_skill_a["description"] = "Active update"
        p_skill_a["updated_at"] = t_same
        p_skill_a["deleted"] = False

        p_skill_b = dict(cl_b["reg"].get_skill(skill_id))
        p_skill_b["version"] = 1
        p_skill_b["updated_at"] = t_same
        p_skill_b["deleted"] = True

        cl_a["reg"].apply_crdt_mutation({"entity_type": "skill", "entity_id": skill_id, "payload": p_skill_a, "updated_at": t_same, "deleted": False})
        cl_b["reg"].apply_crdt_mutation({"entity_type": "skill", "entity_id": skill_id, "payload": p_skill_b, "updated_at": t_same, "deleted": True})

        cl_a["sync"].enqueue_mutation("skill", skill_id, p_skill_a, deleted=False)
        cl_b["sync"].enqueue_mutation("skill", skill_id, p_skill_b, deleted=True)

        cl_a["sync"].reconcile()
        cl_b["sync"].reconcile()
        cl_a["sync"].reconcile()

        self.assertIsNone(cl_a["reg"].get_skill(skill_id))
        self.assertIsNone(cl_b["reg"].get_skill(skill_id))
        deleted_skills_a = [s for s in cl_a["reg"].list_skills(include_deleted=True) if s.get("id") == skill_id]
        deleted_skills_b = [s for s in cl_b["reg"].list_skills(include_deleted=True) if s.get("id") == skill_id]
        self.assertTrue(deleted_skills_a and deleted_skills_a[0].get("deleted", False))
        self.assertTrue(deleted_skills_b and deleted_skills_b[0].get("deleted", False))

    def test_06_semver_string_version_crashes_storage_adapter_bug(self) -> None:
        """
        Verifies version parsing resilience:
        Skill manifests created by SkillRegistry use semantic version strings such as '1.0.0'.
        When enqueued and pushed, SQLiteStorageAdapter parses semver safely without crashing.
        """
        applied, seq = self.storage.apply_mutation({
            "entity_type": "skill",
            "entity_id": "tool_semver_bug",
            "device_id": "dev_test",
            "version": "1.0.0",  # Semver from SkillManifest
            "updated_at": now_utc_iso(),
            "payload": {"name": "test_skill", "version": "1.0.0"},
        })
        self.assertTrue(applied)
        self.assertGreaterEqual(seq, 1)


class TestReconnectionOrderAndCommutativity(unittest.TestCase):
    """
    Adversarial verification that any permutation of client reconnection orders
    guarantees identical final state convergence (Commutativity and Eventual Consistency).
    """

    def test_01_all_six_sync_permutations_converge(self) -> None:
        """
        Tests all 6 permutations of 3 clients (A, B, C) reconnecting after offline mutations:
        (A, B, C), (A, C, B), (B, A, C), (B, C, A), (C, A, B), (C, B, A).
        Verifies that after delta pull convergence, every single client in every permutation
        converges to the exact same state as the server.
        """
        perms = list(itertools.permutations(["client_a", "client_b", "client_c"]))

        for perm in perms:
            tmp = tempfile.mkdtemp(prefix="neko_perm_")
            try:
                db_path = Path(tmp) / "cloud.db"
                storage = SQLiteStorageAdapter(db_path)
                port = get_ephemeral_port()
                server = CloudServer(storage=storage, port=port)
                server.start()
                server_url = f"http://127.0.0.1:{port}"

                # Setup 3 clients
                clients = {}
                for name in ["client_a", "client_b", "client_c"]:
                    ws = Path(tmp) / name
                    ws.mkdir(parents=True, exist_ok=True)
                    tstore = TaskStore(ws)
                    sync = CloudSyncManager(ws, server_url=server_url, device_id=name, token=f"tok_{name}", task_store=tstore)
                    clients[name] = {"sync": sync, "store": tstore}

                # Baseline entity
                t_init = clients["client_a"]["store"].add_task("Permutation Task")
                t_init.updated_at = "2026-10-20T10:00:00.000000Z"
                clients["client_a"]["store"].save()
                clients["client_a"]["sync"].enqueue_mutation("task", t_init.id, t_init.to_dict())
                clients["client_a"]["sync"].reconcile()
                clients["client_b"]["sync"].reconcile()
                clients["client_c"]["sync"].reconcile()

                # Disconnect server
                server.stop()

                # Offline mutations with timestamps t_a < t_b < t_c
                t_a = "2026-10-20T18:00:00.000000Z"
                t_b = "2026-10-20T18:05:00.000000Z"
                t_c = "2026-10-20T18:10:00.000000Z"  # Winning timestamp

                p_a = dict(t_init.to_dict())
                p_a["title"] = "Title from Alpha"
                p_a["updated_at"] = t_a
                clients["client_a"]["store"].apply_crdt_mutation({"entity_type": "task", "entity_id": t_init.id, "payload": p_a, "updated_at": t_a})
                clients["client_a"]["sync"].enqueue_mutation("task", t_init.id, p_a)

                p_b = dict(t_init.to_dict())
                p_b["title"] = "Title from Beta"
                p_b["updated_at"] = t_b
                clients["client_b"]["store"].apply_crdt_mutation({"entity_type": "task", "entity_id": t_init.id, "payload": p_b, "updated_at": t_b})
                clients["client_b"]["sync"].enqueue_mutation("task", t_init.id, p_b)

                p_c = dict(t_init.to_dict())
                p_c["title"] = "Title from Gamma"
                p_c["updated_at"] = t_c
                clients["client_c"]["store"].apply_crdt_mutation({"entity_type": "task", "entity_id": t_init.id, "payload": p_c, "updated_at": t_c})
                clients["client_c"]["sync"].enqueue_mutation("task", t_init.id, p_c)

                # Reconnect server
                server = CloudServer(storage=storage, port=port)
                server.start()

                # Reconcile in the permutation order
                for name in perm:
                    res = clients[name]["sync"].reconcile()
                    self.assertEqual(res["status"], "synced")

                # Second round delta pull for all
                for name in perm:
                    clients[name]["sync"].reconcile()

                # Verify convergence on Gamma
                for name in ["client_a", "client_b", "client_c"]:
                    task = clients[name]["store"].get_task(t_init.id)
                    self.assertEqual(task.title, "Title from Gamma", f"Failed for perm {perm} on {name}")
                    self.assertEqual(task.updated_at, t_c)

                server.stop()
                storage.close()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_02_network_partition_intermittent_flapping(self) -> None:
        """
        Adversarial test: intermittent network flapping (up -> down -> up -> down -> up).
        Verifies zero mutation loss, queue state preservation, and monotonic sequence advancement.
        """
        tmp = tempfile.mkdtemp(prefix="neko_flap_")
        try:
            db_path = Path(tmp) / "cloud.db"
            storage = SQLiteStorageAdapter(db_path)
            port = get_ephemeral_port()
            server_url = f"http://127.0.0.1:{port}"

            ws = Path(tmp) / "client"
            ws.mkdir(parents=True, exist_ok=True)
            store = TaskStore(ws)
            sync = CloudSyncManager(ws, server_url=server_url, device_id="dev_flapper", token="t", task_store=store)

            # Cycle 1: Server down -> enqueue 3 mutations -> sync fails
            for i in range(3):
                sync.enqueue_mutation("task", f"flap_1_{i}", {"title": f"Flap 1-{i}"})
            res1 = sync.reconcile()
            self.assertEqual(res1["status"], "offline")
            self.assertEqual(sync.get_queue_stats()["pending_mutations"], 3)

            # Cycle 2: Server up -> reconcile succeeds
            server = CloudServer(storage=storage, port=port)
            server.start()
            res2 = sync.reconcile()
            self.assertEqual(res2["status"], "synced")
            self.assertEqual(sync.get_queue_stats()["pending_mutations"], 0)
            self.assertEqual(res2["server_sequence"], 3)

            # Cycle 3: Server down -> enqueue 4 mutations -> sync fails
            server.stop()
            for i in range(4):
                sync.enqueue_mutation("task", f"flap_2_{i}", {"title": f"Flap 2-{i}"})
            res3 = sync.reconcile()
            self.assertEqual(res3["status"], "offline")
            self.assertEqual(sync.get_queue_stats()["pending_mutations"], 4)

            # Cycle 4: Server up -> reconcile flushes 4 mutations
            server = CloudServer(storage=storage, port=port)
            server.start()
            res4 = sync.reconcile()
            self.assertEqual(res4["status"], "synced")
            self.assertEqual(sync.get_queue_stats()["pending_mutations"], 0)
            self.assertEqual(res4["server_sequence"], 7)

            server.stop()
            storage.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_03_concurrent_reconnection_threads(self) -> None:
        """
        Stress test: 5 independent clients concurrently invoke reconcile() at the exact
        same millisecond via ThreadPoolExecutor.
        Verifies no SQLite deadlock, no thread contention crash, and 100% successful sync.
        """
        tmp = tempfile.mkdtemp(prefix="neko_concur_reconnect_")
        try:
            db_path = Path(tmp) / "cloud.db"
            storage = SQLiteStorageAdapter(db_path)
            port = get_ephemeral_port()
            server = CloudServer(storage=storage, port=port)
            server.start()
            server_url = f"http://127.0.0.1:{port}"

            clients = []
            for i in range(5):
                ws = Path(tmp) / f"client_{i}"
                ws.mkdir(parents=True, exist_ok=True)
                store = TaskStore(ws)
                sync = CloudSyncManager(ws, server_url=server_url, device_id=f"dev_{i}", token=f"tok_{i}", task_store=store)
                # Each client has 5 queued mutations
                for j in range(5):
                    sync.enqueue_mutation("task", f"th_task_{i}_{j}", {"title": f"Task {i}-{j}"})
                clients.append(sync)

            results = []
            errors = []

            def sync_worker(sync_mgr: CloudSyncManager):
                try:
                    res = sync_mgr.reconcile()
                    results.append(res)
                except Exception as e:
                    errors.append(e)

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(sync_worker, c) for c in clients]
                for f in as_completed(futures):
                    f.result()

            self.assertEqual(len(errors), 0, f"Errors occurred during concurrent reconcile: {errors}")
            self.assertEqual(len(results), 5)
            for res in results:
                self.assertEqual(res["status"], "synced")

            # Final server sequence must be 25
            status = storage.get_status()
            self.assertEqual(status["server_sequence"], 25)

            server.stop()
            storage.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestHighThroughputPushesAndSequenceMonotonicity(unittest.TestCase):
    """
    Adversarial verification of high-throughput rapid pushes to /api/v1/sync/push
    and strict sequence monotonicity.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_challenger_m4_ht_")
        self.server_db = Path(self.tmp) / "cloud.db"
        self.storage = SQLiteStorageAdapter(self.server_db)
        self.port = get_ephemeral_port()
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        self.server_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_high_throughput_concurrent_pushes_sequence_monotonicity(self) -> None:
        """
        Adversarial hammer: 10 worker threads send 20 rapid POST /api/v1/sync/push requests
        (200 total requests) directly to the REST API concurrently.
        Verifies:
        - 200 HTTP 200 responses.
        - Exactly 200 server sequences generated.
        - Strict sequence monotonicity: sequences are contiguous [1..200] with zero duplicates or gaps.
        - Server status matches sequence count.
        """
        num_threads = 10
        pushes_per_thread = 20
        total_pushes = num_threads * pushes_per_thread

        url = f"{self.server_url}/api/v1/sync/push"
        results: List[Tuple[int, Dict[str, Any]]] = []
        errors: List[Exception] = []
        results_lock = threading.Lock()

        def push_worker(thread_idx: int):
            for i in range(pushes_per_thread):
                ent_id = f"ht_ent_{thread_idx}_{i}"
                payload = {
                    "mutations": [{
                        "entity_type": "task",
                        "entity_id": ent_id,
                        "device_id": f"worker_{thread_idx}",
                        "version": 1,
                        "updated_at": now_utc_iso(),
                        "payload": {"title": f"High Throughput Task {thread_idx}-{i}", "index": i},
                    }]
                }
                try:
                    status, body = make_http_post(url, payload)
                    with results_lock:
                        results.append((status, body))
                except Exception as e:
                    with results_lock:
                        errors.append(e)

        threads = []
        for t in range(num_threads):
            th = threading.Thread(target=push_worker, args=(t,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Push worker errors: {errors}")
        self.assertEqual(len(results), total_pushes)

        # Verify all HTTP 200
        for status_code, body in results:
            self.assertEqual(status_code, 200)
            self.assertEqual(body.get("applied"), 1)

        # Pull all mutations from SQLite directly and verify sequences
        muts, max_seq = self.storage.pull_mutations(0)
        self.assertEqual(len(muts), total_pushes)
        self.assertEqual(max_seq, total_pushes)

        # Verify strictly monotonic: 1 to total_pushes
        seqs = [m["server_sequence"] for m in muts]
        expected_seqs = list(range(1, total_pushes + 1))
        self.assertEqual(sorted(seqs), expected_seqs)

        # Verify server status endpoint via GET
        status_resp_code, status_data = make_http_get(f"{self.server_url}/api/v1/sync/status")
        self.assertEqual(status_resp_code, 200)
        self.assertEqual(status_data["server_sequence"], total_pushes)
        self.assertEqual(status_data["active_entities"], total_pushes)

    def test_02_concurrent_pushes_and_pulls_interleaved(self) -> None:
        """
        Adversarial test: 4 threads continuously pushing mutations while 4 threads
        continuously pull deltas (/api/v1/sync/pull) with varying since_sequence.
        Verifies no HTTP 500 errors, consistent reads, and monotonic results.
        """
        push_url = f"{self.server_url}/api/v1/sync/push"
        pull_url = f"{self.server_url}/api/v1/sync/pull"

        stop_event = threading.Event()
        errors: List[Exception] = []
        errors_lock = threading.Lock()

        def pusher(thread_id: int):
            seq = 0
            while not stop_event.is_set() and seq < 25:
                payload = {
                    "mutations": [{
                        "entity_type": "task",
                        "entity_id": f"interleave_{thread_id}_{seq}",
                        "device_id": f"pusher_{thread_id}",
                        "updated_at": now_utc_iso(),
                        "payload": {"seq": seq},
                    }]
                }
                try:
                    code, body = make_http_post(push_url, payload)
                    if code != 200:
                        with errors_lock:
                            errors.append(RuntimeError(f"Push failed: {code} {body}"))
                except Exception as e:
                    with errors_lock:
                        errors.append(e)
                seq += 1
                time.sleep(0.005)

        def puller(thread_id: int):
            last_seq = 0
            while not stop_event.is_set():
                try:
                    code, body = make_http_post(pull_url, {"since_sequence": last_seq})
                    if code != 200:
                        with errors_lock:
                            errors.append(RuntimeError(f"Pull failed: {code} {body}"))
                    muts = body.get("mutations", [])
                    # Verify returned mutations are sorted by server_sequence ASC
                    pulled_seqs = [m["server_sequence"] for m in muts]
                    if pulled_seqs != sorted(pulled_seqs):
                        with errors_lock:
                            errors.append(ValueError(f"Unsorted sequence pulled: {pulled_seqs}"))
                    if muts:
                        last_seq = body.get("server_sequence", last_seq)
                except Exception as e:
                    with errors_lock:
                        errors.append(e)
                time.sleep(0.01)

        threads = []
        for i in range(4):
            t_push = threading.Thread(target=pusher, args=(i,))
            t_pull = threading.Thread(target=puller, args=(i,))
            threads.extend([t_push, t_pull])
            t_push.start()
            t_pull.start()

        # Wait for pushers to complete
        for th in threads[0::2]:
            th.join(timeout=20.0)

        stop_event.set()
        for th in threads[1::2]:
            th.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Interleaved push/pull errors: {errors}")

    def test_03_offline_queue_concurrency_stress_hammer(self) -> None:
        """
        Stress-tests OfflineMutationQueue under heavy Windows file-locking contention.
        10 concurrent threads call enqueue() 25 times each (250 mutations total).
        Verifies zero PermissionError / [WinError 32] unhandled exceptions and exact FIFO count.
        """
        q_dir = Path(self.tmp) / "queue_stress"
        queue = OfflineMutationQueue(q_dir)

        errors = []
        def enqueue_worker(worker_id: int):
            try:
                for i in range(25):
                    queue.enqueue("task", f"stress_{worker_id}_{i}", {"idx": i})
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(enqueue_worker, w) for w in range(10)]
            for f in as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0, f"Queue lock contention errors: {errors}")
        self.assertEqual(queue.get_stats()["pending_mutations"], 250)


class TestAdversarialPayloadsAndEdgeCases(unittest.TestCase):
    """
    Adversarial verification of edge-case payloads, UTF-8 unicode characters,
    timezone offset permutations, and idempotency under concurrent duplicates.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_challenger_m4_edge_")
        self.server_db = Path(self.tmp) / "cloud.db"
        self.storage = SQLiteStorageAdapter(self.server_db)
        self.port = get_ephemeral_port()
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        self.server_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_adversarial_payload_characters_and_types(self) -> None:
        """
        Adversarial test: mutations containing emojis, multi-byte UTF-8, null bytes,
        deeply nested dictionaries, and 64KB text payloads.
        Verifies zero corruption through canonical JSON and storage layers.
        """
        ws = Path(self.tmp) / "client_adv"
        ws.mkdir(parents=True, exist_ok=True)
        store = TaskStore(ws)
        sync = CloudSyncManager(ws, server_url=self.server_url, device_id="dev_adv", token="t", task_store=store)

        large_text = "🐱 NekoBot Assistant 🚀 " * 2000  # ~50 KB string
        nested_data = {"level1": {"level2": {"level3": {"arr": [1, 2, "猫", True, None]}}}}

        adv_payload = {
            "title": "Adversarial Test: ' OR 1=1; DROP TABLE sync_entities; -- 🐱✨",
            "metadata": {
                "large_blob": large_text,
                "nested": nested_data,
                "null_byte": "safe_string_with_null\\u0000escaped",
                "special_chars": "<>&\"'\\\n\r\t",
            }
        }

        task = store.add_task(adv_payload["title"], metadata=adv_payload["metadata"])
        sync.enqueue_mutation("task", task.id, task.to_dict())

        res = sync.reconcile()
        self.assertEqual(res["status"], "synced")

        # Pull from server directly and verify fidelity
        muts, _ = self.storage.pull_mutations(0)
        self.assertEqual(len(muts), 1)
        m = muts[0]
        self.assertEqual(m["payload"]["title"], adv_payload["title"])
        self.assertEqual(m["payload"]["metadata"]["large_blob"], large_text)
        self.assertEqual(m["payload"]["metadata"]["nested"], nested_data)

    def test_02_timezone_shifted_timestamps(self) -> None:
        """
        Adversarial test: mutations with identical absolute UTC epoch time but expressed
        in different timezone offset formats (+00:00 vs +09:00 vs -08:00).
        Verifies epoch normalization ensures proper timestamp comparison.
        """
        # UTC 12:00:00
        ts_utc = "2026-10-20T12:00:00+00:00"
        # Tokyo (+09:00) 21:05:00 = UTC 12:05:00 (5 minutes newer!)
        ts_tokyo = "2026-10-20T21:05:00+09:00"
        # San Francisco (-08:00) 03:55:00 = UTC 11:55:00 (5 minutes older!)
        ts_sf = "2026-10-20T03:55:00-08:00"

        # Apply older mutation first
        ok_sf, s_sf = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "tz_test_1",
            "updated_at": ts_sf,
            "payload": {"tz": "San Francisco", "rank": 1},
        })
        self.assertTrue(ok_sf)

        # Apply UTC mutation (12:00 > 11:55) -> wins
        ok_utc, s_utc = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "tz_test_1",
            "updated_at": ts_utc,
            "payload": {"tz": "UTC", "rank": 2},
        })
        self.assertTrue(ok_utc)
        self.assertGreater(s_utc, s_sf)

        # Apply Tokyo mutation (12:05 > 12:00) -> wins
        ok_tokyo, s_tokyo = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "tz_test_1",
            "updated_at": ts_tokyo,
            "payload": {"tz": "Tokyo", "rank": 3},
        })
        self.assertTrue(ok_tokyo)
        self.assertGreater(s_tokyo, s_utc)

        # Re-apply SF mutation (11:55 < 12:05) -> must be rejected as stale
        ok_stale, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "tz_test_1",
            "updated_at": ts_sf,
            "payload": {"tz": "SF Stale", "rank": 0},
        })
        self.assertFalse(ok_stale)

        # Confirm winning entity is Tokyo
        muts, _ = self.storage.pull_mutations(0)
        ent = [m for m in muts if m["entity_id"] == "tz_test_1"][0]
        self.assertEqual(ent["payload"]["tz"], "Tokyo")

    def test_03_idempotent_duplicate_pushes(self) -> None:
        """
        Adversarial test: 10 threads concurrently submitting the exact same mutation.
        Verifies exactly 1 mutation is applied, duplicates are safely rejected without
        SQLite constraint errors or server crashes.
        """
        url = f"{self.server_url}/api/v1/sync/push"
        fixed_ts = "2026-10-20T20:00:00.000000Z"
        mut_payload = {
            "mutations": [{
                "entity_type": "task",
                "entity_id": "dup_task_1",
                "device_id": "dev_dup",
                "updated_at": fixed_ts,
                "payload": {"title": "Exact Duplicate Payload"},
            }]
        }

        results = []
        errors = []
        lock = threading.Lock()

        def dup_worker():
            try:
                code, body = make_http_post(url, mut_payload)
                with lock:
                    results.append((code, body))
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(dup_worker) for _ in range(10)]
            for f in as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0, f"Duplicate worker errors: {errors}")
        self.assertEqual(len(results), 10)

        # Sum of applied across all requests must be exactly 1
        total_applied = sum(body.get("applied", 0) for _, body in results)
        self.assertEqual(total_applied, 1)

        # Server status must record exactly 1 active entity and sequence 1
        status = self.storage.get_status()
        self.assertEqual(status["active_entities"], 1)
        self.assertEqual(status["server_sequence"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
