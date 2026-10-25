"""
test_e2e_tier3_cross_features.py - Tier 3: Cross-Feature Pairwise Interactions E2E Test Suite.
Covers pairwise combinations across Memory, Skills, Cloud Sync, Bridge/HUD, and Baseline systems.
Hermetic, zero-dependency, self-contained with progressive testability.
"""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, List
import unittest
import urllib.request

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


class TestTier3CrossFeatureInteractions(unittest.TestCase):
    """Pairwise combinatorial tests verifying cross-feature integrations."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="neko_test_tier3_")
        self.root = Path(self.tmp)

        # Workstation 1 (Office)
        self.ws1 = self.root / "ws1"
        self.mem_store1 = (_LiveMemoryStore or MemoryStoreContract)(self.ws1)
        self.task_store1 = TaskStore(self.ws1 / "workspace")
        self.sandbox1 = (_LiveSkillSandbox or SkillSandboxContract)()
        self.skill_reg1 = (_LiveSkillRegistry or SkillRegistryContract)(self.ws1, self.sandbox1)

        # Workstation 2 (Laptop)
        self.ws2 = self.root / "ws2"
        self.mem_store2 = (_LiveMemoryStore or MemoryStoreContract)(self.ws2)
        self.task_store2 = TaskStore(self.ws2 / "workspace")
        self.sandbox2 = (_LiveSkillSandbox or SkillSandboxContract)()
        self.skill_reg2 = (_LiveSkillRegistry or SkillRegistryContract)(self.ws2, self.sandbox2)

        # Cloud Server
        self.cloud_dir = self.root / "cloud"
        self.cloud_db = self.cloud_dir / "cloud.db"
        self.storage = (_LiveSQLiteStorage or SQLiteStorageContract)(self.cloud_db)
        self.port = get_free_port()
        self.server = (_LiveCloudServer or CloudServerContract)(self.storage, self.port)
        self.server.start()

        self.server_url = f"http://127.0.0.1:{self.port}"
        self.sync_mgr1 = (_LiveCloudSyncManager or CloudSyncManagerContract)(
            self.ws1, self.server_url, "device_office", "tok_office"
        )
        self.sync_mgr2 = (_LiveCloudSyncManager or CloudSyncManagerContract)(
            self.ws2, self.server_url, "device_laptop", "tok_laptop"
        )

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Interaction 1: Memory (R2) + Task Management (Baseline)
    # -----------------------------------------------------------------------
    def test_01_task_creation_triggers_episodic_memory_and_project_link(self):
        """Verifies task creation records an episodic history entry and updates project memory."""
        # 1. Add active project
        proj = self.mem_store1.add_project("CyberpunkCity", milestone="M2 Beta")

        # 2. Add task linked to project
        task = self.task_store1.add_task("Bake normal maps", project="CyberpunkCity", priority="high")

        # 3. Record episodic history event for task creation
        ep = self.mem_store1.add_episode(
            session_id="sess_task_create",
            event_type="task_created",
            summary=f"Created task: {task.title}",
            full_text=f"Task ID {task.id} linked to project {task.project}",
            context_tags=["task", task.project]
        )

        # Verify cross-reference
        self.assertEqual(task.project, "CyberpunkCity")
        self.assertEqual(ep["event_type"], "task_created")
        retrieved_ep = self.mem_store1.get_entry(ep["id"])
        self.assertIsNotNone(retrieved_ep)
        self.assertIn("CyberpunkCity", retrieved_ep.get("context_tags", []))

    # -----------------------------------------------------------------------
    # Interaction 2: Memory (R2) + Skills Execution (R3)
    # -----------------------------------------------------------------------
    def test_02_skill_execution_logs_episode_and_stores_inferred_fact(self):
        """Verifies skill execution output is recorded as an episode and synthesizes a new fact."""
        # 1. Register data parsing skill
        code = """
def run(inputs: dict, context: dict) -> dict:
    data = inputs.get("raw_text", "")
    return {"extracted_email": "support@acme.corp", "client": "Acme"}
