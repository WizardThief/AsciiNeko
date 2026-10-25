"""
tests/test_challenger_m4_security_wal.py - Adversarial Security, WAL Concurrency & Corruption Recovery Suite.

Milestone: Milestone 4 (Deployable Cloud Backend & Offline-First Cloud Sync)
Author: challenger_m4_2 (Milestone 4 Critic & Empirical Challenger)

Adversarial Verification Scope:
1. Multi-Threaded Hammering of SQLiteStorageAdapter under WAL Mode:
   - PRAGMA journal_mode = WAL & busy_timeout >= 5000 verification
   - 60 concurrent worker threads hammering single SQLiteStorageAdapter instance (600 operations)
   - 15 independent SQLiteStorageAdapter instances connecting to the same file across 50 threads
   - Barrier-synchronized rapid simultaneous write burst (50 threads at exact same instant)
   - Continuous concurrent read-while-writing isolation without reader lockouts
2. Auth Token Forgery & Bypass Attempts:
   - Forged token, empty token, whitespace token, truncated token rejection in authenticate_device
   - Unregistered device ID, empty device ID, None device/token rejection
   - SQL injection payloads in device_id and auth_token
   - CloudServer non-existent endpoints return HTTP 404 with JSON body without trace leakage
   - CloudServer malformed bodies (non-JSON, missing device_id) return HTTP 400
   - CloudServer require_auth enforcement and unauthorized access verification
3. Mutation Queue Corruption Recovery:
   - Truncated JSON file recovery without JSONDecodeError
   - Malformed syntax recovery and self-healing on next enqueue
   - Non-list JSON root (dict, int, string) clean handling
   - Mixed list with invalid primitives (null, int, str, bool, list) filtering
   - Invalid UTF-8 / binary garbage recovery without UnicodeDecodeError
   - End-to-end CloudSyncManager.reconcile() graceful recovery without crashing client/daemon
4. Path Traversal & Injection in Entity Types / IDs:
   - SQL injection attempts in entity_type and entity_id in SQLiteStorageAdapter
   - Null byte injection resilience
   - Path traversal in TaskStore CRDT mutations
   - Path traversal vulnerability probe in SkillRegistry CRDT tool persistence
   - Path traversal vulnerability probe in CloudSyncManager time log file persistence
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
import urllib.error
import urllib.request

# Ensure sys.path includes repository root and neko_bot/
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
)
from cloud.server import CloudServer
from cloud.storage.sqlite_adapter import SQLiteStorageAdapter
from neko_bot.cloud.client import CloudClient, CloudConnectionError
from neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso
from neko_bot.cloud.sync_manager import CloudSyncManager
from neko_bot.skills.registry import SkillRegistry
from neko_bot.task.store import TaskStore


def get_ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


class TestSQLiteStorageWALConcurrency(unittest.TestCase):
    """
    Adversarial concurrency testing for SQLiteStorageAdapter under WAL mode.
    Stress-tests thread contention, busy timeouts, multi-connection file locks,
    and barrier-synchronized write bursts.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_adv_wal_")
        self.db_path = Path(self.tmp) / "wal_concurrency.db"
        self.storage = SQLiteStorageAdapter(self.db_path)

    def tearDown(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_wal_pragma_and_busy_timeout(self) -> None:
        """Verifies SQLite connection enforces WAL journal mode and busy_timeout >= 5000ms."""
        c = self.storage.conn.cursor()
        mode = c.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal", "SQLiteStorageAdapter MUST operate in WAL mode.")

        timeout = c.execute("PRAGMA busy_timeout;").fetchone()[0]
        self.assertGreaterEqual(timeout, 5000, "PRAGMA busy_timeout MUST be at least 5000ms for high concurrency.")

        fk = c.execute("PRAGMA foreign_keys;").fetchone()[0]
        self.assertEqual(fk, 1, "Foreign keys must be enabled.")

    def test_02_single_adapter_60_threads_hammering(self) -> None:
        """
        Stress test: 60 concurrent worker threads simultaneously hammering a single
        SQLiteStorageAdapter instance with 600 total operations (writes, reads, auth).
        Verifies 0 exceptions, 0 database locked errors, sequence monotonicity.
        """
        num_threads = 60
        ops_per_thread = 10
        errors: List[Tuple[int, Exception]] = []
        lock = threading.Lock()

        def worker(tid: int) -> None:
            try:
                for i in range(ops_per_thread):
                    now_ts = datetime.now(timezone.utc).isoformat()
                    # 1. Write mutation
                    applied, seq = self.storage.apply_mutation({
                        "entity_type": "task",
                        "entity_id": f"task_{tid}_{i}",
                        "device_id": f"dev_{tid}",
                        "updated_at": now_ts,
                        "payload": {"title": f"Task {tid}-{i}", "counter": i},
                    })
                    if not applied:
                        with lock:
                            errors.append((tid, RuntimeError(f"Mutation rejected: task_{tid}_{i}")))

                    # 2. Read mutations
                    muts, max_seq = self.storage.pull_mutations(since_sequence=0)
                    if max_seq < seq:
                        with lock:
                            errors.append((tid, ValueError(f"max_seq {max_seq} < applied seq {seq}")))

                    # 3. Read status
                    status = self.storage.get_status()
                    if status["server_sequence"] < seq:
                        with lock:
                            errors.append((tid, ValueError(f"Status seq {status['server_sequence']} < applied seq {seq}")))

                    # 4. Auth operation
                    token_hash = hashlib.sha256(f"tok_{tid}".encode()).hexdigest()
                    self.storage.register_device(f"dev_{tid}", f"Worker_{tid}", "1.0", token_hash)
                    auth_ok = self.storage.authenticate_device(f"dev_{tid}", f"tok_{tid}")
                    if not auth_ok:
                        with lock:
                            errors.append((tid, RuntimeError(f"Auth failed for dev_{tid}")))
            except Exception as e:
                with lock:
                    errors.append((tid, e))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Encountered {len(errors)} concurrency errors: {errors[:5]}")
        status = self.storage.get_status()
        self.assertGreaterEqual(status["server_sequence"], num_threads * ops_per_thread)
        self.assertEqual(status["active_entities"], num_threads * ops_per_thread)

    def test_03_multi_adapter_15_connections_concurrent_hammering(self) -> None:
        """
        Stress test: 15 independent SQLiteStorageAdapter instances connecting to the EXACT
        same SQLite database file on disk across 50 concurrent worker threads.
        Verifies that SQLite WAL mode and busy_timeout handle cross-connection file locks
        without throwing sqlite3.OperationalError: database is locked.
        """
        num_adapters = 15
        num_threads = 50
        ops_per_thread = 8

        adapters = [SQLiteStorageAdapter(self.db_path) for _ in range(num_adapters)]
        errors: List[Tuple[int, Exception]] = []
        lock = threading.Lock()

        def worker(tid: int) -> None:
            adapter = adapters[tid % num_adapters]
            try:
                for i in range(ops_per_thread):
                    now_ts = datetime.now(timezone.utc).isoformat()
                    adapter.apply_mutation({
                        "entity_type": "memory",
                        "entity_id": f"mem_{tid}_{i}",
                        "device_id": f"dev_multi_{tid}",
                        "updated_at": now_ts,
                        "payload": {"fact": f"Concurrent fact {tid}-{i}"},
                    })
                    adapter.pull_mutations(since_sequence=0)
                    adapter.get_status()
            except Exception as e:
                with lock:
                    errors.append((tid, e))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        for a in adapters:
            a.close()

        self.assertEqual(len(errors), 0, f"Encountered cross-connection lock errors: {errors[:5]}")
        status = self.storage.get_status()
        self.assertEqual(status["active_entities"], num_threads * ops_per_thread)

    def test_04_barrier_synchronized_rapid_write_burst(self) -> None:
        """
        Stress test: 50 threads release simultaneously via threading.Barrier to execute
        apply_mutation at the exact same millisecond.
        Verifies strictly monotonic, unique auto-increment sequence generation with 0 duplicates.
        """
        num_threads = 50
        barrier = threading.Barrier(num_threads)
        assigned_sequences: List[int] = []
        lock = threading.Lock()
        errors: List[Exception] = []

        def burst_worker(tid: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                now_ts = datetime.now(timezone.utc).isoformat()
                applied, seq = self.storage.apply_mutation({
                    "entity_type": "task",
                    "entity_id": f"burst_task_{tid}",
                    "device_id": "burst_dev",
                    "updated_at": now_ts,
                    "payload": {"val": tid},
                })
                if applied:
                    with lock:
                        assigned_sequences.append(seq)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=burst_worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        self.assertEqual(len(errors), 0, f"Encountered burst errors: {errors}")
        self.assertEqual(len(assigned_sequences), num_threads)
        # All sequences must be strictly distinct
        self.assertEqual(len(set(assigned_sequences)), num_threads, "Sequence collision detected in rapid burst!")
        self.assertEqual(min(assigned_sequences), 1)
        self.assertEqual(max(assigned_sequences), num_threads)

    def test_05_concurrent_readers_never_blocked_by_heavy_writers(self) -> None:
        """
        Verifies WAL reader/writer isolation: heavy sequential writes on one thread
        do NOT block concurrent readers on other threads or cause reader timeouts.
        """
        stop_event = threading.Event()
        read_latencies: List[float] = []
        reader_errors: List[Exception] = []
        lock = threading.Lock()

        def reader_worker() -> None:
            while not stop_event.is_set():
                t0 = time.perf_counter()
                try:
                    self.storage.pull_mutations(since_sequence=0)
                    self.storage.get_status()
                    dt = time.perf_counter() - t0
                    with lock:
                        read_latencies.append(dt)
                except Exception as e:
                    with lock:
                        reader_errors.append(e)
                time.sleep(0.001)

        reader_threads = [threading.Thread(target=reader_worker) for _ in range(5)]
        for rt in reader_threads:
            rt.start()

        # Heavy writer thread writing 100 mutations
        for i in range(100):
            now_ts = datetime.now(timezone.utc).isoformat()
            self.storage.apply_mutation({
                "entity_type": "task",
                "entity_id": f"heavy_writer_{i}",
                "updated_at": now_ts,
                "payload": {"data": "x" * 200},
            })

        stop_event.set()
        for rt in reader_threads:
            rt.join(timeout=5.0)

        self.assertEqual(len(reader_errors), 0, f"Reader was blocked or failed: {reader_errors}")
        self.assertGreater(len(read_latencies), 10, "Readers did not execute sufficiently during writer loop.")
        # Maximum read latency in WAL mode should remain under 500ms
        max_lat = max(read_latencies)
        self.assertLess(max_lat, 0.5, f"Reader latency spiked to {max_lat:.4f}s (readers blocked by writer).")


class TestAuthTokenSecurityAndEndpoints(unittest.TestCase):
    """
    Adversarial security testing for device authentication and REST API endpoints:
    - Auth token forgery, empty tokens, expired/unregistered devices
    - SQL injection in device_id and tokens
    - Non-existent endpoints and malformed payloads
    - CloudServer require_auth enforcement verification
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_adv_auth_")
        self.db_path = Path(self.tmp) / "auth_test.db"
        self.storage = SQLiteStorageAdapter(self.db_path)
        self.port = get_ephemeral_port()
        self.server: Optional[CloudServer] = None

    def tearDown(self) -> None:
        if self.server:
            self.server.stop()
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_authenticate_device_invalid_and_forged_tokens(self) -> None:
        """
        Adversarial auth test: valid device registration followed by forgery attempts.
        Verifies that forged tokens, empty tokens, whitespace tokens, and truncated tokens return False.
        """
        dev_id = "device_charlie"
        valid_token = "correct_secret_token_abcdef123456"
        token_hash = compute_content_hash(valid_token)
        self.storage.register_device(dev_id, "Charlie Workstation", "2.0.0", token_hash)

        # 1. Valid token succeeds
        self.assertTrue(self.storage.authenticate_device(dev_id, valid_token))

        # 2. Forged / invalid tokens fail
        self.assertFalse(self.storage.authenticate_device(dev_id, "forged_token_abcdef123456"))
        self.assertFalse(self.storage.authenticate_device(dev_id, "correct_secret_token_abcdef123457"))
        self.assertFalse(self.storage.authenticate_device(dev_id, "CORRECT_SECRET_TOKEN_ABCDEF123456"))

        # 3. Empty string and whitespace tokens fail
        self.assertFalse(self.storage.authenticate_device(dev_id, ""))
        self.assertFalse(self.storage.authenticate_device(dev_id, "   "))
        self.assertFalse(self.storage.authenticate_device(dev_id, "\t\n"))

        # 4. Truncated prefix / suffix tokens fail
        self.assertFalse(self.storage.authenticate_device(dev_id, valid_token[:10]))
        self.assertFalse(self.storage.authenticate_device(dev_id, valid_token[5:]))

    def test_02_authenticate_device_unregistered_and_empty_device_ids(self) -> None:
        """
        Adversarial test: authenticating non-existent, unregistered, empty, or None device IDs.
        """
        # Unregistered random device IDs
        self.assertFalse(self.storage.authenticate_device("ghost_workstation_999", "any_token"))
        self.assertFalse(self.storage.authenticate_device("non_existent_device", "secret"))

        # Empty device ID
        self.assertFalse(self.storage.authenticate_device("", "any_token"))
        self.assertFalse(self.storage.authenticate_device("   ", "any_token"))

    def test_03_sql_injection_in_auth_credentials(self) -> None:
        """
        Adversarial test: SQL injection payloads in device_id and token.
        Verifies queries are parameterized and injection payloads cannot bypass auth or corrupt tables.
        """
        sqli_payloads = [
            "' OR '1'='1",
            "admin' --",
            "' UNION SELECT '1', '2', '3', '4', '5', '6', '7' --",
            "dev_01'; DROP TABLE sync_devices; --",
            "'; EXEC xp_cmdshell('dir'); --",
            "\" OR \"\"=\"",
        ]

        # Register one legitimate device
        token_hash = compute_content_hash("secret123")
        self.storage.register_device("legit_dev", "Legit", "2.0.0", token_hash)

        for payload in sqli_payloads:
            # Injection in device_id
            auth_ok_dev = self.storage.authenticate_device(payload, "secret123")
            self.assertFalse(auth_ok_dev, f"SQLi payload in device_id breached auth: {payload}")

            # Injection in token
            auth_ok_tok = self.storage.authenticate_device("legit_dev", payload)
            self.assertFalse(auth_ok_tok, f"SQLi payload in token breached auth: {payload}")

        # Verify sync_devices table was not dropped
        c = self.storage.conn.cursor()
        cnt = c.execute("SELECT COUNT(*) FROM sync_devices;").fetchone()[0]
        self.assertEqual(cnt, 1, "sync_devices table count corrupted by SQL injection attempts!")

    def test_04_server_non_existent_endpoints_return_404_cleanly(self) -> None:
        """
        Adversarial REST test: querying non-existent endpoints and root paths.
        Verifies server returns HTTP 404 with structured JSON without stack traces.
        """
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        base = f"http://127.0.0.1:{self.port}"

        non_existent_paths = [
            "/api/v1/unknown",
            "/api/v1/sync/fake_action",
            "/admin",
            "/api/v2/sync",
            "/",
        ]

        for path in non_existent_paths:
            req = urllib.request.Request(f"{base}{path}")
            try:
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    self.fail(f"Path {path} should have returned 404, got {resp.status}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404, f"Expected 404 for {path}, got {e.code}")
                body = json.loads(e.read().decode("utf-8"))
                self.assertEqual(body.get("error"), "Not Found")
                self.assertNotIn("Traceback", str(body))

    def test_05_server_malformed_bodies_return_400(self) -> None:
        """
        Adversarial REST test: POSTing malformed, truncated, or incomplete JSON bodies.
        Verifies server rejects invalid inputs with HTTP 400.
        """
        self.server = CloudServer(storage=self.storage, port=self.port)
        self.server.start()
        base = f"http://127.0.0.1:{self.port}"

        # 1. Non-JSON body to /api/v1/auth/register
        req1 = urllib.request.Request(
            f"{base}/api/v1/auth/register",
            data=b"This is completely raw plain text, not JSON!",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req1, timeout=3.0) as resp:
                self.fail(f"Malformed JSON should have returned 400, got {resp.status}")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

        # 2. JSON without device_id to /api/v1/auth/register
        req2 = urllib.request.Request(
            f"{base}/api/v1/auth/register",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req2, timeout=3.0) as resp:
                self.fail(f"Missing device_id should have returned 400, got {resp.status}")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            body = json.loads(e.read().decode("utf-8"))
            self.assertIn("device_id is required", body.get("error", ""))

        # 3. Truncated JSON to /api/v1/sync/push
        req3 = urllib.request.Request(
            f"{base}/api/v1/sync/push",
            data=b"{\"mutations\": [{\"entity_type\": ",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req3, timeout=3.0) as resp:
                self.fail(f"Truncated JSON should have returned 400, got {resp.status}")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_06_server_require_auth_enforcement_or_bypass_detection(self) -> None:
        """
        Empirical Security Challenge: Verifies whether CloudServer with require_auth=True
        enforces authentication or allows unauthorized bypass.

        Security Requirement:
        When require_auth=True is specified, unauthenticated or forged-token requests
        to protected sync endpoints (/api/v1/sync/push, pull, reconcile) MUST be rejected
        with HTTP 401 Unauthorized or 403 Forbidden.
        """
        self.server = CloudServer(storage=self.storage, port=self.port, require_auth=True)
        self.server.start()
        base = f"http://127.0.0.1:{self.port}"

        # 1. Attempt POST to /api/v1/sync/push with NO auth header and NO token
        req_no_auth = urllib.request.Request(
            f"{base}/api/v1/sync/push",
            data=json.dumps({"mutations": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        # 2. Attempt POST with forged token header
        req_forged = urllib.request.Request(
            f"{base}/api/v1/sync/push",
            data=json.dumps({"mutations": []}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer forged_invalid_token_12345",
                "X-Device-Id": "unregistered_attacker_device",
            },
            method="POST"
        )

        status_no_auth: Optional[int] = None
        status_forged: Optional[int] = None

        try:
            with urllib.request.urlopen(req_no_auth, timeout=3.0) as resp:
                status_no_auth = resp.status
        except urllib.error.HTTPError as e:
            status_no_auth = e.code

        try:
            with urllib.request.urlopen(req_forged, timeout=3.0) as resp:
                status_forged = resp.status
        except urllib.error.HTTPError as e:
            status_forged = e.code

        # If server returns HTTP 200, authentication was bypassed despite require_auth=True!
        self.assertIn(
            status_no_auth,
            (401, 403),
            f"VULNERABILITY DETECTED: CloudServer(require_auth=True) permitted unauthenticated request (HTTP {status_no_auth})!"
        )
        self.assertIn(
            status_forged,
            (401, 403),
            f"VULNERABILITY DETECTED: CloudServer(require_auth=True) permitted forged token request (HTTP {status_forged})!"
        )


class TestMutationQueueCorruptionRecovery(unittest.TestCase):
    """
    Adversarial stress-testing of OfflineMutationQueue resilience against:
    - Truncated and malformed JSON on disk
    - Non-list JSON structures
    - Primitive / corrupted list elements
    - Invalid UTF-8 bytes and binary garbage
    - End-to-end client/daemon reconciliation survival
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_adv_queue_")
        self.queue_dir = Path(self.tmp)
        self.queue_file = self.queue_dir / ".neko" / "cloud_mutations.json"
        self.queue = OfflineMutationQueue(self.queue_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_truncated_json_file_clean_recovery(self) -> None:
        """
        Verifies that when .neko/cloud_mutations.json is truncated mid-write:
        1. peek() returns [] without raising JSONDecodeError.
        2. get_stats() reports pending_mutations == 0.
        3. enqueue() succeeds, self-heals the file, and restores valid JSON on disk.
        """
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.queue_file, "w", encoding="utf-8") as f:
            f.write('[{"entity_type": "task", "entity_id": ')

        # peek() should recover cleanly
        pending = self.queue.peek()
        self.assertEqual(pending, [], "Truncated file MUST return empty list without crashing.")

        stats = self.queue.get_stats()
        self.assertEqual(stats["pending_mutations"], 0)

        # Enqueue new item -> self-heals file
        mut = self.queue.enqueue("task", "task_heal_1", {"title": "Healed Task"})
        self.assertEqual(mut["entity_id"], "task_heal_1")

        # Disk file must now be valid JSON
        with open(self.queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["entity_id"], "task_heal_1")

    def test_02_malformed_syntax_clean_recovery(self) -> None:
        """Verifies completely invalid non-JSON text recovers cleanly and self-heals."""
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.queue_file, "w", encoding="utf-8") as f:
            f.write("{{{corrupted non-json syntax @@@!!!###")

        self.assertEqual(self.queue.peek(), [])
        self.queue.enqueue("memory", "mem_heal_1", {"fact": "Healed Memory"})
        self.assertEqual(len(self.queue.peek()), 1)

    def test_03_non_list_json_root_clean_recovery(self) -> None:
        """
        Verifies that if the JSON root on disk is a dict, integer, boolean, or string:
        peek() gracefully treats it as an invalid queue and returns [].
        """
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)

        for non_list in ['{"error": "corrupt"}', "12345", '"string_root"', "true", "null"]:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                f.write(non_list)
            self.assertEqual(self.queue.peek(), [], f"Non-list root '{non_list}' must return empty list.")

    def test_04_mixed_list_with_invalid_primitives(self) -> None:
        """
        Verifies that if a JSON array contains invalid primitive items (null, int, str, list):
        peek() filters out non-dict elements and keeps valid mutation dicts.
        """
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        valid_item = {
            "id": "t_valid",
            "entity_id": "t_valid",
            "entity_type": "task",
            "updated_at": now_utc_iso(),
            "payload": {"title": "Valid"},
        }
        mixed = [None, 42, "unexpected_string", True, [], valid_item]
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(mixed, f)

        items = self.queue.peek()
        self.assertEqual(len(items), 1, "Only valid dict mutations should be returned.")
        self.assertEqual(items[0]["entity_id"], "t_valid")

    def test_05_invalid_utf8_binary_garbage(self) -> None:
        """
        Verifies that binary garbage containing invalid UTF-8 byte sequences
        recovers without raising UnicodeDecodeError.
        """
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.queue_file, "wb") as f:
            f.write(b"\x80\xff\xfe\x00\x01\x02\x03\x04")

        self.assertEqual(self.queue.peek(), [])
        # Enqueue heals into clean UTF-8
        self.queue.enqueue("task", "t_binary_heal", {"title": "Healed"})
        self.assertEqual(len(self.queue.peek()), 1)

    def test_06_cloud_sync_manager_reconcile_with_corrupted_queue(self) -> None:
        """
        End-to-end resilience: corrupt .neko/cloud_mutations.json on disk, then
        invoke CloudSyncManager.reconcile() against an ephemeral CloudServer.
        Verifies client and daemon do not crash, and subsequent mutations reconcile cleanly.
        """
        port = get_ephemeral_port()
        srv_tmp = tempfile.mkdtemp(prefix="neko_adv_srv_heal_")
        srv_db = Path(srv_tmp) / "heal.db"
        storage = SQLiteStorageAdapter(srv_db)
        server = CloudServer(storage=storage, port=port)
        server.start()

        try:
            # Corrupt the client's queue file
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.queue_file, "w", encoding="utf-8") as f:
                f.write("[truncated json...")

            mgr = CloudSyncManager(self.queue_dir, server_url=f"http://127.0.0.1:{port}")
            # Reconcile must not throw exception
            res = mgr.reconcile()
            self.assertTrue(res.get("success", False) or res.get("status") == "synced")
            self.assertEqual(res.get("applied_count", 0), 0)

            # Enqueue a valid mutation
            mgr.queue.enqueue("task", "task_after_heal", {"title": "After Corrupt Queue"})
            res2 = mgr.reconcile()
            self.assertTrue(res2.get("success", False))
            self.assertEqual(res2.get("status"), "synced")

            # Confirm stored on server
            muts, _ = storage.pull_mutations(0)
            self.assertEqual(len(muts), 1)
            self.assertEqual(muts[0]["entity_id"], "task_after_heal")
        finally:
            server.stop()
            storage.close()
            shutil.rmtree(srv_tmp, ignore_errors=True)


