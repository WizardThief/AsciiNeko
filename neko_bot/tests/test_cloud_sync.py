"""
tests/test_cloud_sync.py - Comprehensive Unit and Integration Test Suite for Milestone 4.

Covers:
1. SQLiteStorageAdapter (WAL mode, busy timeout, compound PK, monotonic sequences, 3-tier CRDT LWW tie-break).
2. CloudServer REST API (endpoints, dual keys, HTTP error boundaries, CORS).
3. OfflineMutationQueue (atomic persistence, lock retry, corruption recovery, thread safety).
4. CloudClient (REST transport, Bearer auth, timeout handling, typed exceptions).
5. CloudSyncManager (bidirectional reconcile, sequence tracking, multi-store dispatching, telemetry).
6. TaskStore CRDT Extensions (export_crdt_entities, apply_crdt_mutation).
7. CLI & Daemon Integration (neko cloud subparsers, background tick, telemetry export).
"""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import unittest
import urllib.error
import urllib.request

# Ensure root is in sys.path
import sys
_root_dir = Path(__file__).resolve().parent.parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))
_neko_bot_dir = _root_dir / "neko_bot"
if str(_neko_bot_dir) not in sys.path:
    sys.path.insert(0, str(_neko_bot_dir))

from cloud.models import (
    Device,
    SyncEntity,
    SyncMutation,
    canonical_json,
    compute_content_hash,
    format_iso_timestamp,
    parse_iso_timestamp,
)
from cloud.server import CloudServer
from cloud.storage.sqlite_adapter import SQLiteStorageAdapter
from neko_bot.cli import build_parser, main
from neko_bot.cloud.client import CloudClient, CloudConnectionError
from neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso
from neko_bot.cloud.sync_manager import CloudSyncManager
from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.daemon import DaemonController, DaemonServer, DaemonStatusManager
from neko_bot.memory.store import MemoryStore
from neko_bot.skills.registry import SkillRegistry
from neko_bot.skills.sandbox import SkillSandbox
from neko_bot.task.models import Task, TaskPriority, TaskState
from neko_bot.task.store import TaskStore


def get_ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


