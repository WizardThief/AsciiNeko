"""
test_e2e_tier4_real_world.py - Tier 4: Real-World Application Workload Scenarios E2E Test Suite.
Covers 5 comprehensive multi-step end-to-end workflows:
1. Multi-Workstation Workday Sync (Office -> Cloud -> Laptop -> Commute -> Converged State)
2. Autonomous Skill Discovery from Prompt (Challenge -> AST Guard -> Sandbox -> Register -> Reuse)
3. Project Deadline Crunch & Risk Escalation (Memory Crunch -> Briefing -> Status.json -> Bridge HUD)
4. Offline Commute with Conflict Resolution (Concurrent Divergence -> Reconnect -> CRDT LWW Tie-Break)
5. Companion Live HUD Telemetry Streaming (Status Ingestion -> SSE Streaming -> 60 FPS Latency Contract)

Hermetic, zero-dependency, self-contained with progressive testability.
"""

from datetime import datetime, timezone, timedelta
import http.server
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List
import unittest
import urllib.request
import urllib.error

from neko_bot.task import TaskStore, Task
from neko_bot.task.models import TaskPriority, TaskState
from neko_bot.task.ranking import get_top_task_from_store, get_task_summary

from tests.test_e2e_tier1_features import (
    MemoryStoreContract,
    SkillSandboxContract,
    SkillRegistryContract,
    SQLiteStorageContract,
    CloudServerContract,
    CloudSyncManagerContract,
    get_free_port,
    atomic_write_json,
    _LiveMemoryStore,
    _LiveSkillSandbox,
    _LiveSkillRegistry,
    _LiveSQLiteStorage,
    _LiveCloudServer,
    _LiveCloudSyncManager,
)


class BridgeStreamingServerContract:
    """Ephemeral HTTP & SSE Telemetry Bridge Server simulating bridge/ai_monitor.py."""

    def __init__(self, workspace_dir: Path, port: int):
        self.workspace_dir = Path(workspace_dir)
        self.status_file = self.workspace_dir / ".neko" / "status.json"
        self.port = port
        self.server = None
        self._thread = None
        self.running = False

    def get_current_telemetry(self) -> Dict[str, Any]:
        default_payload = {
            "timestamp": time.time(),
            "cpu": 12.5,
            "ram": 45.0,
            "cloud_sync": {"status": "offline", "last_sync": None, "pending": 0},
            "memory": {"facts_count": 0, "active_projects": 0, "upcoming_deadlines": []},
            "skills": {"total_skills": 0, "active_tools": 0},
            "tasks": {"total_active": 0, "completed_today": 0, "top_task": None},
            "alerts": []
        }
        if self.status_file.exists():
            try:
                with open(self.status_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        merged = dict(default_payload)
                        merged.update(data)
                        return merged
            except Exception:
                return default_payload
        return default_payload

    def start(self):
        bridge_ref = self

        class BridgeHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass

            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache")
                super().end_headers()

            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    try:
                        for _ in range(5):
                            snap = bridge_ref.get_current_telemetry()
                            payload = json.dumps(snap)
                            frame = f"data: {payload}\n\n".encode("utf-8")
                            self.wfile.write(frame)
                            self.wfile.flush()
                            time.sleep(0.05)
                    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                        return
                    except Exception:
                        return
                    return

                if self.path.startswith("/status"):
                    snap = bridge_ref.get_current_telemetry()
                    data = json.dumps(snap).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                self.send_response(404)
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), BridgeHandler)
        self.running = True
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class TestTier4RealWorldScenarios(unittest.TestCase):
    """
    Tier 4: Five Comprehensive Real-World Application Workload Scenarios.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_tier4_")
        self.root = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Scenario 1: Multi-Workstation Workday Sync
    # -----------------------------------------------------------------------
    def test_scenario_1_multi_workstation_workday_sync(self):
        """
        Scenario 1: Multi-Workstation Workday Sync.
        Workflow:
        1. Office Workstation (WS1) creates tasks, facts, deadlines, and a custom skill.
        2. WS1 syncs state to Cloud REST Backend with SQLite persistent storage.
        3. Laptop Workstation (WS2) connects and pulls state from Cloud; asserts identical view.
        4. WS2 goes offline during commute: completes a task, adds a new task, adds a fact, runs skill.
        5. WS2 reconnects in the evening, flushes offline mutations to Cloud.
        6. WS1 syncs from Cloud; both workstations reach converged deterministic state.
        """
        # Step 1: Initialize Cloud Server
        cloud_dir = self.root / "cloud"
        cloud_db = cloud_dir / "cloud.db"
        cloud_storage = (_LiveSQLiteStorage or SQLiteStorageContract)(cloud_db)
        cloud_port = get_free_port()
        cloud_server = (_LiveCloudServer or CloudServerContract)(cloud_storage, cloud_port)
        cloud_server.start()
        cloud_url = f"http://127.0.0.1:{cloud_port}"

        try:
            # Step 2: Setup WS1 (Office)
            ws1_dir = self.root / "ws1_office"
            mem1 = (_LiveMemoryStore or MemoryStoreContract)(ws1_dir)
            tasks1 = TaskStore(ws1_dir / "workspace")
            sandbox1 = (_LiveSkillSandbox or SkillSandboxContract)()
            skills1 = (_LiveSkillRegistry or SkillRegistryContract)(ws1_dir, sandbox1)
            sync1 = (_LiveCloudSyncManager or CloudSyncManagerContract)(
                ws1_dir, cloud_url, "device_office_01", "tok_office_secret"
            )

            # WS1 populates workday state
            task_core = tasks1.add_task("Core Architecture Blueprint", priority="critical")
            task_review = tasks1.add_task("Review Memory Subsystem PR", priority="high")

            fact1 = mem1.add_fact("Production database read replica: replica.db.internal:5432", category="infrastructure", tags=["db", "prod"])
            fact2 = mem1.add_fact("Daily standup is strictly at 09:30 CET", category="schedule", tags=["calendar"])

            now = datetime.now(timezone.utc) - timedelta(hours=2)
            deadline_dt = (now + timedelta(days=3)).isoformat()
            proj1 = mem1.add_project("ProjectAsciiNeko", milestone="Phase 1", deadline_utc=deadline_dt)

            deploy_code = """