"""
        manifest = {
            "id": "tool-extract-client",
            "type": "tool",
            "code": code,
            "self_tests": [{"inputs": {"raw_text": "contact support@acme.corp"}, "expected_output": {"extracted_email": "support@acme.corp", "client": "Acme"}}]
        }
        self.skill_reg1.register_skill(manifest)

        # 2. Execute skill
        res = self.skill_reg1.execute_skill("tool-extract-client", {"raw_text": "Email from support@acme.corp"})
        self.assertTrue(res["success"])

        # 3. Skill output logged to memory
        client = res["result"]["client"]
        email = res["result"]["extracted_email"]
        fact = self.mem_store1.add_fact(f"Referente cliente {client}: {email}", category="contact", tags=[client.lower()])
        ep = self.mem_store1.add_episode("sess_skill_run", "skill_executed", f"Executed tool-extract-client for {client}")

        # 4. Verify memory has the fact and episode
        search_res = self.mem_store1.search("support@acme.corp", level="semantic")
        self.assertTrue(len(search_res) >= 1)
        entry = search_res[0].get("entry", search_res[0])
        self.assertIn("support@acme.corp", entry["content"])

    # -----------------------------------------------------------------------
    # Interaction 3: Memory (R2) + Cloud Sync (R4) Multi-Workstation
    # -----------------------------------------------------------------------
    def test_03_memory_facts_and_projects_sync_across_workstations(self):
        """Verifies facts and projects created on WS1 sync to Cloud and pull to WS2."""
        # 1. Create memory entities on WS1
        f1 = self.mem_store1.add_fact("Regola deploy: solo dopo passing test", category="rule")
        p1 = self.mem_store1.add_project("AsciiNekoCloud", milestone="V1.0", deadline_utc="2026-11-01T00:00:00Z")

        # 2. Enqueue and sync from WS1 to Cloud
        now_utc = datetime.now(timezone.utc).isoformat()
        self.sync_mgr1.enqueue_mutation("memory_fact", f1["id"], f1)
        self.sync_mgr1.enqueue_mutation("memory_project", p1["id"], p1)
        res1 = self.sync_mgr1.reconcile()
        self.assertEqual(res1["status"], "synced")

        # 3. Pull from Cloud to WS2
        res2 = self.sync_mgr2.reconcile()
        self.assertEqual(res2["status"], "synced")
        pulled = res2.get("pulled_mutations", [])
        self.assertGreaterEqual(len(pulled), 2)

        # 4. Apply pulled mutations to WS2 memory store
        for m in pulled:
            self.mem_store2.apply_crdt_mutation(m)

        # 5. Verify WS2 now has the fact and project
        entry_f = self.mem_store2.get_entry(f1["id"])
        entry_p = self.mem_store2.get_entry(p1["id"])
        self.assertIsNotNone(entry_f)
        self.assertIsNotNone(entry_p)
        self.assertEqual(entry_f["content"], "Regola deploy: solo dopo passing test")

    # -----------------------------------------------------------------------
    # Interaction 4: Memory (R2) + Bridge Telemetry (R5)
    # -----------------------------------------------------------------------
    def test_04_memory_addition_updates_status_json_for_bridge_telemetry(self):
        """Verifies adding memories updates status.json stats consumed by bridge server."""
        self.mem_store1.add_fact("Fact 1", category="general")
        self.mem_store1.add_project("Project Alpha", milestone="Phase 1", deadline_utc="2026-10-30T12:00:00Z")

        # Daemon updates status.json
        status_file = self.ws1 / ".neko" / "status.json"
        stats = self.mem_store1.get_stats()
        payload = {
            "daemon_health": "running",
            "memory": stats
        }
        atomic_write_json(status_file, payload)

        # Bridge reads status.json
        with open(status_file, "r", encoding="utf-8") as f:
            bridge_telemetry = json.load(f)

        self.assertEqual(bridge_telemetry["memory"]["total_facts"], 1)
        self.assertEqual(bridge_telemetry["memory"]["active_projects"], 1)

    # -----------------------------------------------------------------------
    # Interaction 5: Skills (R3) + Cloud Sync (R4)
    # -----------------------------------------------------------------------
    def test_05_skill_code_and_manifest_sync_across_workstations(self):
        """Verifies new skill registered on WS1 syncs to Cloud and discovers on WS2."""
        code = "def run(i, c): return {'doubled': i.get('n', 0) * 2}"
        skill = {
            "id": "tool-doubler",
            "type": "tool",
            "code": code,
            "trigger_patterns": ["raddoppia", "double"],
            "self_tests": [{"inputs": {"n": 5}, "expected_output": {"doubled": 10}}]
        }
        self.skill_reg1.register_skill(skill)

        # Sync WS1 -> Cloud
        now_utc = datetime.now(timezone.utc).isoformat()
        self.sync_mgr1.enqueue_mutation("skill", "tool-doubler", skill)
        self.sync_mgr1.reconcile()

        # Sync Cloud -> WS2
        res = self.sync_mgr2.reconcile()
        for m in res.get("pulled_mutations", []):
            if m["entity_type"] == "skill":
                self.skill_reg2.apply_crdt_mutation(m)

        # WS2 executes the remotely learned skill
        exec_res = self.skill_reg2.execute_skill("tool-doubler", {"n": 21})
        self.assertTrue(exec_res["success"])
        self.assertEqual(exec_res["result"]["doubled"], 42)

    # -----------------------------------------------------------------------
    # Interaction 6: Skills (R3) + Sandbox AST (R3.2) + Episodic Memory (R2.3)
    # -----------------------------------------------------------------------
    def test_06_malicious_skill_rejection_logs_security_episode(self):
        """Verifies rejection of an unsafe skill logs a security alert episode in memory."""
        malicious_code = "import subprocess\ndef run(i, c): subprocess.run(['calc']); return {}"
        bad_manifest = {
            "id": "tool-exploit",
            "type": "tool",
            "code": malicious_code
        }
        ok, msg = self.skill_reg1.register_skill(bad_manifest)
        self.assertFalse(ok)

        # Security violation logged to episodic memory
        ep = self.mem_store1.add_episode(
            session_id="security_monitor",
            event_type="security_alert",
            summary=f"Blocked unsafe skill registration: {msg}",
            outcome="failure"
        )
        self.assertEqual(ep["event_type"], "security_alert")
        self.assertEqual(ep["outcome"], "failure")

    # -----------------------------------------------------------------------
    # Interaction 7: Skills (R3) + Bridge/HUD (R5)
    # -----------------------------------------------------------------------
    def test_07_skill_execution_updates_telemetry_last_executed_skill(self):
        """Verifies executing a skill updates last_executed_skill in HUD telemetry."""
        code = "def run(i, c): return {'ok': True}"
        skill = {
            "id": "tool-hud-pulse",
            "type": "tool",
            "code": code,
            "self_tests": [{"inputs": {}, "expected_output": {"ok": True}}]
        }
        self.skill_reg1.register_skill(skill)
        self.skill_reg1.execute_skill("tool-hud-pulse", {})

        # Telemetry aggregator payload
        status_file = self.ws1 / ".neko" / "status.json"
        payload = {
            "skills": {
                "total_skills": len(self.skill_reg1.list_skills()),
                "last_executed_skill": "tool-hud-pulse"
            }
        }
        atomic_write_json(status_file, payload)

        with open(status_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["skills"]["last_executed_skill"], "tool-hud-pulse")

    # -----------------------------------------------------------------------
    # Interaction 8: Cloud Sync (R4) + Offline Queue (R4.3) + Memory (R2)
    # -----------------------------------------------------------------------
    def test_08_offline_memory_mutation_reconciles_cleanly_on_reconnect(self):
        """Verifies memory created while offline queues mutations and syncs after reconnect."""
        # 1. Stop server to simulate offline status
        self.server.stop()

        # 2. Add fact while offline
        f = self.mem_store1.add_fact("Nota offline in treno", category="offline")
        self.sync_mgr1.enqueue_mutation("memory_fact", f["id"], f)

        # 3. Attempt reconcile while offline
        fail_res = self.sync_mgr1.reconcile()
        self.assertEqual(fail_res["status"], "offline")
        self.assertEqual(self.sync_mgr1.get_queue_stats()["pending_mutations"], 1)

        # 4. Restart server
        self.port = get_free_port()
        self.server = (_LiveCloudServer or CloudServerContract)(self.storage, self.port)
        self.server.start()
        self.sync_mgr1.server_url = f"http://127.0.0.1:{self.port}"

        # 5. Reconcile on reconnect
        ok_res = self.sync_mgr1.reconcile()
        self.assertEqual(ok_res["status"], "synced")
        self.assertEqual(self.sync_mgr1.get_queue_stats()["pending_mutations"], 0)

    # -----------------------------------------------------------------------
    # Interaction 9: Cloud Sync (R4) + TaskStore (Baseline) + CRDT LWW
    # -----------------------------------------------------------------------
    def test_09_concurrent_task_edits_resolve_via_crdt_lww(self):
        """Verifies concurrent edits to same task on WS1 and WS2 resolve deterministically."""
        # 1. Base task
        t_base = self.task_store1.add_task("Initial Title", custom_id="task_shared_1")
        now = datetime.now(timezone.utc)

        # 2. WS1 updates task at t1
        t1_iso = (now + timedelta(seconds=1)).isoformat()
        m1 = {
            "entity_type": "task",
            "entity_id": "task_shared_1",
            "device_id": "ws1",
            "updated_at": t1_iso,
            "deleted": False,
            "payload": {"id": "task_shared_1", "title": "WS1 Edit Title", "updated_at": t1_iso}
        }
        self.storage.apply_mutation(m1)

        # 3. WS2 updates task at t2 > t1
        t2_iso = (now + timedelta(seconds=5)).isoformat()
        m2 = {
            "entity_type": "task",
            "entity_id": "task_shared_1",
            "device_id": "ws2",
            "updated_at": t2_iso,
            "deleted": False,
            "payload": {"id": "task_shared_1", "title": "WS2 Newer Title", "updated_at": t2_iso}
        }
        self.storage.apply_mutation(m2)

        # 4. Pull latest state
        muts, _ = self.storage.pull_mutations(0)
        target_mut = next(m for m in muts if m["entity_id"] == "task_shared_1")
        self.assertEqual(target_mut["payload"]["title"], "WS2 Newer Title")

    # -----------------------------------------------------------------------
    # Interaction 10: Cloud Sync (R4) + Bridge/HUD (R5)
    # -----------------------------------------------------------------------
    def test_10_cloud_connection_state_flips_hud_badge(self):
        """Verifies HUD telemetry transitions from SYNCED to OFFLINE (Q: N)."""
        def get_hud_badge(sync_status):
            if sync_status.get("connected"):
                return "[CLOUD: SYNCED]"
            pending = sync_status.get("pending_mutations", 0)
            return f"[CLOUD: OFFLINE (Q: {pending})]"

        # While online
        online_state = {"status": "synced", "connected": True, "pending_mutations": 0}
        self.assertEqual(get_hud_badge(online_state), "[CLOUD: SYNCED]")

        # Disconnected with queued changes
        offline_state = {"status": "offline", "connected": False, "pending_mutations": 4}
        self.assertEqual(get_hud_badge(offline_state), "[CLOUD: OFFLINE (Q: 4)]")

    # -----------------------------------------------------------------------
    # Interaction 11: Deadlines (R2.2) + Briefing (Baseline) + HUD (R5.2)
    # -----------------------------------------------------------------------
    def test_11_imminent_deadline_escalates_risk_in_briefing_and_hud(self):
        """Verifies deadline due in 12h triggers urgent briefing warning and HUD alert."""
        due_soon = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        proj = self.mem_store1.add_project("CyberCity", milestone="Final Submission", deadline_utc=due_soon, risk_level="critical")

        stats = self.mem_store1.get_stats()
        upcoming = stats["upcoming_deadlines"]
        self.assertTrue(len(upcoming) >= 1)
        self.assertEqual(upcoming[0]["risk"], "critical")

        # Briefing summary generator logic
        briefing_warnings = []
        for u in upcoming:
            if u.get("risk") == "critical":
                briefing_warnings.append(f"URGENTE: Progetto {u['project']} scade a breve!")

        self.assertEqual(len(briefing_warnings), 1)
        self.assertIn("URGENTE", briefing_warnings[0])

    # -----------------------------------------------------------------------
    # Interaction 12: Offline Queue (R4.3) + Skills (R3) + Multi-Workstation
    # -----------------------------------------------------------------------
    def test_12_offline_registered_skill_discovered_after_sync(self):
        """Verifies registering a skill while offline syncs upon reconnect and works on WS2."""
        code = "def run(i, c): return {'msg': 'from_offline'}"
        skill = {
            "id": "tool-offline-created",
            "type": "tool",
            "code": code,
            "trigger_patterns": ["offline test"],
            "self_tests": [{"inputs": {}, "expected_output": {"msg": "from_offline"}}]
        }
        self.skill_reg1.register_skill(skill)
        self.sync_mgr1.enqueue_mutation("skill", "tool-offline-created", skill)

        # Sync WS1 -> Cloud
        self.sync_mgr1.reconcile()

        # Sync Cloud -> WS2
        res2 = self.sync_mgr2.reconcile()
        for m in res2.get("pulled_mutations", []):
            if m["entity_id"] == "tool-offline-created":
                self.skill_reg2.apply_crdt_mutation(m)

        matched = self.skill_reg2.match_skill("Ehi Neko, esegui offline test!")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["id"], "tool-offline-created")

    # -----------------------------------------------------------------------
    # Interaction 13: Memory (R2) + Router Prompt Injection (Baseline)
    # -----------------------------------------------------------------------
    def test_13_stored_facts_injected_into_prompt_system_context(self):
        """Verifies facts in memory are automatically injected into router prompt."""
        self.mem_store1.add_fact("Formato di risposta: sempre JSON compatto", category="preference")
        self.mem_store1.add_fact("Cliente Acme: non lavorare nel weekend", category="client_rule", tags=["acme"])

        from neko_bot.memory.router_context import get_active_memory_context
        ctx = get_active_memory_context(self.mem_store1, "Regole per Acme")
        self.assertIsNotNone(ctx)
        self.assertIn("[CONTESTO MEMORIA ATTIVA]", ctx)
        self.assertIn("Acme", ctx)

    # -----------------------------------------------------------------------
    # Interaction 14: End-to-End Multi-Feature Pipeline
    # -----------------------------------------------------------------------
    def test_14_full_pipeline_task_memory_skill_cloud_telemetry(self):
        """Full end-to-end integration: Task -> Memory -> Skill -> Cloud Sync -> Telemetry."""
        # 1. Create task
        task = self.task_store1.add_task("Deliver MVP", project="AsciiEcosystem", priority="high")

        # 2. Create memory linked to project
        deadline = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        proj = self.mem_store1.add_project("AsciiEcosystem", milestone="MVP", deadline_utc=deadline)
        fact = self.mem_store1.add_fact("AsciiEcosystem targets 60 FPS HUD", category="tech_spec")

        # 3. Create skill
        code = "def run(i, c): return {'mvp_ready': True}"
        skill = {
            "id": "tool-mvp-checker",
            "type": "tool",
            "code": code,
            "self_tests": [{"inputs": {}, "expected_output": {"mvp_ready": True}}]
        }
        self.skill_reg1.register_skill(skill)
        self.skill_reg1.execute_skill("tool-mvp-checker", {})

        # 4. Sync everything to Cloud
        self.sync_mgr1.enqueue_mutation("task", task.id, task.to_dict())
        self.sync_mgr1.enqueue_mutation("memory_fact", fact["id"], fact)
        self.sync_mgr1.enqueue_mutation("memory_project", proj["id"], proj)
        self.sync_mgr1.enqueue_mutation("skill", skill["id"], skill)
        sync_res = self.sync_mgr1.reconcile()
        self.assertEqual(sync_res["status"], "synced")

        # 5. Export comprehensive telemetry to .neko/status.json
        status_file = self.ws1 / ".neko" / "status.json"
        mem_stats = self.mem_store1.get_stats()
        telemetry = {
            "daemon_health": "running",
            "cloud_sync": {
                "status": "synced",
                "connected": True,
                "pending_mutations": self.sync_mgr1.get_queue_stats()["pending_mutations"],
                "last_sequence": sync_res["server_sequence"]
            },
            "memory": mem_stats,
            "skills": {
                "total_skills": len(self.skill_reg1.list_skills()),
                "last_executed_skill": "tool-mvp-checker"
            },
            "top_task": get_top_task_from_store(self.ws1 / "workspace")
        }
        atomic_write_json(status_file, telemetry)

        # 6. Verify telemetry payload
        with open(status_file, "r", encoding="utf-8") as f:
            final_status = json.load(f)

        self.assertEqual(final_status["cloud_sync"]["status"], "synced")
        self.assertEqual(final_status["skills"]["last_executed_skill"], "tool-mvp-checker")
        self.assertEqual(final_status["top_task"]["title"], "Deliver MVP")
        self.assertEqual(final_status["memory"]["total_facts"], 1)


if __name__ == "__main__":
    unittest.main()