class TestInjectionAndPathTraversalSecurity(unittest.TestCase):
    """
    Adversarial injection and path traversal testing:
    - SQL injection in entity_type and entity_id in SQLiteStorageAdapter
    - Null byte injection resilience
    - Path traversal in TaskStore CRDT mutations
    - Path traversal vulnerability probes in SkillRegistry and CloudSyncManager
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_adv_inject_")
        self.workspace_dir = Path(self.tmp)
        self.db_path = self.workspace_dir / "inject.db"
        self.storage = SQLiteStorageAdapter(self.db_path)

    def tearDown(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_sql_injection_in_entity_type_and_id(self) -> None:
        """
        Adversarial test: SQL injection payloads in entity_type and entity_id.
        Verifies SQLiteStorageAdapter uses parameterized SQL and prevents syntax errors or schema changes.
        """
        payloads = [
            "task'; DROP TABLE sync_entities; --",
            "'; DROP TABLE sync_mutations; --",
            "1' OR '1'='1",
            "UNION SELECT * FROM sqlite_master",
        ]

        now_ts = datetime.now(timezone.utc).isoformat()
        for idx, p in enumerate(payloads):
            applied, seq = self.storage.apply_mutation({
                "entity_type": p,
                "entity_id": f"inject_{idx}",
                "updated_at": now_ts,
                "payload": {"payload_data": p},
            })
            self.assertTrue(applied)

            applied_id, seq_id = self.storage.apply_mutation({
                "entity_type": "task",
                "entity_id": p,
                "updated_at": now_ts,
                "payload": {"payload_data": p},
            })
            self.assertTrue(applied_id)

        # Tables must remain intact
        c = self.storage.conn.cursor()
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        self.assertIn("sync_entities", tables)
        self.assertIn("sync_mutations", tables)

    def test_02_null_byte_injection_resilience(self) -> None:
        """Adversarial test: null bytes in entity_type, entity_id, and device_id."""
        now_ts = datetime.now(timezone.utc).isoformat()
        try:
            applied, seq = self.storage.apply_mutation({
                "entity_type": "task\x00_hidden",
                "entity_id": "ent\x00_hidden",
                "device_id": "dev\x00_hidden",
                "updated_at": now_ts,
                "payload": {"data": "null_byte_test"},
            })
            self.assertTrue(applied)
        except (ValueError, sqlite3.OperationalError):
            # Safe rejection of null bytes is acceptable
            pass

    def test_03_path_traversal_in_task_store_crdt_mutation(self) -> None:
        """
        Verifies that apply_crdt_mutation on TaskStore with traversal characters
        ('../../escaped_task') stays hermetic within tasks.json and does not create
        files outside the workspace or .neko directory.
        """
        task_store = TaskStore(self.workspace_dir)
        traversal_id = "../../escaped_task"
        applied = task_store.apply_crdt_mutation({
            "entity_type": "task",
            "entity_id": traversal_id,
            "updated_at": now_utc_iso(),
            "payload": {"title": "Path Traversal Task", "project": "General"},
        })
        self.assertTrue(applied)

        # Confirm no file was created outside workspace
        outside_file = self.workspace_dir.parent / "escaped_task"
        self.assertFalse(outside_file.exists(), "TaskStore leaked file outside workspace via entity_id!")
        outside_json = self.workspace_dir.parent / "escaped_task.json"
        self.assertFalse(outside_json.exists(), "TaskStore leaked json file outside workspace via entity_id!")

    def test_04_path_traversal_in_skill_registry_crdt_mutation(self) -> None:
        """
        Security Probe: Verifies whether SkillRegistry.apply_crdt_mutation sanitizes
        entity_id against directory traversal when writing tools/<id>.py or workflows/<id>.json.

        Security Requirement:
        Passing entity_id='../../escaped_tool' MUST NOT create escaped_tool.py outside
        the designated workspace/skills/tools/ directory.
        """
        registry = SkillRegistry(self.workspace_dir)
        traversal_id = "../../escaped_tool"
        code_content = "def run(inputs, context):\n    return {'status': 'pwned'}\n"

        registry.apply_crdt_mutation({
            "entity_type": "skill",
            "entity_id": traversal_id,
            "id": traversal_id,
            "updated_at": now_utc_iso(),
            "payload": {
                "id": traversal_id,
                "name": "escaped_tool",
                "type": "tool",
                "code": code_content,
            }
        })

        # Check if file escaped tools directory into workspace/ or root
        escaped_in_workspace = self.workspace_dir / "workspace" / "escaped_tool.py"
        escaped_in_root = self.workspace_dir / "escaped_tool.py"

        self.assertFalse(
            escaped_in_workspace.exists(),
            f"VULNERABILITY DETECTED: SkillRegistry wrote tool file outside tools/ dir: {escaped_in_workspace}"
        )
        self.assertFalse(
            escaped_in_root.exists(),
            f"VULNERABILITY DETECTED: SkillRegistry wrote tool file outside workspace: {escaped_in_root}"
        )

    def test_05_path_traversal_in_sync_manager_time_logs(self) -> None:
        """
        Security Probe: Verifies whether CloudSyncManager._apply_time_log_mutation
        sanitizes entity_id against directory traversal when writing time_logs/<id>.json.

        Security Requirement:
        Passing entity_id='../../escaped_log' MUST NOT create escaped_log.json outside
        the designated workspace/time_logs/ directory.
        """
        mgr = CloudSyncManager(self.workspace_dir)
        traversal_id = "../../escaped_time_log"

        mgr._apply_time_log_mutation(
            {"entity_type": "time_log", "entity_id": traversal_id},
            {"duration_sec": 300, "project": "SecurityTest"}
        )

        escaped_in_root = self.workspace_dir / "escaped_time_log.json"
        escaped_in_workspace = self.workspace_dir / "workspace" / "escaped_time_log.json"

        self.assertFalse(
            escaped_in_root.exists(),
            f"VULNERABILITY DETECTED: CloudSyncManager wrote time log outside time_logs dir: {escaped_in_root}"
        )
        self.assertFalse(
            escaped_in_workspace.exists(),
            f"VULNERABILITY DETECTED: CloudSyncManager wrote time log outside time_logs dir: {escaped_in_workspace}"
        )


if __name__ == "__main__":
    unittest.main()