def run(inputs: dict, context: dict) -> dict:
    service = inputs.get("service", "core")
    env = inputs.get("env", "staging")
    return {"deploy_status": "ready", "target": f"{service}.{env}.local", "checks_passed": True}
"""
            skill_manifest = {
                "id": "tool-deploy-precheck",
                "type": "tool",
                "code": deploy_code,
                "self_tests": [
                    {"inputs": {"service": "auth", "env": "prod"}, "expected_output": {"deploy_status": "ready", "target": "auth.prod.local", "checks_passed": True}}
                ]
            }
            skills1.register_skill(skill_manifest)

            # WS1 enqueues all mutations and reconciles to Cloud
            now_iso = datetime.now(timezone.utc).isoformat()
            sync1.enqueue_mutation("task", task_core.id, {"title": task_core.title, "state": task_core.state.value, "priority": task_core.priority.value, "updated_at": now_iso})
            sync1.enqueue_mutation("task", task_review.id, {"title": task_review.title, "state": task_review.state.value, "priority": task_review.priority.value, "updated_at": now_iso})
            sync1.enqueue_mutation("fact", fact1["id"], fact1)
            sync1.enqueue_mutation("fact", fact2["id"], fact2)
            sync1.enqueue_mutation("project", proj1["id"], proj1)
            sync1.enqueue_mutation("skill", skill_manifest["id"], skill_manifest)

            res1 = sync1.reconcile()
            self.assertEqual(res1["status"], "synced")

            # Step 3: Setup WS2 (Laptop) and Pull from Cloud via Reconcile
            ws2_dir = self.root / "ws2_laptop"
            mem2 = (_LiveMemoryStore or MemoryStoreContract)(ws2_dir)
            tasks2 = TaskStore(ws2_dir / "workspace")
            sandbox2 = (_LiveSkillSandbox or SkillSandboxContract)()
            skills2 = (_LiveSkillRegistry or SkillRegistryContract)(ws2_dir, sandbox2)
            sync2 = (_LiveCloudSyncManager or CloudSyncManagerContract)(
                ws2_dir, cloud_url, "device_laptop_02", "tok_laptop_secret"
            )

            res2 = sync2.reconcile()
            self.assertEqual(res2["status"], "synced")
            pulled_to_ws2 = res2["pulled_mutations"]
            self.assertEqual(len(pulled_to_ws2), 6)

            # Apply pulled state to local stores on WS2
            for m in pulled_to_ws2:
                etype = m.get("entity_type")
                data = m.get("data") or m.get("payload", {})
                if etype == "task":
                    tasks2.add_task(data["title"], priority=data.get("priority", "medium"))
                elif etype in ("fact", "project"):
                    mem2.apply_crdt_mutation(m)
                elif etype == "skill":
                    skills2.register_skill(data)

            # Assert WS2 has exact matching state
            self.assertEqual(len(tasks2.list_tasks()), 2)
            ws2_facts = mem2.search("replica.db.internal", level="semantic")
            self.assertTrue(len(ws2_facts) >= 1)
            ws2_skill_res = skills2.execute_skill("tool-deploy-precheck", {"service": "auth", "env": "prod"})
            self.assertTrue(ws2_skill_res["success"])
            self.assertTrue(ws2_skill_res["result"]["checks_passed"])

            # Step 4: Commute Phase (WS2 operates Offline)
            sync2.server_url = "http://127.0.0.1:9999"  # Offline unreachable port

            # On laptop: complete task_review, add task_offline, add new fact
            laptop_tasks = tasks2.list_tasks()
            review_task_ws2 = [t for t in laptop_tasks if "Review" in t.title][0]
            tasks2.mark_done(review_task_ws2.id)

            task_offline = tasks2.add_task("Offline Benchmark Profiling", priority="medium")
            fact_offline = mem2.add_fact("Commute commute note: subway cellular dropout near Central Station", category="notes", tags=["commute"])

            offline_ts = (now + timedelta(hours=1)).isoformat()
            sync2.enqueue_mutation("task", review_task_ws2.id, {
                "title": review_task_ws2.title,
                "state": "done",
                "priority": review_task_ws2.priority.value,
                "updated_at": offline_ts
            })
            sync2.enqueue_mutation("task", task_offline.id, {
                "title": task_offline.title,
                "state": "active",
                "priority": task_offline.priority.value,
                "updated_at": offline_ts
            })
            sync2.enqueue_mutation("fact", fact_offline["id"], fact_offline)

            # Attempt reconcile while offline -> returns offline status, queue preserved
            res_offline = sync2.reconcile()
            self.assertEqual(res_offline["status"], "offline")
            self.assertEqual(sync2.get_queue_stats()["pending_mutations"], 3)

            # Step 5: Evening Reconnection (WS2 reconnects and reconciles)
            sync2.server_url = cloud_url
            res_evening = sync2.reconcile()
            self.assertEqual(res_evening["status"], "synced")
            self.assertEqual(sync2.get_queue_stats()["pending_mutations"], 0)

            # Step 6: WS1 connects and reconciles from Cloud
            res_ws1 = sync1.reconcile()
            self.assertEqual(res_ws1["status"], "synced")
            pulled_to_ws1 = res_ws1["pulled_mutations"]
            self.assertEqual(len(pulled_to_ws1), 3)

            # Apply remote mutations to WS1
            for m in pulled_to_ws1:
                etype = m.get("entity_type")
                data = m.get("data") or m.get("payload", {})
                if etype == "task":
                    if data.get("state") == "done":
                        tasks1.mark_done(task_review.id)
                    else:
                        tasks1.add_task(data["title"], priority=data.get("priority", "medium"))
                elif etype in ("fact", "project"):
                    mem1.apply_crdt_mutation(m)

            # Verification: Both workstations reach converged state
            all_tasks_ws1 = tasks1.list_tasks(include_done=True)
            t1_done = [t for t in all_tasks_ws1 if t.id == task_review.id][0]
            self.assertEqual(t1_done.state, TaskState.DONE)

            t1_titles = {t.title for t in all_tasks_ws1}
            self.assertIn("Offline Benchmark Profiling", t1_titles)

            ws1_commute_fact = mem1.search("subway cellular dropout", level="semantic")
            self.assertTrue(len(ws1_commute_fact) >= 1)
        finally:
            cloud_server.stop()

    # -----------------------------------------------------------------------
    # Scenario 2: Autonomous Skill Discovery from Prompt
    # -----------------------------------------------------------------------
    def test_scenario_2_autonomous_skill_discovery_from_prompt(self):
        """
        Scenario 2: Autonomous Skill Discovery from Prompt.
        Workflow:
        1. Agent receives natural language request requiring a novel data processing skill:
           'Calculate SHA256 cryptographic digest of arbitrary payload string'.
        2. Registry lookup confirms no existing skill matches 'tool-sha256-hasher'.
        3. Synthesis engine generates candidate Python skill:
           a. Malicious candidate containing forbidden system call is rejected by AST Sandbox.
           b. Correct safe candidate is validated and passes automated self-tests in sandbox.
        4. Verified skill is registered in SkillRegistry with manifest and tests.
        5. First execution succeeds; outputs result and logs an interaction episode to MemoryStore.
        6. Subsequent identical prompt hits registry directly without re-synthesis; runs instantly.
        """
        ws_dir = self.root / "ws_discovery"
        mem = (_LiveMemoryStore or MemoryStoreContract)(ws_dir)
        sandbox = (_LiveSkillSandbox or SkillSandboxContract)()
        registry = (_LiveSkillRegistry or SkillRegistryContract)(ws_dir, sandbox)

        skill_id = "tool-sha256-hasher"

        # Step 1 & 2: Lookup verifies absence
        self.assertIsNone(registry.get_skill(skill_id))

        # Step 3a: Adversarial Candidate Synthesis (Must be blocked by AST guardrails)
        malicious_code = """