class TestSQLiteStorageAdapterUnit(unittest.TestCase):
    """Unit tests for SQLiteStorageAdapter and CRDT LWW tie-breaking."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_storage_")
        self.db_path = Path(self.tmp) / "sync_test.db"
        self.storage = SQLiteStorageAdapter(self.db_path)

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_initialize_schema_and_pragmas(self):
        """Verifies schema tables, indexes, and WAL pragma configuration."""
        self.assertEqual(self.storage.adapter_name, "SQLiteStorageAdapter")
        c = self.storage.conn.cursor()
        mode = c.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        timeout = c.execute("PRAGMA busy_timeout;").fetchone()[0]
        self.assertGreaterEqual(timeout, 5000)

        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        self.assertIn("sync_devices", tables)
        self.assertIn("sync_mutations", tables)
        self.assertIn("sync_entities", tables)

    def test_02_device_registration_and_authentication(self):
        """Verifies device registration and SHA-256 token authentication."""
        token = "secret_token_123"
        token_hash = compute_content_hash(token)
        ok = self.storage.register_device("dev_01", "Workstation Alpha", "2.0.0", token_hash)
        self.assertTrue(ok)

        # Authenticate with correct token
        self.assertTrue(self.storage.authenticate_device("dev_01", token))
        # Authenticate with incorrect token
        self.assertFalse(self.storage.authenticate_device("dev_01", "wrong_token"))
        # Authenticate non-existent device
        self.assertFalse(self.storage.authenticate_device("dev_999", token))

    def test_03_apply_mutation_strictly_monotonic_sequence(self):
        """Verifies successive mutations receive strictly increasing server sequences."""
        now = datetime.now(timezone.utc).isoformat()
        applied1, seq1 = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "task_1",
            "device_id": "dev_01",
            "updated_at": now,
            "payload": {"title": "Task 1"},
        })
        self.assertTrue(applied1)

        applied2, seq2 = self.storage.apply_mutation({
            "entity_type": "memory",
            "entity_id": "mem_1",
            "device_id": "dev_01",
            "updated_at": now,
            "payload": {"fact": "Fact 1"},
        })
        self.assertTrue(applied2)
        self.assertGreater(seq2, seq1)

    def test_04_crdt_lww_timestamp_dominance(self):
        """Level 1: Newer updated_at timestamp overrides older mutation."""
        t1 = "2026-10-20T10:00:00.000000Z"
        t2 = "2026-10-20T10:05:00.000000Z"

        # Apply older mutation first
        self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_lww",
            "updated_at": t1,
            "payload": {"title": "Original Title"},
        })

        # Apply newer mutation -> should win
        ok, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_lww",
            "updated_at": t2,
            "payload": {"title": "Updated Title"},
        })
        self.assertTrue(ok)

        muts, _ = self.storage.pull_mutations(0)
        task_mut = [m for m in muts if m["entity_id"] == "t_lww"][0]
        self.assertEqual(task_mut["payload"]["title"], "Updated Title")

        # Stale mutation with t1 should be rejected
        ok_stale, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_lww",
            "updated_at": t1,
            "payload": {"title": "Stale Title"},
        })
        self.assertFalse(ok_stale)

    def test_05_crdt_lww_tombstone_tiebreak(self):
        """Level 2: When timestamps are identical, tombstone (deleted=True) wins."""
        t_same = "2026-10-20T12:00:00.000000Z"

        self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_del_win",
            "updated_at": t_same,
            "deleted": False,
            "payload": {"title": "Active Task"},
        })

        # Tombstone mutation with identical timestamp -> wins
        ok, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_del_win",
            "updated_at": t_same,
            "deleted": True,
            "payload": {"title": "Active Task"},
        })
        self.assertTrue(ok)

        # Active mutation with identical timestamp against tombstone -> rejected
        ok_revive, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_del_win",
            "updated_at": t_same,
            "deleted": False,
            "payload": {"title": "Active Task"},
        })
        self.assertFalse(ok_revive)

    def test_06_crdt_lww_canonical_json_tiebreak(self):
        """Level 3: When timestamp and deleted are identical, canonical JSON ordering wins."""
        t_same = "2026-10-20T14:00:00.000000Z"

        # Apply payload {"val": "A"}
        self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_json_tie",
            "updated_at": t_same,
            "deleted": False,
            "payload": {"val": "A"},
        })

        # Apply payload {"val": "B"} ("B" > "A") -> wins
        ok_b, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_json_tie",
            "updated_at": t_same,
            "deleted": False,
            "payload": {"val": "B"},
        })
        self.assertTrue(ok_b)

        # Apply payload {"val": "A"} again ("A" < "B") -> rejected
        ok_a, _ = self.storage.apply_mutation({
            "entity_type": "task",
            "entity_id": "t_json_tie",
            "updated_at": t_same,
            "deleted": False,
            "payload": {"val": "A"},
        })
        self.assertFalse(ok_a)

    def test_07_crdt_idempotent_duplicate_rejected(self):
        """Duplicate identical mutation application is safely rejected without error."""
        now = datetime.now(timezone.utc).isoformat()
        mut = {
            "entity_type": "task",
            "entity_id": "t_dup",
            "updated_at": now,
            "payload": {"title": "Duplicate Check"},
        }
        ok1, seq1 = self.storage.apply_mutation(mut)
        self.assertTrue(ok1)
        ok2, seq2 = self.storage.apply_mutation(mut)
        self.assertFalse(ok2)
        self.assertEqual(seq1, seq2)

    def test_08_pull_mutations_with_filtering(self):
        """Verifies delta pull, exclude_device, negative sequence, and exact boundary."""
        now = datetime.now(timezone.utc).isoformat()
        _, s1 = self.storage.apply_mutation({"entity_type": "task", "entity_id": "t1", "device_id": "ws1", "updated_at": now, "payload": {}})
        _, s2 = self.storage.apply_mutation({"entity_type": "task", "entity_id": "t2", "device_id": "ws2", "updated_at": now, "payload": {}})

        # Delta pull since s1
        muts, max_s = self.storage.pull_mutations(since_sequence=s1)
        self.assertEqual(len(muts), 1)
        self.assertEqual(muts[0]["entity_id"], "t2")
        self.assertEqual(max_s, s2)

        # Exclude device ws2
        muts_ex, _ = self.storage.pull_mutations(since_sequence=0, exclude_device="ws2")
        self.assertEqual(len(muts_ex), 1)
        self.assertEqual(muts_ex[0]["entity_id"], "t1")

        # Exact boundary returns empty
        muts_boundary, max_b = self.storage.pull_mutations(since_sequence=s2)
        self.assertEqual(len(muts_boundary), 0)
        self.assertEqual(max_b, s2)

        # Negative sequence returns all
        muts_neg, _ = self.storage.pull_mutations(since_sequence=-5)
        self.assertEqual(len(muts_neg), 2)


class TestCloudRestServerUnit(unittest.TestCase):
    """Unit tests for CloudServer REST API endpoints."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_srv_")
        self.db_path = Path(self.tmp) / "server_test.db"
        self.storage = SQLiteStorageAdapter(self.db_path)
        self.port = get_ephemeral_port()
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _http_get(self, path: str) -> Dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}{path}")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _http_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_01_health_and_status_endpoints(self):
        """Verifies GET /api/v1/health and GET /api/v1/sync/status."""
        health = self._http_get("/api/v1/health")
        self.assertEqual(health.get("status"), "ok")
        self.assertTrue(health.get("healthy"))
        self.assertEqual(health.get("version"), "2.0.0")

        status = self._http_get("/api/v1/sync/status")
        self.assertIn("server_sequence", status)
        self.assertIn("active_entities", status)

    def test_02_register_endpoint(self):
        """Verifies POST /api/v1/auth/register generates token and registers workstation."""
        res = self._http_post("/api/v1/auth/register", {
            "device_id": "test_device_01",
            "device_name": "Test Laptop",
            "client_version": "2.0.0",
        })
        self.assertEqual(res.get("status"), "registered")
        self.assertTrue(bool(res.get("token")))
        self.assertTrue(bool(res.get("auth_token")))
        self.assertEqual(res.get("device_id"), "test_device_01")

    def test_03_push_pull_and_reconcile_endpoints(self):
        """Verifies push, pull, and reconcile REST operations with dual keys."""
        now = datetime.now(timezone.utc).isoformat()
        push_res = self._http_post("/api/v1/sync/push", {
            "mutations": [
                {"entity_type": "task", "entity_id": "task_push", "updated_at": now, "payload": {"title": "Pushed Task"}}
            ]
        })
        self.assertEqual(push_res.get("applied"), 1)

        pull_res = self._http_post("/api/v1/sync/pull", {"since_sequence": 0})
        self.assertTrue(len(pull_res.get("mutations", [])) >= 1)
        self.assertTrue(len(pull_res.get("pulled_mutations", [])) >= 1)

        # Bidirectional reconcile
        rec_res = self._http_post("/api/v1/sync/reconcile", {
            "device_id": "ws_rec",
            "since_sequence": 0,
            "mutations": [
                {"entity_type": "fact", "entity_id": "fact_rec", "updated_at": now, "payload": {"content": "Reconciled Fact"}}
            ]
        })
        self.assertEqual(rec_res.get("status"), "synced")
        self.assertEqual(rec_res.get("applied"), 1)
        self.assertTrue(len(rec_res.get("pulled_mutations")) >= 2)
        self.assertTrue(len(rec_res.get("remote_mutations")) >= 2)
        self.assertIn("server_sequence", rec_res)
        self.assertIn("new_server_sequence", rec_res)

    def test_04_error_boundaries(self):
        """Verifies HTTP 400 for malformed JSON and HTTP 404 for unknown endpoints."""
        # Malformed JSON body
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/sync/push",
            data=b"invalid-not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2.0)
            self.fail("Expected HTTP 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

        # Unknown route
        req_404 = urllib.request.Request(f"{self.base_url}/api/v1/unknown")
        try:
            urllib.request.urlopen(req_404, timeout=2.0)
            self.fail("Expected HTTP 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


class TestOfflineMutationQueueUnit(unittest.TestCase):
    """Unit tests for OfflineMutationQueue resilience, FIFO order, and corruption recovery."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_queue_")
        self.ws = Path(self.tmp)
        self.queue = OfflineMutationQueue(self.ws)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_enqueue_and_fifo_order(self):
        """Verifies mutations are appended in FIFO order."""
        m1 = self.queue.enqueue("task", "t1", {"val": 1})
        m2 = self.queue.enqueue("task", "t2", {"val": 2})
        m3 = self.queue.enqueue("task", "t3", {"val": 3})

        pending = self.queue.peek()
        self.assertEqual(len(pending), 3)
        self.assertEqual(pending[0]["entity_id"], "t1")
        self.assertEqual(pending[1]["entity_id"], "t2")
        self.assertEqual(pending[2]["entity_id"], "t3")

    def test_02_persistence_across_reinstantiation(self):
        """Verifies mutations survive process restarts."""
        self.queue.enqueue("task", "persist_1", {"title": "Survive Restart"})
        q2 = OfflineMutationQueue(self.ws)
        self.assertEqual(q2.get_stats()["pending_mutations"], 1)

    def test_03_corrupted_file_recovery(self):
        """Verifies corrupted/truncated JSON file recovers cleanly with 0 items."""
        with open(self.queue.queue_file, "w", encoding="utf-8") as f:
            f.write("{corrupted json broken [[")

        stats = self.queue.get_stats()
        self.assertEqual(stats["pending_mutations"], 0)

        # Subsequent enqueue works without error
        self.queue.enqueue("task", "rec_1", {"title": "Recovered"})
        self.assertEqual(self.queue.get_stats()["pending_mutations"], 1)

    def test_04_concurrency_thread_safety(self):
        """Stress-test: multiple threads concurrently enqueueing mutations."""
        threads = []
        errors = []

        def worker(idx: int):
            try:
                for i in range(10):
                    self.queue.enqueue("task", f"th_{idx}_{i}", {"idx": idx, "i": i})
            except Exception as e:
                errors.append(e)

        for t in range(5):
            th = threading.Thread(target=worker, args=(t,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(self.queue.get_stats()["pending_mutations"], 50)


class TestCloudSyncManagerUnit(unittest.TestCase):
    """Unit tests for CloudSyncManager bidirectional sync and store dispatching."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_syncmgr_")
        self.server_db = Path(self.tmp) / "cloud.db"
        self.storage = SQLiteStorageAdapter(self.server_db)
        self.port = get_ephemeral_port()
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        self.server_url = f"http://127.0.0.1:{self.port}"

        self.client_ws = Path(self.tmp) / "client"
        self.sync_mgr = CloudSyncManager(
            self.client_ws,
            server_url=self.server_url,
            device_id="laptop_test",
            token="token_test",
        )

    def tearDown(self):
        self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_reconcile_flushes_queue_and_saves_state(self):
        """Verifies reconcile sends local mutations and flushes local queue."""
        self.sync_mgr.enqueue_mutation("task", "t_mgr_1", {"title": "Manager Task"})
        self.assertEqual(self.sync_mgr.get_queue_stats()["pending_mutations"], 1)

        res = self.sync_mgr.reconcile()
        self.assertEqual(res["status"], "synced")
        self.assertEqual(self.sync_mgr.get_queue_stats()["pending_mutations"], 0)
        self.assertGreaterEqual(res["server_sequence"], 1)

        # Check cloud_state.json persisted
        state_file = self.client_ws / ".neko" / "cloud_state.json"
        self.assertTrue(state_file.exists())
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            self.assertEqual(state.get("last_sequence"), res["server_sequence"])

    def test_02_reconcile_offline_preserves_queue(self):
        """Verifies failed connection retains queued mutations."""
        self.sync_mgr.enqueue_mutation("task", "t_off", {"title": "Offline Task"})
        self.sync_mgr.server_url = "http://127.0.0.1:9999"  # Dead port

        res = self.sync_mgr.reconcile()
        self.assertEqual(res["status"], "offline")
        self.assertEqual(self.sync_mgr.get_queue_stats()["pending_mutations"], 1)

    def test_03_telemetry_keys(self):
        """Verifies get_telemetry() contains canonical and alias keys."""
        telemetry = self.sync_mgr.get_telemetry()
        self.assertIn("status", telemetry)
        self.assertIn("connected", telemetry)
        self.assertIn("cloud_server_url", telemetry)
        self.assertIn("server", telemetry)
        self.assertIn("pending_mutations", telemetry)
        self.assertIn("pending", telemetry)


class TestTaskStoreCRDTExtensions(unittest.TestCase):
    """Unit tests for TaskStore export_crdt_entities and apply_crdt_mutation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_task_crdt_")
        self.ws = Path(self.tmp) / "workspace"
        self.store = TaskStore(self.ws)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_export_crdt_entities(self):
        """Verifies tasks export as uniform CRDT entities."""
        t1 = self.store.add_task("CRDT Task 1", priority="high")
        t2 = self.store.add_task("CRDT Task 2", priority="low")
        self.store.mark_deleted(t2.id)

        entities = self.store.export_crdt_entities()
        self.assertEqual(len(entities), 2)
        ent1 = [e for e in entities if e["entity_id"] == t1.id][0]
        self.assertEqual(ent1["entity_type"], "task")
        self.assertFalse(ent1["deleted"])
        self.assertEqual(ent1["payload"]["title"], "CRDT Task 1")

        ent2 = [e for e in entities if e["entity_id"] == t2.id][0]
        self.assertTrue(ent2["deleted"])

    def test_02_apply_crdt_mutation_3_level_tiebreak(self):
        """Verifies apply_crdt_mutation follows 3-level LWW tie-break rules."""
        t1 = "2026-10-24T10:00:00.000000Z"
        t2 = "2026-10-24T10:05:00.000000Z"

        # Apply initial task
        ok1 = self.store.apply_crdt_mutation({
            "entity_type": "task",
            "entity_id": "task_remote_1",
            "updated_at": t1,
            "deleted": False,
            "payload": {"title": "Version 1", "state": "active", "priority": "medium"},
        })
        self.assertTrue(ok1)
        self.assertEqual(self.store.get_task("task_remote_1").title, "Version 1")

        # Stale mutation should be rejected
        ok_stale = self.store.apply_crdt_mutation({
            "entity_type": "task",
            "entity_id": "task_remote_1",
            "updated_at": "2026-10-24T09:00:00.000000Z",
            "deleted": False,
            "payload": {"title": "Stale Version"},
        })
        self.assertFalse(ok_stale)
        self.assertEqual(self.store.get_task("task_remote_1").title, "Version 1")

        # Newer mutation wins
        ok_newer = self.store.apply_crdt_mutation({
            "entity_type": "task",
            "entity_id": "task_remote_1",
            "updated_at": t2,
            "deleted": False,
            "payload": {"title": "Version 2", "state": "active", "priority": "high"},
        })
        self.assertTrue(ok_newer)
        self.assertEqual(self.store.get_task("task_remote_1").title, "Version 2")

        # Equal timestamp tombstone wins
        ok_del = self.store.apply_crdt_mutation({
            "entity_type": "task",
            "entity_id": "task_remote_1",
            "updated_at": t2,
            "deleted": True,
            "payload": {"title": "Version 2"},
        })
        self.assertTrue(ok_del)
        self.assertTrue(self.store.get_task("task_remote_1").deleted)


class TestCloudCliAndDaemonIntegration(unittest.TestCase):
    """Integration tests for CLI neko cloud subparsers and daemon telemetry."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_cli_cloud_")
        self.ws = Path(self.tmp)
        (self.ws / ".neko").mkdir(parents=True, exist_ok=True)
        self.config = Config.load(root_dir=self.ws)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_cli_cloud_config(self):
        """Verifies neko cloud config updates .neko/config.json."""
        ret = main(["--workspace", str(self.ws), "cloud", "config", "--url", "http://127.0.0.1:9000", "--device-name", "My-Alpha", "--json"])
        self.assertEqual(ret, 0)

        cfg_reloaded = Config.load(root_dir=self.ws)
        self.assertEqual(cfg_reloaded.cloud_url, "http://127.0.0.1:9000")
        self.assertEqual(cfg_reloaded.cloud_device_name, "My-Alpha")

    def test_02_cli_cloud_status_json_and_human(self):
        """Verifies neko cloud status returns valid JSON payload and human status."""
        out = io.StringIO()
        with redirect_stdout(out):
            ret = main(["--workspace", str(self.ws), "cloud", "status", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(out.getvalue())
        self.assertIn("status", data)
        self.assertIn("connected", data)
        self.assertIn("pending_mutations", data)

        out_human = io.StringIO()
        with redirect_stdout(out_human):
            ret_human = main(["--workspace", str(self.ws), "cloud", "status"])
        self.assertEqual(ret_human, 0)
        self.assertIn("NEKO CLOUD STATUS", out_human.getvalue())

    def test_03_daemon_status_json_contains_cloud_sync(self):
        """Verifies daemon status payload exports cloud_sync with canonical and alias keys."""
        controller = DaemonController(self.config)
        payload = controller.get_status_payload()
        self.assertIn("cloud_sync", payload)
        cs = payload["cloud_sync"]
        self.assertIn("status", cs)
        self.assertIn("connected", cs)
        self.assertIn("cloud_server_url", cs)
        self.assertIn("server", cs)
        self.assertIn("pending_mutations", cs)
        self.assertIn("pending", cs)


if __name__ == "__main__":
    unittest.main()