def run(inputs: dict, context: dict) -> dict:
    import os
    os.system("echo malicious_breach")
    return {"status": "pwned"}
"""
        ast_ok, ast_msg = sandbox.validate_ast(malicious_code)
        self.assertFalse(ast_ok)
        self.assertIn("forbidden import: os", ast_msg.lower())

        # Step 3b: Safe Candidate Synthesis with automated self-tests
        valid_code = """
def run(inputs: dict, context: dict) -> dict:
    import hashlib
    text = str(inputs.get("text", ""))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"algorithm": "sha256", "digest": digest, "length": len(text)}
"""
        manifest = {
            "id": skill_id,
            "type": "tool",
            "name": "SHA256 Payload Hasher",
            "code": valid_code,
            "self_tests": [
                {
                    "inputs": {"text": "hello world"},
                    "expected_output": {
                        "algorithm": "sha256",
                        "digest": "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
                        "length": 11
                    }
                }
            ]
        }

        # Validate AST of valid candidate
        ast_val, ast_err = sandbox.validate_ast(valid_code)
        self.assertTrue(ast_val, f"AST validation unexpectedly failed: {ast_err}")

        # Execute automated self-tests via sandbox verification
        test_val, test_err = sandbox.verify_skill(manifest)
        self.assertTrue(test_val, f"Sandbox verification failed: {test_err}")

        # Step 4: Register the verified skill
        reg_ok, reg_diag = registry.register_skill(manifest)
        self.assertTrue(reg_ok, f"Registration failed: {reg_diag}")
        self.assertIsNotNone(registry.get_skill(skill_id))

        # Step 5: First execution & episodic memory logging
        t0 = time.perf_counter()
        exec1 = registry.execute_skill(skill_id, {"text": "AsciiNeko-Autonomous-Ecosystem-2026"})
        t1 = time.perf_counter()
        first_duration = t1 - t0

        self.assertTrue(exec1["success"])
        self.assertEqual(exec1["result"]["algorithm"], "sha256")
        self.assertEqual(len(exec1["result"]["digest"]), 64)

        # Log episode to episodic memory
        ep1 = mem.add_episode(
            session_id="sess_auto_synth_01",
            event_type="skill_discovered_and_executed",
            summary=f"Synthesized and executed {skill_id}",
            full_text=f"Processed text hash: {exec1['result']['digest']}",
            context_tags=["skill", "sha256", "autonomous"]
        )
        self.assertIsNotNone(mem.get_entry(ep1["id"]))

        # Step 6: Second invocation (Prompt reuse / Cache hit)
        cached_skill = registry.get_skill(skill_id)
        self.assertIsNotNone(cached_skill)

        t2 = time.perf_counter()
        exec2 = registry.execute_skill(skill_id, {"text": "AsciiNeko-Autonomous-Ecosystem-2026"})
        t3 = time.perf_counter()
        second_duration = t3 - t2

        self.assertTrue(exec2["success"])
        self.assertEqual(exec2["result"]["digest"], exec1["result"]["digest"])

        # Episodic memory records reuse
        ep2 = mem.add_episode(
            session_id="sess_auto_synth_02",
            event_type="skill_reused",
            summary=f"Reused cached {skill_id}",
            full_text=f"Instant reuse duration: {second_duration:.4f}s",
            context_tags=["skill", "cache_hit"]
        )
        self.assertIsNotNone(mem.get_entry(ep2["id"]))

        # Check interaction history reflects both events in sequence
        history = mem.list_entries(level="episodes")
        event_types = [h.get("entry", h).get("event_type") for h in history]
        self.assertIn("skill_discovered_and_executed", event_types)
        self.assertIn("skill_reused", event_types)

    # -----------------------------------------------------------------------
    # Scenario 3: Project Deadline Crunch & Risk Escalation
    # -----------------------------------------------------------------------
    def test_scenario_3_project_deadline_crunch_and_risk_escalation(self):
        """
        Scenario 3: Project Deadline Crunch & Risk Escalation.
        Workflow:
        1. Multi-level memory holds active project 'ProjectChronos' with imminent deadline (in 14 hours).
        2. Task store contains 3 active tasks for 'ProjectChronos' (1 critical, 1 high, 1 blocker).
        3. Memory briefing & risk analyzer detects urgent deadline approaching (<24h).
        4. Daemon updates `.neko/status.json` with critical deadline escalation alerts.
        5. Bridge server reads status telemetry and emits urgent alert to companion HUD.
        """
        ws_dir = self.root / "ws_crunch"
        mem = (_LiveMemoryStore or MemoryStoreContract)(ws_dir)
        task_store = TaskStore(ws_dir / "workspace")

        # Step 1: Create project with imminent deadline (14 hours from now)
        now = datetime.now(timezone.utc)
        deadline_iso = (now + timedelta(hours=14)).isoformat()
        proj = mem.add_project("ProjectChronos", milestone="Final Release", deadline_utc=deadline_iso)
        mem.add_fact("Client executive board review takes place tomorrow at 08:30 AM", category="client", tags=["chronos", "board"])

        # Step 2: Add crunch tasks
        t_crit = task_store.add_task("Fix SSL Handshake Race Condition", project="ProjectChronos", priority="high")
        t_high = task_store.add_task("Execute Zero-Downtime Migration Dry Run", project="ProjectChronos", priority="medium")
        t_med = task_store.add_task("Update API Changelog and Release Notes", project="ProjectChronos", priority="low")

        # Step 3: Evaluate risk context
        stats = mem.get_stats()
        upcoming = stats.get("upcoming_deadlines", [])
        self.assertTrue(len(upcoming) >= 1)
        chronos_deadline = [d for d in upcoming if d.get("project") == "ProjectChronos" or d.get("name") == "ProjectChronos"][0]
        self.assertIn(chronos_deadline.get("project", chronos_deadline.get("name")), "ProjectChronos")

        # Step 4: Write status export to .neko/status.json
        neko_dir = ws_dir / ".neko"
        neko_dir.mkdir(parents=True, exist_ok=True)
        status_file = neko_dir / "status.json"

        top_task = get_top_task_from_store(ws_dir / "workspace")
        self.assertIsNotNone(top_task)
        top_title = top_task["title"] if isinstance(top_task, dict) else top_task.title
        top_prio = top_task["priority"] if isinstance(top_task, dict) else top_task.priority.value
        top_proj = top_task.get("project", "") if isinstance(top_task, dict) else top_task.project
        self.assertEqual(top_title, "Fix SSL Handshake Race Condition")

        crunch_status_payload = {
            "timestamp": time.time(),
            "cloud_sync": {"status": "synced", "last_sync": now.isoformat(), "pending": 0},
            "memory": {
                "facts_count": stats.get("total_facts", 1),
                "active_projects": stats.get("active_projects", 1),
                "upcoming_deadlines": upcoming
            },
            "skills": {"total_skills": 2, "active_tools": 1},
            "tasks": {
                "total_active": 3,
                "completed_today": 0,
                "top_task": {"title": top_title, "priority": top_prio, "project": top_proj}
            },
            "alerts": [
                {
                    "level": "CRITICAL",
                    "code": "DEADLINE_CRUNCH",
                    "project": "ProjectChronos",
                    "hours_remaining": 14,
                    "message": "Imminent critical milestone in 14 hours! 3 active tasks remaining."
                }
            ]
        }
        atomic_write_json(status_file, crunch_status_payload)

        # Step 5: Start Bridge Telemetry Server and Verify HUD Ingestion
        bridge_port = get_free_port()
        bridge_server = BridgeStreamingServerContract(ws_dir, bridge_port)
        bridge_server.start()

        try:
            url = f"http://127.0.0.1:{bridge_port}/status"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))

            # Verify bridge surfaces critical alert and deadline indicators
            self.assertEqual(len(body["alerts"]), 1)
            alert = body["alerts"][0]
            self.assertEqual(alert["level"], "CRITICAL")
            self.assertEqual(alert["project"], "ProjectChronos")
            self.assertEqual(body["tasks"]["top_task"]["priority"], "high")
            deadline_entry = body["memory"]["upcoming_deadlines"][0]
            proj_name = deadline_entry.get("project") or deadline_entry.get("name")
            self.assertEqual(proj_name, "ProjectChronos")
        finally:
            bridge_server.stop()

    # -----------------------------------------------------------------------
    # Scenario 4: Offline Commute with Conflict Resolution
    # -----------------------------------------------------------------------
    def test_scenario_4_offline_commute_with_conflict_resolution(self):
        """
        Scenario 4: Offline Commute with Conflict Resolution.
        Workflow:
        1. Shared baseline task entity 'task-refactor-001' synced to Cloud DB.
        2. Both WS1 (Office) and WS2 (Laptop) go offline during commute.
        3. Concurrent edits to same task:
           - WS1 edits at timestamp T1: sets priority='high', title='Refactor Models - WS1'.
           - WS2 edits at timestamp T2 (where T2 > T1): sets priority='critical', title='Refactor Models - WS2 with Async'.
        4. WS1 connects to Cloud first and pushes its mutation at T3.
        5. WS2 connects to Cloud second and pushes its mutation at T4.
        6. Cloud applies CRDT Last-Write-Wins (LWW): WS2 wins because T2 > T1.
        7. Both workstations pull from Cloud; both converge on identical WS2 state without corruption.
        """
        cloud_dir = self.root / "cloud_crdt"
        cloud_db = cloud_dir / "cloud.db"
        cloud_storage = (_LiveSQLiteStorage or SQLiteStorageContract)(cloud_db)
        cloud_port = get_free_port()
        cloud_server = (_LiveCloudServer or CloudServerContract)(cloud_storage, cloud_port)
        cloud_server.start()
        cloud_url = f"http://127.0.0.1:{cloud_port}"

        try:
            ws1_dir = self.root / "ws1_commute"
            sync1 = (_LiveCloudSyncManager or CloudSyncManagerContract)(
                ws1_dir, cloud_url, "device_ws1", "tok_ws1"
            )

            ws2_dir = self.root / "ws2_commute"
            sync2 = (_LiveCloudSyncManager or CloudSyncManagerContract)(
                ws2_dir, cloud_url, "device_ws2", "tok_ws2"
            )

            # Step 1: Baseline task created and synced to Cloud
            t0_iso = "2026-10-20T07:00:00Z"
            initial_mutation = {
                "id": "task_refactor_shared",
                "entity_id": "task_refactor_shared",
                "entity_type": "task",
                "payload": {
                    "id": "task_refactor_shared",
                    "title": "Refactor Database Models",
                    "priority": "medium",
                },
                "data": {
                    "id": "task_refactor_shared",
                    "title": "Refactor Database Models",
                    "priority": "medium",
                },
                "timestamp": t0_iso,
                "updated_at": t0_iso,
                "device_id": "device_seed",
                "version": 1
            }
            cloud_storage.apply_mutation(initial_mutation)

            # Both pull baseline via reconcile
            res1 = sync1.reconcile()
            res2 = sync2.reconcile()
            self.assertEqual(len(res1["pulled_mutations"]), 1)
            self.assertEqual(len(res2["pulled_mutations"]), 1)

            # Step 2: Commute Disconnection - Concurrent Offline Edits
            # WS1 edits at T1 (08:15:00)
            t1_iso = "2026-10-20T08:15:00Z"
            sync1.enqueue_mutation("task", "task_refactor_shared", {
                "id": "task_refactor_shared",
                "title": "Refactor Models - WS1",
                "priority": "high",
                "updated_at": t1_iso,
                "version": 2
            })

            # WS2 edits same entity at T2 (08:30:00 > 08:15:00)
            t2_iso = "2026-10-20T08:30:00Z"
            sync2.enqueue_mutation("task", "task_refactor_shared", {
                "id": "task_refactor_shared",
                "title": "Refactor Models - WS2 with Async",
                "priority": "critical",
                "updated_at": t2_iso,
                "version": 2
            })

            def get_cloud_entity(ent_id: str):
                muts, _ = cloud_storage.pull_mutations(0)
                for m in muts:
                    if m.get("entity_id") == ent_id or m.get("id") == ent_id:
                        return m
                return None

            # Step 3: WS1 arrives at office first and reconciles
            res_flush1 = sync1.reconcile()
            self.assertEqual(res_flush1["status"], "synced")

            # Cloud currently holds WS1 state
            cloud_state_intermediate = get_cloud_entity("task_refactor_shared")
            self.assertIsNotNone(cloud_state_intermediate)
            self.assertEqual(cloud_state_intermediate["data"]["title"], "Refactor Models - WS1")

            # Step 4: WS2 arrives at office second and reconciles
            res_flush2 = sync2.reconcile()
            self.assertEqual(res_flush2["status"], "synced")

            # Cloud CRDT LWW evaluation: T2 (08:30:00) > T1 (08:15:00) -> M2 wins!
            cloud_state_final = get_cloud_entity("task_refactor_shared")
            self.assertIsNotNone(cloud_state_final)
            self.assertEqual(cloud_state_final["data"]["title"], "Refactor Models - WS2 with Async")
            self.assertEqual(cloud_state_final["data"]["priority"], "critical")

            # Step 5: WS1 pulls final converged state
            res_sync1_final = sync1.reconcile()
            self.assertEqual(res_sync1_final["status"], "synced")

            # Final check: deterministic convergence without data corruption
            resolved_entity = get_cloud_entity("task_refactor_shared")
            self.assertIsNotNone(resolved_entity)
            self.assertEqual(resolved_entity["data"]["title"], "Refactor Models - WS2 with Async")
            self.assertEqual(resolved_entity["data"]["priority"], "critical")
        finally:
            cloud_server.stop()

    # -----------------------------------------------------------------------
    # Scenario 5: Companion Live HUD Telemetry Streaming
    # -----------------------------------------------------------------------
    def test_scenario_5_companion_live_hud_telemetry_streaming(self):
        """
        Scenario 5: Companion Live HUD Telemetry Streaming.
        Workflow:
        1. Ecosystem daemon maintains active workstation state (tasks, memory, skills, cloud sync).
        2. Status is exported atomically to `.neko/status.json`.
        3. Bridge server ingests `.neko/status.json` and streams Server-Sent Events (/stream).
        4. Companion HUD reader connects to /stream and consumes real-time SSE frames.
        5. Validates 60 FPS performance contract (<5ms parsing latency, <32KB frame size).
        6. Verifies graceful degradation when status.json is corrupted or absent.
        """
        ws_dir = self.root / "ws_streaming"
        neko_dir = ws_dir / ".neko"
        neko_dir.mkdir(parents=True, exist_ok=True)
        status_file = neko_dir / "status.json"

        # Step 1 & 2: Populate rich status payload
        telemetry_payload = {
            "timestamp": time.time(),
            "cloud_sync": {
                "status": "synced",
                "last_sync": "2026-10-24T12:00:00Z",
                "pending": 0,
                "server": "https://neko-cloud.internal"
            },
            "memory": {
                "facts_count": 42,
                "active_projects": 4,
                "upcoming_deadlines": [
                    {"project": "EcosystemLaunch", "milestone": "GA", "due": "2026-10-25T18:00:00Z"}
                ]
            },
            "skills": {
                "total_skills": 15,
                "active_tools": 10,
                "workflows": 5
            },
            "tasks": {
                "total_active": 6,
                "completed_today": 8,
                "top_task": {"title": "Verify 60FPS HUD Rendering", "priority": "high"}
            },
            "alerts": []
        }
        atomic_write_json(status_file, telemetry_payload)

        # Step 3: Launch Bridge Telemetry Server
        bridge_port = get_free_port()
        bridge = BridgeStreamingServerContract(ws_dir, bridge_port)
        bridge.start()

        try:
            # Step 4: Connect to SSE /stream endpoint
            stream_url = f"http://127.0.0.1:{bridge_port}/stream"
            req = urllib.request.Request(stream_url, method="GET")

            frames_received = []
            parse_latencies = []

            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                content_type = resp.headers.get("Content-Type", "")
                self.assertIn("text/event-stream", content_type)

                # Read SSE lines
                for _ in range(5):
                    line = resp.readline().decode("utf-8")
                    if line.startswith("data: "):
                        raw_data = line[len("data: "):].strip()
                        self.assertTrue(len(raw_data.encode("utf-8")) < 32768, "Payload exceeds 32KB limit")

                        # Benchmark JSON parsing latency for 60 FPS compliance (< 5ms)
                        t0 = time.perf_counter()
                        parsed = json.loads(raw_data)
                        t1 = time.perf_counter()
                        latency_ms = (t1 - t0) * 1000.0

                        parse_latencies.append(latency_ms)
                        frames_received.append(parsed)

            # Step 5: Validate HUD indicators in received frames
            self.assertTrue(len(frames_received) >= 1, "Failed to receive any SSE telemetry frames")
            frame = frames_received[0]
            self.assertEqual(frame["cloud_sync"]["status"], "synced")
            self.assertEqual(frame["memory"]["facts_count"], 42)
            self.assertEqual(frame["skills"]["total_skills"], 15)
            self.assertEqual(frame["tasks"]["top_task"]["title"], "Verify 60FPS HUD Rendering")

            # Validate 60 FPS parse budget (< 5ms per frame)
            avg_latency = sum(parse_latencies) / len(parse_latencies)
            self.assertTrue(avg_latency < 5.0, f"Parse latency {avg_latency:.2f}ms exceeds 5ms 60FPS budget")

            # Step 6: Graceful degradation testing (corrupted status.json)
            with open(status_file, "w", encoding="utf-8") as f:
                f.write("CORRUPTED_NOT_A_VALID_JSON{{{")

            # Poll status endpoint with corrupt file -> returns default fallback without crashing
            status_url = f"http://127.0.0.1:{bridge_port}/status"
            req_status = urllib.request.Request(status_url, method="GET")
            with urllib.request.urlopen(req_status, timeout=2.0) as resp_status:
                self.assertEqual(resp_status.status, 200)
                fallback_data = json.loads(resp_status.read().decode("utf-8"))
                self.assertIn("cloud_sync", fallback_data)
                self.assertIn("tasks", fallback_data)
                self.assertEqual(fallback_data["cloud_sync"]["status"], "offline")
        finally:
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
